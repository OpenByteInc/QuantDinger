"""Economic calendar from free-first providers with optional Finnhub fallback."""
from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

from app.config.api_keys import APIKeys
from app.config.data_sources import FinnhubConfig, TradingEconomicsConfig
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Lookback / lookahead window for the dashboard widget.
_CALENDAR_LOOKBACK_DAYS = 3
_CALENDAR_LOOKAHEAD_DAYS = 14
_AKSHARE_LOOKBACK_DAYS = 1
_AKSHARE_LOOKAHEAD_DAYS = 7
_TRADING_ECONOMICS_LOOKBACK_DAYS = 3
_TRADING_ECONOMICS_LOOKAHEAD_DAYS = 14

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

_AKSHARE_COUNTRY_MAP = {
    "\u7f8e\u56fd": "US",
    "\u4e2d\u56fd": "CN",
    "\u6b27\u5143\u533a": "EU",
    "\u6b27\u76df": "EU",
    "\u82f1\u56fd": "UK",
    "\u65e5\u672c": "JP",
    "\u5fb7\u56fd": "DE",
    "\u6fb3\u5927\u5229\u4e9a": "AU",
    "\u52a0\u62ff\u5927": "CA",
    "\u65b0\u897f\u5170": "NZ",
    "\u745e\u58eb": "CH",
    "\u6cd5\u56fd": "FR",
    "\u610f\u5927\u5229": "IT",
}

_AKSHARE_BEARISH_IF_HIGHER_HINTS = (
    "\u5229\u7387",
    "\u901a\u80c0",
    "CPI",
    "PPI",
    "\u5931\u4e1a",
    "\u521d\u8bf7",
    "\u88c1\u5458",
)

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

_VOLATILITY_WINDOW_BY_IMPORTANCE = {
    "high": {"pre_minutes": 30, "post_minutes": 60, "label": "T-30m to T+60m"},
    "medium": {"pre_minutes": 15, "post_minutes": 30, "label": "T-15m to T+30m"},
    "low": {"pre_minutes": 5, "post_minutes": 15, "label": "T-5m to T+15m"},
}

_ASSET_LABELS = {
    "DXY": {"label": "\u7f8e\u5143\u6307\u6570", "label_en": "US Dollar Index"},
    "US10Y": {"label": "\u7f8e\u503a\u6536\u76ca\u7387", "label_en": "US Treasury Yields"},
    "SPX": {"label": "\u7f8e\u80a1\u6307\u6570", "label_en": "US Equity Indices"},
    "NASDAQ": {"label": "\u7eb3\u65af\u8fbe\u514b", "label_en": "Nasdaq"},
    "GOLD": {"label": "\u9ec4\u91d1", "label_en": "Gold"},
    "BTC": {"label": "\u6bd4\u7279\u5e01", "label_en": "Bitcoin"},
    "OIL": {"label": "\u539f\u6cb9", "label_en": "Crude Oil"},
    "CAD": {"label": "\u52a0\u5143", "label_en": "Canadian Dollar"},
    "EURUSD": {"label": "\u6b27\u5143/\u7f8e\u5143", "label_en": "EUR/USD"},
    "JPY": {"label": "\u65e5\u5143", "label_en": "Japanese Yen"},
}

_EVENT_TYPE_RULES: Tuple[Tuple[Tuple[str, ...], str, Tuple[str, ...]], ...] = (
    (("cpi", "inflation", "ppi", "\u901a\u80c0", "\u7269\u4ef7"), "inflation", ("DXY", "US10Y", "SPX", "NASDAQ", "GOLD", "BTC")),
    (("fomc", "fed", "federal funds", "interest rate", "rate decision", "\u5229\u7387", "\u964d\u606f", "\u52a0\u606f"), "central_bank", ("DXY", "US10Y", "SPX", "GOLD", "BTC")),
    (("non farm", "nonfarm", "payroll", "unemployment", "jobless", "\u975e\u519c", "\u5931\u4e1a", "\u521d\u8bf7"), "jobs", ("DXY", "US10Y", "SPX", "GOLD", "BTC")),
    (("gdp", "pmi", "ism", "retail sales", "industrial production", "\u96f6\u552e", "\u5236\u9020\u4e1a", "\u5de5\u4e1a\u4ea7\u51fa"), "growth", ("SPX", "DXY", "US10Y", "GOLD")),
    (("opec", "crude", "oil", "\u539f\u6cb9", "\u6b27\u4f69\u514b"), "energy", ("OIL", "CAD", "DXY")),
    (("earnings", "report", "call", "\u8d22\u62a5"), "earnings", ("SPX", "NASDAQ")),
)


def get_economic_calendar_payload() -> Dict[str, Any]:
    """Return macro calendar events plus status metadata for UI diagnostics.

    Free deployments should not touch Finnhub's paid Economic Calendar endpoint
    by default. We use AkShare's WallstreetCN adapter as the no-key free source,
    optionally try Trading Economics when credentials are configured, and only
    attempt Finnhub when FINNHUB_FREE_ONLY=false.
    """
    if TradingEconomicsConfig.CONFIGURED:
        try:
            events = _fetch_tradingeconomics_calendar()
            if events:
                logger.info("Economic calendar loaded from Trading Economics: %d events", len(events))
                return {
                    "events": events,
                    "status": "ok",
                    "source": "tradingeconomics",
                    "config_key": "TRADING_ECONOMICS_CLIENT",
                    "message": "",
                }
            logger.warning("Trading Economics economic calendar returned no events")
            fallback = _fallback_calendar_payload(
                "empty", "Trading Economics returned no economic calendar events.",
                fallback_from="tradingeconomics",
            )
            if fallback:
                return fallback
        except requests.exceptions.RequestException as exc:
            public_message = _public_request_error_message(exc)
            status_code = _request_status_code(exc)
            logger.warning("Trading Economics economic calendar request failed: %s", public_message)
            fallback = _fallback_calendar_payload(
                f"http_{status_code}" if status_code else "upstream_error",
                public_message,
                fallback_from="tradingeconomics",
            )
            if fallback:
                return fallback
        except Exception as exc:
            logger.warning("Trading Economics economic calendar failed: %s", exc, exc_info=True)
            fallback = _fallback_calendar_payload(
                "error",
                str(exc),
                fallback_from="tradingeconomics",
            )
            if fallback:
                return fallback
    else:
        fallback = _fallback_calendar_payload(
            "not_configured",
            "Trading Economics credentials are not configured; using AkShare WallstreetCN calendar fallback.",
            fallback_from="tradingeconomics",
        )
        if fallback:
            return fallback

    if FinnhubConfig.FREE_ONLY:
        return {
            "events": [],
            "status": "empty",
            "source": "free_calendar_sources",
            "config_key": "TRADING_ECONOMICS_CLIENT",
            "message": "Free economic calendar sources returned no events. Finnhub paid calendar is skipped because FINNHUB_FREE_ONLY=true.",
        }

    if not APIKeys.is_configured("FINNHUB_API_KEY"):
        return {
            "events": [],
            "status": "missing_config",
            "source": "finnhub",
            "config_key": "FINNHUB_API_KEY",
            "message": "FINNHUB_API_KEY is not configured and free calendar sources returned no events.",
        }

    try:
        events = _fetch_finnhub_calendar()
        if events:
            logger.info("Economic calendar loaded from Finnhub: %d events", len(events))
            return {
                "events": events,
                "status": "ok",
                "source": "finnhub",
                "config_key": "FINNHUB_API_KEY",
                "message": "",
            }
        logger.warning("Finnhub economic calendar returned no events")
        return {
            "events": [],
            "status": "empty",
            "source": "finnhub",
            "config_key": "FINNHUB_API_KEY",
            "message": "Finnhub returned no economic calendar events for the current window.",
        }
    except requests.exceptions.RequestException as exc:
        status_code = _request_status_code(exc)
        public_message = _public_request_error_message(exc)
        logger.error("Finnhub economic calendar request failed: %s", public_message)
        if status_code in (401, 403):
            return {
                "events": [],
                "status": "forbidden",
                "source": "finnhub",
                "config_key": "FINNHUB_API_KEY",
                "message": (
                    "Finnhub rejected the economic calendar request. "
                    "Check whether the API key has access to the Economic Calendar endpoint."
                ),
            }
        if status_code == 429:
            return {
                "events": [],
                "status": "rate_limited",
                "source": "finnhub",
                "config_key": "FINNHUB_API_KEY",
                "message": "Finnhub rate limit exceeded for the economic calendar endpoint.",
            }
        return {
            "events": [],
            "status": "upstream_error",
            "source": "finnhub",
            "config_key": "FINNHUB_API_KEY",
            "message": public_message,
        }
    except Exception as exc:
        logger.error("Failed to fetch Finnhub economic calendar: %s", exc, exc_info=True)
        return {
            "events": [],
            "status": "error",
            "source": "finnhub",
            "config_key": "FINNHUB_API_KEY",
            "message": str(exc),
        }


def get_economic_calendar() -> List[Dict[str, Any]]:
    """Return macro calendar events, or an empty list if all providers fail."""
    payload = get_economic_calendar_payload()
    events = payload.get("events") if isinstance(payload, dict) else payload
    return events if isinstance(events, list) else []


def _fallback_calendar_payload(
    reason_status: str,
    reason_message: str,
    fallback_from: str = "finnhub",
) -> Optional[Dict[str, Any]]:
    """Use AkShare/WallstreetCN calendar when the primary source is unavailable."""
    try:
        events = _fetch_akshare_calendar()
        if not events:
            logger.warning("AkShare economic calendar fallback returned no events")
            return None
        logger.info(
            "Economic calendar loaded from AkShare fallback: %d events (%s=%s)",
            len(events),
            fallback_from,
            reason_status,
        )
        if reason_status == "not_configured":
            message = (
                f"{fallback_from} credentials are not configured; "
                "using AkShare WallstreetCN calendar fallback."
            )
        else:
            message = (
                f"{fallback_from} economic calendar is unavailable; "
                "using AkShare WallstreetCN calendar fallback."
            )

        return {
            "events": events,
            "status": "ok",
            "source": "akshare_wallstreetcn",
            "fallback_from": fallback_from,
            "fallback_reason": reason_status,
            "config_key": "",
            "message": message,
        }
    except Exception as exc:
        logger.warning(
            "AkShare economic calendar fallback failed after %s: %s",
            reason_status,
            exc,
        )
        return None


def _request_status_code(exc: requests.exceptions.RequestException) -> Optional[int]:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None)


def _public_request_error_message(exc: requests.exceptions.RequestException) -> str:
    text = str(exc)
    # requests.HTTPError includes the full URL. Never leak query tokens to the UI.
    text = re.sub(r"([?&]token=)[^&\s]+", r"\1***", text)
    text = re.sub(r"([?&]c=)[^&\s]+", r"\1***", text)
    return text


def _fetch_tradingeconomics_calendar() -> List[Dict[str, Any]]:
    today = datetime.now().date()
    date_from = (today - timedelta(days=_TRADING_ECONOMICS_LOOKBACK_DAYS)).isoformat()
    date_to = (today + timedelta(days=_TRADING_ECONOMICS_LOOKAHEAD_DAYS)).isoformat()

    resp = requests.get(
        f"{TradingEconomicsConfig.BASE_URL}/calendar/country/All/{date_from}/{date_to}",
        params={
            "c": TradingEconomicsConfig.CREDENTIALS,
            "f": "json",
        },
        timeout=TradingEconomicsConfig.TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()
    rows: Any = payload.get("Calendar") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []

    events: List[Dict[str, Any]] = []
    seen_keys: set = set()
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        event_en = (
            row.get("Event")
            or row.get("event")
            or row.get("Title")
            or row.get("title")
            or row.get("Category")
            or row.get("category")
            or ""
        )
        event_en = str(event_en).strip()
        if not event_en or not _should_include_tradingeconomics_row(row, event_en):
            continue
        normalized = _normalize_tradingeconomics_event(row, idx)
        if not normalized:
            continue
        dedupe_key = (
            f"{normalized['date']}|{normalized['time']}|"
            f"{(normalized.get('name_en') or '').strip().lower()}|"
            f"{normalized.get('country') or ''}"
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


def _fetch_akshare_calendar() -> List[Dict[str, Any]]:
    """Fetch macro calendar from AkShare WallstreetCN adapter."""
    import akshare as ak

    today = datetime.now().date()
    events: List[Dict[str, Any]] = []
    seen_keys: set = set()
    idx_seq = 0

    for day_offset in range(-_AKSHARE_LOOKBACK_DAYS, _AKSHARE_LOOKAHEAD_DAYS + 1):
        day = today + timedelta(days=day_offset)
        try:
            df = ak.macro_info_ws(day.strftime("%Y%m%d"))
        except Exception as exc:
            logger.debug("AkShare macro_info_ws skipped %s: %s", day, exc)
            continue
        if df is None or getattr(df, "empty", False):
            continue
        for _, series in df.iterrows():
            row = series.to_dict()
            normalized = _normalize_akshare_event(row, idx_seq)
            idx_seq += 1
            if not normalized:
                continue
            dedupe_key = (
                f"{normalized['date']}|{normalized['time']}|"
                f"{(normalized.get('name_en') or '').strip().lower()}|"
                f"{normalized.get('country') or ''}"
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

    return _with_ai_insight({
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
    })


def _normalize_tradingeconomics_event(row: Dict[str, Any], idx: int) -> Optional[Dict[str, Any]]:
    event_en = str(
        row.get("Event")
        or row.get("event")
        or row.get("Title")
        or row.get("title")
        or row.get("Category")
        or row.get("category")
        or ""
    ).strip()
    if not event_en:
        return None

    country_raw = str(row.get("Country") or row.get("country") or "INTL").strip()
    country_code = str(row.get("CountryCode") or row.get("countryCode") or "").strip().upper()
    country = country_code or _COUNTRY_MAP.get(country_raw.upper(), country_raw.upper() or "INTL")

    date_value = (
        row.get("Date")
        or row.get("date")
        or row.get("LastUpdate")
        or row.get("lastUpdate")
        or ""
    )
    date_str = _parse_date({"date": date_value})
    time_str = _parse_time({"time": date_value})
    importance = _map_importance(row.get("Importance") or row.get("importance"))

    unit = str(row.get("Unit") or row.get("unit") or "").strip()
    actual = _format_value(
        _none_if_nan(row.get("Actual") if "Actual" in row else row.get("actual")),
        unit,
    )
    forecast = _format_value(
        _none_if_nan(row.get("Forecast") if "Forecast" in row else row.get("forecast")),
        unit,
    )
    previous = _format_value(
        _none_if_nan(row.get("Previous") if "Previous" in row else row.get("previous")),
        unit,
    )

    impact_if_above, impact_if_below = _impact_rules(event_en)
    actual_impact = None
    if actual and forecast:
        actual_impact = _compare_impact(
            actual, forecast, event_en, impact_if_above, impact_if_below
        )

    is_released = actual is not None
    has_figures = any(v is not None for v in (forecast, previous, actual))
    if is_released:
        expected_impact = actual_impact or "neutral"
    elif has_figures or importance == "high" or _is_macro_event_name(event_en):
        expected_impact = impact_if_above
    else:
        expected_impact = "neutral"

    dedupe_key = f"{date_str}|{time_str}|{event_en}|{country}"
    event_id = row.get("CalendarId") or row.get("calendarId") or row.get("ID") or row.get("id")
    if event_id is None:
        event_id = int(hashlib.md5(dedupe_key.encode()).hexdigest()[:8], 16)

    return _with_ai_insight({
        "id": event_id if isinstance(event_id, int) else int(hashlib.md5(str(event_id).encode()).hexdigest()[:8], 16),
        "name": _zh_event_name(event_en),
        "name_en": event_en,
        "country": country,
        "date": date_str,
        "time": time_str,
        "importance": importance,
        "actual": actual,
        "forecast": forecast or previous or "-",
        "previous": previous or "-",
        "impact_if_above": impact_if_above,
        "impact_if_below": impact_if_below,
        "impact_desc": "",
        "impact_desc_en": "",
        "expected_impact": expected_impact,
        "actual_impact": actual_impact,
        "is_released": is_released,
        "source": "tradingeconomics",
        "url": str(row.get("URL") or row.get("url") or "").strip(),
    })


def _normalize_akshare_event(row: Dict[str, Any], idx: int) -> Optional[Dict[str, Any]]:
    time_col = "\u65f6\u95f4"
    country_col = "\u5730\u533a"
    event_col = "\u4e8b\u4ef6"
    importance_col = "\u91cd\u8981\u6027"
    actual_col = "\u4eca\u503c"
    forecast_col = "\u9884\u671f"
    previous_col = "\u524d\u503c"
    link_col = "\u94fe\u63a5"

    event_name = str(row.get(event_col) or "").strip()
    if not event_name:
        return None

    raw_country = str(row.get(country_col) or "INTL").strip()
    country = _AKSHARE_COUNTRY_MAP.get(raw_country, raw_country.upper() or "INTL")

    raw_time = row.get(time_col)
    if isinstance(raw_time, datetime):
        date_str = raw_time.strftime("%Y-%m-%d")
        time_str = raw_time.strftime("%H:%M")
    else:
        time_text = str(raw_time or "").strip()
        date_str = time_text[:10] if len(time_text) >= 10 else datetime.now().strftime("%Y-%m-%d")
        time_str = time_text[11:16] if len(time_text) >= 16 else "--:--"

    actual = _format_value(_none_if_nan(row.get(actual_col)), "")
    forecast = _format_value(_none_if_nan(row.get(forecast_col)), "")
    previous = _format_value(_none_if_nan(row.get(previous_col)), "")
    importance = _map_akshare_importance(row.get(importance_col))
    impact_if_above, impact_if_below = _akshare_impact_rules(event_name)
    actual_impact = None
    if actual and forecast:
        actual_impact = _compare_impact(
            actual, forecast, event_name, impact_if_above, impact_if_below
        )
    is_released = actual is not None

    dedupe_key = f"{date_str}|{time_str}|{event_name}|{country}|{idx}"
    return _with_ai_insight({
        "id": int(hashlib.md5(dedupe_key.encode()).hexdigest()[:8], 16),
        "date": date_str,
        "time": time_str,
        "country": country,
        "name": event_name,
        "name_en": event_name,
        "importance": importance,
        "actual": actual,
        "forecast": forecast or previous or "-",
        "previous": previous or "-",
        "impact_if_above": impact_if_above,
        "impact_if_below": impact_if_below,
        "impact_desc": "",
        "impact_desc_en": "",
        "expected_impact": actual_impact or "neutral",
        "actual_impact": actual_impact,
        "is_released": is_released,
        "source": "akshare_wallstreetcn",
        "url": str(row.get(link_col) or "").strip(),
    })


def _with_ai_insight(event: Dict[str, Any]) -> Dict[str, Any]:
    event["ai_insight"] = _build_ai_calendar_insight(event)
    return event


def _build_ai_calendar_insight(event: Dict[str, Any]) -> Dict[str, Any]:
    """Create deterministic, explainable macro-event context for the UI."""
    name = f"{event.get('name_en') or ''} {event.get('name') or ''}"
    event_type, assets = _classify_event_context(name)
    importance = str(event.get("importance") or "medium").lower()
    impact = str(event.get("actual_impact") or event.get("expected_impact") or "neutral").lower()
    released = bool(event.get("is_released"))
    window = _VOLATILITY_WINDOW_BY_IMPORTANCE.get(
        importance, _VOLATILITY_WINDOW_BY_IMPORTANCE["medium"]
    )
    score = _event_impact_score(importance, event_type, released, impact)
    affected_assets = _affected_asset_payload(assets, event_type, impact)
    title, title_en = _event_outlook_title(impact, released)
    pre_note, pre_note_en = _event_pre_release_note(event_type, importance, window["label"])
    post_note, post_note_en = _event_post_release_note(event, impact)
    summary = post_note if released else pre_note
    summary_en = post_note_en if released else pre_note_en

    return {
        "version": "macro_event_context_v1",
        "event_type": event_type,
        "impact_score": score,
        "risk_level": _risk_level(score),
        "outlook": impact,
        "title": title,
        "title_en": title_en,
        "summary": summary,
        "summary_en": summary_en,
        "pre_release_note": pre_note,
        "pre_release_note_en": pre_note_en,
        "post_release_note": post_note if released else "",
        "post_release_note_en": post_note_en if released else "",
        "volatility_window": window,
        "affected_assets": affected_assets,
        "tags": _event_context_tags(event_type, importance, released),
        "liquidity_sweep_warning": score >= 70,
        "method": "rules_with_event_context",
    }


def _classify_event_context(event_name: str) -> Tuple[str, Tuple[str, ...]]:
    lowered = event_name.lower()
    for patterns, event_type, assets in _EVENT_TYPE_RULES:
        if any(pattern.lower() in lowered for pattern in patterns):
            return event_type, assets
    return "macro", ("DXY", "SPX", "GOLD", "BTC")


def _event_impact_score(importance: str, event_type: str, released: bool, impact: str) -> int:
    base = {"high": 72, "medium": 48, "low": 28}.get(importance, 48)
    if event_type in ("inflation", "central_bank", "jobs"):
        base += 10
    elif event_type in ("growth", "energy"):
        base += 5
    if released and impact in ("bullish", "bearish"):
        base += 8
    return max(0, min(100, base))


def _risk_level(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 45:
        return "medium"
    return "low"


def _event_outlook_title(impact: str, released: bool) -> Tuple[str, str]:
    if impact == "bullish":
        return (
            "\u504f\u5229\u591a\uff0c\u4f46\u9700\u9632\u6570\u636e\u524d\u540e\u5267\u70c8\u6ce2\u52a8",
            "Bullish bias, but expect event-window volatility",
        )
    if impact == "bearish":
        return (
            "\u504f\u5229\u7a7a\uff0c\u4e8b\u4ef6\u7a97\u53e3\u6ce2\u52a8\u98ce\u9669\u8f83\u9ad8",
            "Bearish bias with elevated event-window volatility",
        )
    if released:
        return (
            "\u5b9e\u9645\u503c\u4e0e\u9884\u671f\u63a5\u8fd1\uff0c\u5e02\u573a\u53ef\u80fd\u66f4\u5173\u6ce8\u540e\u7eed\u89e3\u8bfb",
            "Actual data is near consensus; follow-through matters more",
        )
    return (
        "\u516c\u5e03\u524d\u4ee5\u6ce2\u52a8\u98ce\u9669\u4e3a\u4e3b\uff0c\u65b9\u5411\u9700\u7b49\u6570\u636e\u786e\u8ba4",
        "Pre-release setup: volatility first, direction after confirmation",
    )


def _event_pre_release_note(event_type: str, importance: str, window_label: str) -> Tuple[str, str]:
    type_zh = _event_type_zh(event_type)
    type_en = _event_type_en(event_type)
    risk_zh = "\u9ad8\u5f71\u54cd" if importance == "high" else "\u4e2d\u7b49\u5f71\u54cd"
    risk_en = "high-impact" if importance == "high" else "medium-impact"
    return (
        f"{risk_zh}{type_zh}\u4e8b\u4ef6\uff0c\u5173\u6ce8 {window_label} \u6ce2\u52a8\u7a97\u53e3\uff1b\u516c\u5e03\u524d\u4e0d\u5efa\u8bae\u8ffd\u5355\uff0c\u7b49\u5b9e\u9645\u503c\u4e0e\u9884\u671f\u5dee\u5f02\u786e\u8ba4\u540e\u518d\u5224\u65ad\u65b9\u5411\u3002",
        f"{risk_en} {type_en} event. Watch the {window_label} volatility window; avoid chasing before release and wait for the actual-vs-consensus gap.",
    )


def _event_post_release_note(event: Dict[str, Any], impact: str) -> Tuple[str, str]:
    actual = event.get("actual") or "-"
    forecast = event.get("forecast") or "-"
    previous = event.get("previous") or "-"
    if impact == "bullish":
        direction_zh = "\u6570\u636e\u76f8\u5bf9\u5229\u591a"
        direction_en = "The data is relatively bullish"
    elif impact == "bearish":
        direction_zh = "\u6570\u636e\u76f8\u5bf9\u5229\u7a7a"
        direction_en = "The data is relatively bearish"
    else:
        direction_zh = "\u6570\u636e\u4e0e\u9884\u671f\u5dee\u5f02\u4e0d\u660e\u663e"
        direction_en = "The data is close to consensus"
    return (
        f"{direction_zh}\uff1a\u5b9e\u9645 {actual}\uff0c\u9884\u671f {forecast}\uff0c\u524d\u503c {previous}\u3002\u5efa\u8bae\u7ed3\u5408\u9996\u8f6e\u6ce2\u52a8\u540e\u7684\u6210\u4ea4\u91cf\u4e0e\u8d8b\u52bf\u5ef6\u7eed\u6027\u5224\u65ad\u3002",
        f"{direction_en}: actual {actual}, forecast {forecast}, previous {previous}. Confirm with post-release volume and trend follow-through.",
    )


def _affected_asset_payload(
    assets: Tuple[str, ...], event_type: str, impact: str
) -> List[Dict[str, str]]:
    return [_asset_payload(symbol, event_type, impact) for symbol in assets]


def _asset_payload(symbol: str, event_type: str, impact: str) -> Dict[str, str]:
    meta = _ASSET_LABELS.get(symbol, {"label": symbol, "label_en": symbol})
    bias = _asset_bias(symbol, event_type, impact)
    return {
        "symbol": symbol,
        "label": meta["label"],
        "label_en": meta["label_en"],
        "bias": bias,
        "bias_label": _bias_label(bias),
        "bias_label_en": _bias_label_en(bias),
    }


def _asset_bias(symbol: str, event_type: str, impact: str) -> str:
    if impact not in ("bullish", "bearish"):
        return "volatility"
    risk_assets = {"SPX", "NASDAQ", "BTC", "GOLD"}
    dollar_assets = {"DXY", "US10Y"}
    if event_type in ("inflation", "central_bank", "jobs"):
        if symbol in dollar_assets:
            return "up" if impact == "bearish" else "down"
        if symbol in risk_assets:
            return "down" if impact == "bearish" else "up"
    if event_type == "energy" and symbol == "OIL":
        return "up" if impact == "bullish" else "down"
    return "up" if impact == "bullish" else "down"


def _bias_label(bias: str) -> str:
    return {
        "up": "\u504f\u4e0a\u884c",
        "down": "\u504f\u4e0b\u884c",
        "volatility": "\u6ce2\u52a8\u653e\u5927",
    }.get(bias, "\u4e2d\u6027")


def _bias_label_en(bias: str) -> str:
    return {
        "up": "upside bias",
        "down": "downside bias",
        "volatility": "volatility risk",
    }.get(bias, "neutral")


def _event_context_tags(event_type: str, importance: str, released: bool) -> List[Dict[str, str]]:
    tags = [
        {"label": _event_type_zh(event_type), "label_en": _event_type_en(event_type)},
        {"label": _importance_zh(importance), "label_en": importance.title()},
    ]
    tags.append(
        {
            "label": "\u5df2\u516c\u5e03" if released else "\u5f85\u516c\u5e03",
            "label_en": "Released" if released else "Upcoming",
        }
    )
    return tags


def _event_type_zh(event_type: str) -> str:
    return {
        "inflation": "\u901a\u80c0",
        "central_bank": "\u592e\u884c\u653f\u7b56",
        "jobs": "\u5c31\u4e1a",
        "growth": "\u589e\u957f",
        "energy": "\u80fd\u6e90",
        "earnings": "\u8d22\u62a5",
        "macro": "\u5b8f\u89c2",
    }.get(event_type, "\u5b8f\u89c2")


def _event_type_en(event_type: str) -> str:
    return {
        "inflation": "Inflation",
        "central_bank": "Central bank policy",
        "jobs": "Labor market",
        "growth": "Growth",
        "energy": "Energy",
        "earnings": "Earnings",
        "macro": "Macro",
    }.get(event_type, "Macro")


def _importance_zh(importance: str) -> str:
    return {
        "high": "\u9ad8\u5f71\u54cd",
        "medium": "\u4e2d\u5f71\u54cd",
        "low": "\u4f4e\u5f71\u54cd",
    }.get(importance, "\u4e2d\u5f71\u54cd")


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
    if text in ("3", "4", "high", "h"):
        return "high"
    if text in ("1", "low", "l"):
        return "low"
    return "medium"


def _map_akshare_importance(raw: Any) -> str:
    value = _parse_numeric(_format_value(_none_if_nan(raw), "") or "")
    if value is None:
        return "medium"
    if value >= 3:
        return "high"
    if value <= 1:
        return "low"
    return "medium"


def _none_if_nan(val: Any) -> Any:
    if isinstance(val, float) and math.isnan(val):
        return None
    return val


def _format_value(val: Any, unit: str) -> Optional[str]:
    if val is None or val == "" or val == "-":
        return None
    if isinstance(val, float) and math.isnan(val):
        return None
    if isinstance(val, str):
        if val.strip().lower() == "nan":
            return None
        return val.strip()

    if not isinstance(val, (int, float)):
        return str(val)
    if not math.isfinite(float(val)):
        return None

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


def _tradingeconomics_row_has_figures(row: Dict[str, Any]) -> bool:
    for key in ("Actual", "Forecast", "Previous", "actual", "forecast", "previous"):
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


def _should_include_tradingeconomics_row(row: Dict[str, Any], event_en: str) -> bool:
    """Drop empty calendar noise; keep scheduled macro releases."""
    importance = _map_importance(row.get("Importance") or row.get("importance"))
    if _tradingeconomics_row_has_figures(row):
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


def _akshare_impact_rules(event_name: str) -> Tuple[str, str]:
    if any(pattern in event_name for pattern in _AKSHARE_BEARISH_IF_HIGHER_HINTS):
        return "bearish", "bullish"
    return _impact_rules(event_name)


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
