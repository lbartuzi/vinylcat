from __future__ import annotations

import json
import os
import re
import math
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .i18n import get_i18n, missing_keys_for, runtime_missing_keys

from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.orm import Session, selectinload

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
from .db import SessionLocal
from .models import Collection, CollectionShare, Photo, Record, User
from .auth import hash_password, verify_password
from . import discogs

router = APIRouter()

@router.get("/lang/{code}")
def set_language(request: Request, code: str):
    """Set UI language (cookie override) and redirect back."""
    code_n = (code or "").lower()
    i18n = get_i18n(request)
    # validate
    available = set(i18n.available)
    if code_n not in available:
        code_n = "en" if "en" in available else (next(iter(available)) if available else "en")

    target = request.headers.get("referer") or "/"
    resp = RedirectResponse(url=target, status_code=302)
    # 1 year
    resp.set_cookie(
        "vinylcat_lang",
        code_n,
        max_age=60 * 60 * 24 * 365,
        httponly=False,
        samesite="lax",
    )
    return resp


@router.get("/i18n/debug/{mode}")
def set_i18n_debug(request: Request, mode: str):
    """Enable/disable i18n debug highlighting (cookie)."""
    mode_n = (mode or '').lower()
    on = mode_n in ('1','true','yes','on','enable','enabled')
    target = request.headers.get("referer") or "/"
    resp = RedirectResponse(url=target, status_code=302)
    if on:
        resp.set_cookie("vinylcat_i18n_debug", "1", max_age=60*60*24*365, httponly=False, samesite="lax")
    else:
        resp.delete_cookie("vinylcat_i18n_debug")
    return resp


@router.get("/i18n/missing", response_class=HTMLResponse)
def i18n_missing(request: Request):
    """Show missing translation keys for the current language."""
    i18n = get_i18n(request)
    missing_file = missing_keys_for(i18n.lang)
    missing_runtime = runtime_missing_keys(i18n.lang)

    parts: list[str] = []
    parts.append(f"<h1>Missing translations for <code>{i18n.lang}</code></h1>")
    parts.append("<h2>Missing compared to English (file)</h2>")
    parts.append("<pre>" + "\n".join(missing_file) + "</pre>")
    parts.append("<h2>Observed missing at runtime</h2>")
    parts.append("<pre>" + "\n".join(missing_runtime) + "</pre>")
    parts.append("<p><a href='/i18n/debug/enable'>Enable debug highlighting</a> · <a href='/i18n/debug/disable'>Disable</a></p>")

    return HTMLResponse("\n".join(parts), status_code=200)




BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))



def _translate_at_strings(i18n, obj):
    """Translate strings that start with '@' as i18n keys."""
    if isinstance(obj, str) and obj.startswith('@') and len(obj) > 1:
        return i18n.t(obj[1:])
    return obj
def render(request: Request, template_name: str, context: dict, status_code: int = 200):
    """TemplateResponse with i18n context injected.

    Note: Do NOT call itself recursively; use templates.TemplateResponse.
    """
    i18n = get_i18n(request)
    ctx = dict(context or {})
    # routes usually pass request explicitly; avoid duplicates
    ctx.pop("request", None)

    for _k in ('error','success','info','warning','message'):
        if _k in ctx:
            ctx[_k] = _translate_at_strings(i18n, ctx[_k])

    return templates.TemplateResponse(
        template_name,
        {
            "request": request,
            **ctx,
            "t": i18n.t,
            "lang": i18n.lang,
            "language_options": i18n.language_options(),
        },
        status_code=status_code,
    )

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
        raise HTTPException(status_code=400, detail=get_i18n(request).t("auth.invalid_or_expired_token"))


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
        raise HTTPException(status_code=400, detail=get_i18n(request).t("auth.invalid_or_expired_token"))

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

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    info = None
    qp = request.query_params
    if qp.get("activated") == "1":
        info = "@auth.account_active"
    elif qp.get("reset") == "1":
        info = "@auth.password_updated"
    elif qp.get("reset_sent") == "1":
        info = "@auth.reset_email_sent"
    elif qp.get("check_email") == "1":
        info = "@auth.check_email_activation"
    return render(request, "login.html", {"request": request, "app_name": APP_NAME, "info": info})

@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(db_dep)):
    email_n = email.strip().lower()
    user = db.scalar(select(User).where(User.email == email_n))
    if not user or not verify_password(password, user.password_hash):
        return render(request, "login.html", {"request": request, "app_name": APP_NAME, "error": "@auth.invalid_credentials"}, status_code=400)
    if not getattr(user, "is_active", True):
        return render(request, "login.html", {"request": request, "app_name": APP_NAME, "error": "@auth.not_active"}, status_code=403)
    request.session["user_id"] = user.id
    c = ensure_default_collection(db, user)
    set_active_collection(request, c.id)
    return RedirectResponse("/", status_code=303)


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    # Always show the same page to prevent user enumeration.
    return render(request, "forgot_password.html", {"request": request, "app_name": APP_NAME})


@router.post("/forgot-password", response_class=HTMLResponse)
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
        return render(request, "forgot_password.html", {
                "request": request,
                "app_name": APP_NAME,
                "error": "@auth.reset_not_configured",
            }, status_code=400)

    if user:
        i18n = get_i18n(request)
        token = _password_reset_token(user)
        base = (PUBLIC_BASE_URL or str(request.base_url)).rstrip("/")
        link = f"{base}/reset-password?token={token}"

        subject = i18n.t("email.reset.subject", app_name=APP_NAME)
        body_text = i18n.t("email.reset.text", app_name=APP_NAME, link=link)
        body_html = i18n.t("email.reset.html", app_name=APP_NAME, link=link)

        _send_email(email_n, subject, body_text, body_html)

    return RedirectResponse("/login?reset_sent=1", status_code=303)


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request, token: str, db: Session = Depends(db_dep)):
    # Validate token early so we can show a friendly message.
    try:
        payload = _verify_password_reset_token(token)
        uid = payload.get("uid")
        email = (payload.get("email") or "").strip().lower()
        pwh = (payload.get("pwh") or "")
        user = db.get(User, uid) if uid else None
        if not user or user.email != email or (user.password_hash or "")[-12:] != pwh:
            raise HTTPException(status_code=400, detail=get_i18n(request).t("auth.reset_link_invalid"))
    except HTTPException:
        return render(request, "reset_password.html", {"request": request, "app_name": APP_NAME, "error": "@auth.reset_link_invalid"}, status_code=400)

    return render(request, "reset_password.html", {"request": request, "app_name": APP_NAME, "token": token})


@router.post("/reset-password", response_class=HTMLResponse)
def reset_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    password2: str = Form(...),
    db: Session = Depends(db_dep),
):
    if len(password) < 8:
        return render(request, "reset_password.html", {"request": request, "app_name": APP_NAME, "token": token, "error": "@auth.password_min8"}, status_code=400)
    if password != password2:
        return render(request, "reset_password.html", {"request": request, "app_name": APP_NAME, "token": token, "error": "@auth.passwords_no_match"}, status_code=400)

    payload = _verify_password_reset_token(token)
    uid = payload.get("uid")
    email = (payload.get("email") or "").strip().lower()
    pwh = (payload.get("pwh") or "")
    user = db.get(User, uid) if uid else None
    if not user or user.email != email or (user.password_hash or "")[-12:] != pwh:
        return render(request, "reset_password.html", {"request": request, "app_name": APP_NAME, "error": "@auth.reset_link_invalid"}, status_code=400)

    user.password_hash = hash_password(password)
    db.commit()
    # If the user was logged in somewhere, force re-auth by clearing current session.
    request.session.clear()
    return RedirectResponse("/login?reset=1", status_code=303)

@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return render(request, "register.html", {"request": request, "app_name": APP_NAME})

@router.post("/register")
def register(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(db_dep)):
    email_n = email.strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_n):
        return render(request, "register.html", {"request": request, "app_name": APP_NAME, "error": "@auth.valid_email"}, status_code=400)
    if len(password) < 8:
        return render(request, "register.html", {"request": request, "app_name": APP_NAME, "error": "@auth.password_min8"}, status_code=400)
    exists = db.scalar(select(User).where(User.email == email_n))
    if exists:
        return render(request, "register.html", {"request": request, "app_name": APP_NAME, "error": "@auth.email_already_registered"}, status_code=400)
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
        i18n = get_i18n(request)
        token = _activation_token(user)
        base = (PUBLIC_BASE_URL or str(request.base_url)).rstrip("/")
        link = f"{base}/activate?token={token}"

        subject = i18n.t("email.activate.subject", app_name=APP_NAME)
        body_text = i18n.t("email.activate.text", app_name=APP_NAME, link=link)
        body_html = i18n.t("email.activate.html", app_name=APP_NAME, link=link)

        _send_email(email_n, subject, body_text, body_html)
        return RedirectResponse("/login?check_email=1", status_code=303)

    # Local/dev mode: log in immediately
    request.session["user_id"] = user.id
    set_active_collection(request, c.id)
    return RedirectResponse("/", status_code=303)


@router.get("/activate")
def activate_account(request: Request, token: str, db: Session = Depends(db_dep)):
    payload = _verify_activation_token(token)
    uid = payload.get("uid")
    email = (payload.get("email") or "").strip().lower()
    user = db.get(User, uid) if uid else None
    if not user or user.email != email:
        raise HTTPException(status_code=400, detail=get_i18n(request).t("auth.activation_link_invalid"))

    if not getattr(user, "is_active", True):
        user.is_active = True
        user.activated_at = datetime.utcnow()
        db.commit()

    return RedirectResponse("/login?activated=1", status_code=303)




@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)

# --- collections
@router.get("/collections", response_class=HTMLResponse)
def collections_page(request: Request, db: Session = Depends(db_dep)):
    user = require_user(request, db)
    cols = user_collections(db, user)
    return render(request, "collections.html", {"request": request, "app_name": APP_NAME, "user": user, "collections": cols, "active_id": active_collection_id(request)})

@router.post("/collections/create")
def collections_create(request: Request, name: str = Form(...), db: Session = Depends(db_dep)):
    user = require_user(request, db)
    name = name.strip() or "Untitled"
    c = Collection(name=name, owner_id=user.id)
    db.add(c); db.commit(); db.refresh(c)
    set_active_collection(request, c.id)
    return RedirectResponse("/collections", status_code=303)

@router.post("/collections/{collection_id}/rename")
def collections_rename(collection_id: int, request: Request, name: str = Form(...), db: Session = Depends(db_dep)):
    user = require_user(request, db)
    c, role = can_access_collection(db, user, collection_id)

    # Rename is restricted to the owner (shared editors/viewers can’t rename).
    if role != "owner":
        raise HTTPException(status_code=403)

    new_name = (name or "").strip() or "Untitled"

    # Prevent absurdly long values (DB column is 200 chars).
    if len(new_name) > 200:
        new_name = new_name[:200].rstrip()

    c.name = new_name
    db.commit()
    return RedirectResponse("/collections", status_code=303)

@router.post("/collections/select")
def collections_select(request: Request, collection_id: int = Form(...), db: Session = Depends(db_dep)):
    user = require_user(request, db)
    can_access_collection(db, user, collection_id)
    set_active_collection(request, collection_id)
    return RedirectResponse("/", status_code=303)


@router.post("/collections/{collection_id}/delete")
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

@router.get("/collections/{collection_id}/share", response_class=HTMLResponse)
def collections_share_page(collection_id: int, request: Request, db: Session = Depends(db_dep)):
    user = require_user(request, db)
    c, role = can_access_collection(db, user, collection_id)
    if role != "owner":
        raise HTTPException(status_code=403)
    shares = db.scalars(select(CollectionShare).where(CollectionShare.collection_id == collection_id)).all()
    share_rows = []
    for s in shares:
        share_rows.append({"id": s.id, "email": db.get(User, s.user_id).email, "role": s.role})
    return render(request, "share.html", {"request": request, "app_name": APP_NAME, "user": user, "collection": c, "shares": share_rows})

@router.post("/collections/{collection_id}/share/add")
def collections_share_add(collection_id: int, request: Request, email: str = Form(...), role: str = Form("editor"), db: Session = Depends(db_dep)):
    user = require_user(request, db)
    c, r = can_access_collection(db, user, collection_id)
    if r != "owner":
        raise HTTPException(status_code=403)
    email_n = email.strip().lower()
    target = db.scalar(select(User).where(User.email == email_n))
    if not target:
        return render(request, "share.html", {"request": request, "app_name": APP_NAME, "user": user, "collection": c, "shares": [], "error": "@share.user_not_found"}, status_code=400)
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

@router.post("/collections/{collection_id}/share/remove")
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
@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    q: str = "",
    sort: str = "artist",
    dir: str = "asc",
    per_page: str = "50",
    page: int = 1,
    db: Session = Depends(db_dep),
):
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

    # ---- Sorting (safe allow-list)
    sort_key = (sort or "added").strip().lower()
    sort_dir = (dir or "desc").strip().lower()
    if sort_dir not in ("asc", "desc"):
        sort_dir = "desc"

    def _text_orders(*cols_):
        """Case-insensitive text ordering with NULLs last (portable across SQLite/Postgres)."""
        out = []
        for col in cols_:
            out.append(col.is_(None))  # NULLS LAST
            expr = func.lower(col)
            out.append(expr.asc() if sort_dir == "asc" else expr.desc())
        return tuple(out)

    def _num_orders(*cols_):
        """Numeric/date ordering with NULLs last (portable across SQLite/Postgres)."""
        out = []
        for col in cols_:
            out.append(col.is_(None))  # NULLS LAST
            out.append(col.asc() if sort_dir == "asc" else col.desc())
        return tuple(out)

    sort_map = {
        # keep "added" as default because it’s fast and predictable
        "added": lambda: (
            Record.created_at.asc() if sort_dir == "asc" else Record.created_at.desc(),
            Record.id.desc(),
        ),
        "artist": lambda: (*_text_orders(Record.artist, Record.title), Record.id.desc()),
        "title": lambda: (*_text_orders(Record.title, Record.artist), Record.id.desc()),
        "year": lambda: (*_num_orders(Record.year), *_text_orders(Record.artist, Record.title), Record.id.desc()),
        "label": lambda: (*_text_orders(Record.label, Record.artist, Record.title), Record.id.desc()),
        "country": lambda: (*_text_orders(Record.country, Record.artist, Record.title), Record.id.desc()),
        "catno": lambda: (*_text_orders(Record.catno, Record.artist, Record.title), Record.id.desc()),
    }
    if sort_key not in sort_map:
        sort_key = "added"

    stmt = select(Record).where(Record.collection_id == cid)

    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Record.title.ilike(like),
                Record.artist.ilike(like),
                Record.label.ilike(like),
                Record.catno.ilike(like),
            )
        )

    stmt = stmt.order_by(*sort_map[sort_key]())

    # ---- Pagination (fast + lightweight)
    per_page_raw = (per_page or "50").strip().lower()
    allowed = {"20": 20, "50": 50, "100": 100, "all": 0}
    if per_page_raw not in allowed:
        per_page_raw = "50"
    per_page_n = allowed[per_page_raw]

    try:
        page = int(page)
    except Exception:
        page = 1
    if page < 1:
        page = 1

    # Count using the filtered statement (without ORDER BY)
    base_for_count = stmt.order_by(None)
    total = int(db.execute(select(func.count()).select_from(base_for_count.subquery())).scalar() or 0)

    if per_page_n == 0:  # "all"
        total_pages = 1
        records = db.scalars(stmt).all()
        show_from = 1 if total > 0 else 0
        show_to = total
    else:
        total_pages = max(1, int(math.ceil(total / per_page_n))) if total > 0 else 1
        if page > total_pages:
            page = total_pages

        offset = (page - 1) * per_page_n
        records = db.scalars(stmt.limit(per_page_n).offset(offset)).all()
        show_from = offset + 1 if total > 0 else 0
        show_to = min(offset + len(records), total)

    def _page_items(cur: int, pages: int):
        if pages <= 1:
            return []
        if pages <= 9:
            return list(range(1, pages + 1))
        items = [1]
        start = max(2, cur - 2)
        end = min(pages - 1, cur + 2)
        if start > 2:
            items.append(None)
        items.extend(range(start, end + 1))
        if end < pages - 1:
            items.append(None)
        items.append(pages)
        return items

    page_items = _page_items(page, total_pages)

    cover_urls = {r.id: pick_cover_url(r) for r in records}

    return render(request, "home.html", {
        "request": request,
        "app_name": APP_NAME,
        "user": user,
        "collections": cols,
        "active_collection": collection,
        "active_role": role,
        "records": records,
        "cover_urls": cover_urls,
        "q": q,
        "sort": sort_key,
        "dir": sort_dir,
        "per_page": per_page_raw,
        "page": page,
        "total": total,
        "total_pages": total_pages,
        "show_from": show_from,
        "show_to": show_to,
        "page_items": page_items,
    })
@router.get("/stats", response_class=HTMLResponse)

def stats_page(request: Request, db: Session = Depends(db_dep)):
    """Collection statistics + duplicate explorer."""

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

    total = db.scalar(select(func.count(Record.id)).where(Record.collection_id == cid)) or 0

    # Helpers for non-empty strings
    artist_nonempty = (Record.artist.is_not(None)) & (func.length(func.trim(Record.artist)) > 0)
    label_nonempty = (Record.label.is_not(None)) & (func.length(func.trim(Record.label)) > 0)
    barcode_nonempty = (Record.barcode.is_not(None)) & (func.length(func.trim(Record.barcode)) > 0)
    title_nonempty = (Record.title.is_not(None)) & (func.length(func.trim(Record.title)) > 0)

    unique_artists = (
        db.scalar(
            select(func.count(func.distinct(func.lower(Record.artist)))).where(
                Record.collection_id == cid,
                artist_nonempty,
            )
        )
        or 0
    )
    unique_labels = (
        db.scalar(
            select(func.count(func.distinct(func.lower(Record.label)))).where(
                Record.collection_id == cid,
                label_nonempty,
            )
        )
        or 0
    )
    unique_years = db.scalar(
        select(func.count(func.distinct(Record.year))).where(Record.collection_id == cid, Record.year.is_not(None))
    ) or 0

    # Missing fields
    missing_year = db.scalar(select(func.count(Record.id)).where(Record.collection_id == cid, Record.year.is_(None))) or 0
    missing_barcode = db.scalar(
        select(func.count(Record.id)).where(Record.collection_id == cid, (Record.barcode.is_(None)) | (func.length(func.trim(Record.barcode)) == 0))
    ) or 0
    missing_discogs = db.scalar(
        select(func.count(Record.id)).where(Record.collection_id == cid, Record.discogs_release_id.is_(None))
    ) or 0

    # Photos
    photos_total = (
        db.scalar(select(func.count(Photo.id)).join(Record, Photo.record_id == Record.id).where(Record.collection_id == cid))
        or 0
    )
    records_with_photos = (
        db.scalar(
            select(func.count(func.distinct(Photo.record_id)))
            .join(Record, Photo.record_id == Record.id)
            .where(Record.collection_id == cid)
        )
        or 0
    )
    records_no_photos = max(0, total - records_with_photos)

    # Top contributors
    top_artists = (
        db.execute(
            select(Record.artist.label("name"), func.count(Record.id).label("cnt"))
            .where(Record.collection_id == cid, artist_nonempty)
            .group_by(Record.artist)
            .order_by(func.count(Record.id).desc(), func.lower(Record.artist).asc())
            .limit(10)
        )
        .mappings()
        .all()
    )
    top_labels = (
        db.execute(
            select(Record.label.label("name"), func.count(Record.id).label("cnt"))
            .where(Record.collection_id == cid, label_nonempty)
            .group_by(Record.label)
            .order_by(func.count(Record.id).desc(), func.lower(Record.label).asc())
            .limit(10)
        )
        .mappings()
        .all()
    )
    by_year = (
        db.execute(
            select(Record.year.label("year"), func.count(Record.id).label("cnt"))
            .where(Record.collection_id == cid, Record.year.is_not(None))
            .group_by(Record.year)
            .order_by(Record.year.asc())
        )
        .mappings()
        .all()
    )

    # --- Duplicate group summaries (for limits + totals) ---
    # Discogs release duplicates
    dup_release_rows = (
        db.execute(
            select(
                Record.discogs_release_id.label("key"),
                func.count(Record.id).label("cnt"),
            )
            .where(Record.collection_id == cid, Record.discogs_release_id.is_not(None))
            .group_by(Record.discogs_release_id)
            .having(func.count(Record.id) > 1)
            .order_by(func.count(Record.id).desc())
            .limit(50)
        )
        .mappings()
        .all()
    )

    # Barcode duplicates
    dup_barcode_rows = (
        db.execute(
            select(
                Record.barcode.label("key"),
                func.count(Record.id).label("cnt"),
            )
            .where(Record.collection_id == cid, barcode_nonempty)
            .group_by(Record.barcode)
            .having(func.count(Record.id) > 1)
            .order_by(func.count(Record.id).desc())
            .limit(50)
        )
        .mappings()
        .all()
    )

    # Artist+Title+Year duplicates (case-insensitive)
    akey = func.lower(func.trim(func.coalesce(Record.artist, "")))
    tkey = func.lower(func.trim(func.coalesce(Record.title, "")))

    dup_sig_rows = (
        db.execute(
            select(
                akey.label("akey"),
                tkey.label("tkey"),
                Record.year.label("year"),
                func.count(Record.id).label("cnt"),
                func.min(Record.artist).label("artist"),
                func.min(Record.title).label("title"),
            )
            .where(Record.collection_id == cid, artist_nonempty, title_nonempty)
            .group_by(akey, tkey, Record.year)
            .having(func.count(Record.id) > 1)
            .order_by(func.count(Record.id).desc())
            .limit(50)
        )
        .mappings()
        .all()
    )

    # Estimated duplicate "extras" (beyond first copy) — use full collection, not limited list
    def _dup_extras_release():
        subq = (
            select(func.count(Record.id).label("cnt"))
            .where(Record.collection_id == cid, Record.discogs_release_id.is_not(None))
            .group_by(Record.discogs_release_id)
            .having(func.count(Record.id) > 1)
        ).subquery()
        return int(db.scalar(select(func.coalesce(func.sum(subq.c.cnt - 1), 0))) or 0)

    def _dup_extras_barcode():
        subq = (
            select(func.count(Record.id).label("cnt"))
            .where(Record.collection_id == cid, barcode_nonempty)
            .group_by(Record.barcode)
            .having(func.count(Record.id) > 1)
        ).subquery()
        return int(db.scalar(select(func.coalesce(func.sum(subq.c.cnt - 1), 0))) or 0)

    def _dup_extras_sig():
        subq = (
            select(func.count(Record.id).label("cnt"))
            .where(Record.collection_id == cid, artist_nonempty, title_nonempty)
            .group_by(akey, tkey, Record.year)
            .having(func.count(Record.id) > 1)
        ).subquery()
        return int(db.scalar(select(func.coalesce(func.sum(subq.c.cnt - 1), 0))) or 0)

    dup_release_extras = _dup_extras_release() if total else 0
    dup_barcode_extras = _dup_extras_barcode() if total else 0
    dup_sig_extras = _dup_extras_sig() if total else 0
    dup_estimated = max(dup_release_extras, dup_barcode_extras, dup_sig_extras)

    # --- Completeness percentages (0..100) ---
    def pct(filled: int, total_: int) -> int:
        if not total_:
            return 0
        return int(round((filled / total_) * 100))

    pct_year = pct(total - missing_year, total)
    pct_barcode = pct(total - missing_barcode, total)
    pct_discogs = pct(total - missing_discogs, total)
    pct_photos = pct(records_with_photos, total)

    # Health score (simple, readable)
    base = (pct_year + pct_barcode + pct_discogs + pct_photos) / 4.0
    penalty = min(20.0, (dup_estimated / max(total, 1)) * 100.0 * 0.5)  # up to -20
    health_score = int(round(max(0.0, min(100.0, base - penalty))))

    # --- Build explorer groups with record lists (limit 50 groups each) ---
    def rec_to_dict(r: Record) -> dict:
        return {
            "id": r.id,
            "artist": r.artist,
            "title": r.title,
            "year": r.year,
            "cover_url": pick_cover_url(r),
        }

    # Discogs groups
    release_keys = [g["key"] for g in dup_release_rows if g.get("key") is not None]
    rel_records_map: dict[int, list[dict]] = {}
    if release_keys:
        rel_records = (
            db.execute(
                select(Record)
                .where(Record.collection_id == cid, Record.discogs_release_id.in_(release_keys))
                .options(selectinload(Record.photos))
                .order_by(Record.discogs_release_id.asc(), Record.id.asc())
            )
            .scalars()
            .all()
        )
        for r in rel_records:
            rel_records_map.setdefault(r.discogs_release_id, []).append(rec_to_dict(r))

    dup_release_groups = []
    for g in dup_release_rows:
        k = g["key"]
        dup_release_groups.append({"key": k, "cnt": int(g["cnt"]), "records": rel_records_map.get(k, [])})

    # Barcode groups
    barcode_keys = [g["key"] for g in dup_barcode_rows if g.get("key")]
    bc_records_map: dict[str, list[dict]] = {}
    if barcode_keys:
        bc_records = (
            db.execute(
                select(Record)
                .where(Record.collection_id == cid, Record.barcode.in_(barcode_keys))
                .options(selectinload(Record.photos))
                .order_by(Record.barcode.asc(), Record.id.asc())
            )
            .scalars()
            .all()
        )
        for r in bc_records:
            bc_records_map.setdefault((r.barcode or "").strip(), []).append(rec_to_dict(r))

    dup_barcode_groups = []
    for g in dup_barcode_rows:
        k = (g["key"] or "").strip()
        dup_barcode_groups.append({"key": k, "cnt": int(g["cnt"]), "records": bc_records_map.get(k, [])})

    # Signature groups
    sig_tuples = [(g["akey"], g["tkey"], g["year"]) for g in dup_sig_rows]
    sig_records_map: dict[tuple, list[dict]] = {}
    if sig_tuples:
        sig_records = (
            db.execute(
                select(Record)
                .where(
                    Record.collection_id == cid,
                    tuple_(akey, tkey, Record.year).in_(sig_tuples),
                )
                .options(selectinload(Record.photos))
                .order_by(func.lower(func.coalesce(Record.artist, "")).asc(), func.lower(func.coalesce(Record.title, "")).asc(), Record.year.asc().nulls_last(), Record.id.asc())
            )
            .scalars()
            .all()
        )
        for r in sig_records:
            key = (
                (r.artist or "").strip().lower(),
                (r.title or "").strip().lower(),
                r.year,
            )
            sig_records_map.setdefault(key, []).append(rec_to_dict(r))

    dup_sig_groups = []
    for g in dup_sig_rows:
        label = f"{(g.get('artist') or 'Unknown artist')} — {(g.get('title') or 'Untitled')}" + (f" ({g.get('year')})" if g.get("year") is not None else "")
        key = (g["akey"], g["tkey"], g["year"])
        dup_sig_groups.append({"label": label, "cnt": int(g["cnt"]), "records": sig_records_map.get(key, [])})

    return render(
        request,
        "stats.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "title": "Stats",
            "user": user,
            "collections": cols,
            "active_collection": collection,
            "active_role": role,
            "total": total,
            "unique_artists": unique_artists,
            "unique_labels": unique_labels,
            "unique_years": unique_years,
            "photos_total": photos_total,
            "records_with_photos": records_with_photos,
            "records_no_photos": records_no_photos,
            "missing_year": missing_year,
            "missing_barcode": missing_barcode,
            "missing_discogs": missing_discogs,
            "pct_year": pct_year,
            "pct_barcode": pct_barcode,
            "pct_discogs": pct_discogs,
            "pct_photos": pct_photos,
            "health_score": health_score,
            "dup_estimated": dup_estimated,
            "top_artists": top_artists,
            "top_labels": top_labels,
            "by_year": by_year,
            "dup_release_groups": dup_release_groups,
            "dup_barcode_groups": dup_barcode_groups,
            "dup_sig_groups": dup_sig_groups,
        },
    )



@router.get("/records/add", response_class=HTMLResponse)
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
    return render(request, "add.html", {"request": request, "app_name": APP_NAME, "user": user, "active_collection": collection, "discogs_token_present": bool(getattr(user, "discogs_token", None))})

@router.post("/records/search")
async def search_release_post(
    request: Request,
    barcode: str = Form(""),
    artist: str = Form(""),
    title: str = Form(""),
    year: str = Form(""),
    country: str = Form(""),
    db: Session = Depends(db_dep),
):
    """POST handler from add.html: redirect to GET view so paging links work."""
    user = require_user(request, db)
    cid = active_collection_id(request)
    collection, role = can_access_collection(db, user, cid)
    if role == "viewer":
        raise HTTPException(status_code=403)

    # Preserve only non-empty values in query-string
    qp: dict[str, str] = {}
    if barcode.strip():
        qp["barcode"] = barcode.strip()
    if artist.strip():
        qp["artist"] = artist.strip()
    if title.strip():
        qp["title"] = title.strip()
    if year.strip():
        qp["year"] = year.strip()
    if country.strip():
        qp["country"] = country.strip()
    qp["page"] = "1"

    from urllib.parse import urlencode

    return RedirectResponse(url=f"/records/search?{urlencode(qp)}", status_code=303)


@router.get("/records/search", response_class=HTMLResponse)
async def search_release_get(
    request: Request,
    barcode: str = "",
    artist: str = "",
    title: str = "",
    year: str = "",
    country: str = "",
    page: int = 1,
    db: Session = Depends(db_dep),
):
    """GET handler used for paging (50 results per page)."""
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

    page = max(1, int(page))
    results, pagination = await discogs.search_page(
        barcode=barcode.strip() or None,
        artist=artist.strip() or None,
        title=title.strip() or None,
        year=y,
        country=country.strip() or None,
        page=page,
        per_page=50,
        token=getattr(user, "discogs_token", None),
    )

    # Discogs returns: page, pages, per_page, items
    total_items = int(pagination.get("items") or 0)
    total_pages = int(pagination.get("pages") or 1)
    cur_page = int(pagination.get("page") or page)
    per_page = int(pagination.get("per_page") or 50)

    # showing range
    if total_items > 0:
        from_i = (cur_page - 1) * per_page + 1
        to_i = min(cur_page * per_page, total_items)
    else:
        from_i = 0
        to_i = 0

    # Build paging URLs while keeping search fields
    from urllib.parse import urlencode

    base_qp: dict[str, str] = {}
    if barcode.strip():
        base_qp["barcode"] = barcode.strip()
    if artist.strip():
        base_qp["artist"] = artist.strip()
    if title.strip():
        base_qp["title"] = title.strip()
    if year.strip():
        base_qp["year"] = year.strip()
    if country.strip():
        base_qp["country"] = country.strip()

    def _url(p: int) -> str:
        qp = dict(base_qp)
        qp["page"] = str(max(1, min(p, total_pages)))
        return f"/records/search?{urlencode(qp)}"

    pager = {
        "page": cur_page,
        "pages": total_pages,
        "prev_url": _url(cur_page - 1) if cur_page > 1 else None,
        "next_url": _url(cur_page + 1) if cur_page < total_pages else None,
        "from_": from_i,
        "to_": to_i,
        "total": total_items,
    }

    return render(
        request,
        "pick_release.html",
        {
            "request": request,
            "app_name": APP_NAME,
            "user": user,
            "results": results,
            "form": {
                "barcode": barcode,
                "artist": artist,
                "title": title,
                "year": year,
                "country": country,
            },
            "pager": pager,
        },
    )

@router.post("/records/add_from_discogs")
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



@router.post("/records/add_manual")
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


@router.get("/records/{record_id}/edit", response_class=HTMLResponse)
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

    return render(request, "record_edit.html", {
            "request": request,
            "app_name": APP_NAME,
            "title": "Edit record",
            "user": user,
            "record": rec,
            "collection": collection,
            "role": role,
            "tracklist_text": tracklist_text,
        })


@router.post("/records/{record_id}/update")
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

@router.get("/records/{record_id}", response_class=HTMLResponse)
def record_view(record_id: int, request: Request, db: Session = Depends(db_dep)):
    user = require_user(request, db)
    rec = db.get(Record, record_id)
    if not rec:
        raise HTTPException(status_code=404)
    collection, role = can_access_collection(db, user, rec.collection_id)
    photos = rec.photos
    formats = json.loads(rec.formats_json) if rec.formats_json else []
    tracklist = json.loads(rec.tracklist_json) if rec.tracklist_json else []

    return render(request, "record.html", {
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

@router.post("/records/{record_id}/upload_photo")
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

@router.post("/records/{record_id}/delete")
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

@router.get("/account", response_class=HTMLResponse)
def account_page(request: Request, db: Session = Depends(db_dep)):
    user = require_user(request, db)
    return render(request, "account.html", {"request": request, "app_name": APP_NAME, "title": "Account", "user": user})


@router.post("/account/discogs_token")
def account_set_discogs_token(request: Request, token: str = Form(""), db: Session = Depends(db_dep)):
    user = require_user(request, db)
    tok = (token or "").strip()
    user.discogs_token = tok or None
    db.commit()
    return RedirectResponse("/account", status_code=303)


@router.get("/account/export")
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

@router.post("/account/import")
async def account_import(request: Request, file: UploadFile = File(...), db: Session = Depends(db_dep)):
    user = require_user(request, db)
    raw = await file.read()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return render(request, "account.html", {"request": request, "app_name": APP_NAME, "title": "Account", "user": user, "error": "@account.import_invalid_json"}, status_code=400)

    if not isinstance(payload, dict) or payload.get("version") != 1:
        return render(request, "account.html", {"request": request, "app_name": APP_NAME, "title": "Account", "user": user, "error": "@account.import_unsupported"}, status_code=400)

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

@router.post("/account/delete")
def account_delete(request: Request, password: str = Form(...), confirm: str = Form(...), db: Session = Depends(db_dep)):
    user = require_user(request, db)

    if (confirm or "").strip().upper() != "DELETE":
        return render(request, "account.html", {"request": request, "app_name": APP_NAME, "title": "Account", "user": user, "error": "@account.confirmation_mismatch"}, status_code=400)

    if not verify_password(password, user.password_hash):
        return render(request, "account.html", {"request": request, "app_name": APP_NAME, "title": "Account", "user": user, "error": "@account.password_incorrect"}, status_code=400)

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


@router.post("/api/analyze")
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

@router.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return render(request, "privacy.html", {
            "request": request,
            "title": "Privacy",
            "app_name": "VinylCat",   # <-- set your app name here
            "user": request.session.get("user"),  # optional, only if your base.html expects it
        })
