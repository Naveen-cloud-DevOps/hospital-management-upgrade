"""Shared helpers: DB (primary=write, replica=read), JWT auth, S3, SNS."""
import json, os, smtplib, ssl, time, urllib.error, urllib.request
from email.message import EmailMessage
from email.utils import formataddr
import boto3, jwt, psycopg2
from botocore.config import Config
from fastapi import Depends, FastAPI, Header, HTTPException
from psycopg2.extras import RealDictCursor

REGION = os.environ.get("AWS_REGION", "us-east-1")
SECRET = os.environ.get("JWT_SECRET", "dev-secret")
TOPIC = os.environ.get("SNS_TOPIC_ARN", "")
# in-cluster address of the doctor service (the appointment service asks it for doctor details)
DOCTOR_URL = os.environ.get("DOCTOR_SERVICE_URL", "http://doctor-service.hms.svc.cluster.local")
SMTP_USER = os.environ.get("SMTP_USER", "ritikranjanspiderhacker@gmail.com")   # sender (Gmail)
SMTP_PASS = os.environ.get("SMTP_PASS", "").replace(" ", "")                        # Gmail App Password
SMTP_NAME = os.environ.get("SMTP_NAME", "Ritik HMS")
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
        "IF current_setting('app.allow_delete', true) = 'on' THEN RETURN OLD; END IF; "
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


def notify(email, subject, message, strict=False, attachments=None):
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
        for fname, text in (attachments or []):          # [(filename, text), ...]
            m.add_attachment(text.encode(), maintype="text", subtype="plain", filename=fname)
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


# ---------- service-to-service calls (e.g. appointment -> doctor) ----------
def call_service(base, path, method="GET", payload=None, timeout=5):
    """Call another HMS service inside the cluster with a short-lived internal token (role 'service').
    Raises HTTPException with the other service's status code, or 502 if it cannot be reached."""
    req = urllib.request.Request(
        base.rstrip("/") + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": "Bearer " + make_token("internal-service", "service"),
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read()).get("detail", "Request failed")
        except Exception:
            detail = "Request failed"
        raise HTTPException(e.code, detail)
    except Exception as e:
        print("service call failed", base, path, repr(e))
        raise HTTPException(502, "A required service is not reachable right now. Please try again.")


def write_allow_delete(sql, args=None, fetch=False):
    """Used ONLY by the explicit Delete endpoints. Runs sql in one transaction with the delete guard lifted for
    that transaction alone; every other statement is still blocked by the no_delete trigger."""
    return write("SELECT set_config('app.allow_delete','on',true);" + sql, args, fetch)