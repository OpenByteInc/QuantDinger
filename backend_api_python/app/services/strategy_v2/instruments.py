"""Canonical instrument parsing for Strategy API V2."""

from __future__ import annotations

import re
from typing import Any, Mapping

from app.services.market_context import parse_crypto_identifier

from .frequencies import normalize_frequency  # noqa: F401 - public compatibility export
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
    raw_symbol = body.rsplit("@", 1)[0] if "@" in body else body
    symbol = _normalize_symbol(raw_symbol)
    if not market:
        market = infer_market(symbol)
    if not market:
        raise InstrumentParseError(f"strategyV2.marketUnknown:{raw}")

    if market == "Crypto":
        try:
            symbol, parsed_market_type, parsed_exchange_id = parse_crypto_identifier(body)
        except ValueError as exc:
            raise InstrumentParseError(str(exc)) from exc
        exchange_id = parsed_exchange_id
        market_type = parsed_market_type
        market_type = market_type if market_type in _CRYPTO_MARKET_TYPES else "spot"
    else:
        exchange_id = ""
        market_type = ""
        if market == "HKStock":
            from app.services.openapi_brokers.symbols import equity_symbol

            symbol = equity_symbol(symbol, market)

    return InstrumentSpec(
        market=market,
        symbol=symbol,
        exchange_id=exchange_id,
        market_type=market_type,
        instrument_id=raw,
    )


def instrument_key_from_metadata(item: Mapping[str, Any]) -> str:
    market = str(item.get("market") or "").strip()
    symbol = str(item.get("symbol") or "").strip()
    if market and symbol:
        raw = f"{market}:{symbol}"
        if _normalize_market(market) == "Crypto":
            exchange_id = str(item.get("exchange_id") or "").strip()
            market_type = str(item.get("market_type") or "").strip()
            if exchange_id:
                raw += f"@{exchange_id}"
                if market_type:
                    raw += f":{market_type}"
            elif market_type:
                raw += f"@{market_type}"
        return parse_instrument(raw).key
    fallback = item.get("key") or item.get("instrument_id")
    return parse_instrument(fallback, default_market=market).key if fallback else ""


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
