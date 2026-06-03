"""Shared parsing for heterogeneous exchange position rows."""

from __future__ import annotations

from typing import Any, Dict


def extract_signed_position_qty(item: Dict[str, Any]) -> float:
    """Return signed position qty; OKX ``pos`` must be checked before abs-only fields."""
    for key in (
        "pos",
        "size",
        "positionAmt",
        "posAmt",
        "currentQty",
        "volume",
        "contracts",
        "total",
        "current_qty",
        "availPos",
        "bal",
        "balance",
        "walletBalance",
        "equity",
        "cashBal",
        "qty",
        "availBal",
        "free",
        "available",
    ):
        try:
            value = float(item.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if abs(value) > 1e-10:
            return value
    return 0.0


def infer_position_side_from_row(item: Dict[str, Any]) -> str:
    """Map heterogeneous exchange position rows to ``long`` / ``short``."""
    psu = str(item.get("positionSide") or item.get("position_side") or "").strip().upper()
    if psu == "SHORT":
        return "short"
    if psu == "LONG":
        return "long"

    pos_side = str(item.get("posSide") or "").strip().lower()
    if pos_side in ("long", "short"):
        return pos_side

    # Net mode: posSide=net, sign lives on pos / availPos
    if pos_side == "net":
        signed = extract_signed_position_qty(item)
        if signed < -1e-10:
            return "short"
        if signed > 1e-10:
            return "long"

    hold = str(item.get("holdSide") or "").strip().lower()
    if hold in ("long", "short"):
        return hold

    try:
        idx = int(item.get("positionIdx") or 0)
        if idx == 1:
            return "long"
        if idx == 2:
            return "short"
    except (TypeError, ValueError):
        pass

    exch_side = str(item.get("side") or "").strip().lower()
    if exch_side in ("sell", "s", "short"):
        return "short"
    if exch_side in ("buy", "b", "long"):
        return "long"

    direction = str(item.get("direction") or "").strip().lower()
    if direction in ("sell", "short", "open_short"):
        return "short"
    if direction in ("buy", "long", "open_long"):
        return "long"

    signed = extract_signed_position_qty(item)
    if signed < -1e-10:
        return "short"
    if signed > 1e-10:
        return "long"
    return "long"


def position_base_qty_for_side(
    row: Dict[str, Any],
    side: str,
    *,
    contracts_to_base: float = 1.0,
) -> float:
    """Return base-asset position size when ``row`` matches ``side``, else 0."""
    want = str(side or "").strip().lower()
    if want not in ("long", "short"):
        return 0.0
    if infer_position_side_from_row(row) != want:
        return 0.0
    signed = extract_signed_position_qty(row)
    qty = abs(signed)
    if qty <= 0:
        return 0.0
    mult = float(contracts_to_base or 1.0)
    if mult <= 0:
        mult = 1.0
    return qty * mult
