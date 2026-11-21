"""
Language helpers (local-only).

We want AI analysis output language to follow the frontend UI language.
Frontend sends `X-App-Lang` (and also `Accept-Language`) on each request.
"""

from __future__ import annotations

from typing import Optional


SUPPORTED_LANGS = {
    "en-US",
    "zh-CN",
    "zh-TW",
    "ja-JP",
    "ko-KR",
    "vi-VN",
    "th-TH",
    "ar-SA",
    "fr-FR",
    "de-DE",
    "ru-RU",
}


def _normalize_lang(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = str(raw).strip().replace("_", "-")
    if not s:
        return None

    # Accept-Language can be like: "en-US,en;q=0.9"
    if "," in s:
        s = s.split(",", 1)[0].strip()
    if ";" in s:
        s = s.split(";", 1)[0].strip()

    # Normalize short tags
    lower = s.lower()
    if lower in ("en", "en-us"):
        return "en-US"
    if lower in ("zh", "zh-cn", "zh-hans"):
        return "zh-CN"
    if lower in ("zh-tw", "zh-hant", "zh-hk", "zh-mo", "zh-hant-tw", "zh-hant-hk"):
        return "zh-TW"

    # Keep canonical casing if already supported
    for lang in SUPPORTED_LANGS:
        if lang.lower() == lower:
            return lang
    base = lower.split("-", 1)[0]
    if base == "zh":
        return "zh-CN"
    for lang in SUPPORTED_LANGS:
        if lang.split("-", 1)[0] == base:
            return lang
    return None


def normalize_language(raw: Optional[str], default: str = "en-US") -> str:
    """Resolve a saved language tag to a supported locale."""
    return _normalize_lang(raw) or default


def detect_request_language(flask_request, body: Optional[dict] = None, default: str = "en-US") -> str:
    """
    Detect language for the current request.

    Priority:
    1) Header X-App-Lang (frontend UI language)
    2) body["language"] or query ?language=
    3) Header Accept-Language
    """
    # 1) Custom header
    lang = _normalize_lang(flask_request.headers.get("X-App-Lang"))
    if lang:
        return lang

    # 2) Explicit parameter
    if body and isinstance(body, dict):
        lang = _normalize_lang(body.get("language"))
        if lang:
            return lang
    lang = _normalize_lang(flask_request.args.get("language"))
    if lang:
        return lang

    # 3) Browser default
    lang = _normalize_lang(flask_request.headers.get("Accept-Language"))
    if lang:
        return lang

    return default


