from __future__ import annotations

import os

APP_NAME = os.getenv("APP_NAME", "VinylCat")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://vinyl:vinyl@db:5432/vinyl")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-please")

# Legacy global Discogs token (kept for backwards compatibility). Prefer per-user token.
DISCOGS_TOKEN = os.getenv("DISCOGS_TOKEN", "")

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/data/uploads")
OCR_SERVICE_URL = os.getenv("OCR_SERVICE_URL", "http://ocr:8090")

# Public base URL used to build links in emails (e.g. https://vinylcat.example.com)
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "")

# SMTP settings for account activation emails
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes", "on")
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").lower() in ("1", "true", "yes", "on")
