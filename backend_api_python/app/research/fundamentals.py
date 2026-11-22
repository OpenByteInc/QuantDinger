"""Resolve reported financial metrics without mixing reporting periods."""

from __future__ import annotations

import copy
import math
from typing import Any, Mapping


METRIC_UNITS = {
    "profit_margin": "percent", "revenue_growth": "percent", "roe": "percent",
    "debt_to_equity": "multiple", "liabilities_to_equity": "multiple", "current_ratio": "multiple",
    "free_cash_flow": "currency",
}


def finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def normalize_fundamentals(fundamental: Mapping[str, Any] | None) -> dict[str, Any]:
    """Fill absent canonical fields from same-period statements, including cached payloads.

    Existing provider metrics retain their basis. A generic latest report may be
    interim or year-to-date; it must never be relabelled as a single quarter.
    """
    result = copy.deepcopy(dict(fundamental or {}))
    if not result:
        return result
    statements = result.get("financial_statements") or {}
    if not isinstance(statements, dict):
        return result
    if not any(statements.get(key) for key in ("latest_quarter", "ttm", "latest_annual", "latest_report")):
        sections = {
            key: copy.deepcopy(statements[key])
            for key in ("income_statement", "balance_sheet", "cash_flow")
            if isinstance(statements.get(key), Mapping) and statements[key]
        }
        if sections:
            statements["latest_report"] = {"period_type": "reported", **sections}
    metadata = result.setdefault("field_metadata", {})
    for period_key in ("latest_quarter", "ttm", "latest_report", "latest_annual"):
        period = statements.get(period_key) or {}
        if not period:
            continue
        derived = period.get("derived") or {}
        income = period.get("income_statement") or {}
        balance = period.get("balance_sheet") or {}
        cash_flow = period.get("cash_flow") or {}
        revenue = finite_number(income.get("total_revenue"))
        net_income = finite_number(income.get("net_income"))
        if finite_number(derived.get("profit_margin")) is None and revenue and revenue > 0 and net_income is not None:
            derived["profit_margin"] = net_income / revenue * 100
        assets = finite_number(balance.get("current_assets"))
        liabilities = finite_number(balance.get("current_liabilities"))
        if finite_number(derived.get("current_ratio")) is None and assets is not None and liabilities and liabilities > 0:
            derived["current_ratio"] = assets / liabilities
        if derived:
            period["derived"] = derived
        for key, unit in METRIC_UNITS.items():
            value = finite_number(cash_flow.get(key) if key == "free_cash_flow" else derived.get(key))
            if value is None or finite_number(result.get(key)) is not None:
                continue
            section = cash_flow if key == "free_cash_flow" else balance if key in {"current_ratio", "debt_to_equity"} else income
            result[key] = value
            metadata[key] = {
                "source": section.get("source") or period.get("source") or result.get("source"),
                "unit": unit,
                "currency": (section.get("currency") or period.get("currency") or result.get("financial_currency")) if unit == "currency" else None,
                "period_type": section.get("period_type") or period.get("period_type") or period_key,
                "period_end": section.get("period_end") or section.get("latest_date") or period.get("period_end"),
                "derived_from": f"financial.{period_key}.{'cash_flow' if key == 'free_cash_flow' else 'derived'}.{key}",
            }
    return result
