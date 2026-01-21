# 🗄️ VinylCat – Database Guide

VinylCat supports **SQLite** and **PostgreSQL** via SQLAlchemy.

---

## 🔧 Database Selection

Controlled by:
```env
DATABASE_URL=...
```

### SQLite
```env
DATABASE_URL=sqlite:///data/vinylcat.db
```

### PostgreSQL
```env
DATABASE_URL=postgresql://vinylcat:vinylcat@db:5432/vinylcat
```

---

## 🔄 Switching Databases
1. Stop the application
2. Change DATABASE_URL
3. Start the application

---

## 💾 Backups

### SQLite
```bash
cp data/vinylcat.db vinylcat_backup.db
```

### PostgreSQL (Docker)
```bash
docker exec vinylcat-db pg_dump -U vinylcat vinylcat > backup.sql
```

---

## 🧬 Migrations
- Automatic table creation
- No Alembic yet
- Backup before upgrades

---

## 🚀 Production Recommendations
- Use PostgreSQL
- Daily backups
- Do not expose DB ports
