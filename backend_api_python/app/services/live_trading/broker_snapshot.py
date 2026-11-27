"""Validate broker snapshots before they reach account and strategy reconciliation."""

import math

from app.services.live_trading.records import normalize_strategy_symbol


def broker_position_maps(rows):
    if not isinstance(rows, list):
        raise ValueError("Invalid broker position snapshot")
    sizes, prices, instruments = {}, {}, {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid broker position row")
        symbol = normalize_strategy_symbol(row.get("symbol"))
        quantity = row.get("quantity", row.get("qty"))
        if not symbol or quantity is None:
            raise ValueError("Incomplete broker position row")
        quantity = float(quantity)
        price = float(row.get("avg_entry_price") or row.get("avgCost") or row.get("entry_price") or 0)
        if not math.isfinite(quantity) or not math.isfinite(price):
            raise ValueError("Invalid broker position quantity or price")
        if quantity == 0:
            continue
        side = str(row.get("side") or ("short" if quantity < 0 else "long")).lower()
        if side not in {"long", "short"}:
            raise ValueError("Invalid broker position side")
        old_qty = sizes.setdefault(symbol, {"long": 0.0, "short": 0.0})[side]
        old_price = prices.setdefault(symbol, {"long": 0.0, "short": 0.0})[side]
        sizes[symbol][side] = old_qty + abs(quantity)
        prices[symbol][side] = (old_qty * old_price + abs(quantity) * price) / sizes[symbol][side]
        instruments.setdefault(symbol, {})[side] = symbol
    return sizes, prices, instruments
