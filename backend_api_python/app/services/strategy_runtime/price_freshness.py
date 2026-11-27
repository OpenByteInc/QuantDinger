"""Validate equity execution prices independently of cache/receipt clocks."""

from math import isfinite
import os

import pandas as pd

from app.data_sources.quote_time import quote_timestamp
from app.services.market_schedule import CALENDAR_BY_MARKET, equity_order_session_error


DEFAULT_EQUITY_QUOTE_MAX_AGE_MINUTES = 16


def parse_equity_quote_max_age_minutes(value):
    """Validate the shared settings and worker configuration contract."""
    minutes = float(value)
    if not isfinite(minutes) or not minutes.is_integer() or not 1 <= minutes <= 60:
        raise ValueError("settings.invalidEquityQuoteMaxAge")
    return int(minutes)


def equity_quote_max_age_seconds():
    try:
        minutes = parse_equity_quote_max_age_minutes(
            os.getenv("EQUITY_QUOTE_MAX_AGE_MINUTES", str(DEFAULT_EQUITY_QUOTE_MAX_AGE_MINUTES))
        )
    except (ValueError, TypeError):
        minutes = DEFAULT_EQUITY_QUOTE_MAX_AGE_MINUTES
    return minutes * 60.0


def execution_price_age_limit(key, fallback, execution_mode="live"):
    """Keep receipt expiry from overriding the configured equity quote window."""
    if execution_mode == "live" and str(key).split(":", 1)[0] in CALENDAR_BY_MARKET:
        return equity_quote_max_age_seconds()
    return fallback


def daily_signal_wait_reason(candidates, prices, seen_at, *, clock, max_age, quotes, now, execution_mode="live"):
    """Describe the exact missing execution quotes without weakening freshness."""
    missing = sorted(str(member["key"]) for member in candidates if (
        str(member["key"]) not in prices
        or clock - seen_at.get(str(member["key"]), 0.0)
        > execution_price_age_limit(member["key"], max_age, execution_mode)
    ))
    if not missing:
        return ""
    details = []
    for key in missing[:10]:
        quote = quotes.get(key) or {}
        observed = quote_timestamp(quote.get("quote_time"))
        age = f"{max(0, int(pd.Timestamp(now).timestamp() - observed))}s" if observed else "unknown"
        details.append(f"{key}[source={quote.get('quote_source') or 'unknown'},age={age}]")
    return f"Daily signal waiting for fresh quotes: {len(missing)}/{len(candidates)}; " + ", ".join(details)


def filter_execution_prices(prices, equity_quotes, *, now, execution_mode):
    if execution_mode != "live":
        return prices
    current = pd.Timestamp(now)
    max_age = equity_quote_max_age_seconds()
    accepted = {}
    for key, price in prices.items():
        market = str(key).split(":", 1)[0]
        if market not in CALENDAR_BY_MARKET:
            accepted[key] = price
            continue
        quote = equity_quotes.get(key) or {}
        observed = quote_timestamp(quote.get("quote_time"))
        try:
            quoted_price = float(quote.get("last") or 0)
        except (ValueError, TypeError):
            continue
        if not observed or not isfinite(price) or price <= 0:
            continue
        if quoted_price != price:
            continue
        if not 0 <= current.timestamp() - observed <= max_age:
            continue
        if equity_order_session_error(
            market, now=current.to_pydatetime(),
            queued_at=pd.Timestamp(observed, unit="s", tz="UTC"),
        ):
            continue
        accepted[key] = price
    return accepted
