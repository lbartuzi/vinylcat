from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from fastapi import Request

BASE_DIR = Path(__file__).resolve().parent
LOCALES_DIR = BASE_DIR / "i18n"
DEFAULT_LANG = "en"

LANG_LABELS: Dict[str, str] = {
    "en": "English",
    "nl": "Nederlands",
    "de": "Deutsch",
    "pl": "Polski",
}

_ACCEPT_RE = re.compile(r"\s*,\s*")
_Q_RE = re.compile(r";\s*q=([0-9.]+)\s*$", re.I)


def _safe_load_json(path: Path) -> Dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            out: Dict[str, str] = {}
            for k, v in data.items():
                if isinstance(k, str) and isinstance(v, str):
                    out[k] = v
            return out
    except Exception:
        pass
    return {}


def load_translations() -> Dict[str, Dict[str, str]]:
    translations: Dict[str, Dict[str, str]] = {}
    if not LOCALES_DIR.exists():
        return translations
    for p in sorted(LOCALES_DIR.glob("*.json")):
        code = p.stem.lower()
        translations[code] = _safe_load_json(p)
    return translations


TRANSLATIONS: Dict[str, Dict[str, str]] = load_translations()

# Runtime missing-key tracking (useful for translation completeness)
MISSING_KEYS: dict[str, set[str]] = {}

# Enable debug highlighting of missing keys
I18N_DEBUG_ENV = (os.getenv("VINYLCAT_I18N_DEBUG", "0").strip().lower() in ("1", "true", "yes", "on"))


def available_languages() -> List[str]:
    # Prefer a stable order for UX
    preferred = ["en", "nl", "de", "pl"]
    found = [c for c in preferred if c in TRANSLATIONS]
    for c in sorted(TRANSLATIONS.keys()):
        if c not in found:
            found.append(c)
    # Ensure default exists even if translation files were not found
    if DEFAULT_LANG not in found:
        found.insert(0, DEFAULT_LANG)
    return found


def parse_accept_language(value: str | None) -> List[str]:
    if not value:
        return []
    parts = _ACCEPT_RE.split(value.strip())
    scored: List[Tuple[float, str]] = []
    for part in parts:
        if not part:
            continue
        lang = part
        q = 1.0
        m = _Q_RE.search(part)
        if m:
            try:
                q = float(m.group(1))
            except Exception:
                q = 1.0
            lang = _Q_RE.sub("", part).strip()
        lang = lang.lower()
        if lang:
            scored.append((q, lang))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [l for _, l in scored]


def negotiate_language(accept_langs: Iterable[str], available: List[str], default: str = DEFAULT_LANG) -> str:
    available_set = set(available)
    # 1) Exact match like "nl" or "nl-nl"
    for a in accept_langs:
        if a in available_set:
            return a
        primary = a.split("-")[0]
        if primary in available_set:
            return primary
    return default if default in available_set else (available[0] if available else default)


@dataclass(frozen=True)
class I18N:
    lang: str
    available: List[str]
    translations: Dict[str, Dict[str, str]]
    debug: bool = False

    def t(self, key: str, default: str | None = None, **kwargs: Any) -> str:
        en_map = self.translations.get(DEFAULT_LANG, {})
        lang_map = self.translations.get(self.lang, {})

        text_lang = lang_map.get(key)
        text_en = en_map.get(key)

        # Track missing in the selected language (but present in English)
        if text_lang is None and text_en is not None and self.lang != DEFAULT_LANG:
            MISSING_KEYS.setdefault(self.lang, set()).add(key)

        text_final = text_lang if text_lang is not None else text_en

        # Track completely missing (not even in English)
        if text_final is None:
            MISSING_KEYS.setdefault(self.lang, set()).add(key)
            text_final = default if default is not None else (f"⟦{key}⟧" if self.debug else key)

        # optional formatting (e.g. "Signed in as {email}")
        try:
            if kwargs:
                return text_final.format(**kwargs)
        except Exception:
            return text_final
        return text_final

    def language_options(self) -> List[Dict[str, str]]:
        opts: List[Dict[str, str]] = []
        for code in self.available:
            opts.append({"code": code, "label": LANG_LABELS.get(code, code)})
        return opts


def missing_keys_for(lang: str) -> list[str]:
    """Keys present in English but missing in `lang` translation file."""
    lang = (lang or '').lower()
    en_map = TRANSLATIONS.get(DEFAULT_LANG, {})
    lang_map = TRANSLATIONS.get(lang, {})
    return sorted([k for k in en_map.keys() if k not in lang_map])


def runtime_missing_keys(lang: str) -> list[str]:
    """Keys observed missing at runtime for this language."""
    lang = (lang or '').lower()
    return sorted(list(MISSING_KEYS.get(lang, set())))


def get_i18n(request: Request) -> I18N:
    available = available_languages()

    debug = I18N_DEBUG_ENV or (request.cookies.get("vinylcat_i18n_debug") == "1") or (
        request.query_params.get("i18n_debug") in ("1", "true", "yes", "on")
    )

    # Cookie override (manual selection)
    cookie_lang = request.cookies.get("vinylcat_lang")
    if cookie_lang:
        cookie_lang = cookie_lang.lower()
        if cookie_lang in available:
            return I18N(lang=cookie_lang, available=available, translations=TRANSLATIONS, debug=debug)

    # Browser preference
    accept = parse_accept_language(request.headers.get("accept-language"))
    lang = negotiate_language(accept, available, default=DEFAULT_LANG)
    return I18N(lang=lang, available=available, translations=TRANSLATIONS, debug=debug)
