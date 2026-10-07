"""Record strategy close trades when exchange flat-closes a local leg.

Native TP/SL (algo) / liquidation / manual exchange closes often never create a
``pending_orders`` row, so the private-stream projector cannot attribute them.
Position sync already purges the ghost L3 row; this module also writes the
matching ``close_*`` trade so win-rate / trade history stay honest.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from app.services.live_trading.okx import OkxClient
from app.services.live_trading.records import (
    _fetch_position,
    apply_fill_to_local_position,
    normalize_strategy_symbol,
    record_trade,
)
from app.services.live_trading.symbols import to_okx_swap_inst_id
from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.strategy_runtime_logs import append_strategy_log
from app.utils.trade_close_reason import (
    EXCHANGE_ADL,
    EXCHANGE_FLAT_RECONCILE,
    EXCHANGE_LIQUIDATION,
    EXCHANGE_NATIVE_CLOSE,
)

logger = get_logger(__name__)

_EPS = 1e-12


def trade_side_net_qty(strategy_id: int, symbol: str, side: str) -> float:
    """Net base qty still open on the trade ledger for one side."""
    sid = int(strategy_id or 0)
    sym = normalize_strategy_symbol(symbol) or str(symbol or "").strip()
    side_l = str(side or "").strip().lower()
    if sid <= 0 or not sym or side_l not in ("long", "short"):
        return 0.0
    open_types = ("open_long", "add_long") if side_l == "long" else ("open_short", "add_short")
    close_types = ("close_long", "reduce_long") if side_l == "long" else ("close_short", "reduce_short")
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT type, amount
            FROM qd_strategy_trades
            WHERE strategy_id = %s
              AND UPPER(COALESCE(NULLIF(symbol_canonical, ''), symbol)) = UPPER(%s)
              AND type = ANY(%s)
            ORDER BY id ASC
            """,
            (sid, sym, list(open_types) + list(close_types)),
        )
        rows = cur.fetchall() or []
        cur.close()
    net = 0.0
    for row in rows:
        t = str(row.get("type") or "").strip().lower()
        try:
            qty = float(row.get("amount") or 0.0)
        except Exception:
            qty = 0.0
        if t in open_types:
            net += qty
        elif t in close_types:
            net -= qty
    return net if net > _EPS else 0.0


def _last_open_trade(strategy_id: int, symbol: str, side: str) -> Dict[str, Any]:
    sid = int(strategy_id or 0)
    sym = normalize_strategy_symbol(symbol) or str(symbol or "").strip()
    side_l = str(side or "").strip().lower()
    open_types = ("open_long", "add_long") if side_l == "long" else ("open_short", "add_short")
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id, price, amount, created_at, credential_id, inst_id, market_type
            FROM qd_strategy_trades
            WHERE strategy_id = %s
              AND UPPER(COALESCE(NULLIF(symbol_canonical, ''), symbol)) = UPPER(%s)
              AND type = ANY(%s)
            ORDER BY id DESC
            LIMIT 1
            """,
            (sid, sym, list(open_types)),
        )
        row = cur.fetchone() or {}
        cur.close()
    return dict(row) if isinstance(row, dict) else {}


def _already_recorded_fill(strategy_id: int, exchange_fill_id: str) -> bool:
    fill_id = str(exchange_fill_id or "").strip()
    if not fill_id:
        return False
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT 1 FROM qd_strategy_trades
            WHERE strategy_id = %s AND exchange_fill_id = %s
            LIMIT 1
            """,
            (int(strategy_id), fill_id),
        )
        exists = cur.fetchone() is not None
        cur.close()
    return bool(exists)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _parse_okx_ms(value: Any) -> Optional[datetime]:
    try:
        ms = int(float(value))
    except Exception:
        return None
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)


def resolve_okx_external_close(
    client: Any,
    *,
    symbol: str,
    side: str,
    entry_price: float,
    opened_after: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Best-effort close snapshot from OKX positions-history / fills-history."""
    if not isinstance(client, OkxClient):
        return None
    sym = normalize_strategy_symbol(symbol) or str(symbol or "").strip()
    side_l = str(side or "").strip().lower()
    if not sym or side_l not in ("long", "short"):
        return None
    inst_id = to_okx_swap_inst_id(sym)
    entry = _as_float(entry_price)
    opened_ts = None
    if isinstance(opened_after, datetime):
        opened_ts = opened_after if opened_after.tzinfo else opened_after.replace(tzinfo=timezone.utc)

    # Prefer positions-history: one row per fully closed cycle with open/close px.
    try:
        hist = client._signed_request(
            "GET",
            "/api/v5/account/positions-history",
            params={"instType": "SWAP", "instId": inst_id, "limit": "30"},
        )
        rows = (hist or {}).get("data") if isinstance(hist, dict) else []
    except Exception as exc:
        logger.debug("OKX positions-history lookup failed %s: %s", inst_id, exc)
        rows = []

    best: Optional[Dict[str, Any]] = None
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        pos_side = str(row.get("posSide") or "").strip().lower()
        direction = str(row.get("direction") or "").strip().lower()
        row_side = pos_side if pos_side in ("long", "short") else direction
        if row_side and row_side != side_l:
            continue
        open_px = _as_float(row.get("openAvgPx"))
        close_px = _as_float(row.get("closeAvgPx"))
        if close_px <= 0:
            continue
        if entry > 0 and open_px > 0:
            # Match the cycle that started at our local entry.
            if abs(open_px - entry) / max(entry, 1.0) > 0.0005 and abs(open_px - entry) > 1.0:
                continue
        closed_at = _parse_okx_ms(row.get("uTime") or row.get("cTime"))
        if opened_ts and closed_at and closed_at < opened_ts:
            continue
        close_type = str(row.get("type") or "").strip()
        reason = EXCHANGE_NATIVE_CLOSE
        # OKX type: 1 partial close, 2 full close, 3 liquidation, 4 partial liq, 5 ADL
        if close_type in {"3", "4"}:
            reason = EXCHANGE_LIQUIDATION
        elif close_type == "5":
            reason = EXCHANGE_ADL
        candidate = {
            "price": close_px,
            "fee": 0.0,
            "fee_ccy": "USDT",
            "exchange_fill_id": "",
            "closed_at": closed_at,
            "close_reason": reason,
            "source": "positions_history",
        }
        if best is None:
            best = candidate
            continue
        prev_closed = best.get("closed_at")
        if closed_at and (not prev_closed or closed_at > prev_closed):
            best = candidate

    try:
        fills = client._signed_request(
            "GET",
            "/api/v5/trade/fills-history",
            params={"instType": "SWAP", "instId": inst_id, "limit": "50"},
        )
        fill_rows = (fills or {}).get("data") if isinstance(fills, dict) else []
    except Exception:
        fill_rows = []

    close_side = "sell" if side_l == "long" else "buy"

    # Enrich fee / fill id from recent fills when positions-history matched.
    if best is not None:
        target_px = _as_float(best.get("price"))
        for fill in fill_rows or []:
            if not isinstance(fill, dict):
                continue
            if str(fill.get("side") or "").strip().lower() != close_side:
                continue
            pos_side = str(fill.get("posSide") or "").strip().lower()
            if pos_side and pos_side not in ("net", side_l):
                continue
            fill_px = _as_float(fill.get("fillPx"))
            if target_px > 0 and fill_px > 0 and abs(fill_px - target_px) / target_px > 0.0005:
                continue
            fill_ts = _parse_okx_ms(fill.get("ts"))
            if opened_ts and fill_ts and fill_ts < opened_ts:
                continue
            fee = abs(_as_float(fill.get("fee")))
            best["fee"] = fee
            best["fee_ccy"] = str(fill.get("feeCcy") or "USDT")
            best["exchange_fill_id"] = str(fill.get("tradeId") or fill.get("fillId") or "")
            if fill_ts:
                best["closed_at"] = fill_ts
            break
        return best

    # Race fallback: positions-history can lag a just-closed fill by a few seconds.
    # Prefer the newest reduce-side fill after our open instead of inventing entry==close.
    fill_best: Optional[Dict[str, Any]] = None
    for fill in fill_rows or []:
        if not isinstance(fill, dict):
            continue
        if str(fill.get("side") or "").strip().lower() != close_side:
            continue
        pos_side = str(fill.get("posSide") or "").strip().lower()
        if pos_side and pos_side not in ("net", side_l):
            continue
        fill_px = _as_float(fill.get("fillPx"))
        if fill_px <= 0:
            continue
        fill_ts = _parse_okx_ms(fill.get("ts"))
        if opened_ts and fill_ts and fill_ts < opened_ts:
            continue
        candidate = {
            "price": fill_px,
            "fee": abs(_as_float(fill.get("fee"))),
            "fee_ccy": str(fill.get("feeCcy") or "USDT"),
            "exchange_fill_id": str(fill.get("tradeId") or fill.get("fillId") or ""),
            "closed_at": fill_ts,
            "close_reason": EXCHANGE_NATIVE_CLOSE,
            "source": "fills_history",
        }
        if fill_best is None:
            fill_best = candidate
            continue
        prev_closed = fill_best.get("closed_at")
        if fill_ts and (not prev_closed or fill_ts > prev_closed):
            fill_best = candidate
    return fill_best


def _gross_close_profit(*, side: str, entry_price: float, close_price: float, amount: float) -> float:
    entry = _as_float(entry_price)
    close = _as_float(close_price)
    qty = _as_float(amount)
    if entry <= 0 or close <= 0 or qty <= 0:
        return 0.0
    if str(side).strip().lower() == "long":
        return (close - entry) * qty
    return (entry - close) * qty


def record_external_flat_close(
    *,
    strategy_id: int,
    symbol: str,
    side: str,
    amount: float,
    entry_price: float,
    close_price: float,
    commission: float = 0.0,
    commission_ccy: str = "USDT",
    close_reason: str = EXCHANGE_NATIVE_CLOSE,
    fill_source: str = "position_sync",
    exchange_fill_id: str = "",
    credential_id: int = 0,
    inst_id: str = "",
    market_type: str = "swap",
    created_at: Optional[datetime] = None,
    clear_local_position: bool = True,
) -> int:
    """Persist one external close trade; optionally clear the local L3 leg."""
    sid = int(strategy_id or 0)
    sym = normalize_strategy_symbol(symbol) or str(symbol or "").strip()
    side_l = str(side or "").strip().lower()
    qty = _as_float(amount)
    px = _as_float(close_price)
    if sid <= 0 or not sym or side_l not in ("long", "short") or qty <= _EPS or px <= 0:
        return 0
    if exchange_fill_id and _already_recorded_fill(sid, exchange_fill_id):
        return 0

    trade_type = "close_long" if side_l == "long" else "close_short"
    reason = str(close_reason or EXCHANGE_FLAT_RECONCILE).strip() or EXCHANGE_FLAT_RECONCILE
    entry = _as_float(entry_price)
    profit = _gross_close_profit(side=side_l, entry_price=entry, close_price=px, amount=qty)
    fee = abs(_as_float(commission))

    trade_id = record_trade(
        strategy_id=sid,
        symbol=sym,
        trade_type=trade_type,
        price=px,
        amount=qty,
        commission=fee,
        commission_ccy=str(commission_ccy or "USDT"),
        commission_quote=fee if str(commission_ccy or "USDT").upper() in ("", "USDT", "USD") else None,
        profit=profit,
        close_reason=reason,
        matched_entry_price=entry if entry > 0 else None,
        fill_source=str(fill_source or "position_sync"),
        exchange_fill_id=str(exchange_fill_id or ""),
        credential_id=int(credential_id or 0),
        inst_id=str(inst_id or ""),
        market_type=str(market_type or "swap"),
        fee_status="actual" if fee > 0 else "pending",
        fee_source="rest" if fee > 0 else "",
        created_at=created_at,
    )
    if trade_id > 0 and clear_local_position:
        local = _fetch_position(sid, sym, side_l)
        local_size = _as_float(local.get("size"))
        if local_size > _EPS:
            apply_fill_to_local_position(
                strategy_id=sid,
                symbol=sym,
                signal_type=trade_type,
                filled=min(local_size, qty),
                avg_price=px,
            )
    if trade_id > 0:
        try:
            append_strategy_log(
                sid,
                "trade",
                (
                    f"Trade reconciled: {trade_type} {sym} filled={qty:.8f} @ {px:.8f}, "
                    f"profit={profit:.4f}, reason={reason} (source={fill_source})"
                ),
            )
        except Exception:
            pass
    return int(trade_id or 0)


def reconcile_external_flat_closes(
    *,
    strategy_id: int,
    symbol: str,
    side: str,
    client: Any = None,
    fallback_price: float = 0.0,
    credential_id: int = 0,
    inst_id: str = "",
    market_type: str = "swap",
    clear_local_position: bool = True,
) -> int:
    """
    If the trade ledger still shows open qty for a leg that is flat on the
    exchange, write the missing close and optionally clear local size.
    """
    sid = int(strategy_id or 0)
    sym = normalize_strategy_symbol(symbol) or str(symbol or "").strip()
    side_l = str(side or "").strip().lower()
    residual = trade_side_net_qty(sid, sym, side_l)
    if residual <= _EPS:
        local = _fetch_position(sid, sym, side_l)
        local_size = _as_float(local.get("size"))
        if local_size <= _EPS:
            return 0
        residual = local_size
        entry = _as_float(local.get("entry_price"))
        cred = int(local.get("credential_id") or credential_id or 0)
        iid = str(local.get("inst_id") or inst_id or "")
        mt = str(local.get("market_type") or market_type or "swap")
        opened_after = None
    else:
        last_open = _last_open_trade(sid, sym, side_l)
        entry = _as_float(last_open.get("price"))
        if entry <= 0:
            local = _fetch_position(sid, sym, side_l)
            entry = _as_float(local.get("entry_price"))
        cred = int(last_open.get("credential_id") or credential_id or 0)
        iid = str(last_open.get("inst_id") or inst_id or "")
        mt = str(last_open.get("market_type") or market_type or "swap")
        opened_after = last_open.get("created_at")
        if isinstance(opened_after, datetime) and opened_after.tzinfo is None:
            opened_after = opened_after.replace(tzinfo=timezone.utc)

    snapshot = resolve_okx_external_close(
        client,
        symbol=sym,
        side=side_l,
        entry_price=entry,
        opened_after=opened_after if isinstance(opened_after, datetime) else None,
    ) or {}
    close_px = _as_float(snapshot.get("price"))
    if close_px <= 0:
        close_px = _as_float(fallback_price)
    # Never invent close_price == entry_price: that creates fake 0-pnl rows with
    # pending fees and confuses users. Skip and let a later sync retry.
    if close_px <= 0:
        logger.warning(
            "[ExternalFlatClose] strategy=%s %s %s residual=%.8f but no close price available; deferring",
            sid,
            sym,
            side_l,
            residual,
        )
        return 0
    if entry > 0 and abs(close_px - entry) <= 1e-9 and not snapshot:
        logger.warning(
            "[ExternalFlatClose] strategy=%s %s %s refusing entry-price fallback without exchange snapshot",
            sid,
            sym,
            side_l,
        )
        return 0

    reason = str(snapshot.get("close_reason") or EXCHANGE_NATIVE_CLOSE)
    if not snapshot:
        reason = EXCHANGE_FLAT_RECONCILE

    return record_external_flat_close(
        strategy_id=sid,
        symbol=sym,
        side=side_l,
        amount=residual,
        entry_price=entry,
        close_price=close_px,
        commission=_as_float(snapshot.get("fee")),
        commission_ccy=str(snapshot.get("fee_ccy") or "USDT"),
        close_reason=reason,
        fill_source="position_sync",
        exchange_fill_id=str(snapshot.get("exchange_fill_id") or ""),
        credential_id=cred,
        inst_id=iid,
        market_type=mt,
        created_at=snapshot.get("closed_at") if isinstance(snapshot.get("closed_at"), datetime) else None,
        clear_local_position=clear_local_position,
    )
