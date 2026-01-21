# 🎵 VinylCat

VinylCat is a self-hosted web application for cataloguing vinyl record collections.
It combines Discogs integration, image & barcode recognition, and full manual entry.
It supports both SQLite and PostgreSQL depending on deployment method.

---

## ✨ Features

### 📁 Collections
- Each user owns one or more collections
- A user always has **at least one collection**
- Deleting a collection deletes all contained records
- Records belong to exactly one collection

### 💿 Records
- Add records via Discogs or Manual entry
- Manual mode supports:
  - Artist (required)
  - Title (required)
  - Year
  - Barcode (UPC/EAN)
  - Notes
  - Structured tracklist editor (title + duration)
- Full editing after creation

### 📷 Image & Barcode Recognition
- Upload cover images (front/back)
- OCR & barcode recognition extracts:
  - Artist, Title, Year, Barcode
- Works in both Discogs and Manual modes

### 👤 Accounts & Security
- Registration with email activation
- Account must be activated before login
- Per-user Discogs tokens
- Strong ownership isolation

### 🎨 UI / UX
- Bootstrap UI
- Lumen Light & Dark themes
- Preferences saved per user

---

## 🧱 Technology Stack
- Backend: FastAPI (Python 3.11)
- Frontend: Jinja2 + Bootstrap
- ORM: SQLAlchemy
- Databases:
  - SQLite (local development)
  - PostgreSQL (Docker / production)
- Deployment: Docker / Docker Compose

---

## 🗄️ Database Overview

| Environment | Database |
|------------|----------|
| Local dev  | SQLite |
| Docker Compose | PostgreSQL |
| Production | PostgreSQL (recommended) |

---

## 🚀 Installation

### Prerequisites
- Docker + Docker Compose (recommended)
OR
- Python 3.11+

### Clone
```bash
git clone https://github.com/yourname/vinylcat.git
cd vinylcat
```

### Environment
```bash
cp .env.example .env
```

### Run with Docker
```bash
docker compose up -d --build
```

---

## 📄 License
Private / internal use
