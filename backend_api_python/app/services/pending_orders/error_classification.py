"""Stable categories for exchange order failures shown to users and health checks."""

from __future__ import annotations

import re
from typing import Any


EXCHANGE_IDS = ("binance", "okx", "gate", "bybit", "bitget", "htx", "alpaca", "ibkr")


def _has_error_code(text: str, *codes: str) -> bool:
    """Match codes in common exchange error fields without matching prices/quantities."""
    return any(
        re.search(
            rf"(?:s?code|retcode|error(?:\s+code)?)\s*['\" :=-]*{re.escape(code)}(?!\d)",
            text,
        )
        for code in codes
    )


def _detect_exchange(text: str) -> str:
    lower = text.lower()
    for exchange_id in EXCHANGE_IDS:
        if re.search(rf"(?<![a-z]){re.escape(exchange_id)}(?![a-z])", lower):
            return exchange_id
    return "exchange"


def is_exchange_price_band_error(error: Any) -> bool:
    """Recognize dynamic exchange price-band rejections across adapters."""
    lower = str(error or "").strip().lower()
    if not lower:
        return False
    price_band_codes = (
        "51137",
        "51138",
        "-4016",
        "-4024",
        "110003",
        "30208",
        "30209",
        "110120",
        "110121",
        "25205",
        "25206",
        "22047",
    )
    message_tokens = (
        "highest price limit for the buy leg",
        "lowest price limit for the sell leg",
        "price exceeds the allowable range",
        "price is higher than the maximum",
        "price is lower than the minimum",
        "price higher than multiplier up",
        "price lower than multiplier down",
        "price_highter_than_multiplier_up",
        "price_lower_than_multiplier_down",
        "filter failure: percent_price",
        "filter failure: percent_price_by_side",
        "trading price cannot be below",
        "trading price cannot exceed",
        "order price exceeds the maximum price limit",
        "order price is higher than the maximum buying price",
        "order price is lower than the minimum selling price",
        "order price cannot be smaller than",
        "order price cannot be higher than",
        "price_too_deviated",
        "order price deviates too much from mark price",
        "outside the price band",
        "outside price band",
        "exceeds price limit",
    )
    has_known_code = any(
        re.search(
            rf"(?:s?code|retcode|error)\s*['\" :=-]*{re.escape(code)}(?!\d)",
            lower,
        )
        for code in price_band_codes
    )
    return has_known_code or any(token in lower for token in message_tokens)


def classify_exchange_order_error(error: Any) -> dict[str, Any]:
    raw = str(error or "").strip()
    lower = raw.lower()
    http_match = re.search(r"\bhttp\s+(\d{3})\b|\b(5\d{2})\s+(?:bad gateway|gateway timeout|service unavailable)\b", lower)
    http_status = int(next((value for value in (http_match.groups() if http_match else ()) if value), 0) or 0)
    exchange_id = _detect_exchange(raw)

    def result(category: str, *, retryable: bool = False) -> dict[str, Any]:
        return {
            "category": category,
            "retryable": retryable,
            "http_status": http_status,
            "exchange": exchange_id,
            "raw": raw,
        }

    if (
        http_status == 401
        or _has_error_code(lower, "-1002", "-1022", "-2014", "-2015", "10003", "10004", "10005", "10007", "10010", "33004", "50103", "50104", "50105", "50106", "40002", "40003", "40005", "40009", "40018")
        or any(token in lower for token in (
            "invalid api-key", "invalid api key", "api key is invalid", "api-key format invalid",
            "api key has expired", "permission denied", "invalid signature", "error sign",
            "user authentication failed", "unmatched ip", "invalid ip", "ip whitelist",
            "ip not whitelisted", "not_login", "unauthorized",
        ))
    ):
        return result("credentials")
    if (
        _has_error_code(lower, "-1021", "10002", "50102")
        or any(token in lower for token in ("invalid timestamp", "request time exceeds", "outside of the recvwindow", "timestamp request expired"))
    ):
        return result("clock_skew")
    if (
        http_status == 429
        or _has_error_code(lower, "-1003", "-1008", "-1015", "10006", "10429", "20003", "50011", "429")
        or any(token in lower for token in (
            "rate limit", "rate_limit", "too many request", "too many visits",
            "request_frequency", "operation too frequent", "max rate of messages",
        ))
    ):
        return result("rate_limit", retryable=True)
    if (
        _has_error_code(lower, "-1121", "10029", "25100", "200")
        or any(token in lower for token in (
            "invalid symbol", "bad_symbol", "contract_not_found", "trading pair does not exist",
            "no security definition has been found", "symbol is invalid",
        ))
    ):
        return result("invalid_symbol")
    if http_status >= 500 or any(token in lower for token in (
        "bad gateway", "gateway timeout", "service unavailable", "connection reset",
        "connection timed out", "timeout waiting for response", "temporarily unavailable",
    )):
        return result("transport", retryable=True)
    if is_exchange_price_band_error(raw):
        return result("price_band", retryable=True)
    if (
        _has_error_code(lower, "-2018", "-2019", "110004", "110006", "110007", "110012", "110044", "110045", "110051", "110052", "110053", "51008", "25202", "25203", "40310000")
        or any(token in lower for token in (
            "insufficient_available", "insufficient balance", "insufficient margin",
            "insufficient buying power", "not enough balance", "margin insufficient",
            "balance_not_enough", "balance is not enough", "margin while available",
        ))
    ):
        return result("insufficient_funds")
    if (
        _has_error_code(lower, "-1111", "-4003", "-4004", "-4005", "110017", "110094", "25207", "25208", "110", "140", "434")
        or re.search(r"step size|minqty|min qty|minsize|min size|min_notional|minnotional|invalid (qty|quantity|size|amount)|bad precision|too many decimals|minimum price variation|order size.*(?:too small|below|minimum)", lower)
    ):
        return result("order_size")
    if (
        _has_error_code(lower, "51010", "10008", "110015", "110024", "110025", "110026", "110028", "110029", "110036", "110038", "110073", "25009", "25010")
        or any(token in lower for token in (
            "position mode", "margin mode", "account mode", "hedge mode", "one-way mode",
            "leverage too high", "leverage too low", "okx_swap_account_mode_required",
        ))
    ):
        return result("account_configuration")
    if (
        _has_error_code(lower, "-2022", "-2024", "110005", "110008", "110010", "110034")
        or any(token in lower for token in (
            "reduceonly order is rejected", "reduce-only order", "position not sufficient",
            "position_empty", "position not found", "position does not exist",
            "position side does not match",
        ))
    ):
        return result("position_conflict")
    if (
        _has_error_code(lower, "-2023", "-2027", "-2028", "110011", "110013", "110039", "110040", "110046", "110089", "110090")
        or any(token in lower for token in (
            "risk_limit_exceeded", "liquidate_immediately", "in liquidation",
            "trigger a forced liquidation", "exceeds the maximum risk limit",
        ))
    ):
        return result("risk_limit")
    if (
        _has_error_code(lower, "-1016", "10016", "10019", "110063", "110066", "25101", "25102", "25104")
        or any(token in lower for token in (
            "service is restarting", "settlement in progress", "temporarily closed for maintenance",
            "contract delisted", "trading is currently not allowed", "market is closed",
        ))
    ):
        return result("market_unavailable", retryable=True)
    return result("exchange_rejected")


def classify_strategy_exchange_log(message: Any) -> dict[str, Any] | None:
    """Describe exchange failures in strategy logs, including legacy plain-text rows."""
    raw = str(message or "").strip()
    lower = raw.lower()
    if not raw:
        return None
    wrappers = (
        "exchange order failed (", "unexpected order error (", "auto-stopped (",
        "leverage or margin-mode setup failed for ", "exchange client creation failed (",
        "order rejected because the exchange position snapshot failed:",
        "ibkr order failed (", "ibkr order exception (", "alpaca order failed (",
        "alpaca order exception (",
    )
    looks_like_exchange_error = lower.startswith(wrappers) or (
        any(exchange_id in lower for exchange_id in EXCHANGE_IDS)
        and (" http " in lower or "retcode" in lower or '"code"' in lower or '"label"' in lower)
    )
    if not looks_like_exchange_error:
        return None

    classified = classify_exchange_order_error(raw)
    context_match = re.match(r"^[^(]+\(([^)]+)\)", raw)
    context = (context_match.group(1) if context_match else "").strip()
    if not context:
        symbol_match = re.match(r"^Leverage or margin-mode setup failed for\s+([^:]+)", raw, re.IGNORECASE)
        context = (symbol_match.group(1) if symbol_match else "").strip()
    classified.update({
        "context": context,
        "technical_detail": raw,
        "auto_stopped": lower.startswith("auto-stopped ("),
    })
    return classified


__all__ = [
    "classify_exchange_order_error",
    "classify_strategy_exchange_log",
    "is_exchange_price_band_error",
]
