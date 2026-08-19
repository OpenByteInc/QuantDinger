"""Canonical instrument parsing for Strategy API V2."""

from __future__ import annotations

import re

from .frequencies import normalize_frequency
from .models import InstrumentSpec


_MARKETS = {
    "cnstock": "CNStock",
    "usstock": "USStock",
    "hkstock": "HKStock",
    "crypto": "Crypto",
    "forex": "Forex",
    "futures": "Futures",
    "moex": "MOEX",
}
_CRYPTO_MARKET_TYPES = {"spot", "swap"}


class InstrumentParseError(ValueError):
    pass


def parse_instrument(value: object, *, default_market: str = "") -> InstrumentSpec:
    raw = str(value or "").strip()
    if not raw:
        raise InstrumentParseError("strategyV2.instrumentRequired")

    market = _normalize_market(default_market)
    body = raw
    if ":" in raw:
        prefix, candidate = raw.split(":", 1)
        normalized = _normalize_market(prefix)
        if normalized:
            market = normalized
            body = candidate

    exchange_id = ""
    market_type = ""
    if "@" in body:
        body, venue = body.rsplit("@", 1)
        if venue.strip().lower() in _CRYPTO_MARKET_TYPES:
            market_type = venue
        elif ":" in venue:
            exchange_id, market_type = venue.split(":", 1)
        else:
            exchange_id = venue
        exchange_id = exchange_id.strip().lower()
        market_type = market_type.strip().lower()

    symbol = _normalize_symbol(body)
    if not market:
        market = infer_market(symbol)
    if not market:
        raise InstrumentParseError(f"strategyV2.marketUnknown:{raw}")

    if market == "Crypto":
        symbol = _normalize_crypto_symbol(symbol)
        market_type = market_type if market_type in _CRYPTO_MARKET_TYPES else "spot"
    else:
        exchange_id = ""
        market_type = ""

    return InstrumentSpec(
        market=market,
        symbol=symbol,
        exchange_id=exchange_id,
        market_type=market_type,
        instrument_id=raw,
    )


def infer_market(symbol: str) -> str:
    value = str(symbol or "").strip().upper()
    if not value:
        return ""
    if "/" in value or value.endswith("USDT") or value.endswith("USDC"):
        return "Crypto"
    if value.endswith(".HK") or value.endswith(".XHKG"):
        return "HKStock"
    if value.endswith((".SH", ".SZ", ".BJ", ".XSHG", ".XSHE", ".XBJS")):
        return "CNStock"
    if re.fullmatch(r"\d{6}", value):
        return "CNStock"
    if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", value):
        return "USStock"
    return ""


def is_index_reference(value: object) -> bool:
    raw = str(value or "").strip().upper()
    return raw.startswith("INDEX:") or raw.endswith(".XBHS") or raw.endswith(".XSHG_INDEX")


def normalize_index_reference(value: object) -> str:
    raw = str(value or "").strip()
    if raw.upper().startswith("INDEX:"):
        return raw
    upper = raw.upper()
    if upper.endswith(".XBHS"):
        return f"CNStock:{upper[:-5]}.SH"
    return raw


def normalize_pool_reference(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise InstrumentParseError("strategyV2.universeRequired")
    if raw.upper().startswith("POOL:"):
        raw = raw.split(":", 1)[1]
    return f"POOL:{raw.lower()}"


def _normalize_market(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return _MARKETS.get(raw.lower(), raw if raw in _MARKETS.values() else "")


def _normalize_symbol(value: object) -> str:
    raw = str(value or "").strip().upper()
    replacements = {
        ".XSHG": ".SH",
        ".XSHE": ".SZ",
        ".XBJS": ".BJ",
        ".XHKG": ".HK",
    }
    for old, new in replacements.items():
        if raw.endswith(old):
            raw = f"{raw[:-len(old)]}{new}"
            break
    return raw


def _normalize_crypto_symbol(value: object) -> str:
    raw = str(value or "").strip().upper().replace("-", "/").replace("_", "/")
    if "/" not in raw:
        for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
            if raw.endswith(quote) and len(raw) > len(quote):
                return f"{raw[:-len(quote)]}/{quote}"
    return raw
