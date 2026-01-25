from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy import inspect, text

from .config import APP_NAME, SECRET_KEY, UPLOAD_DIR
from .db import Base, engine


app = FastAPI(title=APP_NAME)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax")

BASE_DIR = Path(__file__).resolve().parent
Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)

# static
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


# --- database bootstrap ------------------------------------------------------

Base.metadata.create_all(bind=engine)


def _ensure_schema() -> None:
    """Best-effort schema migrations for simple deployments.

    This project intentionally uses SQLAlchemy create_all for simplicity.
    For hosted deployments we add a couple of optional columns over time.
    We try to be compatible with SQLite and Postgres without a full migration toolchain.
    """

    try:
        insp = inspect(engine)

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


# --- routes -----------------------------------------------------------------

from .routes import router  # noqa: E402

app.include_router(router)
