import os
from typing import Optional
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from common import *

app = base_app()
BUCKET = os.environ.get("RECEIPT_BUCKET", "")
DDL = """CREATE TABLE IF NOT EXISTS payments(
 id SERIAL PRIMARY KEY, patient_id INT NOT NULL, patient_name TEXT, patient_email TEXT,
 amount NUMERIC(12,2) NOT NULL CHECK (amount > 0), method TEXT, receipt_key TEXT,
 created_by TEXT, created_at TIMESTAMPTZ DEFAULT now());"""


@app.on_event("startup")
def start():
    init_db(DDL, ["payments"])


class Payment(BaseModel):
    patient_id: int
    patient_name: str
    patient_email: Optional[str] = None
    amount: float = Field(gt=0)
    method: str = "cash"


def receipt_text(r):
    when = r["created_at"]
    when = f"{when:%Y-%m-%d %H:%M}" if hasattr(when, "strftime") else str(when)[:16]
    return (f"HOSPITAL MANAGEMENT - PAYMENT RECEIPT\n\nReceipt #: {r['id']}\nDate: {when}\n"
            f"Patient: {r['patient_name']} (ID {r['patient_id']})\nAmount: {float(r['amount']):.2f}\n"
            f"Method: {r['method']}\nReceived by: {r['created_by']}\n\nThank you.\n")


def unique(*emails):
    return list(dict.fromkeys(e for e in emails if e))


def own_payment(pid, u):
    rows = read("SELECT * FROM payments WHERE id=%s", (pid,))
    if not rows:
        raise HTTPException(404, "Receipt not found")
    if u["role"] != "admin" and rows[0]["created_by"] != u["sub"]:
        raise HTTPException(403, "This receipt belongs to another user")
    return rows[0]


@app.post("/payments")
def pay(p: Payment, u=Depends(current_user)):
    row = write("INSERT INTO payments(patient_id,patient_name,patient_email,amount,method,created_by) "
                "VALUES(%s,%s,%s,%s,%s,%s) RETURNING id, created_at",
                (p.patient_id, p.patient_name, p.patient_email, p.amount, p.method, u["sub"]), fetch=True)[0]
    key = f"receipts/receipt-{row['id']}.txt"
    text = receipt_text({"id": row["id"], "created_at": row["created_at"], "patient_name": p.patient_name,
                         "patient_id": p.patient_id, "amount": p.amount, "method": p.method, "created_by": u["sub"]})
    put_bytes(BUCKET, key, text.encode(), "text/plain")
    write("UPDATE payments SET receipt_key=%s WHERE id=%s", (key, row["id"]))
    sent = unique(u["sub"], p.patient_email)          # staff member who took the payment + the patient
    for to in sent:
        notify(to, f"Payment receipt #{row['id']}", text)
    return {"id": row["id"], "receipt_url": presign(BUCKET, key), "emailed_to": sent,
            "created_at": row["created_at"].isoformat()}


@app.get("/payments")
def payments(u=Depends(current_user)):
    if u["role"] == "admin":
        return read("SELECT * FROM payments ORDER BY id DESC")
    return read("SELECT * FROM payments WHERE created_by=%s ORDER BY id DESC", (u["sub"],))


@app.get("/payments/{pid}/receipt")
def receipt(pid: int, u=Depends(current_user)):
    r = own_payment(pid, u)
    r["receipt_url"] = presign(BUCKET, r["receipt_key"]) if r.get("receipt_key") else None
    return r


@app.post("/payments/{pid}/email")
def email_receipt(pid: int, u=Depends(current_user)):
    r = own_payment(pid, u)
    sent = unique(u["sub"], r["patient_email"])
    for to in sent:
        notify(to, f"Payment receipt #{r['id']}", receipt_text(r))
    return {"emailed_to": sent}
