"""Appointment service: doctors + bookings.
Own database (hms-appointmentdb) and own S3 bucket (APPOINTMENT_BUCKET).
Booking status is stored in two places:
  1. the `status` column of the `appointments` table (booked / cancelled / completed)
  2. a JSON record in S3: appointments/<id>.json (bucket is versioned, so every status change is kept)."""
import os, re
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
ALTER TABLE doctors ADD COLUMN IF NOT EXISTS fee INT NOT NULL DEFAULT 500;
CREATE TABLE IF NOT EXISTS appointments(
 id SERIAL PRIMARY KEY, doctor_id INT NOT NULL REFERENCES doctors(id), appt_date DATE NOT NULL, appt_time TEXT NOT NULL,
 patient_name TEXT NOT NULL, reason TEXT, booked_by TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'booked' CHECK (status IN ('booked','cancelled','completed')),
 status_updated_at TIMESTAMPTZ DEFAULT now(), record_key TEXT, created_at TIMESTAMPTZ DEFAULT now());
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS patient_mobile TEXT;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS alt_email TEXT;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS patient_email TEXT;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS payment_method TEXT;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS payment_status TEXT;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS sms_sent BOOLEAN;
CREATE UNIQUE INDEX IF NOT EXISTS one_booking_per_slot ON appointments(doctor_id, appt_date, appt_time) WHERE status<>'cancelled';
"""
DOCTORS = [("Dr. Anil Kumar", "General Physician", "MBBS, MD", 500),
           ("Dr. Sunitha Reddy", "Hepatologist (Liver Specialist)", "MBBS, MD, DM", 1000),
           ("Dr. Rajesh Varma", "Gastroenterologist", "MBBS, MD, DM", 900),
           ("Dr. Priya Sharma", "Cardiologist", "MBBS, MD, DM", 1000),
           ("Dr. Mohan Rao", "Orthopedic Surgeon", "MBBS, MS (Ortho)", 800),
           ("Dr. Lakshmi Devi", "Pediatrician", "MBBS, DCH", 600)]
SLOTS = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30",
         "14:00", "14:30", "15:00", "15:30", "16:00", "16:30"]      # 1 pm - 2 pm is the lunch break
IST = timezone(timedelta(hours=5, minutes=30))
PAY_METHODS = {"later": "Pay at hospital", "cash": "Cash", "upi": "UPI", "card": "Card"}   # later => Pending, others => Paid


def pay_line(a):
    m = PAY_METHODS.get(a.get("payment_method") or "later", "Pay at hospital")
    return f"{a.get('payment_status') or 'Pending'} - {m} - Rs. {a['fee']}"


def receipt_text(a):
    day, tm = pretty(a["appt_date"], a["appt_time"])
    return "\n".join(["RITIK HMS - PAYMENT RECEIPT", "", f"Booking ID: {a['id']}", f"Patient: {a['patient_name']}",
                      f"Doctor: {a['doctor']} ({a['speciality']})", f"Appointment: {day}, {tm}",
                      f"Amount paid: Rs. {a['fee']}", f"Method: {PAY_METHODS.get(a.get('payment_method'), '-')}",
                      "Status: Paid", "", "Thank you - Ritik HMS", ""])


@app.on_event("startup")
def start():
    init_db(DDL, ["doctors", "appointments"])
    for n, sp, q, fee in DOCTORS:
        write("INSERT INTO doctors(name,speciality,qualification,fee) VALUES(%s,%s,%s,%s) "
              "ON CONFLICT(name) DO UPDATE SET fee=EXCLUDED.fee", (n, sp, q, fee))


def clean_mobile(m):
    """Return +E.164 number. 10 digits => India (+91)."""
    m = re.sub(r"[\s\-()]", "", m or "")
    if re.fullmatch(r"[6-9]\d{9}", m):
        return "+91" + m
    if re.fullmatch(r"(91|0)?[6-9]\d{9}", m) and not m.startswith("+"):
        return "+91" + m[-10:]
    if re.fullmatch(r"\+\d{10,15}", m):
        return m
    raise HTTPException(400, "Enter a valid mobile number")


def now_ist():
    return datetime.now(IST)


def pretty(d, tm):
    return d.strftime("%A, %d %b %Y"), datetime.strptime(tm, "%H:%M").strftime("%I:%M %p").lstrip("0")


def appt_text(a, title):
    day, tm = pretty(a["appt_date"], a["appt_time"])
    return "\n".join([f"RITIK HMS - {title}", "",
                      f"Appointment ID: {a['id']}", f"Patient: {a['patient_name']}",
                      f"Doctor: {a['doctor']} ({a['speciality']})", f"Consultation fee: Rs. {a['fee']}",
                      f"Mobile: {a.get('patient_mobile') or '-'}", f"Date: {day}", f"Time: {tm}",
                      f"Status: {a['status']}", f"Payment: {pay_line(a)}", f"Reason: {a.get('reason') or '-'}", "",
                      "Please arrive 10 minutes early." if a["status"] == "booked" else "",
                      "Thank you for choosing Ritik HMS.", ""])


def save_record(a):
    """Write the booking + its current status to S3 (new object version on every change)."""
    key = f"appointments/{a['id']}.json"
    try:
        put_json(BUCKET, key, {"id": a["id"], "doctor": a["doctor"], "speciality": a["speciality"],
                               "date": str(a["appt_date"]), "time": a["appt_time"], "patient_name": a["patient_name"],
                               "reason": a.get("reason") or "", "booked_by": a["booked_by"], "status": a["status"],
                               "fee": a["fee"], "patient_mobile": a.get("patient_mobile") or "",
                               "patient_email": a.get("patient_email") or "",
                               "payment_method": a.get("payment_method") or "", "payment_status": a.get("payment_status") or "",
                               "confirmed_at": str(a["confirmed_at"]) if a.get("confirmed_at") else None,
                               "updated_at": now_ist().isoformat()})
        write("UPDATE appointments SET record_key=%s WHERE id=%s", (key, a["id"]))
    except Exception as e:
        print("appointment s3 record failed", e)


def get_full(aid):
    rows = write("SELECT a.*, d.name AS doctor, d.speciality, d.fee FROM appointments a JOIN doctors d ON d.id=a.doctor_id "
                 "WHERE a.id=%s", (aid,), fetch=True)
    if not rows:
        raise HTTPException(404, "Appointment not found")
    return rows[0]


class Booking(BaseModel):
    doctor_id: int
    date: Date
    time: str
    patient_name: str = ""
    patient_mobile: str = ""
    patient_email: str
    payment_method: str = "later"
    reason: str = ""


@app.get("/doctors")
def doctors(_=Depends(current_user)):
    return read("SELECT id,name,speciality,qualification,fee FROM doctors ORDER BY id")


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
    mobile = clean_mobile(b.patient_mobile)
    email = (b.patient_email or "").strip().lower()[:150]
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(400, "Enter a valid patient email")
    method = b.payment_method if b.payment_method in PAY_METHODS else "later"
    pstatus = "Pending" if method == "later" else "Paid"
    try:
        row = write("INSERT INTO appointments(doctor_id,appt_date,appt_time,patient_name,patient_mobile,patient_email,reason,booked_by,"
                    "payment_method,payment_status) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (b.doctor_id, b.date, b.time, name, mobile, email, b.reason.strip()[:300], u["sub"], method, pstatus), fetch=True)[0]
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "That time is already booked. Please pick another slot.")
    a = get_full(row["id"])
    save_record(a)
    files = [(f"receipt-booking-{a['id']}.txt", receipt_text(a))] if pstatus == "Paid" else None   # receipt attached only when paid
    notify(email, "Booking done from Ritik HMS", appt_text(a, "BOOKING DONE FROM RITIK HMS"), attachments=files)
    day, tm = pretty(b.date, b.time)
    return {"id": a["id"], "doctor": a["doctor"], "date": day, "time": tm, "status": a["status"],
            "fee": a["fee"], "payment_status": pstatus, "emailed_to": email}


@app.get("/appointments")
def my_appointments(u=Depends(current_user)):
    q = ("SELECT a.id,a.appt_date,a.appt_time,a.patient_name,a.patient_mobile,a.patient_email,a.alt_email,a.payment_method,a.payment_status,a.reason,a.status,a.booked_by,"
         "a.confirmed_at,a.sms_sent,d.name AS doctor,d.speciality,d.fee "
         "FROM appointments a JOIN doctors d ON d.id=a.doctor_id ")
    if u["role"] == "admin":
        return write(q + "ORDER BY a.appt_date DESC, a.appt_time DESC", fetch=True)
    return write(q + "WHERE a.booked_by=%s ORDER BY a.appt_date DESC, a.appt_time DESC", (u["sub"],), fetch=True)


def refund_amount(a):
    """Cancelling a paid booking refunds only 50% of the fee; the other half is kept as cancellation charge."""
    return int(a["fee"]) // 2


def refund_text(a):
    day, tm = pretty(a["appt_date"], a["appt_time"])
    r = refund_amount(a)
    return "\n".join(["RITIK HMS - REFUND RECEIPT", "", f"Booking ID: {a['id']}", f"Patient: {a['patient_name']}",
                      f"Doctor: {a['doctor']} ({a['speciality']})", f"Cancelled appointment: {day}, {tm}",
                      f"Amount paid: Rs. {a['fee']}", f"Refund (50%): Rs. {r}",
                      f"Cancellation charge (50%): Rs. {int(a['fee']) - r}",
                      f"Original payment method: {PAY_METHODS.get(a.get('payment_method'), '-')}",
                      "Refund status: Partially refunded (50%)", "",
                      "The refund will be returned to your original payment method.",
                      "For cash payments, please collect it from the Ritik HMS billing desk.", "", "Ritik HMS", ""])


def change_status(a, status, title):
    """Update booking status. On cancel: a paid booking gets a 50% refund, marked Partially Refunded, and a refund mail is sent."""
    refund = status == "cancelled" and a.get("payment_status") == "Paid"
    pstatus = "Partially Refunded" if refund else ("Cancelled" if status == "cancelled" else a.get("payment_status"))
    write("UPDATE appointments SET status=%s, payment_status=%s, status_updated_at=now() WHERE id=%s",
          (status, pstatus, a["id"]))
    a["status"], a["payment_status"] = status, pstatus
    save_record(a)
    to = a.get("patient_email") or a["booked_by"]
    if refund:
        notify(to, f"Appointment cancelled - Refund of Rs. {refund_amount(a)} (50%) - Ritik HMS",
               appt_text(a, title) + "\n" + refund_text(a),
               attachments=[(f"refund-booking-{a['id']}.txt", refund_text(a))])
    else:
        paid = status == "completed" and pstatus == "Paid"
        notify(to, f"{title.title()} - {a['doctor']}", appt_text(a, title),
               attachments=[(f"receipt-booking-{a['id']}.txt", receipt_text(a))] if paid else None)


@app.post("/appointments/{aid}/confirm")
def confirm(aid: int, _=Depends(admin_only)):
    """Admin clicks YES: mark confirmed and SMS the patient's mobile with name + appointment details."""
    a = get_full(aid)
    if a["status"] != "booked":
        raise HTTPException(409, "Only a booked appointment can be confirmed")
    if a.get("confirmed_at"):
        raise HTTPException(409, "Already confirmed")
    day, tm = pretty(a["appt_date"], a["appt_time"])
    sms = (f"Hello {a['patient_name']}, your appointment is CONFIRMED. "
           f"Dr: {a['doctor']} ({a['speciality']}), Date: {day}, Time: {tm}, "
           f"Fee: Rs.{a['fee']}, ID: {a['id']}. Please arrive 10 min early. - Hospital Management")
    sent = send_sms(a["patient_mobile"], sms) if a.get("patient_mobile") else False
    write("UPDATE appointments SET confirmed_at=now(), sms_sent=%s WHERE id=%s", (sent, aid))
    a["confirmed_at"] = now_ist()
    save_record(a)
    return {"ok": True, "sms_sent": sent, "to": a.get("patient_mobile")}


@app.post("/appointments/{aid}/cancel")
def cancel(aid: int, u=Depends(current_user)):
    a = get_full(aid)
    if u["role"] != "admin" and a["booked_by"] != u["sub"]:
        raise HTTPException(403, "This appointment belongs to another user")
    if a["status"] == "completed":
        raise HTTPException(409, "A completed appointment cannot be cancelled")
    refund = refund_amount(a) if a.get("payment_status") == "Paid" and a["status"] != "cancelled" else 0
    if a["status"] != "cancelled":
        change_status(a, "cancelled", "APPOINTMENT CANCELLED")
    return {"ok": True, "status": "cancelled", "refund": refund}


class PayIn(BaseModel):
    method: str


@app.post("/appointments/{aid}/pay")
def pay_appointment(aid: int, p: PayIn, u=Depends(current_user)):
    """Pay the consultation fee after booking (for bookings made with 'Pay at hospital')."""
    a = get_full(aid)
    if u["role"] != "admin" and a["booked_by"] != u["sub"]:
        raise HTTPException(403, "This appointment belongs to another user")
    if a["status"] != "booked":
        raise HTTPException(409, "Only a booked appointment can be paid")
    if a.get("payment_status") == "Paid":
        raise HTTPException(409, "This appointment is already paid")
    if p.method not in ("cash", "upi", "card"):
        raise HTTPException(400, "Choose cash, UPI or card")
    write("UPDATE appointments SET payment_method=%s, payment_status='Paid' WHERE id=%s", (p.method, aid))
    a["payment_method"], a["payment_status"] = p.method, "Paid"
    save_record(a)
    notify(a.get("patient_email") or a["booked_by"], "Payment received - Ritik HMS",
           appt_text(a, "PAYMENT RECEIVED"), attachments=[(f"receipt-booking-{aid}.txt", receipt_text(a))])
    return {"ok": True, "payment_status": "Paid", "fee": a["fee"]}


@app.post("/appointments/{aid}/complete")
def complete(aid: int, _=Depends(admin_only)):
    a = get_full(aid)
    if a["status"] != "booked":
        raise HTTPException(409, "Only a booked appointment can be completed")
    change_status(a, "completed", "APPOINTMENT COMPLETED")
    return {"ok": True, "status": "completed"}