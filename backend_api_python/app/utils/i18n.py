"""Simple i18n loader that reads translations from .github/extensions/copilot-zh-cn/zh-CN.json.

Provides a minimal `t(key, locale)` helper that returns the translation string
or None if not found. Cached for performance.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import json
from typing import Optional, Dict, Any


@lru_cache()
def _load_translations() -> Dict[str, Dict[str, Any]]:
    # repo root: assume this file is at <repo>/backend_api_python/app/utils/i18n.py
    root = Path(__file__).resolve().parents[3]
    p = root / ".github" / "extensions" / "copilot-zh-cn" / "zh-CN.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf8"))
        # Support either {"zh-CN": {...}} or bare {...} (assume zh-CN)
        if isinstance(data, dict) and any("-" in k for k in data.keys()):
            return data
        return {"zh-CN": data}
    except Exception:
        return {}


def t(key: str, locale: str = "zh-CN") -> Optional[str]:
    """Return translation for key in given locale or None if missing."""
    tr = _load_translations()
    loc = tr.get(locale)
    if not loc:
        return None
    val = loc.get(key)
    if isinstance(val, str):
        return val
    return None
