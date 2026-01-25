from __future__ import annotations

from typing import Any

import httpx

from .config import DISCOGS_TOKEN

BASE = "https://api.discogs.com"


def _headers(token: str | None = None) -> dict[str, str]:
    h = {"User-Agent": "VinylCat/1.0 +self-hosted"}
    tok = (token or "").strip() or (DISCOGS_TOKEN or "").strip()
    if tok:
        h["Authorization"] = f"Discogs token={tok}"
    return h


async def search_page(
    *,
    barcode: str | None = None,
    artist: str | None = None,
    title: str | None = None,
    year: int | None = None,
    country: str | None = None,
    page: int = 1,
    per_page: int = 50,
    token: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch exactly one page of Discogs database search results."""
    page = max(1, int(page))
    per_page = 50  # fixed page size

    params: dict[str, Any] = {"type": "release", "per_page": per_page, "page": page}

    if barcode and barcode.strip():
        params["barcode"] = barcode.strip()
    if artist and artist.strip():
        params["artist"] = artist.strip()
    if title and title.strip():
        params["release_title"] = title.strip()
    if year:
        params["year"] = int(year)
    if country and country.strip():
        params["country"] = country.strip()

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{BASE}/database/search", params=params, headers=_headers(token))
        r.raise_for_status()
        data = r.json() or {}
        return (data.get("results", []) or []), (data.get("pagination", {}) or {})


async def search(
    barcode: str | None = None,
    artist: str | None = None,
    title: str | None = None,
    year: int | None = None,
    country: str | None = None,
    per_page: int = 50,
    token: str | None = None,
) -> list[dict[str, Any]]:
    """Backwards-compatible helper: returns only the first page."""
    results, _ = await search_page(
        barcode=barcode,
        artist=artist,
        title=title,
        year=year,
        country=country,
        page=1,
        per_page=per_page,
        token=token,
    )
    return results


async def release(release_id: int, token: str | None = None) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{BASE}/releases/{release_id}", headers=_headers(token))
        r.raise_for_status()
        return r.json()
