"""
Alpaca symbol normalization.

Alpaca uses standard US ticker symbols for stocks/ETFs (e.g., "SPY", "AAPL")
and slash-separated pairs for crypto (e.g., "BTC/USD", "ETH/USD").

Invalid crypto symbols (e.g. BTC/USDT) are a common cause of API/WebSocket 400
"invalid syntax" errors on Alpaca market-data streams.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

# Quotes users often send; Alpaca crypto API uses USD pairs.
_ALPACA_CRYPTO_QUOTES = ("USD", "USDT", "USDC")
_CRYPTO_BASES = frozenset(
    {
        "BTC", "ETH", "SOL", "DOGE", "LTC", "BCH", "LINK", "UNI", "AAVE",
        "AVAX", "DOT", "MATIC", "SHIB", "XRP", "ADA",
    }
)

# Class-B style tickers: BRK/B, BF/B — not crypto pairs.
_CLASS_SHARE_SUFFIX = re.compile(r"^[A-Z]{1,5}/[A-Z]{1,2}$")


def _strip_exchange_prefix(symbol: str) -> str:
    s = (symbol or "").strip().upper()
    if ":" in s:
        s = s.split(":", 1)[-1].strip()
    return s


def _is_class_share_ticker(symbol: str) -> bool:
    return bool(_CLASS_SHARE_SUFFIX.match(symbol))


def _is_crypto_pair_symbol(symbol: str) -> bool:
    s = _strip_exchange_prefix(symbol)
    if "/" not in s:
        return any(s.endswith(q) for q in _ALPACA_CRYPTO_QUOTES) and len(s) > 3
    base, quote = s.split("/", 1)
    if not base or not quote:
        return False
    if _is_class_share_ticker(s):
        return False
    return quote in _ALPACA_CRYPTO_QUOTES or base in _CRYPTO_BASES


def infer_asset_class(symbol: str, *, market_hint: Optional[str] = None) -> str:
    """
    Infer us_equity vs crypto.

    market_hint: "USStock", "Crypto", or None (heuristic).
    """
    hint = (market_hint or "").strip().lower()
    if hint in ("usstock", "us_stock", "stock", "stocks", "equity"):
        return "us_equity"
    if hint in ("crypto", "cryptocurrency"):
        return "crypto"

    if _is_crypto_pair_symbol(symbol):
        return "crypto"
    return "us_equity"


def normalize_symbol(
    symbol: str,
    asset_class: str = "us_equity",
    *,
    market_hint: Optional[str] = None,
) -> str:
    """
    Normalize a symbol to Alpaca's expected format.

    Args:
        symbol: User-provided symbol (e.g., "AAPL", "BTCUSD", "BTC/USD")
        asset_class: "us_equity" or "crypto" (ignored when market_hint is set)
        market_hint: Optional market category ("USStock" / "Crypto")

    Returns:
        Normalized symbol for Alpaca REST / WebSocket subscribe
    """
    if market_hint:
        ac = infer_asset_class(symbol, market_hint=market_hint)
    else:
        ac = (asset_class or "us_equity").strip().lower()
        if ac not in ("crypto", "us_equity"):
            ac = infer_asset_class(symbol)

    s = _strip_exchange_prefix(symbol)

    if ac == "crypto":
        if "/" in s:
            base, quote = s.split("/", 1)
            if quote in ("USDT", "USDC"):
                quote = "USD"
            return f"{base}/{quote}"
        for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
            if s.endswith(quote) and len(s) > len(quote):
                base = s[: -len(quote)]
                q = "USD" if quote in ("USDT", "USDC") else quote
                return f"{base}/{q}"
        return s

    if "/" in s:
        return s.replace("/", ".")
    return s


def parse_symbol(
    symbol: str,
    *,
    market_hint: Optional[str] = None,
) -> Tuple[str, str]:
    """Parse into (normalized_symbol, asset_class)."""
    ac = infer_asset_class(symbol, market_hint=market_hint)
    return normalize_symbol(symbol, ac, market_hint=market_hint), ac


def format_display_symbol(symbol: str, asset_class: str = "us_equity") -> str:
    """Format symbol for UI display."""
    if asset_class == "crypto":
        return normalize_symbol(symbol, "crypto")
    return _strip_exchange_prefix(symbol)
