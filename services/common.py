"""Shared helpers: DB (primary=write, replica=read), JWT auth, S3, SNS."""
import json, os, smtplib, ssl, time
from email.message import EmailMessage
from email.utils import formataddr
import boto3, jwt, psycopg2
from botocore.config import Config
from fastapi import Depends, FastAPI, Header, HTTPException
from psycopg2.extras import RealDictCursor

REGION = os.environ.get("AWS_REGION", "us-east-1")
SECRET = os.environ.get("JWT_SECRET", "dev-secret")
TOPIC = os.environ.get("SNS_TOPIC_ARN", "")
SMTP_USER = os.environ.get("SMTP_USER", "ritikranjanpatispiderhacker@gmail.com")   # sender (Gmail)
SMTP_PASS = os.environ.get("SMTP_PASS", "").replace(" ", "")                        # Gmail App Password
SMTP_NAME = os.environ.get("SMTP_NAME", "Hospital Management")
s3 = boto3.client("s3", region_name=REGION, config=Config(signature_version="s3v4"))
sns = boto3.client("sns", region_name=REGION)


def base_app():
    app = FastAPI()
    app.get("/healthz")(lambda: {"ok": True})
    return app


def _run(host, sql, args, fetch):
    c = psycopg2.connect(host=host, dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"],
                         password=os.environ["DB_PASS"], connect_timeout=5)
    try:
        with c, c.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, args)
            return cur.fetchall() if fetch else None
    finally:
        c.close()


def write(sql, args=None, fetch=False):   # PRIMARY (-rw service)
    return _run(os.environ["DB_RW_HOST"], sql, args, fetch)


def read(sql, args=None):                 # REPLICA (-ro service)
    return _run(os.environ["DB_RO_HOST"], sql, args, True)


def init_db(ddl, tables):
    """Create tables + a trigger that blocks every DELETE (saved data can never be deleted)."""
    sql = "SELECT pg_advisory_xact_lock(42);" + ddl + (
        "CREATE OR REPLACE FUNCTION no_delete() RETURNS trigger AS $$ BEGIN "
        "RAISE EXCEPTION 'Deleting saved data is not allowed'; END $$ LANGUAGE plpgsql;")
    for t in tables:
        sql += (f"DROP TRIGGER IF EXISTS nd ON {t}; CREATE TRIGGER nd BEFORE DELETE ON {t} "
                "FOR EACH ROW EXECUTE FUNCTION no_delete();")
    for _ in range(40):
        try:
            return write(sql)
        except psycopg2.OperationalError:
            time.sleep(3)
    raise RuntimeError("database not reachable")


# ---------- auth ----------
def make_token(email, role):
    return jwt.encode({"sub": email, "role": role, "exp": int(time.time()) + 8 * 3600}, SECRET, algorithm="HS256")


def current_user(authorization: str = Header("")):
    try:
        return jwt.decode(authorization.removeprefix("Bearer "), SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")


def admin_only(u=Depends(current_user)):
    if u["role"] != "admin":
        raise HTTPException(403, "Admins only")
    return u


# ---------- S3 ----------
def put_bytes(bucket, key, data, content_type="application/octet-stream"):
    s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type, ServerSideEncryption="AES256")


def put_json(bucket, key, obj):
    put_bytes(bucket, key, json.dumps(obj).encode(), "application/json")


def presign(bucket, key, secs=900):
    return s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=secs)


# ---------- Email: sent from the Gmail account above (SMTP), not AWS SNS ----------
def subscribe(email):
    """Kept so old calls still work. Gmail SMTP needs no per-address confirmation."""
    return None


def notify(email, subject, message, strict=False):
    """Send mail from the Gmail account. strict=True raises an error that says WHY it failed (used by 'Send test email')."""
    if not SMTP_PASS:
        print("email not sent: SMTP_PASS is not set")
        if strict:
            raise HTTPException(502, "Email not configured: SMTP_PASS (Gmail App Password) is empty in the cluster secret")
        return False
    try:
        m = EmailMessage()
        m["From"] = formataddr((SMTP_NAME, SMTP_USER))
        m["To"] = email
        m["Subject"] = subject[:150]
        m.set_content(message)
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as srv:
            srv.starttls(context=ssl.create_default_context())
            srv.login(SMTP_USER, SMTP_PASS)
            srv.send_message(m)
        return True
    except Exception as e:
        print("email failed", repr(e))
        if strict:
            raise HTTPException(502, f"Email failed: {e}")
        return False


# ---------- SMS (AWS SNS direct to phone number) ----------
def send_sms(phone, message):
    """Direct SMS to a phone number (E.164, e.g. +919876543210). Returns True/False."""
    try:
        sns.publish(PhoneNumber=phone, Message=message[:600],
                    MessageAttributes={"AWS.SNS.SMS.SMSType": {"DataType": "String", "StringValue": "Transactional"}})
        return True
    except Exception as e:
        print("sns sms failed", e)
        return False