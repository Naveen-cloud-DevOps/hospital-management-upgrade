import hashlib, hmac, os, secrets, string
from datetime import datetime, timezone
import jwt
import psycopg2.errors
from botocore.exceptions import ClientError
from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from common import *

app = base_app()
DDL = """CREATE TABLE IF NOT EXISTS users(
 id SERIAL PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT NOT NULL, password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK (role IN ('admin','employee')), created_at TIMESTAMPTZ DEFAULT now());
ALTER TABLE users ADD COLUMN IF NOT EXISTS cognito_sub TEXT;"""

POOL = os.environ.get("COGNITO_USER_POOL_ID", "")
CLIENT = os.environ.get("COGNITO_CLIENT_ID", "")
cognito = boto3.client("cognito-idp", region_name=REGION)
MARK = "COGNITO"
PW_RULE = "Password must be at least 8 characters and include an upper-case letter, a lower-case letter and a number."


# ---------- old local hash (only used to move existing users into Cognito on first login) ----------
def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    return f"{salt}${hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 200_000).hex()}"


def check_pw(pw, stored):
    return "$" in stored and hmac.compare_digest(hash_pw(pw, stored.split("$")[0]), stored)


# ---------- Cognito helpers ----------
@app.exception_handler(ClientError)
async def aws_error(request, exc):
    print("aws error", repr(exc))
    return JSONResponse(status_code=502, content={"detail": "The sign-in service had a problem. Please try again."})


def code_of(e):
    return e.response.get("Error", {}).get("Code", "")


def need_cognito():
    if not POOL or not CLIENT:
        raise HTTPException(503, "Sign-in is not configured yet (Cognito settings are missing).")


def random_password():
    pool = string.ascii_letters + string.digits
    return (secrets.choice(string.ascii_uppercase) + secrets.choice(string.ascii_lowercase) + secrets.choice(string.digits)
            + "".join(secrets.choice(pool) for _ in range(20)))


def cognito_exists(email):
    try:
        cognito.admin_get_user(UserPoolId=POOL, Username=email)
        return True
    except ClientError as e:
        if code_of(e) == "UserNotFoundException":
            return False
        raise


def cognito_create(email, name, role, password, weak_ok=False):
    """Create a confirmed Cognito user in group `role`. Returns True if `password` was applied."""
    try:
        cognito.admin_create_user(
            UserPoolId=POOL, Username=email, TemporaryPassword=random_password(), MessageAction="SUPPRESS",
            UserAttributes=[{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"},
                            {"Name": "name", "Value": name}])
    except ClientError as e:
        if code_of(e) == "UsernameExistsException":
            raise HTTPException(409, "User already exists")
        if code_of(e) == "InvalidPasswordException":
            raise HTTPException(400, PW_RULE)
        raise
    try:
        cognito.admin_add_user_to_group(UserPoolId=POOL, Username=email, GroupName=role)
        cognito.admin_set_user_password(UserPoolId=POOL, Username=email, Password=password, Permanent=True)
        return True
    except ClientError as e:
        if code_of(e) == "InvalidPasswordException":
            if weak_ok:
                cognito.admin_set_user_password(UserPoolId=POOL, Username=email, Password=random_password(), Permanent=True)
                return False
            try:
                cognito.admin_delete_user(UserPoolId=POOL, Username=email)
            except ClientError:
                pass
            raise HTTPException(400, PW_RULE)
        try:
            cognito.admin_delete_user(UserPoolId=POOL, Username=email)
        except ClientError:
            pass
        raise


def sign_in(email, password):
    try:
        return cognito.admin_initiate_auth(
            UserPoolId=POOL, ClientId=CLIENT, AuthFlow="ADMIN_USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": email, "PASSWORD": password})
    except ClientError as e:
        c = code_of(e)
        if c in ("NotAuthorizedException", "UserNotFoundException"):
            raise HTTPException(401, "Invalid credentials")
        if c == "PasswordResetRequiredException":
            raise HTTPException(403, "You must reset your password. Use 'Forgot password?' on the sign-in page.")
        if c == "UserNotConfirmedException":
            raise HTTPException(403, "Your account is not confirmed yet. Please contact your administrator.")
        if c == "TooManyRequestsException":
            raise HTTPException(429, "Too many attempts. Please wait a moment and try again.")
        raise


def sync_profile(email, name, role, sub=None):
    rows = write("UPDATE users SET name=%s, role=%s, password_hash=%s, cognito_sub=COALESCE(%s, cognito_sub) "
                 "WHERE lower(email)=lower(%s) RETURNING id", (name, role, MARK, sub, email), fetch=True)
    if not rows:
        write("INSERT INTO users(email,name,password_hash,role,cognito_sub) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(email) DO NOTHING",
              (email, name, MARK, role, sub))


def finish_login(email, resp):
    if resp.get("ChallengeName") == "NEW_PASSWORD_REQUIRED":
        return {"challenge": "NEW_PASSWORD_REQUIRED", "session": resp["Session"], "email": email,
                "username": resp.get("ChallengeParameters", {}).get("USER_ID_FOR_SRP", email)}
    if resp.get("ChallengeName") or "AuthenticationResult" not in resp:
        raise HTTPException(403, "This sign-in method is not supported.")
    claims = jwt.decode(resp["AuthenticationResult"]["IdToken"], options={"verify_signature": False})
    email = (claims.get("email") or email).lower()
    role = "admin" if "admin" in claims.get("cognito:groups", []) else "employee"
    name = claims.get("name") or email.split("@")[0]
    sync_profile(email, name, role, claims.get("sub"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    notify(email, "Welcome to our CarePulse",
           f"Hi {name},\n\nWelcome to our CarePulse system!\n"
           f"You signed in on {now}.\n\n"
           "You can now book a doctor appointment from the Appointments page. "
           "We will email you the doctor name, date and time as soon as it is booked.\n\n"
           "Thank you,\nCarePulse Team")
    return {"token": make_token(email, role), "role": role, "name": name}


def migrate_legacy(email, password):
    rows = write("SELECT name, role, password_hash FROM users WHERE lower(email)=lower(%s)", (email,), fetch=True)
    if not rows or rows[0]["password_hash"] == MARK or not check_pw(password, rows[0]["password_hash"]):
        return False
    if cognito_exists(email):
        return False
    applied = cognito_create(email, rows[0]["name"], rows[0]["role"], password, weak_ok=True)
    write("UPDATE users SET password_hash=%s WHERE lower(email)=lower(%s)", (MARK, email))
    if not applied:
        raise HTTPException(401, "Your account was moved to our new sign-in system, but your old password does not meet "
                                 "its rules. Click 'Forgot password?' to choose a new one.")
    return True


@app.on_event("startup")
def start():
    init_db(DDL, ["users"])
    e, p = os.environ["ADMIN_EMAIL"].strip().lower(), os.environ["ADMIN_PASSWORD"]
    write("INSERT INTO users(email,name,password_hash,role) SELECT %s,'Admin',%s,'admin' "
          "WHERE NOT EXISTS (SELECT 1 FROM users WHERE lower(email)=lower(%s))", (e, MARK, e))
    try:
        if POOL and CLIENT and not cognito_exists(e):
            ok = cognito_create(e, "Admin", "admin", p, weak_ok=True)
            write("UPDATE users SET password_hash=%s WHERE lower(email)=lower(%s)", (MARK, e))
            if not ok:
                print("ADMIN_PASSWORD does not meet the Cognito password rules - use 'Forgot password?'")
    except Exception as ex:
        print("cognito admin bootstrap skipped:", repr(ex))
    subscribe(e)


class NewUser(BaseModel):
    email: str
    name: str
    password: str
    role: str = "employee"


class Login(BaseModel):
    email: str
    password: str


class NewPassword(BaseModel):
    email: str
    session: str
    new_password: str
    username: str = ""


class Forgot(BaseModel):
    email: str


class ForgotConfirm(BaseModel):
    email: str
    code: str
    new_password: str


# ---------- public ----------
@app.post("/auth/login")
def login(b: Login):
    need_cognito()
    email = b.email.strip().lower()
    try:
        resp = sign_in(email, b.password)
    except HTTPException as e:
        if e.status_code != 401 or not migrate_legacy(email, b.password):
            raise
        resp = sign_in(email, b.password)
    return finish_login(email, resp)


@app.post("/auth/new-password")
def new_password(b: NewPassword):
    need_cognito()
    email = b.email.strip().lower()
    try:
        resp = cognito.admin_respond_to_auth_challenge(
            UserPoolId=POOL, ClientId=CLIENT, ChallengeName="NEW_PASSWORD_REQUIRED", Session=b.session,
            ChallengeResponses={"USERNAME": b.username or email, "NEW_PASSWORD": b.new_password})
    except ClientError as e:
        c = code_of(e)
        if c == "InvalidPasswordException":
            raise HTTPException(400, PW_RULE)
        if c in ("NotAuthorizedException", "ExpiredCodeException", "CodeMismatchException"):
            raise HTTPException(401, "That sign-in step expired. Please sign in again.")
        raise
    return finish_login(email, resp)


@app.post("/auth/forgot")
def forgot(b: Forgot):
    need_cognito()
    email = b.email.strip().lower()
    try:
        rows = write("SELECT name, role, password_hash FROM users WHERE lower(email)=lower(%s)", (email,), fetch=True)
        if rows and rows[0]["password_hash"] != MARK and not cognito_exists(email):
            cognito_create(email, rows[0]["name"], rows[0]["role"], random_password(), weak_ok=True)
            write("UPDATE users SET password_hash=%s WHERE lower(email)=lower(%s)", (MARK, email))
        cognito.forgot_password(ClientId=CLIENT, Username=email)
    except ClientError as e:
        if code_of(e) in ("LimitExceededException", "TooManyRequestsException"):
            raise HTTPException(429, "Too many attempts. Please wait a while and try again.")
        print("forgot password:", code_of(e))
    return {"ok": True}


@app.post("/auth/forgot/confirm")
def forgot_confirm(b: ForgotConfirm):
    need_cognito()
    try:
        cognito.confirm_forgot_password(ClientId=CLIENT, Username=b.email.strip().lower(),
                                        ConfirmationCode=b.code.strip(), Password=b.new_password)
    except ClientError as e:
        c = code_of(e)
        if c == "InvalidPasswordException":
            raise HTTPException(400, PW_RULE)
        if c in ("CodeMismatchException", "ExpiredCodeException", "UserNotFoundException", "NotAuthorizedException"):
            raise HTTPException(400, "That code is wrong or has expired. Request a new one.")
        if c in ("LimitExceededException", "TooManyRequestsException", "TooManyFailedAttemptsException"):
            raise HTTPException(429, "Too many attempts. Please wait a while and try again.")
        raise
    return {"ok": True}


# ---------- admin ----------
@app.post("/auth/register")
def register(u: NewUser, _=Depends(admin_only)):
    need_cognito()
    email, name = u.email.strip().lower(), u.name.strip()
    if not email or not name:
        raise HTTPException(400, "Enter a name and an e-mail address")
    if u.role not in ("admin", "employee"):
        raise HTTPException(400, "Role must be admin or employee")
    if write("SELECT 1 FROM users WHERE lower(email)=lower(%s)", (email,), fetch=True):
        raise HTTPException(409, "User already exists")
    cognito_create(email, name, u.role, u.password)
    try:
        write("INSERT INTO users(email,name,password_hash,role) VALUES(%s,%s,%s,%s)", (email, name, MARK, u.role))
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "User already exists")
    put_json(os.environ["USER_BUCKET"], f"users/{email}.json", {"email": email, "name": name, "role": u.role})
    subscribe(email)
    return {"ok": True}


@app.get("/auth/users")
def users(_=Depends(admin_only)):
    return read("SELECT id,email,name,role,created_at FROM users ORDER BY id")


@app.post("/auth/notifications/resend")
def resend_confirmation(u=Depends(current_user)):
    subscribe(u["sub"])
    return {"ok": True, "email": u["sub"]}


@app.post("/auth/notifications/test")
def test_email(u=Depends(current_user)):
    notify(u["sub"], "Test email - CarePulse",
           "This is a test. If you can read this, email alerts and payment receipts will reach you.", strict=True)
    return {"ok": True, "email": u["sub"]}


class UserEdit(BaseModel):
    name: str
    role: str
    password: str = ""


def main_admin(email):
    return email.lower() == os.environ["ADMIN_EMAIL"].lower()


@app.put("/auth/users/{uid}")
def edit_user(uid: int, b: UserEdit, adm=Depends(admin_only)):
    need_cognito()
    name = b.name.strip()
    if not name:
        raise HTTPException(400, "Enter a name")
    if b.role not in ("admin", "employee"):
        raise HTTPException(400, "Role must be admin or employee")
    if b.password and len(b.password) < 8:
        raise HTTPException(400, "Password needs at least 8 characters")
    rows = write("SELECT email, role, password_hash FROM users WHERE id=%s", (uid,), fetch=True)
    if not rows:
        raise HTTPException(404, "User not found")
    row = rows[0]
    email = row["email"].lower()
    if b.role != row["role"] and (email == adm["sub"].lower() or main_admin(email)):
        raise HTTPException(400, "The role of your own account or of the main administrator cannot be changed")
    password_hash = row["password_hash"]
    if cognito_exists(email):
        cognito.admin_update_user_attributes(UserPoolId=POOL, Username=email, UserAttributes=[{"Name": "name", "Value": name}])
        if b.role != row["role"]:
            cognito.admin_remove_user_from_group(UserPoolId=POOL, Username=email, GroupName=row["role"])
            cognito.admin_add_user_to_group(UserPoolId=POOL, Username=email, GroupName=b.role)
        if b.password:
            try:
                cognito.admin_set_user_password(UserPoolId=POOL, Username=email, Password=b.password, Permanent=True)
            except ClientError as e:
                if code_of(e) == "InvalidPasswordException":
                    raise HTTPException(400, PW_RULE)
                raise
        password_hash = MARK
    elif b.password:
        cognito_create(email, name, b.role, b.password)
        password_hash = MARK
    write("UPDATE users SET name=%s, role=%s, password_hash=%s WHERE id=%s", (name, b.role, password_hash, uid))
    try:
        put_json(os.environ["USER_BUCKET"], f"users/{row['email']}.json", {"email": row["email"], "name": name, "role": b.role})
    except Exception as e:
        print("user s3 record failed", e)
    return {"ok": True}


@app.delete("/auth/users/{uid}")
def delete_user(uid: int, adm=Depends(admin_only)):
    need_cognito()
    rows = write("SELECT email FROM users WHERE id=%s", (uid,), fetch=True)
    if not rows:
        raise HTTPException(404, "User not found")
    email = rows[0]["email"]
    if email.lower() == adm["sub"].lower():
        raise HTTPException(400, "You cannot delete your own account")
    if main_admin(email):
        raise HTTPException(400, "The main administrator account cannot be deleted")
    try:
        cognito.admin_delete_user(UserPoolId=POOL, Username=email.lower())
    except ClientError as e:
        if code_of(e) != "UserNotFoundException":
            raise
    write_allow_delete("DELETE FROM users WHERE id=%s", (uid,))
    return {"ok": True}