"""Economic calendar via Finnhub (requires FINNHUB_API_KEY)."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

from app.config.api_keys import APIKeys
from app.config.data_sources import FinnhubConfig
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Lookback / lookahead window for the dashboard widget.
_CALENDAR_LOOKBACK_DAYS = 3
_CALENDAR_LOOKAHEAD_DAYS = 14

_COUNTRY_MAP = {
    "GB": "UK",
    "UK": "UK",
    "EU": "EU",
    "EMU": "EU",
    "EZ": "EU",
    "US": "US",
    "CN": "CN",
    "JP": "JP",
    "DE": "DE",
    "AU": "AU",
    "CA": "CA",
}

_EVENT_ZH_EXACT = {
    "us non-farm payrolls": "美国非农就业数据",
    "non farm payrolls": "美国非农就业数据",
    "nonfarm payrolls": "美国非农就业数据",
    "initial jobless claims": "美国初请失业金人数",
    "fed interest rate decision": "美联储利率决议",
    "federal funds rate": "美联储联邦基金利率",
    "us cpi m/m": "美国CPI月率",
    "us cpi y/y": "美国CPI年率",
    "ecb interest rate decision": "欧洲央行利率决议",
    "boj interest rate decision": "日本央行利率决议",
    "boe interest rate decision": "英国央行利率决议",
    "us retail sales m/m": "美国零售销售月率",
    "opec monthly report": "OPEC月度报告",
}

_EVENT_ZH_PATTERNS: Tuple[Tuple[str, str], ...] = (
    ("non farm payroll", "美国非农就业数据"),
    ("nonfarm payroll", "美国非农就业数据"),
    ("initial jobless claims", "美国初请失业金人数"),
    ("jobless claims", "美国初请失业金人数"),
    ("fomc", "美联储利率决议"),
    ("fed interest rate", "美联储利率决议"),
    ("federal funds rate", "美联储联邦基金利率"),
    ("consumer price index", "消费者物价指数"),
    (" cpi", "消费者物价指数"),
    ("ecb interest rate", "欧洲央行利率决议"),
    ("european central bank", "欧洲央行利率决议"),
    ("boj interest rate", "日本央行利率决议"),
    ("bank of japan", "日本央行利率决议"),
    ("boe interest rate", "英国央行利率决议"),
    ("bank of england", "英国央行利率决议"),
    ("retail sales", "零售销售"),
    ("opec", "OPEC月度报告"),
    ("gdp", "GDP"),
)

_BEARISH_IF_HIGHER_PATTERNS = (
    "cpi",
    "inflation",
    "ppi",
    "price index",
    "interest rate",
    "rate decision",
    "fomc",
    "fed funds",
    "unemployment",
    "jobless",
    "job cuts",
)

# Macro releases we keep even when estimate/prev are not yet published.
_MACRO_EVENT_HINTS = (
    "non farm payroll",
    "nonfarm payroll",
    "jobless claims",
    "interest rate",
    "rate decision",
    "fomc",
    "federal funds",
    "cpi",
    "ppi",
    "gdp",
    "retail sales",
    "pmi",
    "ism ",
    "unemployment",
    "trade balance",
    "consumer confidence",
    "industrial production",
    "housing starts",
    "opec",
)


def get_economic_calendar() -> List[Dict[str, Any]]:
    """Return macro calendar events from Finnhub, or an empty list if unavailable."""
    if not APIKeys.is_configured("FINNHUB_API_KEY"):
        logger.warning("FINNHUB_API_KEY not configured; economic calendar unavailable")
        return []
    try:
        events = _fetch_finnhub_calendar()
        if events:
            logger.info("Economic calendar loaded from Finnhub: %d events", len(events))
            return events
        logger.warning("Finnhub economic calendar returned no events")
    except requests.exceptions.RequestException as exc:
        logger.error("Finnhub economic calendar request failed: %s", exc)
    except Exception as exc:
        logger.error("Failed to fetch Finnhub economic calendar: %s", exc, exc_info=True)
    return []


def _fetch_finnhub_calendar() -> List[Dict[str, Any]]:
    today = datetime.now().date()
    date_from = (today - timedelta(days=_CALENDAR_LOOKBACK_DAYS)).isoformat()
    date_to = (today + timedelta(days=_CALENDAR_LOOKAHEAD_DAYS)).isoformat()

    resp = requests.get(
        f"{FinnhubConfig.BASE_URL}/calendar/economic",
        params={
            "from": date_from,
            "to": date_to,
            "token": APIKeys.FINNHUB_API_KEY,
        },
        timeout=FinnhubConfig.TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()

    rows: Any = payload.get("economicCalendar") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []

    events: List[Dict[str, Any]] = []
    seen_keys: set = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        event_en = (row.get("event") or row.get("title") or row.get("name") or "").strip()
        if not event_en or not _should_include_finnhub_row(row, event_en):
            continue
        normalized = _normalize_finnhub_event(row, idx)
        if not normalized:
            continue
        dedupe_key = (
            f"{normalized['date']}|{normalized['time']}|"
            f"{(normalized.get('name_en') or '').strip().lower()}"
        )
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        events.append(normalized)

    events.sort(
        key=lambda item: (
            _importance_rank(item.get("importance")),
            item["date"],
            item["time"],
        )
    )
    return events


def _normalize_finnhub_event(row: Dict[str, Any], idx: int) -> Optional[Dict[str, Any]]:
    event_en = (row.get("event") or row.get("title") or row.get("name") or "").strip()
    if not event_en:
        return None

    country_raw = (row.get("country") or row.get("region") or "INTL").strip().upper()
    country = _COUNTRY_MAP.get(country_raw, country_raw)

    date_str = _parse_date(row)
    time_str = _parse_time(row)
    importance = _map_importance(row.get("impact"))

    unit = (row.get("unit") or row.get("unitLabel") or "").strip()
    forecast = _format_value(row.get("estimate", row.get("forecast")), unit)
    previous = _format_value(row.get("prev", row.get("previous")), unit)

    actual_raw = row.get("actual")
    actual = (
        _format_value(actual_raw, unit)
        if actual_raw not in (None, "", "-")
        else None
    )

    is_released = actual is not None
    impact_if_above, impact_if_below = _impact_rules(event_en)
    actual_impact = None
    if is_released and actual and forecast:
        actual_impact = _compare_impact(
            actual, forecast, event_en, impact_if_above, impact_if_below
        )

    dedupe_key = f"{date_str}|{time_str}|{event_en}|{country}"
    event_id = row.get("id")
    if event_id is None:
        event_id = int(hashlib.md5(dedupe_key.encode()).hexdigest()[:8], 16)

    display_forecast = forecast or previous or "-"
    has_figures = any(v is not None for v in (forecast, previous, actual))
    if is_released:
        expected_impact = actual_impact or "neutral"
    elif has_figures or importance == "high" or _is_macro_event_name(event_en):
        expected_impact = impact_if_above
    else:
        expected_impact = "neutral"

    return {
        "id": event_id if isinstance(event_id, int) else idx + 1,
        "name": _zh_event_name(event_en),
        "name_en": event_en,
        "country": country,
        "date": date_str,
        "time": time_str,
        "importance": importance,
        "actual": actual,
        "forecast": display_forecast,
        "previous": previous or "-",
        "impact_if_above": impact_if_above,
        "impact_if_below": impact_if_below,
        "impact_desc": "",
        "impact_desc_en": "",
        "expected_impact": expected_impact,
        "actual_impact": actual_impact,
        "is_released": is_released,
        "source": "finnhub",
    }


def _parse_date(row: Dict[str, Any]) -> str:
    raw = row.get("date") or row.get("time") or row.get("datetime") or ""
    if isinstance(raw, (int, float)):
        return datetime.utcfromtimestamp(raw).strftime("%Y-%m-%d")
    text = str(raw).strip()
    if not text:
        return datetime.now().strftime("%Y-%m-%d")
    if "T" in text:
        return text.split("T", 1)[0]
    if " " in text and len(text) >= 10:
        return text[:10]
    return text[:10]


def _parse_time(row: Dict[str, Any]) -> str:
    raw = row.get("time") or row.get("datetime") or ""
    text = str(raw).strip()
    if not text:
        return "--:--"
    if "T" in text:
        part = text.split("T", 1)[1]
        return part[:5] if len(part) >= 5 else "--:--"
    if " " in text and len(text) >= 16:
        return text[11:16]
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", text):
        return text[:5]
    return "--:--"


def _map_importance(raw: Any) -> str:
    if raw is None:
        return "medium"
    text = str(raw).strip().lower()
    if text in ("3", "high", "h"):
        return "high"
    if text in ("1", "low", "l"):
        return "low"
    return "medium"


def _format_value(val: Any, unit: str) -> Optional[str]:
    if val is None or val == "" or val == "-":
        return None
    if isinstance(val, str):
        return val.strip()

    if not isinstance(val, (int, float)):
        return str(val)

    unit_l = (unit or "").lower()
    if unit_l in ("k", "thousand", "thousands"):
        return f"{int(round(val))}K"
    if unit_l in ("%", "percent", "pct", "percentage"):
        return f"{val:.2f}%"
    if isinstance(val, float) and not val.is_integer():
        return f"{val:.2f}"
    return str(int(val))


def _parse_numeric(text: Optional[str]) -> Optional[float]:
    if not text or text == "-":
        return None
    cleaned = str(text).strip().replace(",", "")
    multiplier = 1.0
    if cleaned.endswith("%"):
        cleaned = cleaned[:-1]
    elif cleaned.endswith("K") or cleaned.endswith("k"):
        cleaned = cleaned[:-1]
        multiplier = 1000.0
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None


def _importance_rank(importance: Optional[str]) -> int:
    order = {"high": 0, "medium": 1, "low": 2}
    return order.get(str(importance or "medium").lower(), 1)


def _row_has_figures(row: Dict[str, Any]) -> bool:
    for key in ("estimate", "forecast", "prev", "previous", "actual"):
        val = row.get(key)
        if val not in (None, "", "-"):
            return True
    return False


def _is_macro_event_name(event_en: str) -> bool:
    lowered = event_en.strip().lower()
    return any(hint in lowered for hint in _MACRO_EVENT_HINTS)


def _should_include_finnhub_row(row: Dict[str, Any], event_en: str) -> bool:
    """Drop market holidays / empty rows; keep scheduled macro releases."""
    importance = _map_importance(row.get("impact"))
    if _row_has_figures(row):
        return True
    if importance == "high":
        return True
    if _is_macro_event_name(event_en):
        return True
    return False


def _impact_rules(event_name: str) -> Tuple[str, str]:
    lowered = event_name.lower()
    if any(pattern in lowered for pattern in _BEARISH_IF_HIGHER_PATTERNS):
        return "bearish", "bullish"
    return "bullish", "bearish"


def _compare_impact(
    actual: str,
    forecast: str,
    event_name: str,
    impact_if_above: str,
    impact_if_below: str,
) -> str:
    actual_num = _parse_numeric(actual)
    forecast_num = _parse_numeric(forecast)
    if actual_num is None or forecast_num is None:
        return "neutral"
    if actual_num > forecast_num:
        return impact_if_above
    if actual_num < forecast_num:
        return impact_if_below
    return "neutral"


def _zh_event_name(event_en: str) -> str:
    lowered = event_en.strip().lower()
    if lowered in _EVENT_ZH_EXACT:
        return _EVENT_ZH_EXACT[lowered]
    for pattern, zh_name in _EVENT_ZH_PATTERNS:
        if pattern in lowered:
            return zh_name
    return event_en
