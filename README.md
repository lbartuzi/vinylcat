# 🎵 VinylCat

VinylCat is a self-hosted web application for cataloguing vinyl record collections.
It supports Discogs integration, image/barcode recognition, and full manual entry,
making it usable both online and completely offline.

---

## ✨ Features

### 📁 Collections
- Users own one or more collections
- A user always has **at least one collection**
- Deleting a collection deletes all contained records
- Records belong to exactly one collection

### 💿 Records
- Add records via Discogs search or manual entry
- Manual mode supports:
  - Artist (required)
  - Title (required)
  - Year
  - Barcode (UPC/EAN)
  - Notes
  - Structured tracklist editor (title + duration)
- Full edit support after creation

### 📷 Image & Barcode Recognition
- Upload front/back cover images
- OCR & barcode recognition detects:
  - Artist
  - Title
  - Year
  - Barcode
- Works in both Discogs and Manual modes

### 👤 Accounts & Security
- User registration with email activation
- Account must be activated before login
- Per-user Discogs API tokens

### 🎨 UI / UX
- Bootstrap-based UI
- Lumen Light and Dark themes
- User preferences saved per account

---

## 🧱 Technology Stack
- Backend: FastAPI (Python 3.11)
- Frontend: Jinja2 + Bootstrap
- Database: SQLite (default)
- Deployment: Docker / Docker Compose

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

Edit `.env` with your settings.

### Run with Docker
```bash
docker compose up -d --build
```

Access at: http://localhost:8080

---

## 👤 Usage Flow
1. Register account
2. Activate via email
3. Login
4. (Optional) Add Discogs token
5. Add records
6. Manage collections

---

## 🔒 Ownership Rules
- Users can only access their own data
- Collections cannot be empty (auto-created)

---

## 📄 License
Private / internal use
