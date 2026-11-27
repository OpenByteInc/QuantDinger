"""Validate equity execution prices independently of cache/receipt clocks."""

from math import isfinite

import pandas as pd

from app.data_sources.quote_time import quote_timestamp
from app.services.market_schedule import CALENDAR_BY_MARKET, equity_order_session_error


# Minute-bar quotes are dated at the beginning of the observed minute.
EQUITY_QUOTE_MAX_AGE_SECONDS = 90.0


def daily_signal_wait_reason(candidates, prices, seen_at, *, clock, max_age, quotes, now):
    """Describe the exact missing execution quotes without weakening freshness."""
    missing = sorted(str(member["key"]) for member in candidates if (
        str(member["key"]) not in prices
        or clock - seen_at.get(str(member["key"]), 0.0) > max_age
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
        if not 0 <= current.timestamp() - observed <= EQUITY_QUOTE_MAX_AGE_SECONDS:
            continue
        if equity_order_session_error(
            market, now=current.to_pydatetime(),
            queued_at=pd.Timestamp(observed, unit="s", tz="UTC"),
        ):
            continue
        accepted[key] = price
    return accepted
