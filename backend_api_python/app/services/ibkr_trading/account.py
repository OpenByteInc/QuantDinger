"""Normalization of IBKR account summary tags.

``IB.accountSummary()`` returns tag rows (``NetLiquidation``, ``BuyingPower``,
...) whose values are strings paired with a currency.  The broker-account UI
reads flat numeric fields off the account payload -- the same shape the Alpaca
route already returns -- so the nested tag map alone renders as "--".

This module maps the tags the UI needs onto flat fields.  The original
``summary`` map stays in the response for agent/MCP consumers.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

# Flat field -> IB tags, in priority order.
_FLAT_FIELDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("net_liquidation", ("NetLiquidation",)),
    ("total_cash_value", ("TotalCashValue",)),
    ("buying_power", ("BuyingPower",)),
    ("init_margin_req", ("InitMarginReq", "FullInitMarginReq")),
    ("maint_margin_req", ("MaintMarginReq", "FullMaintMarginReq")),
    ("available_funds", ("AvailableFunds", "FullAvailableFunds")),
    ("excess_liquidity", ("ExcessLiquidity", "FullExcessLiquidity")),
    ("equity_with_loan", ("EquityWithLoanValue",)),
    ("gross_position_value", ("GrossPositionValue",)),
)

# Currency is read from a monetary tag; descriptive tags carry an empty one.
_CURRENCY_TAGS: Tuple[str, ...] = ("NetLiquidation", "TotalCashValue", "BuyingPower")


def _tag_value(summary: Mapping[str, Any], tag: str) -> Optional[float]:
    row = summary.get(tag)
    raw = row.get("value") if isinstance(row, Mapping) else row
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _tag_currency(summary: Mapping[str, Any], tag: str) -> str:
    row = summary.get(tag)
    if not isinstance(row, Mapping):
        return ""
    return str(row.get("currency") or "").strip()


def position_entry_price(position: Mapping[str, Any]) -> float:
    """Per-unit entry price for a position row from ``IBKRClient.get_positions``.

    ``avgCost`` is reported per contract, i.e. already multiplied, so a futures
    position read through it is inflated by the contract multiplier. Callers
    that compare against quotes, stops or fills want ``avgPrice``; ``avgCost``
    remains the fallback for rows produced before it existed.
    """
    if not isinstance(position, Mapping):
        return 0.0
    for field in ("avgPrice", "avgCost"):
        raw = position.get(field)
        if raw in (None, ""):
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return 0.0


def flatten_account_summary(summary: Any) -> Dict[str, Any]:
    """Return the flat numeric fields the broker-account UI reads.

    Tags that IBKR did not report are omitted rather than zeroed, so the UI
    shows "--" for genuinely missing values instead of a misleading 0.
    """
    if not isinstance(summary, Mapping):
        return {}

    flat: Dict[str, Any] = {}
    for field, tags in _FLAT_FIELDS:
        for tag in tags:
            value = _tag_value(summary, tag)
            if value is not None:
                flat[field] = value
                break

    if not flat:
        # Currency belongs to an amount; on its own it tells the caller nothing.
        return flat

    for tag in _CURRENCY_TAGS:
        currency = _tag_currency(summary, tag)
        if currency:
            flat["currency"] = currency
            flat["account_currency"] = currency
            break

    return flat


__all__ = ["flatten_account_summary", "position_entry_price"]
