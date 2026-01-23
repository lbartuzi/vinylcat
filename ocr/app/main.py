from __future__ import annotations

import io
import re
from typing import Optional, Dict, Any, List, Tuple

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from PIL import Image, ImageOps
import pytesseract
from pyzbar.pyzbar import decode as zbar_decode

app = FastAPI(title="VinylCat OCR")

# --- Barcode helpers (validate + normalize) ----------------------------------

EAN13_RE = re.compile(r"^\d{13}$")
UPCA_RE  = re.compile(r"^\d{12}$")
EAN8_RE  = re.compile(r"^\d{8}$")

def _ean13_checkdigit_ok(code: str) -> bool:
    if not EAN13_RE.match(code):
        return False
    digits = [int(c) for c in code]
    s = sum(digits[i] for i in range(0, 12, 2)) + 3 * sum(digits[i] for i in range(1, 12, 2))
    check = (10 - (s % 10)) % 10
    return check == digits[12]

def _upca_checkdigit_ok(code: str) -> bool:
    if not UPCA_RE.match(code):
        return False
    digits = [int(c) for c in code]
    s = 3 * sum(digits[i] for i in range(0, 11, 2)) + sum(digits[i] for i in range(1, 11, 2))
    check = (10 - (s % 10)) % 10
    return check == digits[11]

def _ean8_checkdigit_ok(code: str) -> bool:
    # Optional support; harmless if present.
    if not EAN8_RE.match(code):
        return False
    digits = [int(c) for c in code]
    s = 3 * sum(digits[i] for i in range(0, 7, 2)) + sum(digits[i] for i in range(1, 7, 2))
    check = (10 - (s % 10)) % 10
    return check == digits[7]

def _normalize_digits(s: str) -> str:
    return re.sub(r"\D", "", (s or "").strip())

def _best_valid_barcode(candidates: List[str]) -> Optional[str]:
    """
    Choose best valid code:
      1) EAN-13 (valid)
      2) UPC-A (valid)
      3) EAN-8 (valid)
    """
    # prefer EAN-13, then UPC-A, then EAN-8
    for c in candidates:
        if EAN13_RE.match(c) and _ean13_checkdigit_ok(c):
            return c
    for c in candidates:
        if UPCA_RE.match(c) and _upca_checkdigit_ok(c):
            return c
    for c in candidates:
        if EAN8_RE.match(c) and _ean8_checkdigit_ok(c):
            return c
    return None

def extract_barcode(im: Image.Image) -> Optional[str]:
    # Try multiple preprocess variants
    variants = []
    variants.append(im)
    gray = ImageOps.grayscale(im)
    variants.append(gray)
    variants.append(ImageOps.autocontrast(gray))
    variants.append(ImageOps.invert(gray))

    seen: List[str] = []
    for v in variants:
        try:
            codes = zbar_decode(v)
            if not codes:
                continue
            for c in codes:
                raw = c.data.decode("utf-8", errors="ignore")
                d = _normalize_digits(raw)
                if d:
                    seen.append(d)
        except Exception:
            continue

    if not seen:
        return None

    # de-dup while preserving order
    dedup = list(dict.fromkeys(seen))
    return _best_valid_barcode(dedup)

# --- OCR + field guessing ----------------------------------------------------

def ocr_text(im: Image.Image) -> str:
    gray = ImageOps.grayscale(im)
    gray = ImageOps.autocontrast(gray)
    # PSM 6: assume a block of text
    try:
        return pytesseract.image_to_string(gray, lang="eng", config="--psm 6")
    except Exception:
        return ""

def guess_fields(text: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    cleaned = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
    cleaned = [ln for ln in cleaned if ln and len(ln) >= 3]

    # year
    m = re.search(r"\b(19\d{2}|20\d{2})\b", " ".join(cleaned))
    if m:
        out["year"] = int(m.group(1))

    # title/artist: pick top strong lines (often uppercase)
    strong: List[Tuple[int, str]] = []
    for ln in cleaned[:30]:
        if len(ln) < 3:
            continue
        # ignore common junk
        if re.search(r"\b(stereo|mono|side\s*[ab]|rpm|vinyl|limited|edition|copyright|all rights)\b", ln, re.I):
            continue
        score = 0
        score += sum(ch.isupper() for ch in ln)
        score += 5 if len(ln) >= 8 else 0
        score += 2 if re.search(r"[A-Za-z]", ln) else 0
        strong.append((score, ln))

    strong.sort(reverse=True, key=lambda x: x[0])
    top = [ln for _, ln in strong[:6]]

    # Heuristic: first line artist, second title (common on covers). If only one, put in title.
    if top:
        if len(top) == 1:
            out["title"] = top[0][:300]
        else:
            out["artist"] = top[0][:300]
            out["title"] = top[1][:300]

    return out

# --- Endpoint ----------------------------------------------------------------

@app.post("/analyze")
async def analyze(front: Optional[UploadFile] = File(None), back: Optional[UploadFile] = File(None)):
    data: Dict[str, Any] = {}

    # Read files (same external API)
    images: List[Tuple[str, Image.Image]] = []
    if front is not None:
        raw = await front.read()
        try:
            images.append(("front", Image.open(io.BytesIO(raw)).convert("RGB")))
        except Exception:
            pass
    if back is not None:
        raw = await back.read()
        try:
            images.append(("back", Image.open(io.BytesIO(raw)).convert("RGB")))
        except Exception:
            pass

    # 1) BARCODE-FIRST PASS (prefer back)
    barcode: Optional[str] = None

    # Prefer scanning back first if present
    for label, im in sorted(images, key=lambda x: 0 if x[0] == "back" else 1):
        bc = extract_barcode(im)
        if bc:
            barcode = bc
            break

    # If barcode found: return ONLY barcode (skip OCR entirely)
    if barcode:
        return JSONResponse({"ok": True, "data": {"barcode": barcode}})

    # 2) OCR PASS (only when no barcode)
    for label, im in images:
        txt = ocr_text(im)
        fields = guess_fields(txt)

        # merge: prefer front for artist/title, any for year
        for k, v in fields.items():
            if k in ("artist", "title"):
                if k not in data and label == "front":
                    data[k] = v
                elif k not in data and label != "front":
                    data[k] = v
            elif k == "year" and "year" not in data:
                data["year"] = v

    return JSONResponse({"ok": True, "data": data})
