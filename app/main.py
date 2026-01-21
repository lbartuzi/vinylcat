from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Request, Form, UploadFile, File, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy import select, or_, text, inspect
from sqlalchemy.orm import Session

from .config import (
    APP_NAME,
    SECRET_KEY,
    UPLOAD_DIR,
    OCR_SERVICE_URL,
    PUBLIC_BASE_URL,
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USERNAME,
    SMTP_PASSWORD,
    SMTP_FROM,
    SMTP_USE_TLS,
    SMTP_USE_SSL,
)
from .db import SessionLocal, engine, Base
from .models import User, Collection, CollectionShare, Record, Photo
from .auth import hash_password, verify_password
from . import discogs

# --- app setup
app = FastAPI(title=APP_NAME)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# static
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)


def pick_cover_url(rec: Record) -> str | None:
    """Choose a reasonable cover URL for list views.

    Priority:
      1) Uploaded front
      2) Discogs front
      3) Any uploaded
      4) Any discogs
    """
    uploads_front = [p for p in (rec.photos or []) if p.kind == "upload" and (p.label or "") == "front" and p.filename]
    if uploads_front:
        return f"/uploads/{uploads_front[0].filename}"
    discogs_front = [p for p in (rec.photos or []) if p.kind == "discogs" and (p.label or "") == "front" and p.url]
    if discogs_front:
        return discogs_front[0].url
    uploads_any = [p for p in (rec.photos or []) if p.kind == "upload" and p.filename]
    if uploads_any:
        return f"/uploads/{uploads_any[0].filename}"
    discogs_any = [p for p in (rec.photos or []) if p.kind == "discogs" and p.url]
    if discogs_any:
        return discogs_any[0].url
    return None


# create tables
Base.metadata.create_all(bind=engine)


def _ensure_schema():
    """Best-effort schema migrations for simple deployments.

    This project intentionally uses SQLAlchemy create_all for simplicity.
    For hosted deployments we add a couple of optional columns over time.
    We try to be compatible with SQLite and Postgres without a full migration toolchain.
    """
    try:
        insp = inspect(engine)
        dialect = engine.dialect.name.lower()

        def has_column(table: str, col: str) -> bool:
            try:
                cols = insp.get_columns(table)
                return any(c.get("name") == col for c in cols)
            except Exception:
                return False

        with engine.begin() as conn:
            # Users: activation + Discogs token
            if not has_column("users", "discogs_token"):
                conn.execute(text("ALTER TABLE users ADD COLUMN discogs_token VARCHAR(255)"))
            if not has_column("users", "is_active"):
                conn.execute(text("ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT TRUE"))
            if not has_column("users", "activated_at"):
                conn.execute(text("ALTER TABLE users ADD COLUMN activated_at TIMESTAMP"))

            # Records: optional barcode for manual entry / OCR
            if not has_column("records", "barcode"):
                conn.execute(text("ALTER TABLE records ADD COLUMN barcode VARCHAR(64)"))

            # Backfill NULLs to true (keep existing users able to login)
            try:
                conn.execute(text("UPDATE users SET is_active=TRUE WHERE is_active IS NULL"))
            except Exception:
                pass
    except Exception:
        # If the DB user lacks ALTER rights, the app can still start.
        pass


_ensure_schema()


def _send_email(to_email: str, subject: str, body_text: str, body_html: str | None = None) -> None:
    """Send an email using basic SMTP settings.

    If SMTP isn't configured, this is a no-op.
    """
    if not SMTP_HOST or not SMTP_FROM:
        return

    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    if SMTP_USE_SSL:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as smtp:
            if SMTP_USERNAME:
                smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
            if SMTP_USE_TLS:
                smtp.starttls()
            if SMTP_USERNAME:
                smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
            smtp.send_message(msg)


def _activation_token(user: User) -> str:
    from itsdangerous import URLSafeTimedSerializer

    s = URLSafeTimedSerializer(SECRET_KEY, salt="vinylcat-activate")
    return s.dumps({"uid": user.id, "email": user.email})


def _verify_activation_token(token: str, max_age_seconds: int = 60 * 60 * 48) -> dict:
    from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

    s = URLSafeTimedSerializer(SECRET_KEY, salt="vinylcat-activate")
    try:
        return s.loads(token, max_age=max_age_seconds)
    except (BadSignature, SignatureExpired):
        raise HTTPException(status_code=400, detail="Invalid or expired token")


def _password_reset_token(user: User) -> str:
    """Create a signed, time-limited password reset token.

    Token is stateless (no DB writes). We include a small fingerprint of the
    current password hash so that changing the password invalidates older links.
    """
    from itsdangerous import URLSafeTimedSerializer

    s = URLSafeTimedSerializer(SECRET_KEY, salt="vinylcat-reset")
    return s.dumps({
        "uid": user.id,
        "email": user.email,
        "pwh": (user.password_hash or "")[-12:],
    })


def _verify_password_reset_token(token: str, max_age_seconds: int = 60 * 60) -> dict:
    from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

    s = URLSafeTimedSerializer(SECRET_KEY, salt="vinylcat-reset")
    try:
        return s.loads(token, max_age=max_age_seconds)
    except (BadSignature, SignatureExpired):
        raise HTTPException(status_code=400, detail="Invalid or expired token")

def db_dep():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_user_id(request: Request) -> Optional[int]:
    return request.session.get("user_id")

def require_user(request: Request, db: Session) -> User:
    uid = get_user_id(request)
    if not uid:
        raise HTTPException(status_code=401)
    user = db.get(User, uid)
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401)
    if not getattr(user, "is_active", True):
        # Inactive accounts must activate via email.
        request.session.clear()
        raise HTTPException(status_code=403)
    return user

def active_collection_id(request: Request) -> Optional[int]:
    return request.session.get("active_collection_id")

def set_active_collection(request: Request, cid: int):
    request.session["active_collection_id"] = cid

def user_collections(db: Session, user: User) -> list[Collection]:
    owned = db.scalars(select(Collection).where(Collection.owner_id == user.id).order_by(Collection.created_at.desc())).all()
    shared = db.scalars(
        select(Collection).join(CollectionShare).where(CollectionShare.user_id == user.id).order_by(Collection.created_at.desc())
    ).all()
    # de-dupe
    seen = set()
    out: list[Collection] = []
    for c in owned + shared:
        if c.id not in seen:
            out.append(c); seen.add(c.id)
    return out

def can_access_collection(db: Session, user: User, collection_id: int) -> tuple[Collection, str]:
    c = db.get(Collection, collection_id)
    if not c:
        raise HTTPException(status_code=404)
    if c.owner_id == user.id:
        return c, "owner"
    share = db.scalar(select(CollectionShare).where(CollectionShare.collection_id == collection_id, CollectionShare.user_id == user.id))
    if not share:
        raise HTTPException(status_code=403)
    return c, share.role

def ensure_default_collection(db: Session, user: User) -> Collection:
    c = db.scalar(select(Collection).where(Collection.owner_id == user.id).order_by(Collection.created_at.asc()))
    if c:
        return c
    c = Collection(name="My Collection", owner_id=user.id)
    db.add(c); db.commit(); db.refresh(c)
    return c

# --- auth pages

def parse_tracklist_text(text: str) -> list[dict]:
    """Parse a user-provided tracklist text into a Discogs-like structure.

    Accepted formats per line:
      - Title
      - Title - 3:45
      - 01. Title - 3:45
    Duration is optional and recognized if it looks like mm:ss or hh:mm:ss at the end.
    """
    lines = [ln.strip() for ln in (text or "").splitlines()]
    out: list[dict] = []
    dur_re = re.compile(r"^(\d{1,2}:\d{2})(?::\d{2})?$")
    for ln in lines:
        if not ln:
            continue
        # strip leading numbering like "1.", "01.", "A1", etc.
        ln2 = re.sub(r"^([A-D]?\d+\.?\s+)", "", ln).strip()
        title = ln2
        duration = None
        if " - " in ln2:
            left, right = ln2.rsplit(" - ", 1)
            if dur_re.match(right.strip()):
                title = left.strip()
                duration = right.strip()
        out.append({"title": title, "duration": duration})
    return out

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    info = None
    qp = request.query_params
    if qp.get("activated") == "1":
        info = "Your account is now active. You can log in."
    elif qp.get("reset") == "1":
        info = "Your password has been updated. You can log in now."
    elif qp.get("reset_sent") == "1":
        info = "If that email exists in our system, you will receive a password reset link shortly."
    elif qp.get("check_email") == "1":
        info = "Check your email for an activation link to finish creating your account."
    return templates.TemplateResponse("login.html", {"request": request, "app_name": APP_NAME, "info": info})

@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(db_dep)):
    email_n = email.strip().lower()
    user = db.scalar(select(User).where(User.email == email_n))
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse("login.html", {"request": request, "app_name": APP_NAME, "error": "Invalid credentials"}, status_code=400)
    if not getattr(user, "is_active", True):
        return templates.TemplateResponse("login.html", {"request": request, "app_name": APP_NAME, "error": "Please activate your account via the link sent to your email."}, status_code=403)
    request.session["user_id"] = user.id
    c = ensure_default_collection(db, user)
    set_active_collection(request, c.id)
    return RedirectResponse("/", status_code=303)


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    # Always show the same page to prevent user enumeration.
    return templates.TemplateResponse("forgot_password.html", {"request": request, "app_name": APP_NAME})


@app.post("/forgot-password", response_class=HTMLResponse)
def forgot_password_submit(request: Request, email: str = Form(...), db: Session = Depends(db_dep)):
    email_n = email.strip().lower()
    # We intentionally do not reveal whether the email exists.
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_n):
        # Still respond with the generic message.
        return RedirectResponse("/login?reset_sent=1", status_code=303)

    user = db.scalar(select(User).where(User.email == email_n))

    # If SMTP isn't configured, we can't send emails.
    if not (SMTP_HOST and SMTP_FROM):
        # For local/dev deployments this is still useful feedback.
        return templates.TemplateResponse(
            "forgot_password.html",
            {
                "request": request,
                "app_name": APP_NAME,
                "error": "Password reset email sending is not configured (SMTP_HOST/SMTP_FROM).",
            },
            status_code=400,
        )

    if user:
        token = _password_reset_token(user)
        base = (PUBLIC_BASE_URL or str(request.base_url)).rstrip("/")
        link = f"{base}/reset-password?token={token}"
        body_text = (
            f"A password reset was requested for your {APP_NAME} account.\n\n"
            f"Open this link to set a new password:\n{link}\n\n"
            "If you did not request this, you can ignore this email."
        )
        body_html = (
            f"<p>A password reset was requested for your <strong>{APP_NAME}</strong> account.</p>"
            f"<p><a href=\"{link}\">Reset my password</a></p>"
            "<p>If you did not request this, you can ignore this email.</p>"
        )
        _send_email(email_n, f"Reset your {APP_NAME} password", body_text, body_html)

    return RedirectResponse("/login?reset_sent=1", status_code=303)


@app.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request, token: str, db: Session = Depends(db_dep)):
    # Validate token early so we can show a friendly message.
    try:
        payload = _verify_password_reset_token(token)
        uid = payload.get("uid")
        email = (payload.get("email") or "").strip().lower()
        pwh = (payload.get("pwh") or "")
        user = db.get(User, uid) if uid else None
        if not user or user.email != email or (user.password_hash or "")[-12:] != pwh:
            raise HTTPException(status_code=400, detail="Invalid reset link")
    except HTTPException:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "app_name": APP_NAME, "error": "This password reset link is invalid or has expired."},
            status_code=400,
        )

    return templates.TemplateResponse(
        "reset_password.html",
        {"request": request, "app_name": APP_NAME, "token": token},
    )


@app.post("/reset-password", response_class=HTMLResponse)
def reset_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    db: Session = Depends(db_dep),
):
    if len(password) < 8:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "app_name": APP_NAME, "token": token, "error": "Password must be at least 8 characters."},
            status_code=400,
        )
    if password != password2:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "app_name": APP_NAME, "token": token, "error": "Passwords do not match."},
            status_code=400,
        )

    payload = _verify_password_reset_token(token)
    uid = payload.get("uid")
    email = (payload.get("email") or "").strip().lower()
    pwh = (payload.get("pwh") or "")
    user = db.get(User, uid) if uid else None
    if not user or user.email != email or (user.password_hash or "")[-12:] != pwh:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "app_name": APP_NAME, "error": "This password reset link is invalid or has expired."},
            status_code=400,
        )

    user.password_hash = hash_password(password)
    db.commit()
    # If the user was logged in somewhere, force re-auth by clearing current session.
    request.session.clear()
    return RedirectResponse("/login?reset=1", status_code=303)

@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "app_name": APP_NAME})

@app.post("/register")
def register(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(db_dep)):
    email_n = email.strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_n):
        return templates.TemplateResponse("register.html", {"request": request, "app_name": APP_NAME, "error": "Please enter a valid email."}, status_code=400)
    if len(password) < 8:
        return templates.TemplateResponse("register.html", {"request": request, "app_name": APP_NAME, "error": "Password must be at least 8 characters."}, status_code=400)
    exists = db.scalar(select(User).where(User.email == email_n))
    if exists:
        return templates.TemplateResponse("register.html", {"request": request, "app_name": APP_NAME, "error": "Email already registered."}, status_code=400)
    # If SMTP is configured, require email activation. Otherwise (dev/local), activate immediately.
    require_activation = bool(SMTP_HOST and SMTP_FROM)
    user = User(
        email=email_n,
        password_hash=hash_password(password),
        is_active=(not require_activation),
        activated_at=None,
    )
    db.add(user); db.commit(); db.refresh(user)
    c = Collection(name="My Collection", owner_id=user.id)
    db.add(c); db.commit(); db.refresh(c)
    if require_activation:
        token = _activation_token(user)
        base = (PUBLIC_BASE_URL or str(request.base_url)).rstrip("/")
        link = f"{base}/activate?token={token}"
        body_text = (
            f"Welcome to {APP_NAME}!\n\n"
            f"Please activate your account by opening this link:\n{link}\n\n"
            "If you did not create this account, you can ignore this email."
        )
        body_html = (
            f"<p>Welcome to <strong>{APP_NAME}</strong>!</p>"
            f"<p>Please activate your account by clicking this link:</p>"
            f"<p><a href=\"{link}\">Activate my account</a></p>"
            "<p>If you did not create this account, you can ignore this email.</p>"
        )
        _send_email(email_n, f"Activate your {APP_NAME} account", body_text, body_html)
        return RedirectResponse("/login?check_email=1", status_code=303)

    # Local/dev mode: log in immediately
    request.session["user_id"] = user.id
    set_active_collection(request, c.id)
    return RedirectResponse("/", status_code=303)


@app.get("/activate")
def activate_account(request: Request, token: str, db: Session = Depends(db_dep)):
    payload = _verify_activation_token(token)
    uid = payload.get("uid")
    email = (payload.get("email") or "").strip().lower()
    user = db.get(User, uid) if uid else None
    if not user or user.email != email:
        raise HTTPException(status_code=400, detail="Invalid activation link")

    if not getattr(user, "is_active", True):
        user.is_active = True
        user.activated_at = datetime.utcnow()
        db.commit()

    return RedirectResponse("/login?activated=1", status_code=303)


@app.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    """Request a password reset link."""
    return templates.TemplateResponse("forgot_password.html", {"request": request, "app_name": APP_NAME})


@app.post("/forgot-password")
def forgot_password(request: Request, email: str = Form(...), db: Session = Depends(db_dep)):
    email_n = (email or "").strip().lower()
    # Always respond the same to prevent account enumeration.
    if SMTP_HOST and SMTP_FROM and re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_n):
        user = db.scalar(select(User).where(User.email == email_n))
        if user:
            token = _password_reset_token(user)
            base = (PUBLIC_BASE_URL or str(request.base_url)).rstrip("/")
            link = f"{base}/reset-password?token={token}"
            body_text = (
                f"You (or someone else) requested a password reset for {APP_NAME}.\n\n"
                f"Open this link to set a new password (valid for 1 hour):\n{link}\n\n"
                "If you did not request this, you can ignore this email."
            )
            body_html = (
                f"<p>You (or someone else) requested a password reset for <strong>{APP_NAME}</strong>.</p>"
                f"<p><a href=\"{link}\">Set a new password</a> (valid for 1 hour)</p>"
                "<p>If you did not request this, you can ignore this email.</p>"
            )
            _send_email(email_n, f"Reset your {APP_NAME} password", body_text, body_html)

    return RedirectResponse("/login?reset_sent=1", status_code=303)


@app.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request, token: str, db: Session = Depends(db_dep)):
    """Show the password reset form if the token is valid."""
    try:
        payload = _verify_password_reset_token(token)
        uid = payload.get("uid")
        email = (payload.get("email") or "").strip().lower()
        pwh = payload.get("pwh") or ""
        user = db.get(User, uid) if uid else None
        if not user or user.email != email or (user.password_hash or "")[-12:] != pwh:
            raise HTTPException(status_code=400, detail="Invalid or expired token")
    except HTTPException:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "app_name": APP_NAME, "error": "This reset link is invalid or has expired."},
            status_code=400,
        )

    return templates.TemplateResponse(
        "reset_password.html",
        {"request": request, "app_name": APP_NAME, "token": token, "email": email},
    )


@app.post("/reset-password")
def reset_password(request: Request, token: str = Form(...), password: str = Form(...), password2: str = Form(...), db: Session = Depends(db_dep)):
    if password != password2:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "app_name": APP_NAME, "token": token, "error": "Passwords do not match."},
            status_code=400,
        )
    if len(password) < 8:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "app_name": APP_NAME, "token": token, "error": "Password must be at least 8 characters."},
            status_code=400,
        )

    payload = _verify_password_reset_token(token)
    uid = payload.get("uid")
    email = (payload.get("email") or "").strip().lower()
    pwh = payload.get("pwh") or ""
    user = db.get(User, uid) if uid else None
    if not user or user.email != email or (user.password_hash or "")[-12:] != pwh:
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "app_name": APP_NAME, "error": "This reset link is invalid or has expired."},
            status_code=400,
        )

    user.password_hash = hash_password(password)
    db.commit()

    # If the user was logged in elsewhere, force re-login in this browser as well.
    request.session.clear()
    return RedirectResponse("/login?reset=1", status_code=303)

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)

# --- collections
@app.get("/collections", response_class=HTMLResponse)
def collections_page(request: Request, db: Session = Depends(db_dep)):
    user = require_user(request, db)
    cols = user_collections(db, user)
    return templates.TemplateResponse("collections.html", {"request": request, "app_name": APP_NAME, "user": user, "collections": cols, "active_id": active_collection_id(request)})

@app.post("/collections/create")
def collections_create(request: Request, name: str = Form(...), db: Session = Depends(db_dep)):
    user = require_user(request, db)
    name = name.strip() or "Untitled"
    c = Collection(name=name, owner_id=user.id)
    db.add(c); db.commit(); db.refresh(c)
    set_active_collection(request, c.id)
    return RedirectResponse("/collections", status_code=303)

@app.post("/collections/select")
def collections_select(request: Request, collection_id: int = Form(...), db: Session = Depends(db_dep)):
    user = require_user(request, db)
    can_access_collection(db, user, collection_id)
    set_active_collection(request, collection_id)
    return RedirectResponse("/", status_code=303)


@app.post("/collections/{collection_id}/delete")
def collections_delete(collection_id: int, request: Request, db: Session = Depends(db_dep)):
    user = require_user(request, db)
    c, role = can_access_collection(db, user, collection_id)
    if role != "owner":
        raise HTTPException(status_code=403)

    # Collect uploaded filenames so we can delete files from disk after the DB commit.
    recs = db.scalars(select(Record).where(Record.collection_id == collection_id)).all()
    rec_ids = [r.id for r in recs]
    filenames: list[str] = []
    if rec_ids:
        ups = db.scalars(select(Photo).where(Photo.record_id.in_(rec_ids), Photo.kind == "upload")).all()
        for p in ups:
            if p.filename:
                filenames.append(p.filename)

    # Delete the collection (cascades to records/photos/shares)
    db.delete(c)
    db.commit()

    # Ensure the user always has at least one owned collection.
    new_default = ensure_default_collection(db, user)
    # If the active collection was deleted, reset it.
    if active_collection_id(request) == collection_id:
        set_active_collection(request, new_default.id)

    # Best-effort delete uploaded files.
    for fn in set(filenames):
        try:
            (Path(UPLOAD_DIR) / fn).unlink(missing_ok=True)
        except Exception:
            pass

    return RedirectResponse("/collections", status_code=303)

@app.get("/collections/{collection_id}/share", response_class=HTMLResponse)
def collections_share_page(collection_id: int, request: Request, db: Session = Depends(db_dep)):
    user = require_user(request, db)
    c, role = can_access_collection(db, user, collection_id)
    if role != "owner":
        raise HTTPException(status_code=403)
    shares = db.scalars(select(CollectionShare).where(CollectionShare.collection_id == collection_id)).all()
    share_rows = []
    for s in shares:
        share_rows.append({"id": s.id, "email": db.get(User, s.user_id).email, "role": s.role})
    return templates.TemplateResponse("share.html", {"request": request, "app_name": APP_NAME, "user": user, "collection": c, "shares": share_rows})

@app.post("/collections/{collection_id}/share/add")
def collections_share_add(collection_id: int, request: Request, email: str = Form(...), role: str = Form("editor"), db: Session = Depends(db_dep)):
    user = require_user(request, db)
    c, r = can_access_collection(db, user, collection_id)
    if r != "owner":
        raise HTTPException(status_code=403)
    email_n = email.strip().lower()
    target = db.scalar(select(User).where(User.email == email_n))
    if not target:
        return templates.TemplateResponse("share.html", {"request": request, "app_name": APP_NAME, "user": user, "collection": c, "shares": [], "error": "User not found (they must register first)."}, status_code=400)
    if target.id == user.id:
        return RedirectResponse(f"/collections/{collection_id}/share", status_code=303)
    if role not in ("viewer", "editor"):
        role = "editor"
    existing = db.scalar(select(CollectionShare).where(CollectionShare.collection_id==collection_id, CollectionShare.user_id==target.id))
    if existing:
        existing.role = role
    else:
        db.add(CollectionShare(collection_id=collection_id, user_id=target.id, role=role))
    db.commit()
    return RedirectResponse(f"/collections/{collection_id}/share", status_code=303)

@app.post("/collections/{collection_id}/share/remove")
def collections_share_remove(collection_id: int, request: Request, share_id: int = Form(...), db: Session = Depends(db_dep)):
    user = require_user(request, db)
    c, r = can_access_collection(db, user, collection_id)
    if r != "owner":
        raise HTTPException(status_code=403)
    s = db.get(CollectionShare, share_id)
    if s and s.collection_id == collection_id:
        db.delete(s); db.commit()
    return RedirectResponse(f"/collections/{collection_id}/share", status_code=303)

# --- records
@app.get("/", response_class=HTMLResponse)
def home(request: Request, q: str = "", db: Session = Depends(db_dep)):
    # If not logged in, show the login screen instead of a JSON 401.
    if not get_user_id(request):
        return RedirectResponse(url="/login", status_code=302)
    user = require_user(request, db)
    cols = user_collections(db, user)
    cid = active_collection_id(request)
    if not cid and cols:
        set_active_collection(request, cols[0].id)
        cid = cols[0].id
    if not cid:
        c = ensure_default_collection(db, user)
        set_active_collection(request, c.id)
        cid = c.id
    collection, role = can_access_collection(db, user, cid)

    stmt = select(Record).where(Record.collection_id == cid).order_by(Record.created_at.desc())
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Record.title.ilike(like), Record.artist.ilike(like), Record.label.ilike(like), Record.catno.ilike(like)))
    records = db.scalars(stmt).all()

    cover_urls = {r.id: pick_cover_url(r) for r in records}

    return templates.TemplateResponse("home.html", {
        "request": request,
        "app_name": APP_NAME,
        "user": user,
        "collections": cols,
        "active_collection": collection,
        "active_role": role,
        "records": records,
        "cover_urls": cover_urls,
        "q": q
    })

@app.get("/records/add", response_class=HTMLResponse)
def add_page(request: Request, db: Session = Depends(db_dep)):
    user = require_user(request, db)
    cid = active_collection_id(request)
    if not cid:
        c = ensure_default_collection(db, user)
        set_active_collection(request, c.id)
        cid = c.id
    collection, role = can_access_collection(db, user, cid)
    if role == "viewer":
        raise HTTPException(status_code=403)
    return templates.TemplateResponse("add.html", {"request": request, "app_name": APP_NAME, "user": user, "active_collection": collection, "discogs_token_present": bool(getattr(user, "discogs_token", None))})

@app.post("/records/search", response_class=HTMLResponse)
async def search_release(request: Request,
                         barcode: str = Form(""),
                         artist: str = Form(""),
                         title: str = Form(""),
                         year: str = Form(""),
                         db: Session = Depends(db_dep)):
    user = require_user(request, db)
    cid = active_collection_id(request)
    collection, role = can_access_collection(db, user, cid)
    if role == "viewer":
        raise HTTPException(status_code=403)

    y = None
    try:
        if year.strip():
            y = int(year.strip())
    except Exception:
        y = None

    results = await discogs.search(
        barcode=barcode.strip() or None,
        artist=artist.strip() or None,
        title=title.strip() or None,
        year=y,
        token=getattr(user, "discogs_token", None),
    )
    return templates.TemplateResponse("pick_release.html", {"request": request, "app_name": APP_NAME, "user": user, "results": results, "form": {"barcode": barcode, "artist": artist, "title": title, "year": year}})

@app.post("/records/add_from_discogs")
async def add_from_discogs(request: Request,
                           release_id: int = Form(...),
                           notes: str = Form(""),
                           db: Session = Depends(db_dep)):
    user = require_user(request, db)
    cid = active_collection_id(request)
    collection, role = can_access_collection(db, user, cid)
    if role == "viewer":
        raise HTTPException(status_code=403)

    data = await discogs.release(release_id, token=getattr(user, "discogs_token", None))
    rec = Record(
        collection_id=cid,
        discogs_release_id=release_id,
        artist=", ".join([a.get("name","") for a in data.get("artists", [])]) or None,
        title=data.get("title"),
        year=data.get("year"),
        label=", ".join([l.get("name","") for l in data.get("labels", [])]) or None,
        catno=", ".join([l.get("catno","") for l in data.get("labels", []) if l.get("catno")]) or None,
        country=data.get("country"),
        formats_json=json.dumps(data.get("formats", [])),
        tracklist_json=json.dumps(data.get("tracklist", [])),
        notes=notes.strip() or None,
    )
    db.add(rec); db.commit(); db.refresh(rec)

    # store discogs images
    for i, img in enumerate(data.get("images", [])[:10]):
        db.add(Photo(record_id=rec.id, kind="discogs", url=img.get("uri"), label=img.get("type") or None))
    db.commit()

    return RedirectResponse(f"/records/{rec.id}", status_code=303)



@app.post("/records/add_manual")
def add_manual_record(
    request: Request,
    artist: str = Form(""),
    title: str = Form(""),
    year: str = Form(""),
    barcode: str = Form(""),
    tracklist_text: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(db_dep),
):
    user = require_user(request, db)
    cid = active_collection_id(request)
    if not cid:
        c = ensure_default_collection(db, user)
        set_active_collection(request, c.id)
        cid = c.id
    collection, role = can_access_collection(db, user, cid)
    if role == "viewer":
        raise HTTPException(status_code=403)

    y = None
    try:
        if (year or "").strip():
            y = int((year or "").strip())
    except Exception:
        y = None

    tl = parse_tracklist_text(tracklist_text)
    rec = Record(
        collection_id=collection.id,
        discogs_release_id=None,
        artist=(artist or "").strip() or None,
        title=(title or "").strip() or None,
        year=y,
        barcode=(barcode or '').strip() or None,
        tracklist_json=json.dumps(tl) if tl else None,
        notes=(notes or "").strip() or None,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return RedirectResponse(f"/records/{rec.id}", status_code=303)


@app.get("/records/{record_id}/edit", response_class=HTMLResponse)
def record_edit_page(record_id: int, request: Request, db: Session = Depends(db_dep)):
    user = require_user(request, db)
    rec = db.get(Record, record_id)
    if not rec:
        raise HTTPException(status_code=404)
    collection, role = can_access_collection(db, user, rec.collection_id)
    if role == "viewer":
        raise HTTPException(status_code=403)

    tracklist = json.loads(rec.tracklist_json) if rec.tracklist_json else []
    # serialize to editable text (Title - duration)
    lines = []
    for t in tracklist:
        ttitle = (t.get("title") or "").strip()
        tdur = (t.get("duration") or "").strip() if isinstance(t, dict) else ""
        if tdur:
            lines.append(f"{ttitle} - {tdur}")
        else:
            lines.append(ttitle)
    tracklist_text = "\n".join([ln for ln in lines if ln])

    return templates.TemplateResponse(
        "record_edit.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "title": "Edit record",
            "user": user,
            "record": rec,
            "collection": collection,
            "role": role,
            "tracklist_text": tracklist_text,
        },
    )


@app.post("/records/{record_id}/update")
def record_update(
    record_id: int,
    request: Request,
    artist: str = Form(""),
    title: str = Form(""),
    year: str = Form(""),
    barcode: str = Form(""),
    tracklist_text: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(db_dep),
):
    user = require_user(request, db)
    rec = db.get(Record, record_id)
    if not rec:
        raise HTTPException(status_code=404)
    collection, role = can_access_collection(db, user, rec.collection_id)
    if role == "viewer":
        raise HTTPException(status_code=403)

    y = None
    try:
        if (year or "").strip():
            y = int((year or "").strip())
    except Exception:
        y = None

    rec.artist = (artist or "").strip() or None
    rec.title = (title or "").strip() or None
    rec.year = y
    rec.barcode = (barcode or '').strip() or None
    tl = parse_tracklist_text(tracklist_text)
    rec.tracklist_json = json.dumps(tl) if tl else None
    rec.notes = (notes or "").strip() or None

    db.commit()
    return RedirectResponse(f"/records/{rec.id}", status_code=303)

@app.get("/records/{record_id}", response_class=HTMLResponse)
def record_view(record_id: int, request: Request, db: Session = Depends(db_dep)):
    user = require_user(request, db)
    rec = db.get(Record, record_id)
    if not rec:
        raise HTTPException(status_code=404)
    collection, role = can_access_collection(db, user, rec.collection_id)
    photos = rec.photos
    formats = json.loads(rec.formats_json) if rec.formats_json else []
    tracklist = json.loads(rec.tracklist_json) if rec.tracklist_json else []

    return templates.TemplateResponse("record.html", {
        "request": request,
        "app_name": APP_NAME,
        "user": user,
        "record": rec,
        "collection": collection,
        "role": role,
        "photos": photos,
        "formats": formats,
        "tracklist": tracklist
    })

@app.post("/records/{record_id}/upload_photo")
async def upload_photo(record_id: int, request: Request, label: str = Form("other"), photo: UploadFile = File(...), db: Session = Depends(db_dep)):
    user = require_user(request, db)
    rec = db.get(Record, record_id)
    if not rec:
        raise HTTPException(status_code=404)
    collection, role = can_access_collection(db, user, rec.collection_id)
    if role == "viewer":
        raise HTTPException(status_code=403)

    safe_label = label if label in ("front","back","other") else "other"
    ext = (Path(photo.filename).suffix or ".jpg").lower()
    fname = f"{record_id}_{int(datetime.utcnow().timestamp())}_{safe_label}{ext}"
    out_path = Path(UPLOAD_DIR) / fname
    content = await photo.read()
    out_path.write_bytes(content)

    db.add(Photo(record_id=record_id, kind="upload", filename=fname, label=safe_label))
    db.commit()

    return RedirectResponse(f"/records/{record_id}", status_code=303)

@app.post("/records/{record_id}/delete")
def delete_record(record_id: int, request: Request, db: Session = Depends(db_dep)):
    user = require_user(request, db)
    rec = db.get(Record, record_id)
    if not rec:
        raise HTTPException(status_code=404)
    collection, role = can_access_collection(db, user, rec.collection_id)
    if role != "owner" and role != "editor":
        raise HTTPException(status_code=403)

    # delete uploaded files
    for ph in rec.photos:
        if ph.kind == "upload" and ph.filename:
            try:
                (Path(UPLOAD_DIR) / ph.filename).unlink(missing_ok=True)
            except Exception:
                pass

    db.delete(rec); db.commit()
    return RedirectResponse("/", status_code=303)

# --- OCR analyze proxy


# ----------------------------
# Account management
# ----------------------------

@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, db: Session = Depends(db_dep)):
    user = require_user(request, db)
    return templates.TemplateResponse("account.html", {"request": request, "app_name": APP_NAME, "title": "Account", "user": user})


@app.post("/account/discogs_token")
def account_set_discogs_token(request: Request, token: str = Form(""), db: Session = Depends(db_dep)):
    user = require_user(request, db)
    tok = (token or "").strip()
    user.discogs_token = tok or None
    db.commit()
    return RedirectResponse("/account", status_code=303)


@app.get("/account/export")
def account_export(request: Request, db: Session = Depends(db_dep)):
    user = require_user(request, db)

    cols = db.scalars(select(Collection).where(Collection.owner_id == user.id).order_by(Collection.created_at.asc())).all()
    export = {
        "version": 1,
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "user_email": user.email,
        "collections": [],
    }

    for c in cols:
        c_obj = {
            "name": c.name,
            "created_at": c.created_at.isoformat() + "Z" if c.created_at else None,
            "records": [],
        }
        # eager load records+photos
        recs = db.scalars(select(Record).where(Record.collection_id == c.id).order_by(Record.created_at.asc())).all()
        for r in recs:
            photos = []
            for p in (r.photos or []):
                photos.append({
                    "kind": p.kind,
                    "url": p.url,
                    "filename": p.filename,
                    "label": p.label,
                })
            c_obj["records"].append({
                "discogs_release_id": r.discogs_release_id,
                "artist": r.artist,
                "title": r.title,
                "year": r.year,
                "label": r.label,
                "catno": r.catno,
                "country": r.country,
                "formats_json": r.formats_json,
                "tracklist_json": r.tracklist_json,
                "notes": r.notes,
                "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
                "photos": photos,
            })
        export["collections"].append(c_obj)

    filename = f"vinylcat-export-{user.id}.json"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return JSONResponse(export, headers=headers)

@app.post("/account/import")
async def account_import(request: Request, file: UploadFile = File(...), db: Session = Depends(db_dep)):
    user = require_user(request, db)
    raw = await file.read()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return templates.TemplateResponse("account.html", {"request": request, "app_name": APP_NAME, "title": "Account", "user": user, "error": "Invalid JSON file."}, status_code=400)

    if not isinstance(payload, dict) or payload.get("version") != 1:
        return templates.TemplateResponse("account.html", {"request": request, "app_name": APP_NAME, "title": "Account", "user": user, "error": "Unsupported export format/version."}, status_code=400)

    collections = payload.get("collections") or []
    created = 0
    for c in collections:
        name = (c.get("name") or "Imported collection").strip()
        newc = Collection(name=name, owner_id=user.id)
        db.add(newc)
        db.flush()  # get id

        for r in (c.get("records") or []):
            rec = Record(
                collection_id=newc.id,
                discogs_release_id=r.get("discogs_release_id"),
                artist=r.get("artist"),
                title=r.get("title"),
                year=r.get("year"),
                label=r.get("label"),
                catno=r.get("catno"),
                country=r.get("country"),
                formats_json=r.get("formats_json"),
                tracklist_json=r.get("tracklist_json"),
                notes=r.get("notes"),
            )
            db.add(rec)
            db.flush()

            # Photos: preserve Discogs URLs. Uploaded filenames are only kept if the file exists locally.
            for p in (r.get("photos") or []):
                kind = p.get("kind")
                url = p.get("url")
                filename = p.get("filename")
                label = p.get("label")
                if kind == "discogs" and url:
                    db.add(Photo(record_id=rec.id, kind="discogs", url=url, label=label))
                elif kind == "upload" and filename:
                    fpath = Path(UPLOAD_DIR) / filename
                    if fpath.exists():
                        db.add(Photo(record_id=rec.id, kind="upload", filename=filename, label=label))

        created += 1

    db.commit()
    return RedirectResponse("/account", status_code=303)

@app.post("/account/delete")
def account_delete(request: Request, password: str = Form(...), confirm: str = Form(...), db: Session = Depends(db_dep)):
    user = require_user(request, db)

    if (confirm or "").strip().upper() != "DELETE":
        return templates.TemplateResponse("account.html", {"request": request, "app_name": APP_NAME, "title": "Account", "user": user, "error": "Confirmation text did not match."}, status_code=400)

    if not verify_password(password, user.password_hash):
        return templates.TemplateResponse("account.html", {"request": request, "app_name": APP_NAME, "title": "Account", "user": user, "error": "Password was incorrect."}, status_code=400)

    # Collect upload filenames for owned data so we can remove files on disk
    owned_col_ids = [c.id for c in db.scalars(select(Collection).where(Collection.owner_id == user.id)).all()]
    filenames = []
    if owned_col_ids:
        recs = db.scalars(select(Record).where(Record.collection_id.in_(owned_col_ids))).all()
        rec_ids = [r.id for r in recs]
        if rec_ids:
            ups = db.scalars(select(Photo).where(Photo.record_id.in_(rec_ids)).where(Photo.kind == "upload")).all()
            for p in ups:
                if p.filename:
                    filenames.append(p.filename)

    # Remove shares where user is a member
    db.query(CollectionShare).filter(CollectionShare.user_id == user.id).delete(synchronize_session=False)

    # Delete owned collections (cascades to records/photos/shares)
    for c in db.scalars(select(Collection).where(Collection.owner_id == user.id)).all():
        db.delete(c)

    # Delete user
    db.delete(user)
    db.commit()

    # Best effort delete files
    for fn in set(filenames):
        try:
            (Path(UPLOAD_DIR) / fn).unlink(missing_ok=True)
        except Exception:
            pass

    request.session.clear()
    return RedirectResponse("/register", status_code=303)


@app.post("/api/analyze")
async def analyze(front: Optional[UploadFile] = File(None), back: Optional[UploadFile] = File(None)):
    files = {}
    if front is not None:
        files["front"] = (front.filename or "front.jpg", await front.read(), front.content_type or "image/jpeg")
    if back is not None:
        files["back"] = (back.filename or "back.jpg", await back.read(), back.content_type or "image/jpeg")
    if not files:
        return JSONResponse({"ok": True, "data": {}})

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{OCR_SERVICE_URL.rstrip('/')}/analyze", files=files)
            r.raise_for_status()
            return JSONResponse(r.json())
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)
