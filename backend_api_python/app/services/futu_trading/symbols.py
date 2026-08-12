"""Symbol mapping between QuantDinger and Futu OpenAPI codes."""

from __future__ import annotations

from typing import Optional, Tuple


def _clean(symbol: str) -> str:
    return str(symbol or "").strip().upper().replace(" ", "")


def infer_market_category(symbol: str, market_hint: str = "") -> str:
    hint = str(market_hint or "").strip()
    if hint in ("HKStock", "USStock"):
        return hint
    s = _clean(symbol)
    if s.startswith("HK.") or s.endswith(".HK") or s.startswith("HK:"):
        return "HKStock"
    if s.startswith("US.") or s.endswith(".US") or s.startswith("US:"):
        return "USStock"
    # Pure digits (with optional leading zeros) → HK
    code = s.split(".")[-1].split(":")[-1]
    if code.isdigit():
        return "HKStock"
    return "USStock"


def to_futu_code(symbol: str, market_category: str = "") -> str:
    """
    Convert QuantDinger symbol to Futu code.

    Examples:
        00700.HK / 700.HK / 00700 -> HK.00700
        AAPL / US.AAPL / AAPL.US -> US.AAPL
    """
    s = _clean(symbol)
    if not s:
        return ""
    market = infer_market_category(s, market_category)

    if s.startswith("HK.") or s.startswith("US."):
        prefix, code = s.split(".", 1)
        if prefix == "HK":
            return f"HK.{code.zfill(5) if code.isdigit() else code}"
        return f"US.{code}"

    if ":" in s:
        # NASDAQ:AAPL / HKEX:00700
        _, code = s.split(":", 1)
        s = code

    if s.endswith(".HK"):
        code = s[:-3]
        return f"HK.{code.zfill(5) if code.isdigit() else code}"
    if s.endswith(".US"):
        return f"US.{s[:-3]}"

    if market == "HKStock":
        code = s
        if code.isdigit():
            code = code.zfill(5)
        return f"HK.{code}"
    return f"US.{s}"


def from_futu_code(code: str) -> Tuple[str, str]:
    """
    Convert Futu code to QuantDinger display symbol + market category.

    Returns:
        (display_symbol, market_category)
        e.g. ("00700.HK", "HKStock"), ("AAPL", "USStock")
    """
    s = _clean(code)
    if not s:
        return "", ""
    if s.startswith("HK."):
        raw = s[3:]
        if raw.isdigit():
            raw = raw.zfill(5)
        return f"{raw}.HK", "HKStock"
    if s.startswith("US."):
        return s[3:], "USStock"
    if s.endswith(".HK"):
        return from_futu_code(to_futu_code(s, "HKStock"))
    return s, infer_market_category(s)


def parse_symbol(symbol: str, market_hint: str = "") -> Tuple[str, str]:
    """Return (futu_code, market_category)."""
    market = infer_market_category(symbol, market_hint)
    return to_futu_code(symbol, market), market


def format_display_symbol(futu_code: str) -> str:
    display, _ = from_futu_code(futu_code)
    return display


def normalize_symbol(symbol: str, market_type: str = "") -> Tuple[str, str]:
    """
    IBKR/Alpaca-compatible helper.

    Returns:
        (futu_code, market_category)
    """
    hint = "HKStock" if str(market_type or "").strip() in ("HKStock", "hk", "HK") else market_type
    if str(market_type or "").strip() in ("USStock", "us", "US", "spot"):
        # spot alone is ambiguous; prefer inference from symbol
        if str(market_type or "").strip() == "spot":
            hint = ""
        else:
            hint = "USStock"
    return parse_symbol(symbol, hint)


def lot_size_hint(market_category: str) -> Optional[int]:
    """Conservative default lot size when static info is unavailable."""
    if market_category == "HKStock":
        return 100
    if market_category == "USStock":
        return 1
    return None
