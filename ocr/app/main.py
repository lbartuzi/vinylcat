from __future__ import annotations
import re
from typing import Optional, Dict, Any

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from PIL import Image, ImageOps
import pytesseract
from pyzbar.pyzbar import decode as zbar_decode

app = FastAPI(title="VinylCat OCR")

def _open_image(data: bytes) -> Image.Image:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    return im

import io

def extract_barcode(im: Image.Image) -> Optional[str]:
    # Try multiple preprocess variants
    variants = []
    variants.append(im)
    gray = ImageOps.grayscale(im)
    variants.append(gray)
    variants.append(ImageOps.autocontrast(gray))
    variants.append(ImageOps.invert(gray))
    for v in variants:
        try:
            codes = zbar_decode(v)
            if codes:
                # prefer numeric, longest
                cand = []
                for c in codes:
                    s = c.data.decode("utf-8", errors="ignore").strip()
                    if re.search(r"\d", s):
                        cand.append(s)
                if cand:
                    cand.sort(key=lambda x: (sum(ch.isdigit() for ch in x), len(x)), reverse=True)
                    return cand[0]
                return codes[0].data.decode("utf-8", errors="ignore").strip()
        except Exception:
            continue
    return None

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
    strong = []
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

@app.post("/analyze")
async def analyze(front: Optional[UploadFile] = File(None), back: Optional[UploadFile] = File(None)):
    data: Dict[str, Any] = {}

    images = []
    if front is not None:
        images.append(("front", await front.read()))
    if back is not None:
        images.append(("back", await back.read()))

    for label, raw in images:
        try:
            im = Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception:
            continue

        # barcode: prefer back
        bc = extract_barcode(im)
        if bc:
            if "barcode" not in data or label == "back":
                data["barcode"] = bc

        txt = ocr_text(im)
        fields = guess_fields(txt)
        # merge: prefer front for artist/title, any for year
        for k, v in fields.items():
            if k in ("artist","title"):
                if k not in data and label == "front":
                    data[k] = v
                elif k not in data and label != "front":
                    data[k] = v
            elif k == "year" and "year" not in data:
                data["year"] = v

    return JSONResponse({"ok": True, "data": data})
