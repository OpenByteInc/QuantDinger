"""
HTX USDT-M swap API V5 helpers.

Maps V5 responses to the legacy linear-swap shapes consumed by quick_trade / pending_order_worker.
Ref: https://www.htx.com/zh-cn/opend/newApiPages/?id=5521
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set


def v5_ok(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    if str(raw.get("status") or "").lower() == "ok":
        return True
    code = raw.get("code")
    if code is None:
        return False
    try:
        return int(code) == 200
    except (TypeError, ValueError):
        return False


def v5_data(raw: Dict[str, Any]) -> Any:
    return raw.get("data")


def v5_err_message(raw: Dict[str, Any]) -> str:
    return str(
        raw.get("err_msg")
        or raw.get("msg")
        or raw.get("message")
        or raw.get("err_code")
        or raw
    )


def is_single_asset_mode_unavailable(msg: str) -> bool:
    """HTX V5 may reject private calls while account is still on single-asset collateral."""
    text = str(msg or "").lower()
    return "single-asset" in text and ("unavailable" in text or "not available" in text or "暂停" in text)


def is_multi_asset_v1_unavailable(msg: str) -> bool:
    """V1 linear-swap order APIs reject accounts on multi-asset (联合保证金) collateral."""
    text = str(msg or "").lower()
    return "multi-assets" in text or "multi assets" in text or "联合保证金" in text


def _balance_row_from_item(item: Dict[str, Any]) -> Dict[str, Any]:
    ccy = str(item.get("currency") or item.get("margin_asset") or "USDT").upper()
    avail = float(
        item.get("available")
        or item.get("withdraw_available")
        or item.get("margin_available")
        or item.get("available_margin")
        or 0
    )
    equity = float(
        item.get("equity")
        or item.get("margin_balance")
        or item.get("margin_static")
        or avail
        or 0
    )
    return {
        "margin_asset": ccy,
        "margin_available": avail,
        "withdraw_available": float(item.get("withdraw_available") or avail),
        "margin_balance": equity,
        "margin_static": equity,
        "margin_mode": "cross",
    }


def normalize_balance(raw: Dict[str, Any]) -> Dict[str, Any]:
    """V5 balance -> legacy swap_cross_account_info-like list in data."""
    data = v5_data(raw)
    rows: List[Dict[str, Any]] = []

    # Some V5 deployments return a list of per-asset rows directly under ``data``.
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                rows.append(_balance_row_from_item(item))
        return {"status": "ok", "code": 200, "data": rows}

    if not isinstance(data, dict):
        return {"status": "ok", "data": []}

    details = data.get("details") or []
    if isinstance(details, list):
        for item in details:
            if isinstance(item, dict):
                rows.append(_balance_row_from_item(item))
    if not rows:
        avail = float(
            data.get("available_margin")
            or data.get("available")
            or data.get("withdraw_available")
            or data.get("equity")
            or 0
        )
        equity = float(data.get("equity") or data.get("margin_balance") or avail)
        if equity > 0 or avail > 0:
            rows.append(_balance_row_from_item({
                "currency": "USDT",
                "available": avail,
                "equity": equity,
                "withdraw_available": avail,
            }))
    return {"status": "ok", "code": 200, "data": rows}


def normalize_positions(raw: Dict[str, Any]) -> Dict[str, Any]:
    """V5 positions -> legacy swap_position_info list."""
    data = v5_data(raw)
    items: List[Any] = []
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in ("positions", "list", "data"):
            nested = data.get(key)
            if isinstance(nested, list):
                items = nested
                break
        if not items and data.get("contract_code"):
            items = [data]
    rows: List[Dict[str, Any]] = []
    for p in items:
        if not isinstance(p, dict):
            continue
        row = dict(p)
        vol = p.get("volume") or p.get("qty") or p.get("position_qty") or p.get("amount") or 0
        row.setdefault("volume", vol)
        row.setdefault("contract_code", p.get("contract_code") or p.get("symbol") or "")
        row.setdefault("direction", p.get("direction") or p.get("side") or "")
        rows.append(row)
    return {"status": "ok", "code": 200, "data": rows}


def normalize_order_place(raw: Dict[str, Any]) -> Dict[str, Any]:
    data = v5_data(raw)
    if not isinstance(data, dict):
        data = raw if isinstance(raw, dict) else {}
    oid = str(
        data.get("order_id_str")
        or data.get("order_id")
        or data.get("id")
        or ""
    )
    return {"status": "ok", "code": 200, "data": {"order_id_str": oid, "order_id": oid}}


def normalize_order_detail(raw: Dict[str, Any]) -> Dict[str, Any]:
    data = v5_data(raw)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    if isinstance(data, dict):
        return data
    return {}


def map_v1_order_price_type_to_v5_type(order_price_type: str, *, has_limit_price: bool = False) -> str:
    """
    Map legacy V1 ``order_price_type`` to V5 POST /v5/trade/order field ``type``.

    V5 ``type`` is the order kind (``market``, ``limit``, ``ioc``, ``fok``, ``post_only``),
    **not** the old V1 price-style tokens like ``opponent`` / ``optimal_5`` (those caused
    ``Illegal parameter type`` in production).
    """
    if has_limit_price:
        return "limit"
    opt = str(order_price_type or "").strip().lower()
    if opt in ("limit",):
        return "limit"
    if opt in ("ioc",):
        return "ioc"
    if opt in ("fok",):
        return "fok"
    if opt in ("post_only", "poc", "maker"):
        return "post_only"
    # opponent / optimal_* / lightning / market / empty -> market order
    return "market"


def map_v1_order_price_type_to_v5_price_style(order_price_type: str) -> Optional[str]:
    """Optional V1 price-style token; some V5 builds accept ``order_price_type`` alongside ``type``."""
    opt = str(order_price_type or "").strip().lower()
    if opt in ("opponent", "optimal_5", "optimal_10", "optimal_20", "lightning", "ioc", "fok", "post_only"):
        return opt
    return None


def parse_position_mode_hedged(data: Any) -> Optional[bool]:
    """Return True for dual_side (hedge), False for single_side (one-way), None if unknown."""

    def _one(item: Dict[str, Any]) -> Optional[bool]:
        mode = str(item.get("position_mode") or item.get("positionMode") or "").strip().lower()
        if mode in ("dual_side", "dual", "hedge", "hedged"):
            return True
        if mode in ("single_side", "single", "oneway", "one_way", "one-way"):
            return False
        return None

    if isinstance(data, dict):
        return _one(data)
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                parsed = _one(item)
                if parsed is not None:
                    return parsed
    return None


def position_mode_label(hedge_mode: bool) -> str:
    return "dual_side" if hedge_mode else "single_side"


def is_hedge_order_rejected_on_oneway(msg: str) -> bool:
    """HTX err 1499: account is dual_side but order used one-way parameters."""
    text = str(msg or "").lower()
    return "hedge mode currently" in text or (
        "1499" in text and "one-way" in text
    )


def is_oneway_order_rejected_on_hedge(msg: str) -> bool:
    """HTX err 1500: account is single_side but order used hedge parameters."""
    text = str(msg or "").lower()
    return "one-way mode currently" in text or (
        "1500" in text and "hedge" in text
    )


def build_v1_cross_order_body(
    *,
    contract_code: str,
    volume: int,
    side: str,
    lever_rate: int,
    order_price_type: str,
    price: Optional[float] = None,
    client_order_id: Optional[int] = None,
    channel_code: str = "",
    reduce_only: bool = False,
    hedge_mode: bool = False,
    offset_override: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build POST /linear-swap-api/v1/swap_cross_order body (legacy, still widely supported).

    Uses ``direction`` + ``order_price_type``; does not send ``position_mode``.
    """
    sd = str(side or "").strip().lower()
    if sd not in ("buy", "sell"):
        raise ValueError(f"Invalid HTX side: {side}")
    px = float(price) if price is not None else 0.0
    opt = str(order_price_type or "").strip().lower()
    if px > 0:
        opt = "limit"
    body: Dict[str, Any] = {
        "contract_code": contract_code,
        "volume": int(volume),
        "direction": sd,
        "lever_rate": int(lever_rate),
        "order_price_type": opt or "opponent",
    }
    if offset_override is not None:
        off = str(offset_override).strip().lower()
        if off:
            body["offset"] = off
    elif hedge_mode:
        body["offset"] = "close" if reduce_only else "open"
    else:
        body["offset"] = "both"
        if reduce_only:
            body["reduce_only"] = 1
    if px > 0:
        body["price"] = px
    if client_order_id is not None:
        body["client_order_id"] = int(client_order_id)
    if channel_code:
        body["channel_code"] = channel_code
    return body


def resolve_v5_position_side(
    *, side: str, reduce_only: bool, hedge_mode: bool, pos_side: str = ""
) -> str:
    """
    HTX V5 multi-asset (联合保证金) ``position_side``: ``long`` / ``short`` / ``both``.

    - single_side (one-way) account: always ``both``
    - dual_side (hedge) account:
        * caller-supplied ``pos_side`` wins when it's ``long``/``short``
        * else infer from ``side`` + ``reduce_only``:
            open_long (buy)  -> long
            open_short (sell)-> short
            close_long (sell, reduce_only)  -> long
            close_short (buy, reduce_only)  -> short
    """
    if not hedge_mode:
        return "both"
    ps = str(pos_side or "").strip().lower()
    if ps in ("long", "short"):
        return ps
    sd = str(side or "").strip().lower()
    if reduce_only:
        return "long" if sd == "sell" else "short"
    return "long" if sd == "buy" else "short"


def build_swap_order_body(
    *,
    contract_code: str,
    volume: int,
    side: str,
    order_price_type: str,
    price: Optional[float] = None,
    client_order_id: Optional[int] = None,
    channel_code: str = "",
    margin_mode: str = "cross",
    reduce_only: bool = False,
    position_side: str = "both",
    lever_rate: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Build POST /v5/trade/order body for HTX **multi-asset collateral (联合保证金 / asset_mode=1)**.

    Per the V5 spec (ccxt PR #28088, ref id=8cb89359-77b5-11ed-9966-19588768fe7):

    - ``contract_code``, ``volume``, ``side``, ``type``, ``margin_mode``, ``position_side`` are core
    - ``position_side``: ``long`` / ``short`` (dual_side) or ``both`` (single_side)
    - ``reduce_only``: 1 when closing
    - **Do NOT send** ``offset`` / ``trade_type`` / ``order_price_type`` /
      ``position_mode`` / ``lever_rate`` (lever is set via ``/v5/position/lever``).
    """
    sd = str(side or "").strip().lower()
    if sd not in ("buy", "sell"):
        raise ValueError(f"Invalid HTX V5 side: {side}")
    ps = str(position_side or "").strip().lower()
    if ps not in ("long", "short", "both"):
        ps = "both"
    px = float(price) if price is not None else 0.0
    v5_type = map_v1_order_price_type_to_v5_type(order_price_type, has_limit_price=px > 0)
    body: Dict[str, Any] = {
        "contract_code": contract_code,
        "volume": int(volume),
        "side": sd,
        "type": v5_type,
        "margin_mode": margin_mode,
        "position_side": ps,
    }
    if reduce_only:
        body["reduce_only"] = 1
    if px > 0:
        body["price"] = px
    if client_order_id is not None:
        body["client_order_id"] = int(client_order_id)
    if channel_code:
        body["channel_code"] = channel_code
    return body


def swap_order_body_variants(
    *,
    contract_code: str,
    volume: int,
    side: str,
    order_price_type: str,
    price: Optional[float] = None,
    client_order_id: Optional[int] = None,
    channel_code: str = "",
    margin_mode: str = "cross",
    reduce_only: bool = False,
    preferred_hedge: bool = False,
    pos_side: str = "",
    hedge_only: bool = False,
    oneway_only: bool = False,
    lever_rate: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Candidate bodies for V5 multi-asset order: dual_side (long/short) vs single_side (both)."""
    _ = lever_rate  # lever is set via /v5/position/lever, not on the order body
    common = dict(
        contract_code=contract_code,
        volume=volume,
        side=side,
        order_price_type=order_price_type,
        price=price,
        client_order_id=client_order_id,
        channel_code=channel_code,
        margin_mode=margin_mode,
        reduce_only=reduce_only,
    )
    bodies: List[Dict[str, Any]] = []
    seen: Set[str] = set()

    def _add(body: Dict[str, Any]) -> None:
        key = str(sorted(body.items()))
        if key not in seen:
            seen.add(key)
            bodies.append(body)

    def _hedge_body() -> None:
        ps = resolve_v5_position_side(
            side=side, reduce_only=reduce_only, hedge_mode=True, pos_side=pos_side
        )
        _add(build_swap_order_body(**common, position_side=ps))

    def _oneway_body() -> None:
        _add(build_swap_order_body(**common, position_side="both"))

    if hedge_only:
        _hedge_body()
        return bodies
    if oneway_only:
        _oneway_body()
        return bodies

    if preferred_hedge:
        _hedge_body()
        _oneway_body()
    else:
        _oneway_body()
        _hedge_body()
    return bodies


def v1_order_body_variants(
    *,
    contract_code: str,
    volume: int,
    side: str,
    lever_rate: int,
    order_price_type: str,
    price: Optional[float] = None,
    client_order_id: Optional[int] = None,
    channel_code: str = "",
    reduce_only: bool = False,
    preferred_hedge: bool = False,
) -> List[Dict[str, Any]]:
    """Candidate bodies for POST /linear-swap-api/v1/swap_cross_order."""
    common = dict(
        contract_code=contract_code,
        volume=volume,
        side=side,
        lever_rate=lever_rate,
        order_price_type=order_price_type,
        price=price,
        client_order_id=client_order_id,
        channel_code=channel_code,
        reduce_only=reduce_only,
    )
    bodies: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for hedge in ([bool(preferred_hedge), not bool(preferred_hedge)]):
        for body in (
            build_v1_cross_order_body(**common, hedge_mode=hedge),
            build_v1_cross_order_body(**common, hedge_mode=hedge, offset_override=""),
        ):
            key = str(sorted(body.items()))
            if key not in seen:
                seen.add(key)
                bodies.append(body)
    return bodies


def build_cancel_body(
    *,
    contract_code: str,
    order_id: str = "",
    client_order_id: str = "",
) -> Dict[str, Any]:
    body: Dict[str, Any] = {"contract_code": contract_code}
    if order_id:
        body["order_id"] = str(order_id)
    elif client_order_id:
        body["client_order_id"] = str(client_order_id)
    return body


def build_order_query_params(
    *,
    contract_code: str,
    order_id: str = "",
    client_order_id: str = "",
) -> Dict[str, Any]:
    params: Dict[str, Any] = {"contract_code": contract_code}
    if order_id:
        params["order_id"] = str(order_id)
    elif client_order_id:
        params["client_order_id"] = str(client_order_id)
    return params


def build_lever_body(*, contract_code: str, lever_rate: int, margin_mode: str = "cross") -> Dict[str, Any]:
    return {
        "contract_code": contract_code,
        "lever_rate": int(lever_rate),
        "margin_mode": margin_mode,
    }
