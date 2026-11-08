"""Appointment service: doctors + bookings.
Own database (hms-appointmentdb) and own S3 bucket (APPOINTMENT_BUCKET).
Booking status is stored in two places:
  1. the `status` column of the `appointments` table (booked / cancelled / completed)
  2. a JSON record in S3: appointments/<id>.json (bucket is versioned, so every status change is kept)."""
import os
from datetime import date as Date, datetime, timedelta, timezone
import psycopg2.errors
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from common import *

app = base_app()
BUCKET = os.environ.get("APPOINTMENT_BUCKET", "")
DDL = """
CREATE TABLE IF NOT EXISTS doctors(
 id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, speciality TEXT NOT NULL, qualification TEXT,
 created_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE IF NOT EXISTS appointments(
 id SERIAL PRIMARY KEY, doctor_id INT NOT NULL REFERENCES doctors(id), appt_date DATE NOT NULL, appt_time TEXT NOT NULL,
 patient_name TEXT NOT NULL, reason TEXT, booked_by TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked','cancelled','completed')),
 status_updated_at TIMESTAMPTZ DEFAULT now(), record_key TEXT, created_at TIMESTAMPTZ DEFAULT now());
CREATE UNIQUE INDEX IF NOT EXISTS one_booking_per_slot ON appointments(doctor_id, appt_date, appt_time) WHERE status<>'cancelled';
"""
DOCTORS = [("Dr. Anil Kumar", "General Physician", "MBBS, MD"),
           ("Dr. Sunitha Reddy", "Hepatologist (Liver Specialist)", "MBBS, MD, DM"),
           ("Dr. Rajesh Varma", "Gastroenterologist", "MBBS, MD, DM"),
           ("Dr. Priya Sharma", "Cardiologist", "MBBS, MD, DM"),
           ("Dr. Mohan Rao", "Orthopedic Surgeon", "MBBS, MS (Ortho)"),
           ("Dr. Lakshmi Devi", "Pediatrician", "MBBS, DCH")]
SLOTS = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30",
         "14:00", "14:30", "15:00", "15:30", "16:00", "16:30"]      # 1 pm - 2 pm is the lunch break
IST = timezone(timedelta(hours=5, minutes=30))


@app.on_event("startup")
def start():
    init_db(DDL, ["doctors", "appointments"])
    for n, sp, q in DOCTORS:
        write("INSERT INTO doctors(name,speciality,qualification) VALUES(%s,%s,%s) ON CONFLICT(name) DO NOTHING", (n, sp, q))


def now_ist():
    return datetime.now(IST)


def pretty(d, tm):
    return d.strftime("%A, %d %b %Y"), datetime.strptime(tm, "%H:%M").strftime("%I:%M %p").lstrip("0")


def appt_text(a, title):
    day, tm = pretty(a["appt_date"], a["appt_time"])
    return "\n".join([f"HOSPITAL MANAGEMENT - {title}", "",
                      f"Appointment ID: {a['id']}", f"Patient: {a['patient_name']}",
                      f"Doctor: {a['doctor']} ({a['speciality']})", f"Date: {day}", f"Time: {tm}",
                      f"Status: {a['status']}", f"Reason: {a.get('reason') or '-'}", "",
                      "Please arrive 10 minutes early. You can cancel from the Appointments page.", ""])


def save_record(a):
    """Write the booking + its current status to S3 (new object version on every change)."""
    key = f"appointments/{a['id']}.json"
    try:
        put_json(BUCKET, key, {"id": a["id"], "doctor": a["doctor"], "speciality": a["speciality"],
                               "date": str(a["appt_date"]), "time": a["appt_time"], "patient_name": a["patient_name"],
                               "reason": a.get("reason") or "", "booked_by": a["booked_by"], "status": a["status"],
                               "updated_at": now_ist().isoformat()})
        write("UPDATE appointments SET record_key=%s WHERE id=%s", (key, a["id"]))
    except Exception as e:
        print("appointment s3 record failed", e)


def get_full(aid):
    rows = write("SELECT a.*, d.name AS doctor, d.speciality FROM appointments a JOIN doctors d ON d.id=a.doctor_id "
                 "WHERE a.id=%s", (aid,), fetch=True)
    if not rows:
        raise HTTPException(404, "Appointment not found")
    return rows[0]


class Booking(BaseModel):
    doctor_id: int
    date: Date
    time: str
    patient_name: str = ""
    reason: str = ""


@app.get("/doctors")
def doctors(_=Depends(current_user)):
    return read("SELECT id,name,speciality,qualification FROM doctors ORDER BY id")


@app.get("/doctors/{did}/slots")
def slots(did: int, date: Date, _=Depends(current_user)):
    taken = {r["appt_time"] for r in write("SELECT appt_time FROM appointments WHERE doctor_id=%s AND appt_date=%s "
                                           "AND status<>'cancelled'", (did, date), fetch=True)}
    now = now_ist()
    return [{"time": s, "available": s not in taken and not (
        date < now.date() or (date == now.date() and s <= now.strftime("%H:%M")))} for s in SLOTS]


@app.post("/appointments")
def book(b: Booking, u=Depends(current_user)):
    if b.time not in SLOTS:
        raise HTTPException(400, "Choose a valid time slot")
    now = now_ist()
    if b.date < now.date() or (b.date == now.date() and b.time <= now.strftime("%H:%M")):
        raise HTTPException(400, "Choose a date and time in the future")
    if b.date > now.date() + timedelta(days=60):
        raise HTTPException(400, "You can book up to 60 days ahead")
    if not write("SELECT 1 FROM doctors WHERE id=%s", (b.doctor_id,), fetch=True):
        raise HTTPException(404, "Doctor not found")
    name = (b.patient_name or "").strip()[:100] or u["sub"]
    try:
        row = write("INSERT INTO appointments(doctor_id,appt_date,appt_time,patient_name,reason,booked_by) "
                    "VALUES(%s,%s,%s,%s,%s,%s) RETURNING id",
                    (b.doctor_id, b.date, b.time, name, b.reason.strip()[:300], u["sub"]), fetch=True)[0]
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "That time is already booked. Please pick another slot.")
    a = get_full(row["id"])
    save_record(a)
    notify(u["sub"], f"Appointment confirmed - {a['doctor']}", appt_text(a, "APPOINTMENT CONFIRMED"))
    day, tm = pretty(b.date, b.time)
    return {"id": a["id"], "doctor": a["doctor"], "date": day, "time": tm, "status": a["status"], "emailed_to": u["sub"]}


@app.get("/appointments")
def my_appointments(u=Depends(current_user)):
    q = ("SELECT a.id,a.appt_date,a.appt_time,a.patient_name,a.reason,a.status,a.booked_by,d.name AS doctor,d.speciality "
         "FROM appointments a JOIN doctors d ON d.id=a.doctor_id ")
    if u["role"] == "admin":
        return write(q + "ORDER BY a.appt_date DESC, a.appt_time DESC", fetch=True)
    return write(q + "WHERE a.booked_by=%s ORDER BY a.appt_date DESC, a.appt_time DESC", (u["sub"],), fetch=True)


def change_status(a, status, title):
    write("UPDATE appointments SET status=%s, status_updated_at=now() WHERE id=%s", (status, a["id"]))
    a["status"] = status
    save_record(a)
    notify(a["booked_by"], f"{title.title()} - {a['doctor']}", appt_text(a, title))


@app.post("/appointments/{aid}/cancel")
def cancel(aid: int, u=Depends(current_user)):
    a = get_full(aid)
    if u["role"] != "admin" and a["booked_by"] != u["sub"]:
        raise HTTPException(403, "This appointment belongs to another user")
    if a["status"] == "completed":
        raise HTTPException(409, "A completed appointment cannot be cancelled")
    if a["status"] != "cancelled":
        change_status(a, "cancelled", "APPOINTMENT CANCELLED")
    return {"ok": True, "status": "cancelled"}


@app.post("/appointments/{aid}/complete")
def complete(aid: int, _=Depends(admin_only)):
    a = get_full(aid)
    if a["status"] != "booked":
        raise HTTPException(409, "Only a booked appointment can be completed")
    change_status(a, "completed", "APPOINTMENT COMPLETED")
    return {"ok": True, "status": "completed"}
