"""Validate equity execution prices independently of cache/receipt clocks."""

from math import isfinite

import pandas as pd

from app.data_sources.quote_time import quote_timestamp
from app.services.market_schedule import CALENDAR_BY_MARKET, equity_order_session_error


# Minute-bar quotes are dated at the beginning of the observed minute.
EQUITY_QUOTE_MAX_AGE_SECONDS = 90.0


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
