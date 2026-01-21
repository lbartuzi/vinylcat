from __future__ import annotations
from typing import Any, Optional
import httpx
from .config import DISCOGS_TOKEN

BASE = "https://api.discogs.com"

def _headers(token: str | None = None) -> dict[str,str]:
    h = {"User-Agent": "VinylCat/1.0 +self-hosted"}
    tok = (token or "").strip() or (DISCOGS_TOKEN or "").strip()
    if tok:
        h["Authorization"] = f"Discogs token={tok}"
    return h

async def search(
    barcode: str | None = None,
    artist: str | None = None,
    title: str | None = None,
    year: int | None = None,
    country: str | None = None,
    per_page: int = 50,
    token: str | None = None,
) -> list[dict[str,Any]]:
    params: dict[str, Any] = {"type": "release", "per_page": per_page}
    if barcode:
        params["barcode"] = barcode
    if artist:
        params["artist"] = artist
    if title:
        params["release_title"] = title
    if year:
        params["year"] = year
    if country and country.strip():
        params["country"] = country.strip()
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{BASE}/database/search", params=params, headers=_headers(token))
        r.raise_for_status()
        data = r.json()
        return data.get("results", [])

async def release(release_id: int, token: str | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{BASE}/releases/{release_id}", headers=_headers(token))
        r.raise_for_status()
        return r.json()
