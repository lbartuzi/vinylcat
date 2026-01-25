# 🎵 VinylCat

VinylCat is a self-hosted web application for cataloguing vinyl record collections.  
It combines Discogs integration, cover-image + barcode recognition, and full manual entry — with a clean Bootstrap UI and multi-collection support.

Designed for personal use, small groups, and “family/friends sharing” scenarios where you want one place to manage collections without relying on a third-party hosted service.

---

## ✨ Features

### 📁 Collections
- Each user can own **one or more collections**
- Every user always has **at least one collection**
- **Edit collection name** after creation
- Deleting a collection deletes all contained records
- Records belong to exactly one collection
- **Collection sharing by email** (share access with another user if their email is known)

### 💿 Records
- Add records via:
  - **Discogs search/import**
  - **Manual entry**
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
- OCR + barcode recognition can extract:
  - Artist, Title, Year, Barcode
- Designed to help in both Discogs and Manual flows  
  *(Real-world barcode reliability varies with lighting/sharpness; see notes in Timeline.)*

### 📱 Mobile-friendly barcode input
- Mobile barcode scanning flow integrated in the “Add record” experience (camera-based UX)
- Improved control over scanning behavior (avoid auto-submit, more predictable open/close flow)

### 🔎 Search & Sorting
- Improved record browsing with sorting options (e.g., artist ascending, etc.)
- Discogs import/search logic improvements:
  - Better handling of large result sets
  - **Country/market filtering support** to narrow results (where applicable)

### 👤 Accounts & Security
- Registration with email activation
- Account must be activated before login
- Per-user Discogs tokens
- Strong ownership isolation
- **Password reset** (lost password → email link → set a new password)

### 🎨 UI / UX
- Bootstrap UI
- Theme support (Bootswatch-based light & dark)
- Preferences saved per user
- Gallery-style image viewing on top of record pages (overlay), with “open image in new tab”

### 📊 Stats
- A refreshed stats page with improved layout and visual components
- Translation handling improved: defaults gracefully to English if keys are missing

### 📈 Analytics (optional)
- Google Analytics support with consent flow (where enabled)

### ☕ Support button (optional)
- “Buy me a coffee” button can be embedded and styled to work across themes

---

## 🧱 Technology Stack
- Backend: **FastAPI** (Python 3.11)
- Frontend: **Jinja2 + Bootstrap**
- ORM: **SQLAlchemy**
- Databases:
  - **SQLite** (local development)
  - **PostgreSQL** (Docker / production)
- Deployment: **Docker / Docker Compose**

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
  **or**
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

## ⚙️ Configuration notes

### Discogs
VinylCat uses Discogs for search/import. You’ll need to configure Discogs API details and/or per-user tokens depending on your setup.

### OCR / Barcode recognition
Results depend heavily on photo quality:
- take sharp photos
- avoid glare
- ensure barcode area is readable and not curved/blurred  
If you experience poor barcode detection, try a closer crop or improved lighting.

---

## 🧭 Timeline (what changed since the earlier README version)

This section is a “living changelog” capturing the main improvements added after the initial baseline README.

### ✅ Collections & sharing
- Added **collection name editing** (rename collections after creation)
- Added **collection sharing by email** (share access between users)

### ✅ Record browsing quality
- Added/expanded **sorting options** (e.g., default fetch based on artist ascending)
- General UX improvements around record lists and navigation

### ✅ Discogs search improvements
- Added **country/market filtering support** to reduce irrelevant results
- Worked on improving logic where Discogs API results may miss known releases even when increasing result counts

### ✅ Mobile barcode scanning
- Implemented a mobile-friendly barcode scanning flow in the Add Record page
- Improved scan behavior (avoid unwanted auto-submit; increased reliability when closing/reopening scanner)

### ✅ Image viewing experience
- Added a **gallery overlay** on record pages:
  - clicking a photo opens an overlay gallery on top of the record page
  - clicking the image in the gallery can open it in a new tab

### ✅ Stats page refresh
- Improved stats UI with more “dashboard-like” components
- Translation handling improved (fallback to English if keys are missing)

### ✅ Account features
- Added **password reset** flow (lost password recovery via email link)

### ✅ Optional integrations / polish
- Added **Google Analytics consent** support (when enabled)
- Embedded **Buy Me a Coffee** button with improvements for theme awareness and styling consistency

### 🧪 OCR / barcode detection learnings
- Tested OCR + barcode extraction on real photos; found that OCR-only approaches can fail on barcodes in difficult images
- Adjustments and iterations were made to improve reliability, but photo quality remains a key factor

---

## 🗺️ Roadmap ideas (optional)
- Smarter Discogs matching (hybrid: web search → candidate list → Discogs refine)
- Duplicate explorer / accordion-style components for top artists/labels
- Improved barcode pipeline (better preprocessing; optional external barcode service fallback)
- More sharing controls (read-only vs edit access)

---

## 📄 License
Private / internal use
