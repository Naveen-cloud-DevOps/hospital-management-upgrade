"""Laboratory service: test catalogue, lab orders and results.
Own database (hms-laboratorydb) and own S3 bucket (LAB_BUCKET) for uploaded report files (versioned, encrypted).
Order status: ordered -> sample-collected -> completed   (or cancelled)."""
import os, uuid
from typing import Optional
import psycopg2.errors
from fastapi import Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from common import *

app = base_app()
BUCKET = os.environ.get("LAB_BUCKET", "")
DDL = """
CREATE TABLE IF NOT EXISTS lab_tests(
 id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, sample_type TEXT, price NUMERIC(10,2) NOT NULL CHECK (price >= 0),
 active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE IF NOT EXISTS lab_orders(
 id SERIAL PRIMARY KEY, test_id INT NOT NULL, test_name TEXT NOT NULL, price NUMERIC(10,2) NOT NULL,
 patient_id INT, patient_name TEXT NOT NULL, patient_email TEXT, ordered_by TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'ordered' CHECK (status IN ('ordered','sample-collected','completed','cancelled')),
 result_text TEXT, report_key TEXT, completed_by TEXT, completed_at TIMESTAMPTZ, created_at TIMESTAMPTZ DEFAULT now());
"""
TESTS = [("Complete Blood Count (CBC)", "Blood", 350), ("Liver Function Test (LFT)", "Blood", 600),
         ("Blood Sugar - Fasting", "Blood", 120), ("Lipid Profile", "Blood", 500), ("Urine Routine", "Urine", 150),
         ("HBsAg (Hepatitis B)", "Blood", 400), ("Thyroid Profile (T3, T4, TSH)", "Blood", 550),
         ("Chest X-Ray", "Imaging", 450)]


@app.on_event("startup")
def start():
    init_db(DDL, ["lab_tests", "lab_orders"])
    for n, sample, price in TESTS:
        write("INSERT INTO lab_tests(name,sample_type,price) VALUES(%s,%s,%s) ON CONFLICT(name) DO NOTHING", (n, sample, price))


class TestIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    sample_type: str = Field("Blood", max_length=40)
    price: float = Field(ge=0, le=1000000)


class OrderIn(BaseModel):
    test_id: int
    patient_id: Optional[int] = None
    patient_name: str = Field(min_length=1, max_length=100)
    patient_email: Optional[str] = Field(None, max_length=150)


class StatusIn(BaseModel):
    status: str


def get_order(oid, u):
    rows = write("SELECT * FROM lab_orders WHERE id=%s", (oid,), fetch=True)
    if not rows:
        raise HTTPException(404, "Lab order not found")
    if u["role"] != "admin" and rows[0]["ordered_by"] != u["sub"]:
        raise HTTPException(403, "This lab order belongs to another user")
    return rows[0]


def order_text(o, title):
    lines = [f"RITIK HMS - {title}", "", f"Lab order ID: {o['id']}", f"Patient: {o['patient_name']}",
             f"Test: {o['test_name']}", f"Price: Rs. {float(o['price']):.2f}", f"Status: {o['status']}"]
    if o.get("result_text"):
        lines += ["", "RESULT", o["result_text"]]
    return "\n".join(lines + ["", "Thank you - Ritik HMS", ""])


@app.get("/lab/tests")
def tests(_=Depends(current_user)):
    return read("SELECT id,name,sample_type,price FROM lab_tests WHERE active ORDER BY id")


@app.post("/lab/tests")
def add_test(t: TestIn, _=Depends(admin_only)):
    try:
        row = write("INSERT INTO lab_tests(name,sample_type,price) VALUES(%s,%s,%s) RETURNING id",
                    (t.name.strip(), t.sample_type.strip(), t.price), fetch=True)[0]
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "This test already exists")
    return {"ok": True, "id": row["id"]}


@app.post("/lab/orders")
def order(b: OrderIn, u=Depends(current_user)):
    t = write("SELECT id,name,price FROM lab_tests WHERE id=%s AND active", (b.test_id,), fetch=True)
    if not t:
        raise HTTPException(404, "Test not found")
    email = (b.patient_email or "").strip().lower() or None
    row = write("INSERT INTO lab_orders(test_id,test_name,price,patient_id,patient_name,patient_email,ordered_by) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (t[0]["id"], t[0]["name"], t[0]["price"], b.patient_id, b.patient_name.strip(), email, u["sub"]), fetch=True)[0]
    o = get_order(row["id"], u)
    if email:
        notify(email, f"Lab test ordered - {o['test_name']} - Ritik HMS", order_text(o, "LAB TEST ORDERED"))
    return {"id": row["id"], "emailed_to": email}


@app.get("/lab/orders")
def orders(u=Depends(current_user)):
    q = ("SELECT id,test_id,test_name,price,patient_id,patient_name,patient_email,ordered_by,status,result_text,"
         "(report_key IS NOT NULL) AS has_report,completed_at,created_at FROM lab_orders ")
    if u["role"] == "admin":
        return write(q + "ORDER BY id DESC", fetch=True)           # primary: a new order shows up immediately
    return write(q + "WHERE ordered_by=%s ORDER BY id DESC", (u["sub"],), fetch=True)


@app.patch("/lab/orders/{oid}/status")
def set_status(oid: int, s: StatusIn, u=Depends(current_user)):
    o = get_order(oid, u)
    if s.status not in ("sample-collected", "cancelled"):
        raise HTTPException(400, "Status must be sample-collected or cancelled")
    if o["status"] in ("completed", "cancelled"):
        raise HTTPException(409, f"This order is already {o['status']}")
    write("UPDATE lab_orders SET status=%s WHERE id=%s", (s.status, oid))
    if s.status == "cancelled" and o.get("patient_email"):
        notify(o["patient_email"], f"Lab test cancelled - {o['test_name']}", order_text({**o, "status": "cancelled"}, "LAB TEST CANCELLED"))
    return {"ok": True, "status": s.status}


@app.post("/lab/orders/{oid}/result")
async def add_result(oid: int, result_text: str = Form(...), report: Optional[UploadFile] = File(None),
                     u=Depends(current_user)):
    o = get_order(oid, u)
    if o["status"] in ("completed", "cancelled"):
        raise HTTPException(409, f"This order is already {o['status']}")
    text = result_text.strip()[:4000]
    if not text:
        raise HTTPException(400, "Enter the result")
    key = None
    if report and report.filename:
        ext = os.path.splitext(report.filename)[1].lower()[:6] or ".bin"
        key = f"reports/order-{oid}-{uuid.uuid4().hex}{ext}"
    rows = write("UPDATE lab_orders SET status='completed', result_text=%s, report_key=%s, completed_by=%s, completed_at=now() "
                 "WHERE id=%s AND status IN ('ordered','sample-collected') RETURNING id",
                 (text, key, u["sub"], oid), fetch=True)
    if not rows:
        raise HTTPException(409, "This order was just updated by someone else")
    if key:
        put_bytes(BUCKET, key, await report.read(), report.content_type or "application/octet-stream")
    o = {**o, "status": "completed", "result_text": text}
    if o.get("patient_email"):
        notify(o["patient_email"], f"Lab result ready - {o['test_name']} - Ritik HMS", order_text(o, "LAB RESULT READY"),
               attachments=[(f"lab-result-{oid}.txt", order_text(o, "LAB RESULT"))])
    return {"ok": True, "status": "completed"}


@app.get("/lab/orders/{oid}/report")
def report_link(oid: int, u=Depends(current_user)):
    o = get_order(oid, u)
    if not o.get("report_key"):
        raise HTTPException(404, "No report file was uploaded for this order")
    return {"url": presign(BUCKET, o["report_key"])}