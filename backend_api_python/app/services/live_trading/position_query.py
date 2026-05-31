"""
Resolve close/reduce order quantity from local DB and live exchange positions.

When the DB snapshot lags (e.g. open fill not written yet), fall back to the
exchange as source of truth instead of rejecting with amount=0.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.services.live_trading.records import (
    fetch_position_size_for_side,
    normalize_strategy_symbol,
)
from app.services.live_trading.symbols import (
    to_bitget_um_symbol,
    to_gate_currency_pair,
    to_okx_swap_inst_id,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


def symbols_equivalent(a: str, b: str) -> bool:
    na = normalize_strategy_symbol(a)
    nb = normalize_strategy_symbol(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return na.replace("/", "") == nb.replace("/", "")


def query_exchange_position_size(
    *,
    client: Any,
    symbol: str,
    pos_side: str,
    market_type: str,
    exchange_config: Optional[Dict[str, Any]] = None,
) -> float:
    """
    Best-effort base-asset position size on the connected exchange for ``pos_side``.
    Returns 0.0 when unknown or flat.
    """
    if client is None:
        return 0.0
    side = str(pos_side or "").strip().lower()
    if side not in ("long", "short"):
        return 0.0
    mt = str(market_type or "swap").strip().lower()
    cfg = exchange_config if isinstance(exchange_config, dict) else {}
    sym = str(symbol or "").strip()

    # Spot long close = sell base balance.
    if mt == "spot":
        if side != "long":
            return 0.0
        try:
            from app.services.live_trading.spot_sizing import get_spot_free_base_balance

            return max(0.0, float(get_spot_free_base_balance(client, symbol=sym) or 0.0))
        except Exception as e:
            logger.debug("spot free balance query failed symbol=%s: %s", sym, e)
            return 0.0

    try:
        from app.services.live_trading.binance import BinanceFuturesClient
        from app.services.live_trading.okx import OkxClient
        from app.services.live_trading.bybit import BybitClient
        from app.services.live_trading.bitget import BitgetMixClient
        from app.services.live_trading.gate import GateUsdtFuturesClient
        from app.services.live_trading.kucoin import KucoinFuturesClient
        from app.services.live_trading.kraken_futures import KrakenFuturesClient
        from app.services.live_trading.deepcoin import DeepcoinClient
        from app.services.live_trading.htx import HtxClient
    except Exception:
        return 0.0

    if isinstance(client, OkxClient):
        inst_id = to_okx_swap_inst_id(sym)
        pos_resp = client.get_positions(inst_id=inst_id) or {}
        pos_data = (pos_resp.get("data") or []) if isinstance(pos_resp, dict) else []
        for pos in pos_data:
            if not isinstance(pos, dict):
                continue
            if str(pos.get("instId") or "").strip() != inst_id:
                continue
            if str(pos.get("posSide") or "").strip().lower() != side:
                continue
            pos_qty = abs(float(pos.get("pos") or 0.0))
            ct_val = float(pos.get("ctVal") or 0.0)
            if ct_val > 0:
                return pos_qty * ct_val
            return pos_qty

    if isinstance(client, BinanceFuturesClient):
        norm_sym = sym.replace("/", "").replace("-", "").upper()
        pos_list = client.get_positions() or []
        if isinstance(pos_list, dict) and "raw" in pos_list:
            pos_list = pos_list["raw"]
        if not isinstance(pos_list, list):
            return 0.0
        for pos in pos_list:
            if not isinstance(pos, dict):
                continue
            if str(pos.get("symbol") or "").upper() != norm_sym:
                continue
            p_side = str(pos.get("positionSide") or "").strip().lower()
            if p_side == side or (p_side == "both" and side in ("long", "short")):
                amt = abs(float(pos.get("positionAmt") or 0.0))
                if amt > 0:
                    return amt
        return 0.0

    if isinstance(client, BybitClient):
        pos_resp = client.get_positions(symbol=sym) or {}
        pos_list = (pos_resp.get("result") or {}).get("list") or [] if isinstance(pos_resp, dict) else []
        want = sym.replace("/", "").replace("-", "").upper()
        for pos in pos_list:
            if not isinstance(pos, dict):
                continue
            if str(pos.get("symbol") or "").strip().upper() != want:
                continue
            p_side = str(pos.get("side") or "").strip().lower()
            if (p_side == "buy" and side == "long") or (p_side == "sell" and side == "short"):
                sz = abs(float(pos.get("size") or 0.0))
                if sz > 0:
                    return sz
        return 0.0

    if isinstance(client, BitgetMixClient):
        product_type = str(cfg.get("product_type") or cfg.get("productType") or "USDT-FUTURES")
        pos_resp = client.get_positions(product_type=product_type, symbol=sym) or {}
        pos_list = (pos_resp.get("data") or []) if isinstance(pos_resp, dict) else []
        want = to_bitget_um_symbol(sym).upper()
        for pos in pos_list:
            if not isinstance(pos, dict):
                continue
            if to_bitget_um_symbol(str(pos.get("symbol") or "")).upper() != want:
                continue
            if str(pos.get("holdSide") or "").strip().lower() != side:
                continue
            sz = abs(float(pos.get("total") or pos.get("available") or 0.0))
            if sz > 0:
                return sz
        return 0.0

    if isinstance(client, GateUsdtFuturesClient):
        resp = client.get_positions()
        items = resp if isinstance(resp, list) else []
        want_contract = to_gate_currency_pair(sym)
        for p in items:
            if not isinstance(p, dict):
                continue
            contract = str(p.get("contract") or "").strip()
            if contract != want_contract and not symbols_equivalent(contract.replace("_", "/"), sym):
                continue
            try:
                sz_ct = float(p.get("size") or 0.0)
            except Exception:
                sz_ct = 0.0
            if abs(sz_ct) <= 0:
                continue
            p_side = "long" if sz_ct > 0 else "short"
            if p_side != side:
                continue
            qty_base = abs(sz_ct)
            try:
                meta = client.get_contract(contract=contract) or {}
                qm = float(meta.get("quanto_multiplier") or meta.get("contract_size") or 0.0)
                if qm > 0:
                    qty_base = qty_base * qm
            except Exception:
                pass
            return float(qty_base)

    if isinstance(client, KucoinFuturesClient):
        resp = client.get_positions() or {}
        data = (resp.get("data") if isinstance(resp, dict) else None) or []
        for p in data:
            if not isinstance(p, dict):
                continue
            p_sym = str(p.get("symbol") or "").strip()
            if not symbols_equivalent(p_sym, sym):
                continue
            try:
                qty_ct = float(p.get("currentQty") or p.get("quantity") or 0.0)
            except Exception:
                qty_ct = 0.0
            p_side = "long" if qty_ct > 0 else "short"
            if p_side != side:
                continue
            qty_base = abs(qty_ct)
            try:
                meta = client.get_contract(symbol=p_sym) or {}
                mult = float(meta.get("multiplier") or meta.get("lotSize") or 0.0)
                if mult > 0:
                    qty_base = qty_base * mult
            except Exception:
                pass
            if qty_base > 0:
                return float(qty_base)
        return 0.0

    if isinstance(client, KrakenFuturesClient):
        resp = client.get_open_positions() or {}
        positions = (
            (resp.get("openPositions") if isinstance(resp, dict) else None)
            or (resp.get("open_positions") if isinstance(resp, dict) else None)
            or []
        )
        for p in positions:
            if not isinstance(p, dict):
                continue
            p_sym = str(p.get("symbol") or p.get("instrument") or "").strip()
            if sym and p_sym and not symbols_equivalent(p_sym, sym):
                continue
            try:
                sz = float(p.get("size") or p.get("positionSize") or 0.0)
            except Exception:
                sz = 0.0
            p_side = "long" if sz > 0 else "short"
            if p_side != side:
                continue
            if abs(sz) > 0:
                return abs(sz)
        return 0.0

    if isinstance(client, DeepcoinClient):
        resp = client.get_positions(symbol=sym) or {}
        data = _extract_position_rows(resp)
        for p in data:
            if not isinstance(p, dict):
                continue
            p_side = str(p.get("posSide") or p.get("holdSide") or "").strip().lower()
            if p_side and p_side != side:
                continue
            inst = str(p.get("instId") or p.get("symbol") or "")
            if sym and inst and not symbols_equivalent(inst, sym):
                continue
            sz = abs(float(p.get("pos") or p.get("availPos") or p.get("size") or 0.0))
            if sz > 0:
                return sz
        return 0.0

    if isinstance(client, HtxClient):
        resp = client.get_positions(symbol=sym) or {}
        data = (resp.get("data") or []) if isinstance(resp, dict) else []
        for p in data:
            if not isinstance(p, dict):
                continue
            vol = abs(float(p.get("volume") or p.get("bal") or p.get("qty") or 0.0))
            if vol <= 0:
                continue
            direction = str(p.get("direction") or p.get("side") or "").strip().lower()
            if direction in ("buy", "long"):
                p_side = "long"
            elif direction in ("sell", "short"):
                p_side = "short"
            else:
                p_side = "long"
            if p_side != side:
                continue
            contract = str(p.get("contract_code") or p.get("symbol") or "")
            if sym and contract and not symbols_equivalent(contract.replace("-", "/"), sym):
                continue
            return vol
        return 0.0

    # MT5 / IBKR / Alpaca (desktop brokers)
    try:
        positions = client.get_positions() if hasattr(client, "get_positions") else []
    except Exception:
        positions = []
    if isinstance(positions, list):
        for p in positions:
            if not isinstance(p, dict):
                continue
            p_sym = str(
                p.get("symbol")
                or p.get("ib_symbol")
                or p.get("contract")
                or ""
            ).strip()
            if sym and p_sym and not symbols_equivalent(p_sym, sym):
                continue
            qty = _broker_position_qty_and_side(p)
            if qty is None:
                continue
            p_side, sz = qty
            if p_side == side and sz > 0:
                return sz
    return 0.0


def _extract_position_rows(resp: Any) -> List[Any]:
    if isinstance(resp, list):
        return resp
    if not isinstance(resp, dict):
        return []
    for key in ("data", "list", "positions", "result"):
        chunk = resp.get(key)
        if isinstance(chunk, list):
            return chunk
        if isinstance(chunk, dict):
            nested = chunk.get("list") or chunk.get("data")
            if isinstance(nested, list):
                return nested
    return []


def _broker_position_qty_and_side(p: Dict[str, Any]) -> Optional[Tuple[str, float]]:
    """Normalize MT5 / IBKR / Alpaca position dicts to (side, size)."""
    try:
        qty = float(p.get("quantity") or p.get("volume") or p.get("size") or 0.0)
    except Exception:
        qty = 0.0
    if abs(qty) <= 0:
        return None
    side_str = str(p.get("side") or p.get("type") or "").strip().lower()
    if side_str in ("buy", "long") or qty > 0:
        return "long", abs(qty)
    if side_str in ("sell", "short") or qty < 0:
        return "short", abs(qty)
    return ("long" if qty > 0 else "short"), abs(qty)


def resolve_reduce_only_quantity(
    *,
    strategy_id: int,
    symbol: str,
    pos_side: str,
    requested_amount: float,
    client: Any,
    market_type: str,
    exchange_config: Optional[Dict[str, Any]] = None,
) -> Tuple[float, Dict[str, Any]]:
    """
    Choose a safe close/reduce base quantity.

    Priority:
    1. Local DB size (cap requested amount).
    2. Exchange size when DB missing or requested amount is zero.
    3. Min(requested, exchange) when both exist.
    """
    meta: Dict[str, Any] = {}
    amount = max(0.0, float(requested_amount or 0.0))
    meta["requested"] = amount

    db_size = fetch_position_size_for_side(int(strategy_id), symbol, pos_side)
    meta["db_size"] = db_size
    if db_size > 0:
        if amount <= 0:
            amount = db_size
            meta["filled_from"] = "db"
        elif amount > db_size:
            meta["capped_by"] = "db"
            amount = db_size
    else:
        meta["db_missing"] = True

    exch_size = query_exchange_position_size(
        client=client,
        symbol=symbol,
        pos_side=pos_side,
        market_type=market_type,
        exchange_config=exchange_config,
    )
    meta["exchange_size"] = exch_size

    if exch_size > 0:
        if amount <= 0:
            amount = exch_size
            meta["filled_from"] = "exchange"
            logger.info(
                "[RiskControl] Close %s %s: no DB size; using exchange position=%s",
                symbol,
                pos_side,
                exch_size,
            )
        elif amount > exch_size:
            logger.info(
                "[RiskControl] Close %s %s: capping amount %s -> exchange position %s",
                symbol,
                pos_side,
                amount,
                exch_size,
            )
            meta["capped_by"] = "exchange"
            amount = exch_size
    elif amount <= 0:
        logger.warning(
            "[RiskControl] Close %s %s: no position in DB or on exchange (amount stays 0)",
            symbol,
            pos_side,
        )
        meta["filled_from"] = "none"

    meta["resolved"] = amount
    return amount, meta
