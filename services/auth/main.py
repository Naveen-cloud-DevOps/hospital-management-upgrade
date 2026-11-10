import hashlib, hmac, os, secrets
from datetime import datetime, timezone
import psycopg2.errors
from fastapi import Depends, HTTPException
from pydantic import BaseModel
from common import *

app = base_app()
DDL = """CREATE TABLE IF NOT EXISTS users(
 id SERIAL PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK (role IN ('admin','employee')), created_at TIMESTAMPTZ DEFAULT now());"""


def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    return f"{salt}${hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 200_000).hex()}"


def check_pw(pw, stored):
    return hmac.compare_digest(hash_pw(pw, stored.split("$")[0]), stored)


@app.on_event("startup")
def start():
    init_db(DDL, ["users"])
    e, p = os.environ["ADMIN_EMAIL"], os.environ["ADMIN_PASSWORD"]
    write("INSERT INTO users(email,name,password_hash,role) VALUES(%s,'Admin',%s,'admin') ON CONFLICT(email) DO NOTHING",
          (e, hash_pw(p)))
    subscribe(e)


class NewUser(BaseModel):
    email: str
    name: str
    password: str
    role: str = "employee"


class Login(BaseModel):
    email: str
    password: str


@app.post("/auth/register")
def register(u: NewUser, _=Depends(admin_only)):
    try:
        write("INSERT INTO users(email,name,password_hash,role) VALUES(%s,%s,%s,%s)",
              (u.email, u.name, hash_pw(u.password), u.role))
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "User already exists")
    put_json(os.environ["USER_BUCKET"], f"users/{u.email}.json", {"email": u.email, "name": u.name, "role": u.role})
    subscribe(u.email)
    return {"ok": True}


@app.post("/auth/login")
def login(b: Login):
    # read from the primary so a user an admin just created can log in immediately (replica may lag)
    rows = write("SELECT * FROM users WHERE lower(email)=lower(%s)", (b.email.strip(),), fetch=True)
    if not rows or not check_pw(b.password, rows[0]["password_hash"]):
        raise HTTPException(401, "Invalid credentials")
    u = rows[0]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    notify(u["email"], "Welcome to our Hospital Management",
           f"Hi {u['name']},\n\nWelcome to our Hospital Management system!\n"
           f"You signed in on {now}.\n\n"
           "You can now book a doctor appointment from the Appointments page. "
           "We will email you the doctor name, date and time as soon as it is booked.\n\n"
           "Thank you,\nHospital Management Team")
    return {"token": make_token(u["email"], u["role"]), "role": u["role"], "name": u["name"]}


@app.get("/auth/users")
def users(_=Depends(admin_only)):
    return read("SELECT id,email,name,role,created_at FROM users ORDER BY id")


@app.post("/auth/notifications/resend")
def resend_confirmation(u=Depends(current_user)):
    """Ask AWS to send the 'Confirm subscription' email again (needed once per address)."""
    subscribe(u["sub"])
    return {"ok": True, "email": u["sub"]}


@app.post("/auth/notifications/test")
def test_email(u=Depends(current_user)):
    notify(u["sub"], "Test email - Hospital Management",
           "This is a test. If you can read this, email alerts and payment receipts will reach you.", strict=True)
    return {"ok": True, "email": u["sub"]}