"""Pharmacy service: medicine stock, sales and bills.
Own database (hms-pharmacydb) and own S3 bucket (PHARMACY_BUCKET) for the saved bills (versioned, encrypted).
A sale lowers the stock; stock can never go below zero."""
import os
from typing import List, Optional
import psycopg2.errors
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from common import *

app = base_app()
BUCKET = os.environ.get("PHARMACY_BUCKET", "")
DDL = """
CREATE TABLE IF NOT EXISTS medicines(
 id SERIAL PRIMARY KEY, name TEXT UNIQUE NOT NULL, unit TEXT NOT NULL DEFAULT 'tablet',
 unit_price NUMERIC(10,2) NOT NULL CHECK (unit_price >= 0), stock INT NOT NULL DEFAULT 0 CHECK (stock >= 0),
 created_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE IF NOT EXISTS pharmacy_sales(
 id SERIAL PRIMARY KEY, patient_id INT, patient_name TEXT NOT NULL, patient_email TEXT,
 payment_method TEXT NOT NULL DEFAULT 'cash', total NUMERIC(10,2) NOT NULL, items TEXT NOT NULL,
 sold_by TEXT NOT NULL, bill_key TEXT, created_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE IF NOT EXISTS pharmacy_sale_items(
 id SERIAL PRIMARY KEY, sale_id INT NOT NULL REFERENCES pharmacy_sales(id), medicine_id INT NOT NULL,
 medicine_name TEXT NOT NULL, unit_price NUMERIC(10,2) NOT NULL, qty INT NOT NULL CHECK (qty > 0));
"""
MEDICINES = [("Paracetamol 500 mg", "tablet", 2, 500), ("Amoxicillin 500 mg", "capsule", 12, 200),
             ("Cetirizine 10 mg", "tablet", 3, 300), ("Pantoprazole 40 mg", "tablet", 9, 250),
             ("Metformin 500 mg", "tablet", 2.5, 400), ("Azithromycin 500 mg", "tablet", 25, 150),
             ("ORS sachet", "sachet", 20, 200), ("Cough syrup 100 ml", "bottle", 85, 80)]


@app.on_event("startup")
def start():
    init_db(DDL, ["medicines", "pharmacy_sales", "pharmacy_sale_items"])
    for n, unit, price, stock in MEDICINES:
        write("INSERT INTO medicines(name,unit,unit_price,stock) VALUES(%s,%s,%s,%s) ON CONFLICT(name) DO NOTHING",
              (n, unit, price, stock))


class MedicineIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    unit: str = Field("tablet", max_length=30)
    unit_price: float = Field(ge=0, le=1000000)
    stock: int = Field(0, ge=0, le=1000000)


class RestockIn(BaseModel):
    qty: int = Field(gt=0, le=1000000)


class Line(BaseModel):
    medicine_id: int
    qty: int = Field(gt=0, le=1000)


class SaleIn(BaseModel):
    patient_id: Optional[int] = None
    patient_name: str = Field(min_length=1, max_length=100)
    patient_email: Optional[str] = Field(None, max_length=150)
    payment_method: str = "cash"
    items: List[Line]


def bill_text(sale, lines):
    rows = [f"  {l['medicine_name']} x{l['qty']} @ Rs. {float(l['unit_price']):.2f} = Rs. {float(l['unit_price']) * l['qty']:.2f}"
            for l in lines]
    return "\n".join(["CAREPULSE - PHARMACY BILL", "", f"Bill no: {sale['id']}", f"Patient: {sale['patient_name']}",
                      f"Payment: {sale['payment_method']}", "", *rows, "", f"TOTAL: Rs. {float(sale['total']):.2f}", "",
                      "Thank you - CarePulse", ""])


def save_bill(sale, lines):
    """Store the bill in S3 (best effort) and remember its key."""
    key = f"bills/sale-{sale['id']}.txt"
    try:
        put_bytes(BUCKET, key, bill_text(sale, lines).encode(), "text/plain; charset=utf-8")
        write("UPDATE pharmacy_sales SET bill_key=%s WHERE id=%s", (key, sale["id"]))
    except Exception as e:
        print("pharmacy bill upload failed", e)
        return None
    return key


@app.get("/pharmacy/medicines")
def medicines(_=Depends(current_user)):
    return write("SELECT id,name,unit,unit_price,stock FROM medicines ORDER BY name", fetch=True)  # primary: stock is always current


@app.post("/pharmacy/medicines")
def add_medicine(m: MedicineIn, _=Depends(admin_only)):
    try:
        row = write("INSERT INTO medicines(name,unit,unit_price,stock) VALUES(%s,%s,%s,%s) RETURNING id",
                    (m.name.strip(), (m.unit or "tablet").strip(), m.unit_price, m.stock), fetch=True)[0]
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "This medicine already exists")
    return {"ok": True, "id": row["id"]}


@app.post("/pharmacy/medicines/{mid}/restock")
def restock(mid: int, r: RestockIn, _=Depends(admin_only)):
    row = write("UPDATE medicines SET stock=stock+%s WHERE id=%s RETURNING stock", (r.qty, mid), fetch=True)
    if not row:
        raise HTTPException(404, "Medicine not found")
    return {"ok": True, "stock": row[0]["stock"]}


@app.post("/pharmacy/sales")
def sell(b: SaleIn, u=Depends(current_user)):
    if b.payment_method not in ("cash", "upi", "card"):
        raise HTTPException(400, "Payment must be cash, upi or card")
    if not b.items or len(b.items) > 50:
        raise HTTPException(400, "Add between 1 and 50 medicines")
    want = {}
    for it in b.items:                       # same medicine twice -> one line
        want[it.medicine_id] = want.get(it.medicine_id, 0) + it.qty
    taken, lines = [], []
    try:
        for mid, qty in want.items():        # lower the stock first; the condition stops overselling
            r = write("UPDATE medicines SET stock=stock-%s WHERE id=%s AND stock>=%s RETURNING id,name,unit_price",
                      (qty, mid, qty), fetch=True)
            if not r:
                m = write("SELECT name,stock FROM medicines WHERE id=%s", (mid,), fetch=True)
                raise HTTPException(409, f"Only {m[0]['stock']} of {m[0]['name']} in stock" if m else "Medicine not found")
            taken.append((mid, qty))
            lines.append({"medicine_id": mid, "medicine_name": r[0]["name"], "unit_price": r[0]["unit_price"], "qty": qty})
        total = round(sum(float(l["unit_price"]) * l["qty"] for l in lines), 2)
        summary = ", ".join(f"{l['medicine_name']} x{l['qty']}" for l in lines)[:500]
        email = (b.patient_email or "").strip().lower() or None
        sale = write("INSERT INTO pharmacy_sales(patient_id,patient_name,patient_email,payment_method,total,items,sold_by) "
                     "VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id,patient_name,payment_method,total",
                     (b.patient_id, b.patient_name.strip(), email, b.payment_method, total, summary, u["sub"]), fetch=True)[0]
        for l in lines:
            write("INSERT INTO pharmacy_sale_items(sale_id,medicine_id,medicine_name,unit_price,qty) VALUES(%s,%s,%s,%s,%s)",
                  (sale["id"], l["medicine_id"], l["medicine_name"], l["unit_price"], l["qty"]))
    except Exception:
        for mid, qty in taken:               # sale failed: give the stock back
            write("UPDATE medicines SET stock=stock+%s WHERE id=%s", (qty, mid))
        raise
    save_bill(sale, lines)
    if email:
        text = bill_text(sale, lines)
        notify(email, f"Pharmacy bill #{sale['id']} - CarePulse", text, attachments=[(f"pharmacy-bill-{sale['id']}.txt", text)])
    return {"id": sale["id"], "total": total, "emailed_to": email}


@app.get("/pharmacy/sales")
def sales(u=Depends(current_user)):
    q = "SELECT id,patient_name,items,total,payment_method,created_at FROM pharmacy_sales "
    if u["role"] == "admin":
        return write(q + "ORDER BY id DESC", fetch=True)
    return write(q + "WHERE sold_by=%s ORDER BY id DESC", (u["sub"],), fetch=True)


@app.get("/pharmacy/sales/{sid}/bill")
def bill(sid: int, u=Depends(current_user)):
    rows = write("SELECT * FROM pharmacy_sales WHERE id=%s", (sid,), fetch=True)
    if not rows:
        raise HTTPException(404, "Sale not found")
    sale = rows[0]
    if u["role"] != "admin" and sale["sold_by"] != u["sub"]:
        raise HTTPException(403, "This sale belongs to another user")
    key = sale.get("bill_key")
    if not key:                              # the first upload failed: build it again
        lines = write("SELECT medicine_id,medicine_name,unit_price,qty FROM pharmacy_sale_items WHERE sale_id=%s", (sid,), fetch=True)
        key = save_bill(sale, lines)
        if not key:
            raise HTTPException(503, "The bill could not be saved right now. Please try again.")
    return {"url": presign(BUCKET, key)}
