"""Doctor service: the doctor directory (name, speciality, qualification, consultation fee).
Own database (hms-doctordb). The appointment service asks this service for doctor details when a booking is made.
Saved data is never deleted: a doctor who leaves is switched to active=false."""
from typing import Optional
import psycopg2.errors
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from common import *

app = base_app()
DDL = """
CREATE TABLE IF NOT EXISTS doctors(
 id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, speciality TEXT NOT NULL, qualification TEXT,
 fee INT NOT NULL DEFAULT 500 CHECK (fee >= 0), active BOOLEAN NOT NULL DEFAULT TRUE,
 created_at TIMESTAMPTZ DEFAULT now());
"""
# Fixed ids 1-6 = the same ids the appointment service already stored in old bookings.
DOCTORS = [(1, "Dr. Anil Kumar", "General Physician", "MBBS, MD", 500),
           (2, "Dr. Sunitha Reddy", "Hepatologist (Liver Specialist)", "MBBS, MD, DM", 1000),
           (3, "Dr. Rajesh Varma", "Gastroenterologist", "MBBS, MD, DM", 900),
           (4, "Dr. Priya Sharma", "Cardiologist", "MBBS, MD, DM", 1000),
           (5, "Dr. Mohan Rao", "Orthopedic Surgeon", "MBBS, MS (Ortho)", 800),
           (6, "Dr. Lakshmi Devi", "Pediatrician", "MBBS, DCH", 600)]


@app.on_event("startup")
def start():
    init_db(DDL, ["doctors"])
    for i, n, sp, q, fee in DOCTORS:
        write("INSERT INTO doctors(id,name,speciality,qualification,fee) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
              (i, n, sp, q, fee))
    write("SELECT setval(pg_get_serial_sequence('doctors','id'), (SELECT MAX(id) FROM doctors))")


class DoctorIn(BaseModel):
    name: str = Field(min_length=3, max_length=100)
    speciality: str = Field(min_length=2, max_length=100)
    qualification: str = Field("", max_length=100)
    fee: int = Field(500, ge=0, le=100000)


class DoctorPatch(BaseModel):
    speciality: Optional[str] = Field(None, min_length=2, max_length=100)
    qualification: Optional[str] = Field(None, max_length=100)
    fee: Optional[int] = Field(None, ge=0, le=100000)
    active: Optional[bool] = None


@app.get("/doctors")
def list_doctors(_=Depends(current_user)):
    return read("SELECT id,name,speciality,qualification,fee FROM doctors WHERE active ORDER BY id")


@app.get("/doctors/{did}")
def get_doctor(did: int, _=Depends(current_user)):
    """Used by the appointment service. Reads the primary so a doctor added a moment ago is found."""
    rows = write("SELECT id,name,speciality,qualification,fee,active FROM doctors WHERE id=%s", (did,), fetch=True)
    if not rows:
        raise HTTPException(404, "Doctor not found")
    return rows[0]


@app.post("/doctors")
def add_doctor(d: DoctorIn, _=Depends(admin_only)):
    try:
        row = write("INSERT INTO doctors(name,speciality,qualification,fee) VALUES(%s,%s,%s,%s) RETURNING id",
                    (d.name.strip(), d.speciality.strip(), d.qualification.strip(), d.fee), fetch=True)[0]
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "A doctor with this name already exists")
    return {"ok": True, "id": row["id"]}


@app.patch("/doctors/{did}")
def update_doctor(did: int, p: DoctorPatch, _=Depends(admin_only)):
    changes = {k: v for k, v in p.model_dump().items() if v is not None}
    if not changes:
        raise HTTPException(400, "Nothing to change")
    sets = ",".join(f"{k}=%s" for k in changes)          # keys come from the model above, never from the user
    rows = write(f"UPDATE doctors SET {sets} WHERE id=%s RETURNING id", [*changes.values(), did], fetch=True)
    if not rows:
        raise HTTPException(404, "Doctor not found")
    return {"ok": True}
@app.put("/doctors/{did}")
def edit_doctor(did: int, d: DoctorIn, _=Depends(admin_only)):
    try:
        rows = write("UPDATE doctors SET name=%s, speciality=%s, qualification=%s, fee=%s WHERE id=%s RETURNING id",
                     (d.name.strip(), d.speciality.strip(), d.qualification.strip(), d.fee, did), fetch=True)
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "A doctor with this name already exists")
    if not rows:
        raise HTTPException(404, "Doctor not found")
    return {"ok": True}


@app.delete("/doctors/{did}")
def delete_doctor(did: int, _=Depends(admin_only)):
    """Same rule as the rest of this service: the row is kept, the doctor is switched to active=false and
    disappears from every list (the 6 starter doctors would otherwise be re-created on the next restart)."""
    rows = write("UPDATE doctors SET active=FALSE WHERE id=%s RETURNING id", (did,), fetch=True)
    if not rows:
        raise HTTPException(404, "Doctor not found")
    return {"ok": True}