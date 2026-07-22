"""
Pending order worker.

This worker polls `pending_orders` periodically and dispatches orders based on `execution_mode`:
- signal: send notifications (no real trading).
- live: dispatch normalized live orders through exchange and broker clients.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from app.services.signal_notifier import SignalNotifier
from app.services.exchange_execution import load_strategy_configs, resolve_exchange_config, safe_exchange_config_for_log
from app.services.live_trading.execution import place_order_from_signal
from app.services.live_trading.factory import create_client
from app.services.live_trading.records import (
    ensure_position_ledger_schema,
    fetch_allocated_position_size,
    fetch_position_size_for_side,
    normalize_strategy_symbol,
    strategy_allowed_symbols,
)
from app.services.live_trading.strategy_position_sync import (
    strategy_uses_fill_ledger,
)
from app.services.live_trading.account_positions import (
    account_legs_from_exchange_maps,
    sync_account_positions,
)
from app.services.live_trading.adapters import LiveOrderPhaseAdapter
from app.services.live_trading.contracts import OrderIntent
from app.services.live_trading.executors import (
    LimitThenMarketExecutor,
    MarketOrderExecutor,
    RestingLimitExecutor,
)
from app.services.live_trading.leg_context import (
    credential_id_from_exchange_config,
)
from app.services.live_trading.position_query import resolve_reduce_only_quantity
from app.utils.pnl import calc_notional_value
from app.services.live_trading.base import LiveTradingError, is_file_descriptor_exhausted
from app.services.pending_orders.fill_records import (
    persist_strategy_fill,
    trade_close_reason_from_payload,
)
from app.services.pending_orders.fee_reconciliation import (
    backfill_zero_commission_trades,
    commission_snapshot as _commission_snapshot,
    fee_breakdown_snapshot as _fee_breakdown_snapshot,
    fee_breakdown_to_quote,
    fee_storage_values,
    incremental_fees,
    previous_commission as _previous_commission,
    previous_fee_breakdown as _previous_fee_breakdown,
)
from app.services.pending_orders.live_order_support import (
    FillAccumulator,
    LiveOrderNotifier,
    LiveOrderRejected,
    apply_execution_result,
    build_live_order_context,
    console_print,
    make_client_order_id,
    signal_to_side_pos_reduce,
)
from app.services.pending_orders.live_order_phases import (
    maker_limit_price,
    wait_live_order_fill,
)
from app.services.grid.exchange_orders import query_grid_order_fill
from app.services.pending_orders.position_sync_cache import (
    exchange_sync_backoff_sec,
    get_position_sync_snapshot,
    invalidate_position_sync_snapshot_for_exchange,
    is_exchange_rate_limit_error,
    is_exchange_sync_backoff,
    position_sync_cache_key,
    set_exchange_sync_backoff,
    set_position_sync_snapshot,
)
from app.services.pending_order_position_sync import PendingOrderPositionSyncMixin
from app.services.pending_orders.sent_order_recovery import (
    is_final_fill, normalize_live_order_status,
    tracked_fill_baseline,
)
from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.okx import OkxClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.coinbase_exchange import CoinbaseExchangeClient
from app.services.live_trading.kraken import KrakenClient
from app.services.live_trading.kraken_futures import KrakenFuturesClient
from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient
from app.services.live_trading.htx import HtxClient
from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.strategy_runtime_logs import append_strategy_log
from app.services.strategy_lifecycle import (
    auto_stop_live_strategy,
    is_fatal_exchange_error,
    should_skip_position_sync,
)

# Lazy import IBKR to avoid ImportError if ib_insync not installed
IBKRClient = None


# Lazy import Alpaca to avoid ImportError if alpaca-py not installed
AlpacaClient = None

logger = get_logger(__name__)

ALPACA_FILL_DELTA_EPSILON = 1e-8


def _broker_order_type(payload: Dict[str, Any], ref_price: float) -> Tuple[str, float]:
    order_type = str(payload.get("order_type") or "market").strip().lower()
    if order_type == "maker_then_market":
        raise LiveTradingError("maker_then_market is not supported by broker execution")
    if order_type not in ("market", "limit"):
        raise LiveTradingError(f"unsupported_broker_order_type:{order_type}")
    limit_price = float(payload.get("limit_price") or 0.0)
    if order_type == "limit" and limit_price <= 0:
        limit_price = float(ref_price or 0.0)
    if order_type == "limit" and limit_price <= 0:
        raise LiveTradingError("broker_limit_price_required")
    return order_type, limit_price


def _broker_protection_prices(
    payload: Dict[str, Any],
    *,
    signal_type: str,
    entry_price: float,
) -> Tuple[float, float]:
    sig = str(signal_type or "").strip().lower()
    if sig not in ("open_long", "add_long", "open_short", "add_short") or entry_price <= 0:
        return 0.0, 0.0
    from app.services.live_trading.native_protection import protection_prices_from_payload

    stop, take, _trailing, _activation = protection_prices_from_payload(
        payload,
        entry_price=float(entry_price),
        pos_side="short" if "short" in sig else "long",
    )
    return float(stop or 0.0), float(take or 0.0)


def _redact_exchange_json(value: str) -> str:
    from app.services.live_trading.partner_attribution import redact_partner_attribution

    text_value = str(value or "")
    try:
        parsed = json.loads(text_value or "{}")
    except Exception:
        return str(redact_partner_attribution(text_value))
    return json.dumps(redact_partner_attribution(parsed), ensure_ascii=False)


class PendingOrderWorker(PendingOrderPositionSyncMixin):
    def __init__(self, poll_interval_sec: float = 1.0, batch_size: int = 50):
        self.poll_interval_sec = float(poll_interval_sec)
        self.batch_size = int(batch_size)
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._notifier = SignalNotifier()

        # Reclaim stuck orders (e.g. if the worker crashed after claiming an order).
        try:
            self._stale_processing_sec = int(os.getenv("PENDING_ORDER_STALE_SEC", "90"))
        except Exception:
            self._stale_processing_sec = 90
        self._fee_sync_retry_sec = max(60, int(os.getenv("LIVE_FEE_SYNC_RETRY_SEC", "300")))
        # Position sync self-check (best-effort): keep local positions aligned with exchange.
        self._position_sync_enabled = os.getenv("POSITION_SYNC_ENABLED", "true").lower() == "true"
        self._position_sync_interval_sec = float(os.getenv("POSITION_SYNC_INTERVAL_SEC", "30"))
        self._last_position_sync_ts = 0.0
        logger.info(f"PendingOrderWorker: sync_enabled={self._position_sync_enabled}, interval={self._position_sync_interval_sec}s")

    def start(self) -> bool:
        with self._lock:
            try:
                ensure_position_ledger_schema()
            except Exception as e:
                logger.warning("ensure_position_ledger_schema failed: %s", e)
            if self._thread and self._thread.is_alive():
                return True
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, name="PendingOrderWorker", daemon=True)
            self._thread.start()
            logger.info("PendingOrderWorker started")
            return True

    def stop(self, timeout_sec: float = 5.0) -> None:
        with self._lock:
            self._stop_event.set()
            th = self._thread
        if th and th.is_alive():
            th.join(timeout=timeout_sec)
        logger.info("PendingOrderWorker stopped")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                logger.warning(f"PendingOrderWorker tick error: {e}")
            time.sleep(self.poll_interval_sec)

    def _tick(self) -> None:
        # logger.info(f"[PendingOrderWorker] _tick start. last_sync={self._last_position_sync_ts}")
        self._sync_quick_trade_orders()
        self._sync_alpaca_sent_orders()
        self._sync_live_sent_orders()
        orders = self._fetch_pending_orders(limit=self.batch_size)
        # logger.info(f"[PendingOrderWorker] orders fetched: {len(orders)}")
        if not orders:
            self._maybe_sync_positions()
            return

        for o in orders:
            oid = o.get("id")
            if not oid:
                continue

            # Mark processing (best-effort)
            if not self._mark_processing(order_id=int(oid)):
                continue

            try:
                self._dispatch_one(o)
            except Exception as e:
                self._mark_failed(order_id=int(oid), error=str(e))

        self._maybe_sync_positions()

    def _sync_quick_trade_orders(self, limit: int = 50) -> None:
        """Reconcile non-terminal Quick Trade orders and protect new fills."""
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT *
                    FROM qd_quick_trades
                    WHERE status IN ('submitted', 'partially_filled')
                      AND COALESCE(exchange_order_id, '') <> ''
                      AND created_at >= NOW() - INTERVAL '7 days'
                    ORDER BY created_at ASC, id ASC
                    LIMIT %s
                    """,
                    (int(limit),),
                )
                rows = cur.fetchall() or []
                cur.close()
        except Exception as exc:
            logger.debug("Quick Trade reconciliation query failed: %s", exc)
            return

        for raw_row in rows:
            row = dict(raw_row)
            try:
                self._sync_one_quick_trade_order(row)
            except Exception as exc:
                logger.warning(
                    "Quick Trade reconciliation failed trade_id=%s: %s",
                    row.get("id"),
                    exc,
                )

    def _sync_one_quick_trade_order(self, row: Dict[str, Any]) -> None:
        from app.services.quick_trade.credentials import build_exchange_config, create_exchange_client
        from app.services.quick_trade.orders import (
            attach_quick_trade_protection,
            enrich_fill,
            quick_order_status,
        )

        trade_id = int(row.get("id") or 0)
        user_id = int(row.get("user_id") or 0)
        credential_id = int(row.get("credential_id") or 0)
        if trade_id <= 0 or user_id <= 0 or credential_id <= 0:
            return
        market_type = str(row.get("market_type") or "swap").strip().lower()
        stored_raw = row.get("raw_result") or {}
        if isinstance(stored_raw, str):
            try:
                stored_raw = json.loads(stored_raw) or {}
            except Exception:
                stored_raw = {}
        if not isinstance(stored_raw, dict):
            stored_raw = {"raw": stored_raw}
        metadata = stored_raw.get("_quick_trade")
        if not isinstance(metadata, dict):
            metadata = {}
        margin_mode = str(metadata.get("margin_mode") or "cross").strip().lower()
        exchange_config = build_exchange_config(
            credential_id,
            user_id,
            {"market_type": market_type, "margin_mode": margin_mode, "td_mode": margin_mode},
        )
        client = create_exchange_client(exchange_config, market_type=market_type)
        enrich = enrich_fill(
            client,
            order_id=str(row.get("exchange_order_id") or ""),
            symbol=str(row.get("symbol") or ""),
            market_type=market_type,
            max_wait_sec=0.25,
        )
        observed_filled = max(0.0, float(enrich.get("filled") or 0.0))
        cumulative_avg = max(0.0, float(enrich.get("avg_price") or 0.0))
        previous_filled = max(0.0, float(row.get("filled_amount") or 0.0))
        cumulative_filled = max(previous_filled, observed_filled)
        if observed_filled + ALPACA_FILL_DELTA_EPSILON < previous_filled:
            cumulative_avg = float(row.get("avg_fill_price") or 0.0)
        protected_filled = max(0.0, float(metadata.get("protected_filled_qty") or 0.0))
        delta_to_protect = cumulative_filled - protected_filled
        protection_error = ""
        metadata_changed = False
        if delta_to_protect > ALPACA_FILL_DELTA_EPSILON and cumulative_avg > 0:
            try:
                protection_result = attach_quick_trade_protection(
                    client,
                    symbol=str(row.get("symbol") or ""),
                    side=str(row.get("side") or ""),
                    filled_qty=delta_to_protect,
                    avg_price=cumulative_avg,
                    tp_price=float(row.get("tp_price") or 0.0),
                    sl_price=float(row.get("sl_price") or 0.0),
                    market_type=market_type,
                    exchange_config=exchange_config,
                    leverage=float(row.get("leverage") or 1.0),
                    margin_mode=margin_mode,
                    client_order_id=f"qdsync{trade_id}",
                )
                if protection_result:
                    prior = metadata.get("native_protection")
                    metadata["native_protection"] = (
                        list(prior) if isinstance(prior, list) else []
                    ) + protection_result
                    metadata["protected_filled_qty"] = cumulative_filled
                    metadata["native_protection_error"] = ""
                    metadata_changed = True
            except Exception as exc:
                protection_error = str(exc)
                metadata["native_protection_error"] = protection_error

        requested_qty = max(0.0, float(metadata.get("requested_base_qty") or 0.0))
        status = quick_order_status(
            requested_qty=requested_qty,
            filled_qty=cumulative_filled,
            exchange_status=str(enrich.get("status") or ""),
        )
        metadata["exchange_status"] = str(enrich.get("status") or "")
        metadata["last_reconciled_at"] = int(time.time())
        stored_raw["_quick_trade"] = metadata
        avg_to_store = cumulative_avg or float(row.get("avg_fill_price") or 0.0)
        fee_to_store = max(float(row.get("commission") or 0.0), float(enrich.get("fee") or 0.0))
        fee_ccy = str(enrich.get("fee_ccy") or row.get("commission_ccy") or "").strip().upper()
        from app.services.live_trading.fee_quote import fee_to_quote
        fee_quote = fee_to_quote(
            client,
            symbol=str(row.get("symbol") or ""),
            fee=fee_to_store,
            fee_ccy=fee_ccy,
            fill_price=avg_to_store,
        )
        if fee_quote is None and row.get("commission_quote") is not None:
            fee_quote = float(row.get("commission_quote") or 0.0)
        error_msg = protection_error or str(row.get("error_msg") or "")

        if (
            cumulative_filled <= previous_filled + ALPACA_FILL_DELTA_EPSILON
            and status == str(row.get("status") or "")
            and not protection_error
            and not metadata_changed
        ):
            return
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE qd_quick_trades
                SET status = %s,
                    filled_amount = %s,
                    avg_fill_price = %s,
                    commission = %s,
                    commission_ccy = %s,
                    commission_quote = %s,
                    error_msg = %s,
                    raw_result = %s
                WHERE id = %s
                  AND status IN ('submitted', 'partially_filled')
                """,
                (
                    status,
                    cumulative_filled,
                    avg_to_store,
                    fee_to_store,
                    fee_ccy,
                    fee_quote,
                    error_msg,
                    json.dumps(stored_raw, ensure_ascii=False),
                    trade_id,
                ),
            )
            db.commit()
            cur.close()

    def _maybe_sync_positions(self) -> None:
        if not self._position_sync_enabled:
            return
        now = time.time()
        if self._position_sync_interval_sec <= 0:
            return
        if now - float(self._last_position_sync_ts or 0.0) < float(self._position_sync_interval_sec):
            return
        logger.debug(f"[PendingOrderWorker] Triggering sync... (now={now}, last={self._last_position_sync_ts})")
        self._last_position_sync_ts = now
        try:
            self._sync_positions_best_effort()
            from app.services.live_trading.funding_reconciliation import sync_running_strategy_funding
            sync_running_strategy_funding()
            from app.services.live_trading.alpaca_activity_reconciliation import sync_running_alpaca_activities
            sync_running_alpaca_activities()
        except Exception as e:
            logger.debug(f"position sync skipped/failed: {e}")

    def _sync_alpaca_sent_orders(self, limit: int = 50) -> None:
        rows = self._fetch_alpaca_sent_orders(limit=limit)
        for row in rows:
            try:
                self._sync_one_alpaca_sent_order(row)
            except Exception as e:
                logger.warning(
                    "Alpaca fill sync failed: pending_id=%s err=%s",
                    row.get("id"),
                    e,
                )

    def _fetch_alpaca_sent_orders(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            try:
                stale_sec = int(self._stale_processing_sec or 0)
            except Exception:
                stale_sec = 0
            if stale_sec > 0:
                with get_db_connection() as db:
                    cur = db.cursor()
                    cur.execute(
                        """
                        UPDATE pending_orders
                        SET status = 'sent',
                            dispatch_note = 'alpaca_fill_sync:requeued_stale_sync',
                            updated_at = NOW()
                        WHERE status = 'syncing'
                          AND LOWER(COALESCE(exchange_id, '')) = 'alpaca'
                          AND updated_at < NOW() - (%s * INTERVAL '1 second')
                        """,
                        (stale_sec,),
                    )
                    db.commit()
                    cur.close()
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT *
                    FROM pending_orders
                    WHERE status = 'sent'
                      AND LOWER(COALESCE(exchange_id, '')) = 'alpaca'
                      AND COALESCE(exchange_order_id, '') <> ''
                    ORDER BY sent_at ASC NULLS FIRST, id ASC
                    LIMIT %s
                    """,
                    (int(limit),),
                )
                rows = cur.fetchall() or []
                cur.close()
            return rows
        except Exception as e:
            logger.warning("fetch_alpaca_sent_orders failed: %s", e)
            return []

    def _claim_alpaca_sent_order(self, order_id: int) -> Optional[Dict[str, Any]]:
        """Atomically claim one Alpaca sent order for fill sync."""
        if int(order_id or 0) <= 0:
            return None
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    UPDATE pending_orders
                    SET status = 'syncing',
                        dispatch_note = 'alpaca_fill_sync:syncing',
                        updated_at = NOW()
                    WHERE id = %s
                      AND status = 'sent'
                      AND LOWER(COALESCE(exchange_id, '')) = 'alpaca'
                      AND COALESCE(exchange_order_id, '') <> ''
                    RETURNING *
                    """,
                    (int(order_id),),
                )
                row = cur.fetchone()
                db.commit()
                cur.close()
            return row if isinstance(row, dict) else None
        except Exception as e:
            logger.warning("claim_alpaca_sent_order failed: pending_id=%s err=%s", order_id, e)
            return None

    def _sync_one_alpaca_sent_order(self, row: Dict[str, Any]) -> None:
        order_id = int(row.get("id") or 0)
        if order_id <= 0:
            return
        claimed = self._claim_alpaca_sent_order(order_id)
        if not claimed:
            return
        row = claimed
        exchange_order_id = str(row.get("exchange_order_id") or "").strip()
        if not exchange_order_id:
            return

        payload = {}
        payload_json = row.get("payload_json") or ""
        if isinstance(payload_json, str) and payload_json.strip():
            try:
                payload = json.loads(payload_json) or {}
            except Exception:
                payload = {}

        strategy_id = int(payload.get("strategy_id") or row.get("strategy_id") or 0)
        if strategy_id <= 0:
            return

        sc = load_strategy_configs(strategy_id)
        exchange_config = resolve_exchange_config(sc.get("exchange_config") or {}, user_id=int(sc.get("user_id") or 1))
        if str(exchange_config.get("exchange_id") or "").strip().lower() != "alpaca":
            return

        try:
            client = create_client(exchange_config)
        except Exception as e:
            logger.warning("Alpaca fill sync create_client failed: pending_id=%s err=%s", order_id, e)
            return

        global AlpacaClient
        if AlpacaClient is None:
            try:
                from app.services.alpaca_trading import AlpacaClient as _AlpacaClient
                AlpacaClient = _AlpacaClient
            except Exception:
                AlpacaClient = None
        if AlpacaClient is None or not isinstance(client, AlpacaClient):
            return

        result = client.get_order_status(exchange_order_id)
        status = str(result.status or "").strip().lower()
        cumulative_filled = float(result.filled or 0.0)
        cumulative_avg = float(result.avg_price or 0.0)
        previous_filled = float(row.get("filled") or 0.0)
        previous_avg = float(row.get("avg_price") or 0.0)
        raw_json = json.dumps(result.raw or {}, ensure_ascii=False)
        cumulative_commission, commission_ccy = _commission_snapshot(result.raw)
        commission_delta = max(0.0, cumulative_commission - _previous_commission(row))

        delta = cumulative_filled - previous_filled
        if delta > ALPACA_FILL_DELTA_EPSILON and cumulative_avg > 0:
            delta_avg = cumulative_avg
            if previous_filled > 0 and previous_avg > 0:
                delta_notional = cumulative_filled * cumulative_avg - previous_filled * previous_avg
                if delta_notional > 0:
                    delta_avg = delta_notional / delta

            signal_type = payload.get("signal_type") or row.get("signal_type")
            symbol = payload.get("symbol") or row.get("symbol")
            market_category = str(
                sc.get("market_category")
                or (sc.get("trading_config") or {}).get("market_category")
                or "USStock"
            )
            market_type_for_client = "crypto" if market_category.lower() in ("crypto", "cryptocurrency") else "USStock"
            from app.services.live_trading.fee_quote import fee_to_quote
            commission_quote = fee_to_quote(
                client,
                symbol=str(symbol or ""),
                fee=commission_delta,
                fee_ccy=commission_ccy,
                fill_price=delta_avg,
            )
            profit, _matched_entry = persist_strategy_fill(
                strategy_id=strategy_id,
                symbol=str(symbol or ""),
                signal_type=str(signal_type or ""),
                filled=float(delta),
                avg_price=float(delta_avg),
                exchange_config=exchange_config,
                market_type=market_type_for_client,
                order_id=order_id,
                fill_source="worker_alpaca_fill_sync",
                commission=commission_delta,
                commission_ccy=commission_ccy,
                commission_quote=commission_quote,
                close_reason=trade_close_reason_from_payload(payload, str(signal_type or "")),
                strategy_run_id=int(payload.get("strategy_run_id") or row.get("strategy_run_id") or 0),
                order_intent_id=int(payload.get("order_intent_id") or row.get("order_intent_id") or 0),
                exchange_id="alpaca",
                exchange_order_id=str(exchange_order_id or ""),
                raw_fill=result.raw or {},
            )
            _pstr = f", profit={profit:.4f}" if profit is not None else ""
            append_strategy_log(
                strategy_id,
                "trade",
                f"Alpaca fill synced: {signal_type} {symbol} filled={delta:.6f} @ {delta_avg:.6f}{_pstr}",
            )

        final_statuses = {"filled", "canceled", "cancelled", "rejected", "expired"}
        new_status = "sent"
        if status == "filled":
            new_status = "filled"
        elif status in ("canceled", "cancelled"):
            new_status = "cancelled"
        elif status in ("rejected", "expired"):
            new_status = "failed"

        self._update_alpaca_sent_order_snapshot(
            order_id=order_id,
            status=new_status,
            exchange_status=status,
            filled=cumulative_filled,
            avg_price=cumulative_avg,
            exchange_response_json=raw_json,
            final=status in final_statuses,
        )

    def _update_alpaca_sent_order_snapshot(
        self,
        *,
        order_id: int,
        status: str,
        exchange_status: str,
        filled: float,
        avg_price: float,
        exchange_response_json: str,
        final: bool,
    ) -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE pending_orders
                SET status = %s,
                    last_error = CASE WHEN %s = 'failed' THEN %s ELSE '' END,
                    dispatch_note = %s,
                    filled = %s,
                    avg_price = %s,
                    exchange_response_json = %s,
                    executed_at = CASE WHEN %s THEN NOW() ELSE executed_at END,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    str(status or "sent"),
                    str(status or "sent"),
                    str(exchange_status or ""),
                    f"alpaca_fill_sync:{exchange_status or 'unknown'}",
                    float(filled or 0.0),
                    float(avg_price or 0.0),
                    str(exchange_response_json or ""),
                    bool(final and float(filled or 0.0) > 0),
                    int(order_id),
                ),
            )
            cur.execute(
                """
                UPDATE strategy_order_intents soi
                SET status = CASE
                        WHEN %s = 'filled' THEN 'filled'
                        WHEN %s = 'failed' THEN 'rejected'
                        WHEN %s = 'cancelled' THEN 'cancelled'
                        WHEN %s > 0 THEN 'partially_filled'
                        ELSE 'submitted'
                    END,
                    exchange_order_id = COALESCE(NULLIF(po.exchange_order_id, ''), soi.exchange_order_id),
                    updated_at = NOW()
                FROM pending_orders po
                WHERE po.id = %s
                  AND po.order_intent_id = soi.id
                """,
                (
                    str(status or "sent"),
                    str(status or "sent"),
                    str(status or "sent"),
                    float(filled or 0.0),
                    int(order_id),
                ),
            )
            db.commit()
            cur.close()

    def _sync_live_sent_orders(self, limit: int = 50) -> None:
        """Reconcile submitted crypto orders, including durable resting limits."""
        rows = self._fetch_live_sent_orders(limit=limit)
        for row in rows:
            try:
                self._sync_one_live_sent_order(row)
            except Exception as exc:
                logger.warning(
                    "Live fill sync failed: pending_id=%s exchange=%s err=%s",
                    row.get("id"),
                    row.get("exchange_id"),
                    exc,
                )

    def _fetch_live_sent_orders(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            stale_sec = max(0, int(self._stale_processing_sec or 0))
            with get_db_connection() as db:
                cur = db.cursor()
                if stale_sec > 0:
                    cur.execute(
                        """
                        UPDATE pending_orders
                        SET status = 'sent',
                            dispatch_note = 'live_fill_sync:requeued_stale_sync',
                            updated_at = NOW()
                        WHERE status = 'syncing'
                          AND LOWER(COALESCE(exchange_id, '')) <> 'alpaca'
                          AND updated_at < NOW() - (%s * INTERVAL '1 second')
                        """,
                        (stale_sec,),
                    )
                    db.commit()
                cur.execute(
                    """
                    SELECT *
                    FROM pending_orders
                    WHERE (
                            status = 'sent'
                            OR (
                                status = 'filled'
                                AND COALESCE(filled, 0) <= 0
                                AND COALESCE(avg_price, 0) <= 0
                            )
                            OR (
                                status = 'filled'
                                AND COALESCE(filled, 0) > 0
                                AND (COALESCE(dispatch_note, '') NOT IN ('live_fee_sync:no_fee', 'live_fee_sync:retry') OR updated_at < NOW() - (%s * INTERVAL '1 second'))
                                AND EXISTS (
                                    SELECT 1
                                    FROM qd_strategy_trades t
                                    WHERE t.pending_order_id = pending_orders.id
                                      AND COALESCE(t.commission_quote, 0) = 0
                                )
                            )
                          )
                      AND LOWER(COALESCE(exchange_id, '')) <> 'alpaca'
                      AND COALESCE(exchange_id, '') <> ''
                      AND COALESCE(exchange_order_id, '') <> ''
                    ORDER BY sent_at ASC NULLS FIRST, id ASC
                    LIMIT %s
                    """,
                    (int(self._fee_sync_retry_sec), int(limit)),
                )
                rows = cur.fetchall() or []
                cur.close()
            return rows
        except Exception as exc:
            logger.warning("fetch_live_sent_orders failed: %s", exc)
            return []

    def _claim_live_sent_order(self, order_id: int) -> Optional[Dict[str, Any]]:
        if int(order_id or 0) <= 0:
            return None
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE pending_orders
                SET status = 'syncing',
                    dispatch_note = 'live_fill_sync:syncing',
                    updated_at = NOW()
                WHERE id = %s
                  AND (
                        status = 'sent'
                        OR (
                            status = 'filled'
                            AND COALESCE(filled, 0) <= 0
                            AND COALESCE(avg_price, 0) <= 0
                        )
                        OR (
                            status = 'filled'
                            AND COALESCE(filled, 0) > 0
                            AND (COALESCE(dispatch_note, '') NOT IN ('live_fee_sync:no_fee', 'live_fee_sync:retry') OR updated_at < NOW() - (%s * INTERVAL '1 second'))
                            AND EXISTS (
                                SELECT 1
                                FROM qd_strategy_trades t
                                WHERE t.pending_order_id = pending_orders.id
                                  AND COALESCE(t.commission_quote, 0) = 0
                            )
                        )
                      )
                  AND LOWER(COALESCE(exchange_id, '')) <> 'alpaca'
                  AND COALESCE(exchange_order_id, '') <> ''
                RETURNING *
                """,
                (int(order_id), int(self._fee_sync_retry_sec)),
            )
            row = cur.fetchone()
            db.commit()
            cur.close()
        return row if isinstance(row, dict) else None

    def _sync_one_live_sent_order(self, row: Dict[str, Any]) -> None:
        order_id = int(row.get("id") or 0)
        claimed = self._claim_live_sent_order(order_id)
        if not claimed:
            return
        row = claimed
        payload: Dict[str, Any] = {}
        try:
            payload = json.loads(str(row.get("payload_json") or "{}")) or {}
        except Exception:
            payload = {}

        strategy_id = int(payload.get("strategy_id") or row.get("strategy_id") or 0)
        symbol = str(payload.get("symbol") or row.get("symbol") or "").strip()
        market_type = str(payload.get("market_type") or row.get("market_type") or "swap").strip().lower()
        exchange_order_id = str(row.get("exchange_order_id") or "").strip()
        exchange_id = str(row.get("exchange_id") or "").strip().lower()
        if strategy_id <= 0 or not symbol or not exchange_order_id:
            self._mark_failed(order_id=order_id, error="live_fill_sync_invalid_order_context")
            return

        sc = load_strategy_configs(strategy_id)
        exchange_config = resolve_exchange_config(
            sc.get("exchange_config") or {},
            user_id=int(sc.get("user_id") or row.get("user_id") or 1),
        )
        try:
            client = create_client(exchange_config, market_type=market_type)
            sync_raw: Dict[str, Any] = {}
            if exchange_id == "ibkr" and hasattr(client, "get_order_status"):
                broker_result = client.get_order_status(exchange_order_id)
                cumulative_filled = float(broker_result.filled or 0.0)
                cumulative_avg = float(broker_result.avg_price or 0.0)
                exchange_status = normalize_live_order_status(broker_result.status)
                broker_raw = getattr(broker_result, "raw", {})
                sync_raw = broker_raw if isinstance(broker_raw, dict) else {}
            else:
                cumulative_filled, cumulative_avg, exchange_status = query_grid_order_fill(
                    client,
                    symbol=symbol,
                    market_type=market_type,
                    exchange_order_id=exchange_order_id,
                    client_order_id="",
                    exchange_config=exchange_config,
                )
                if cumulative_filled > 0:
                    try:
                        sync_raw = wait_live_order_fill(
                            client=client,
                            symbol=symbol,
                            order_id=exchange_order_id,
                            client_order_id="",
                            market_type=market_type,
                            exchange_config=exchange_config,
                            max_wait_sec=0.0,
                            phase="reconcile",
                        )
                    except Exception as fee_exc:
                        logger.debug(
                            "Live fee reconciliation unavailable: pending_id=%s exchange=%s err=%s",
                            order_id,
                            exchange_id,
                            fee_exc,
                        )
                        sync_raw = {}
        except Exception as exc:
            self._update_live_sent_order_snapshot(
                order_id=order_id,
                status="sent",
                exchange_status="sync_error",
                filled=float(row.get("filled") or 0.0),
                avg_price=float(row.get("avg_price") or 0.0),
                exchange_response_json=json.dumps({"error": str(exc)}, ensure_ascii=False),
            )
            return

        cumulative_filled = max(0.0, float(cumulative_filled or 0.0))
        cumulative_avg = max(0.0, float(cumulative_avg or 0.0))
        exchange_status = str(exchange_status or "unknown").strip().lower()
        previous_filled = max(0.0, float(row.get("filled") or 0.0))
        previous_avg = max(0.0, float(row.get("avg_price") or 0.0))
        tracked_previous_filled, tracked_previous_avg = tracked_fill_baseline(
            row,
            exchange_order_id=exchange_order_id,
            previous_filled=previous_filled,
            previous_avg=previous_avg,
        )
        delta = cumulative_filled - tracked_previous_filled
        aggregate_filled = previous_filled
        aggregate_avg = previous_avg
        cumulative_fees = _fee_breakdown_snapshot(sync_raw)
        previous_fees = _previous_fee_breakdown(row)
        fee_deltas = incremental_fees(cumulative_fees, previous_fees)
        commission_delta, commission_ccy = fee_storage_values(fee_deltas)

        if delta > ALPACA_FILL_DELTA_EPSILON and cumulative_avg > 0:
            delta_avg = cumulative_avg
            if tracked_previous_filled > 0 and tracked_previous_avg > 0:
                delta_notional = (
                    cumulative_filled * cumulative_avg
                    - tracked_previous_filled * tracked_previous_avg
                )
                if delta_notional > 0:
                    delta_avg = delta_notional / delta
            if previous_filled > 0 and previous_avg > 0:
                aggregate_notional = previous_filled * previous_avg + delta * delta_avg
                aggregate_filled = previous_filled + delta
                aggregate_avg = aggregate_notional / aggregate_filled
            else:
                aggregate_filled = previous_filled + delta
                aggregate_avg = delta_avg
            signal_type = str(payload.get("signal_type") or row.get("signal_type") or "")
            commission_quote = fee_breakdown_to_quote(
                client,
                symbol=symbol,
                fees=fee_deltas,
                fill_price=delta_avg,
            )
            protection_result: List[Dict[str, Any]] = []
            try:
                protection_result = self._attach_native_protection(
                    client=client,
                    payload=payload,
                    symbol=symbol,
                    signal_type=signal_type,
                    quantity=delta,
                    entry_price=delta_avg,
                    exchange_config=exchange_config,
                    market_type=market_type,
                    client_order_id=f"qdprot{order_id}",
                )
            except Exception as exc:
                append_strategy_log(
                    strategy_id,
                    "error",
                    f"Native protection placement failed; runtime protection remains active: {symbol}: {exc}",
                )
            persist_strategy_fill(
                strategy_id=strategy_id,
                symbol=symbol,
                signal_type=signal_type,
                filled=delta,
                avg_price=delta_avg,
                exchange_config=exchange_config,
                market_type=market_type,
                order_id=order_id,
                fill_source="worker_live_fill_sync",
                commission=commission_delta,
                commission_ccy=commission_ccy,
                commission_quote=commission_quote,
                close_reason=trade_close_reason_from_payload(payload, signal_type),
                strategy_run_id=int(payload.get("strategy_run_id") or row.get("strategy_run_id") or 0),
                order_intent_id=int(payload.get("order_intent_id") or row.get("order_intent_id") or 0),
                exchange_id=exchange_id,
                exchange_order_id=exchange_order_id,
                raw_fill={
                    "status": exchange_status,
                    "cumulative_filled": cumulative_filled,
                    "native_protection": protection_result,
                },
            )
            append_strategy_log(
                strategy_id,
                "trade",
                f"Exchange fill synced: {signal_type} {symbol} filled={delta:.6f} @ {delta_avg:.6f}",
            )

        if delta > ALPACA_FILL_DELTA_EPSILON and cumulative_avg <= 0:
            exchange_status = "fill_price_missing"

        if delta <= ALPACA_FILL_DELTA_EPSILON:
            aggregate_filled = previous_filled
            aggregate_avg = previous_avg

        fee_backfilled = 0
        if cumulative_fees and cumulative_avg > 0:
            cumulative_quote_fee = fee_breakdown_to_quote(
                client,
                symbol=symbol,
                fees=cumulative_fees,
                fill_price=cumulative_avg,
            )
            fee_backfilled = backfill_zero_commission_trades(
                order_id=order_id,
                fees_by_ccy=cumulative_fees,
                commission_quote=cumulative_quote_fee,
            )

        queue_status = "sent"
        if exchange_status == "filled" and cumulative_avg > 0:
            queue_status = "filled"
        elif exchange_status == "cancelled":
            queue_status = "cancelled"

        self._update_live_sent_order_snapshot(
            order_id=order_id,
            status=queue_status,
            exchange_status=exchange_status,
            filled=aggregate_filled,
            avg_price=aggregate_avg,
            exchange_response_json=json.dumps(
                {
                    "status": exchange_status,
                    "filled": aggregate_filled,
                    "avg_price": aggregate_avg,
                    "live_fill_sync": {
                        "tracked_filled": cumulative_filled,
                        "tracked_avg_price": cumulative_avg,
                        "fees_by_ccy": cumulative_fees,
                    },
                },
                ensure_ascii=False,
            ),
            dispatch_note=(
                "live_fee_sync:retry"
                if queue_status == "filled" and not cumulative_fees and delta <= ALPACA_FILL_DELTA_EPSILON
                else ""
            ),
        )

        if fee_backfilled:
            append_strategy_log(
                strategy_id,
                "info",
                f"Exchange fee reconciled: {symbol} rows={fee_backfilled}",
            )

    @staticmethod
    def _attach_native_protection(
        *,
        client: Any,
        payload: Dict[str, Any],
        symbol: str,
        signal_type: str,
        quantity: float,
        entry_price: float,
        exchange_config: Dict[str, Any],
        market_type: str,
        client_order_id: str,
    ) -> List[Dict[str, Any]]:
        sig = str(signal_type or "").strip().lower()
        if sig not in {"open_long", "add_long", "open_short", "add_short"}:
            return []
        if str(market_type or "").strip().lower() != "swap":
            return []

        from app.services.live_trading.native_protection import (
            NativeProtectionRequest,
            place_native_protection_orders,
            protection_prices_from_payload,
        )

        pos_side = "short" if "short" in sig else "long"
        stop, take, trailing, activation = protection_prices_from_payload(
            payload,
            entry_price=float(entry_price or 0.0),
            pos_side=pos_side,
        )
        if stop <= 0 and take <= 0 and trailing <= 0:
            return []
        margin_mode = str(
            payload.get("margin_mode")
            or payload.get("marginMode")
            or exchange_config.get("margin_mode")
            or exchange_config.get("marginMode")
            or "cross"
        ).strip().lower()
        request = NativeProtectionRequest(
            symbol=str(symbol),
            pos_side=pos_side,
            quantity=float(quantity or 0.0),
            entry_price=float(entry_price or 0.0),
            stop_loss_price=stop,
            take_profit_price=take,
            trailing_stop_pct=trailing,
            trailing_activation_pct=activation,
            margin_mode="isolated" if margin_mode in ("isolated", "iso") else "cross",
            leverage=float(payload.get("leverage") or exchange_config.get("leverage") or 1.0),
            product_type=str(
                payload.get("product_type")
                or payload.get("productType")
                or exchange_config.get("product_type")
                or exchange_config.get("productType")
                or "USDT-FUTURES"
            ),
            margin_coin=str(
                payload.get("margin_coin")
                or payload.get("marginCoin")
                or exchange_config.get("margin_coin")
                or exchange_config.get("marginCoin")
                or "USDT"
            ),
            client_order_id=str(client_order_id or ""),
        )
        return place_native_protection_orders(client, request)

    def _update_live_sent_order_snapshot(
        self,
        *,
        order_id: int,
        status: str,
        exchange_status: str,
        filled: float,
        avg_price: float,
        exchange_response_json: str,
        dispatch_note: str = "",
    ) -> None:
        exchange_response_json = _redact_exchange_json(exchange_response_json)
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE pending_orders
                SET status = %s,
                    dispatch_note = %s,
                    filled = %s,
                    avg_price = %s,
                    exchange_response_json = %s,
                    executed_at = CASE WHEN %s > 0 THEN COALESCE(executed_at, NOW()) ELSE executed_at END,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    str(status or "sent"),
                    str(dispatch_note or f"live_fill_sync:{exchange_status or 'unknown'}"),
                    float(filled or 0.0),
                    float(avg_price or 0.0),
                    str(exchange_response_json or ""),
                    float(filled or 0.0),
                    int(order_id),
                ),
            )
            cur.execute(
                """
                UPDATE strategy_order_intents soi
                SET status = CASE
                        WHEN %s = 'filled' THEN 'filled'
                        WHEN %s = 'cancelled' THEN 'cancelled'
                        WHEN %s > 0 THEN 'partially_filled'
                        ELSE 'submitted'
                    END,
                    exchange_order_id = COALESCE(NULLIF(po.exchange_order_id, ''), soi.exchange_order_id),
                    updated_at = NOW()
                FROM pending_orders po
                WHERE po.id = %s
                  AND po.order_intent_id = soi.id
                """,
                (
                    str(status or "sent"),
                    str(status or "sent"),
                    float(filled or 0.0),
                    int(order_id),
                ),
            )
            db.commit()
            cur.close()

    def _fetch_pending_orders(self, limit: int = 50) -> List[Dict[str, Any]]:
        try:
            # Best-effort: requeue stale "processing" rows to avoid deadlocks after crashes.
            try:
                stale_sec = int(self._stale_processing_sec or 0)
            except Exception:
                stale_sec = 0
            if stale_sec > 0:
                with get_db_connection() as db:
                    cur = db.cursor()
                    cur.execute(
                        """
                        UPDATE pending_orders
                        SET status = 'pending',
                            updated_at = NOW(),
                            dispatch_note = CASE
                                WHEN dispatch_note IS NULL OR dispatch_note = '' THEN 'requeued_stale_processing'
                                ELSE dispatch_note
                            END
                        WHERE status = 'processing'
                          AND (updated_at IS NULL OR updated_at < NOW() - INTERVAL '%s seconds')
                          AND (attempts < max_attempts)
                        """,
                        (stale_sec,),
                    )
                    db.commit()
                    cur.close()

            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT *
                    FROM pending_orders
                    WHERE status = 'pending'
                      AND (attempts < max_attempts)
                    ORDER BY priority DESC, id ASC
                    LIMIT %s
                    """,
                    (int(limit),),
                )
                rows = cur.fetchall() or []
                cur.close()
            return rows
        except Exception as e:
            logger.warning(f"fetch_pending_orders failed: {e}")
            return []

    def _mark_processing(self, order_id: int) -> bool:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                # Only claim if still pending to avoid double-processing.
                cur.execute(
                    """
                    UPDATE pending_orders
                    SET status = 'processing',
                        attempts = COALESCE(attempts, 0) + 1,
                        processed_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s AND status = 'pending'
                    """,
                    (int(order_id),),
                )
                claimed = getattr(cur, "rowcount", None)
                db.commit()
                cur.close()
            # Only treat as success if we actually changed a row.
            if claimed is None:
                return True
            return int(claimed) > 0
        except Exception as e:
            logger.warning(f"mark_processing failed: id={order_id}, err={e}")
            return False

    def _dispatch_one(self, order_row: Dict[str, Any]) -> None:
        order_id = int(order_row["id"])
        mode = (order_row.get("execution_mode") or "signal").strip().lower()
        payload_json = order_row.get("payload_json") or ""

        payload: Dict[str, Any] = {}
        if payload_json and isinstance(payload_json, str):
            try:
                payload = json.loads(payload_json) or {}
            except Exception:
                payload = {}

        signal_type = payload.get("signal_type") or order_row.get("signal_type")
        symbol = payload.get("symbol") or order_row.get("symbol")
        strategy_id = payload.get("strategy_id") or order_row.get("strategy_id")
        price = float(payload.get("price") or order_row.get("price") or 0.0)
        amount = float(payload.get("amount") or order_row.get("amount") or 0.0)
        direction = "short" if "short" in str(signal_type) else "long"
        notification_config = payload.get("notification_config") or {}
        strategy_name = str(payload.get("strategy_name") or "").strip()
        if not strategy_name:
            # Best-effort: load from DB for nicer notifications.
            strategy_name = self._load_strategy_name(int(strategy_id or 0)) if strategy_id else ""
        if not strategy_name:
            strategy_name = f"Strategy_{strategy_id}"

        # If the queued record is legacy ("signal") but the strategy is configured as live,
        # automatically upgrade it to live execution to keep the system moving.
        try:
            if mode != "live" and strategy_id:
                sc = load_strategy_configs(int(strategy_id))
                if (sc.get("execution_mode") or "").strip().lower() == "live":
                    mode = "live"
        except Exception:
            pass

        if mode == "signal":
            # Signal-only mode: dispatch notifications (no real trading).
            # Note: notification_config is stored in payload_json at enqueue time; fallback to DB if missing.
            if (not notification_config) and strategy_id:
                notification_config = self._load_notification_config(int(strategy_id))

            stake_quote = calc_notional_value(float(price or 0.0), float(amount or 0.0)) or float(amount or 0.0)
            results = self._notifier.notify_signal(
                strategy_id=int(strategy_id or 0),
                strategy_name=str(strategy_name or ""),
                symbol=str(symbol or ""),
                signal_type=str(signal_type or ""),
                price=float(price or 0.0),
                stake_amount=float(stake_quote),
                direction=str(direction or "long"),
                notification_config=notification_config if isinstance(notification_config, dict) else {},
                extra={"pending_order_id": order_id, "mode": mode},
            )

            attempted = list(results.keys())
            ok_channels = [c for c, r in results.items() if (r or {}).get("ok")]
            fail_channels = [c for c, r in results.items() if not (r or {}).get("ok")]

            if ok_channels:
                note = f"notified_ok={','.join(ok_channels)}"
                if fail_channels:
                    note += f";fail={','.join(fail_channels)}"
                self._mark_sent(order_id=order_id, note=note[:200])
                append_strategy_log(
                    int(strategy_id or 0), "signal",
                    f"Signal notification sent: {signal_type} {symbol} @ {price:.6f}, channels={','.join(ok_channels)}",
                )
            else:
                # Nothing succeeded -> mark failed with a compact error summary.
                first_err = ""
                for c in attempted:
                    err = (results.get(c) or {}).get("error") or ""
                    if err:
                        first_err = f"{c}:{err}"
                        break
                self._mark_failed(order_id=order_id, error=first_err or "notify_failed")
                append_strategy_log(
                    int(strategy_id or 0), "error",
                    f"Signal notification failed: {signal_type} {symbol}, error={first_err or 'notify_failed'}",
                )
            return

        if mode == "live":
            self._execute_live_order(order_id=order_id, order_row=order_row, payload=payload)
            return

        self._mark_failed(order_id=order_id, error=f"unsupported_execution_mode:{mode}")

    def _load_notification_config(self, strategy_id: int) -> Dict[str, Any]:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    "SELECT notification_config FROM qd_strategies_trading WHERE id = ?",
                    (int(strategy_id),),
                )
                row = cur.fetchone() or {}
                cur.close()
            s = row.get("notification_config") or ""
            if isinstance(s, dict):
                return s
            if isinstance(s, str) and s.strip():
                try:
                    obj = json.loads(s)
                    return obj if isinstance(obj, dict) else {}
                except Exception:
                    return {}
            return {}
        except Exception:
            return {}

    @staticmethod
    def _as_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return float(default)

    def _estimate_min_order_notional(
        self,
        client: Any,
        *,
        symbol: str,
        price: float,
        market_order: bool = True,
    ) -> Tuple[float, float]:
        """Best-effort minQty/minNotional estimate from live client filters."""
        px = float(price or 0.0)
        if px <= 0 or client is None:
            return 0.0, 0.0
        try:
            if not hasattr(client, "get_symbol_filters"):
                return 0.0, 0.0
            filters = client.get_symbol_filters(symbol=symbol) or {}
            lot = {}
            if isinstance(filters.get("MARKET_LOT_SIZE"), dict) and market_order:
                lot = filters.get("MARKET_LOT_SIZE") or {}
                try:
                    if float(lot.get("minQty") or 0) <= 0:
                        lot = filters.get("LOT_SIZE") or lot
                except Exception:
                    pass
            if not lot and isinstance(filters.get("LOT_SIZE"), dict):
                lot = filters.get("LOT_SIZE") or {}
            min_qty = self._as_float((lot or {}).get("minQty"), 0.0)

            min_notional = 0.0
            notional_filter = filters.get("MIN_NOTIONAL") or filters.get("NOTIONAL") or {}
            if isinstance(notional_filter, dict):
                min_notional = self._as_float(
                    notional_filter.get("notional")
                    or notional_filter.get("minNotional")
                    or notional_filter.get("minNotionalValue"),
                    0.0,
                )
            if min_qty > 0:
                min_notional = max(min_notional, min_qty * px)
            return max(0.0, min_qty), max(0.0, min_notional)
        except Exception:
            return 0.0, 0.0

    def _friendly_order_error(
        self,
        error: Any,
        *,
        client: Any,
        exchange_id: str,
        symbol: str,
        signal_type: str,
        amount: float,
        price: float,
        payload: Dict[str, Any],
    ) -> str:
        raw = str(error or "")
        lower = raw.lower()
        is_size_error = bool(
            re.search(r"below|step|minqty|min qty|minsize|min size|min_notional|minnotional|invalid (qty|quantity|size|amount)", lower)
        )
        if not is_size_error:
            return raw

        px = float(price or payload.get("ref_price") or 0.0)
        qty = float(amount or 0.0)
        actual_notional = qty * px if px > 0 else 0.0
        min_qty, min_notional = self._estimate_min_order_notional(
            client,
            symbol=str(symbol or ""),
            price=px,
            market_order=True,
        )
        sizing = payload.get("sizing") if isinstance(payload, dict) else {}
        sizing = sizing if isinstance(sizing, dict) else {}
        source = str(sizing.get("source") or "unknown")
        entry_pct = sizing.get("entry_pct")
        capital = sizing.get("initial_capital")
        leverage = sizing.get("leverage") or payload.get("leverage")

        parts = [
            raw,
            (
                f"actual notional is about {actual_notional:.4f} USDT"
                if actual_notional > 0
                else f"actual order quantity is {qty:.12f}"
            ),
        ]
        if min_notional > 0:
            parts.append(f"minimum notional is about {min_notional:.4f} USDT at the current price")
        elif min_qty > 0:
            parts.append(f"exchange minimum quantity is about {min_qty:.12f}")
        if capital is not None or entry_pct is not None or leverage is not None:
            parts.append(
                "sizing="
                f"capital={self._as_float(capital, 0.0):.4f}, "
                f"entry_pct={self._as_float(entry_pct, 0.0):.4f}%, "
                f"leverage={self._as_float(leverage, 1.0):.4f}x, "
                f"source={source}"
            )
        parts.append("Increase capital, entry percentage, or leverage, or choose a symbol that meets the minimum order size.")
        return "; ".join(parts)

    def _log_live_order_sizing(
        self,
        *,
        strategy_id: int,
        client: Any,
        symbol: str,
        signal_type: str,
        reduce_only: bool,
        amount: float,
        ref_price: float,
        leverage: float,
        payload: Dict[str, Any],
        phases: Dict[str, Any],
    ) -> None:
        if reduce_only or signal_type not in ("open_long", "open_short", "add_long", "add_short"):
            return
        try:
            min_qty, min_notional = self._estimate_min_order_notional(
                client,
                symbol=str(symbol or ""),
                price=float(ref_price or 0.0),
                market_order=True,
            )
            sizing = payload.get("sizing") if isinstance(payload, dict) else {}
            sizing = sizing if isinstance(sizing, dict) else {}
            append_strategy_log(
                strategy_id,
                "info",
                (
                    "Live order sizing: "
                    f"capital={self._as_float(sizing.get('initial_capital'), 0.0):.4f}, "
                    f"entry_pct={self._as_float(sizing.get('entry_pct'), 0.0):.4f}%, "
                    f"leverage={self._as_float(sizing.get('leverage') or leverage, 1.0):.4f}x, "
                    f"price={float(ref_price or 0.0):.8f}, "
                    f"final_qty={float(amount or 0.0):.12f}, "
                    f"min_qty={float(min_qty or 0.0):.12f}, "
                    f"min_notional={float(min_notional or 0.0):.4f}, "
                    f"source={sizing.get('source') or 'unknown'}"
                ),
            )
            phases["sizing_check"] = {
                "amount": float(amount or 0.0),
                "ref_price": float(ref_price or 0.0),
                "min_qty": float(min_qty or 0.0),
                "min_notional": float(min_notional or 0.0),
                "sizing": sizing,
            }
        except Exception:
            return

    def _load_strategy_name(self, strategy_id: int) -> str:
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute("SELECT strategy_name FROM qd_strategies_trading WHERE id = ?", (int(strategy_id),))
                row = cur.fetchone() or {}
                cur.close()
            return str(row.get("strategy_name") or "").strip()
        except Exception:
            return ""

    def _execute_live_order(self, *, order_id: int, order_row: Dict[str, Any], payload: Dict[str, Any]) -> None:
        """
        Execute a pending order using direct exchange REST clients (no ccxt).
        """
        _console_print = console_print

        try:
            ctx = build_live_order_context(
                order_id=order_id,
                order_row=order_row,
                payload=payload,
                load_strategy_configs=load_strategy_configs,
                resolve_exchange_config=resolve_exchange_config,
                safe_exchange_config_for_log=safe_exchange_config_for_log,
            )
        except LiveOrderRejected as rejected:
            self._mark_failed(order_id=order_id, error=rejected.error)
            if rejected.console_message:
                _console_print(rejected.console_message)
            if rejected.strategy_id > 0:
                live_notifier = LiveOrderNotifier(
                    order_id=order_id,
                    strategy_id=rejected.strategy_id,
                    order_row=order_row,
                    payload=payload,
                    notifier=self._notifier,
                    load_notification_config=self._load_notification_config,
                    load_strategy_name=self._load_strategy_name,
                )
                live_notifier.notify(status="failed", error=rejected.error)
                if rejected.strategy_log:
                    append_strategy_log(rejected.strategy_id, "error", rejected.strategy_log)
            return

        strategy_id = ctx.strategy_id
        signal_type = ctx.signal_type
        symbol = ctx.symbol
        amount = ctx.amount
        cfg = ctx.cfg
        exchange_config = ctx.exchange_config
        safe_cfg = ctx.safe_exchange_config
        exchange_id = ctx.exchange_id
        market_category = ctx.market_category
        market_type = ctx.market_type

        live_notifier = LiveOrderNotifier(
            order_id=order_id,
            strategy_id=strategy_id,
            order_row=order_row,
            payload=payload,
            notifier=self._notifier,
            load_notification_config=self._load_notification_config,
            load_strategy_name=self._load_strategy_name,
        )
        _notify_live_best_effort = live_notifier.notify

        client = None
        try:
            client = create_client(exchange_config, market_type=market_type)
        except Exception as e:
            self._mark_failed(order_id=order_id, error=f"create_client_failed:{e}")
            _console_print(f"[worker] create_client_failed: strategy_id={strategy_id} pending_id={order_id} err={e}")
            _notify_live_best_effort(status="failed", error=f"create_client_failed:{e}")
            append_strategy_log(strategy_id, "error", f"Exchange client creation failed ({exchange_id}): {e}")
            if is_fatal_exchange_error(str(e)):
                auto_stop_live_strategy(int(strategy_id), str(e), source="pending_order_client")
            return

        # Check if this is an IBKR client (US stocks)
        global IBKRClient
        if IBKRClient is None:
            try:
                from app.services.ibkr_trading import IBKRClient as _IBKRClient
                IBKRClient = _IBKRClient
            except ImportError:
                pass

        if IBKRClient is not None and isinstance(client, IBKRClient):
            # Execute IBKR order (separate flow for stocks)
            self._execute_ibkr_order(
                order_id=order_id,
                order_row=order_row,
                payload=payload,
                client=client,
                strategy_id=strategy_id,
                exchange_config=exchange_config,
                _notify_live_best_effort=_notify_live_best_effort,
                _console_print=_console_print,
            )
            return

        global AlpacaClient
        if AlpacaClient is None:
            try:
                from app.services.alpaca_trading import AlpacaClient as _AlpacaClient
                AlpacaClient = _AlpacaClient
            except ImportError:
                pass

        if AlpacaClient is not None and isinstance(client, AlpacaClient):
            self._execute_alpaca_order(
                order_id=order_id,
                order_row=order_row,
                payload=payload,
                client=client,
                strategy_id=strategy_id,
                exchange_config=exchange_config,
                market_category=market_category,
                _notify_live_best_effort=_notify_live_best_effort,
                _console_print=_console_print,
            )
            return

        client_oid = make_client_order_id(exchange_id=exchange_id, strategy_id=strategy_id, order_id=order_id)
        sig = str(signal_type or "").strip().lower()
        # Spot does not support short signals in this system.
        if market_type == "spot" and "short" in sig:
            self._mark_failed(order_id=order_id, error="spot_market_does_not_support_short_signals")
            _console_print(f"[worker] order rejected: strategy_id={strategy_id} pending_id={order_id} spot short not supported")
            _notify_live_best_effort(status="failed", error="spot_market_does_not_support_short_signals")
            append_strategy_log(strategy_id, "error", f"Order rejected: spot market does not support short signals ({symbol} {signal_type})")
            return

        # Unified maker->market fallback settings
        # Priority: payload config > environment variable > default value
        _default_order_mode = os.getenv("ORDER_MODE", "market").strip().lower()
        _default_maker_wait_sec = float(os.getenv("MAKER_WAIT_SEC", "10"))
        _default_maker_offset_bps = float(os.getenv("MAKER_OFFSET_BPS", "2"))

        order_mode = str(payload.get("order_mode") or payload.get("orderMode") or _default_order_mode).strip().lower()
        execution_algo = str(payload.get("execution_algo") or "").strip().lower()
        if execution_algo == "market":
            order_mode = "market"
        elif execution_algo in ("limit_then_market", "maker", "maker_then_market"):
            order_mode = "maker_then_market"
        elif execution_algo == "limit":
            order_mode = "limit"
        maker_wait_sec = float(payload.get("maker_wait_sec") or payload.get("makerWaitSec") or _default_maker_wait_sec)
        maker_offset_bps = float(payload.get("maker_offset_bps") or payload.get("makerOffsetBps") or _default_maker_offset_bps)
        if maker_wait_sec <= 0:
            maker_wait_sec = _default_maker_wait_sec if _default_maker_wait_sec > 0 else 10.0
        if maker_offset_bps < 0:
            maker_offset_bps = 0.0
        maker_offset = maker_offset_bps / 10000.0

        ref_price = float(payload.get("ref_price") or payload.get("price") or order_row.get("price") or 0.0)

        side, pos_side, reduce_only = signal_to_side_pos_reduce(signal_type)

        from app.services.live_trading.position_query import query_exchange_position_size

        # Leverage handling (best-effort):
        # - For OKX swap, leverage must be set via private endpoint; otherwise exchange defaults apply.
        # - For other exchanges, leverage setting is not implemented yet in this local client.
        leverage = payload.get("leverage")
        if leverage is None:
            leverage = cfg.get("leverage")
        try:
            leverage = float(leverage or 1.0)
        except Exception:
            leverage = 1.0
        if leverage <= 0:
            leverage = 1.0

        # Refresh the account mirror before validating an opening order.
        try:
            logger.info(f"[Sync] Triggering pre-execution sync for strategy {strategy_id} before order {order_id}")
            self._sync_positions_best_effort(target_strategy_id=strategy_id)
        except Exception as e:
            logger.warning(f"Pre-execution sync failed: {e}")

        pre_position_qty = 0.0
        try:
            pre_position_qty = float(
                query_exchange_position_size(
                    client=client,
                    symbol=str(symbol),
                    pos_side=str(pos_side or ""),
                    market_type=str(market_type or "swap"),
                    exchange_config=exchange_config if isinstance(exchange_config, dict) else {},
                    strict=not reduce_only,
                )
                or 0.0
            )
        except Exception as e:
            error = f"position_snapshot_failed:{e}"
            self._mark_failed(order_id=order_id, error=error)
            _notify_live_best_effort(status="failed", error=error)
            append_strategy_log(strategy_id, "error", f"Order rejected because the exchange position snapshot failed: {symbol}")
            return

        if not reduce_only and market_type == "swap":
            credential_id = credential_id_from_exchange_config(exchange_config)
            expected_qty = fetch_allocated_position_size(
                strategy_id=int(strategy_id),
                credential_id=int(credential_id or 0),
                market_type=str(market_type),
                symbol=str(symbol),
                side=str(pos_side),
            )
            tolerance = max(1e-8, expected_qty * 0.001)
            if abs(pre_position_qty - expected_qty) > tolerance:
                error = (
                    "position_drift_detected:"
                    f"side={pos_side},exchange={pre_position_qty},allocated={expected_qty}"
                )
                self._mark_failed(order_id=order_id, error=error)
                _notify_live_best_effort(status="failed", error=error)
                append_strategy_log(strategy_id, "error", f"Order rejected because account and strategy positions differ: {symbol} {pos_side}")
                return

            opposite_side = "short" if str(pos_side) == "long" else "long"
            local_opposite = fetch_position_size_for_side(int(strategy_id), str(symbol), opposite_side)
            if local_opposite > 1e-8:
                try:
                    live_opposite = float(
                        query_exchange_position_size(
                            client=client,
                            symbol=str(symbol),
                            pos_side=opposite_side,
                            market_type=str(market_type),
                            exchange_config=exchange_config if isinstance(exchange_config, dict) else {},
                            strict=True,
                        )
                        or 0.0
                    )
                except Exception as e:
                    error = f"opposite_position_snapshot_failed:{e}"
                    self._mark_failed(order_id=order_id, error=error)
                    _notify_live_best_effort(status="failed", error=error)
                    return
                if live_opposite > 1e-8:
                    error = (
                        "opposite_position_still_open:"
                        f"side={opposite_side},exchange={live_opposite},local={local_opposite}"
                    )
                    self._mark_failed(order_id=order_id, error=error)
                    _notify_live_best_effort(status="failed", error=error)
                    append_strategy_log(strategy_id, "error", f"Reverse entry rejected until the opposite position is fully closed: {symbol}")
                    return

        # Collect raw exchange interactions / intermediate states for debugging & persistence.
        phases: Dict[str, Any] = {"pre_position_qty": pre_position_qty}

        if not reduce_only and market_type == "swap":
            try:
                from app.services.live_trading.account_risk import (
                    account_risk_limits,
                    account_risk_snapshot,
                )

                credential_id = credential_id_from_exchange_config(exchange_config)
                risk_snapshot = account_risk_snapshot(
                    user_id=int(cfg.get("user_id") or 1),
                    credential_id=int(credential_id or 0),
                    market_type=str(market_type),
                    strategy_id=int(strategy_id),
                    proposed_symbol=str(symbol),
                    proposed_side=str(pos_side),
                    proposed_quantity=float(amount or 0.0),
                    proposed_price=float(ref_price or 0.0),
                    proposed_leverage=float(leverage or 1.0),
                    limits=account_risk_limits(cfg),
                )
                phases["account_risk"] = risk_snapshot
                if not risk_snapshot.get("allowed"):
                    violations = list(risk_snapshot.get("violations") or [])
                    error = str(violations[0] if violations else "accountRisk.rejected")
                    self._mark_failed(order_id=order_id, error=error)
                    _notify_live_best_effort(status="failed", error=error)
                    append_strategy_log(
                        strategy_id,
                        "error",
                        f"Order rejected by account risk controls: {','.join(violations)}",
                    )
                    return
            except Exception as e:
                error = f"accountRisk.snapshotFailed:{e}"
                self._mark_failed(order_id=order_id, error=error)
                _notify_live_best_effort(status="failed", error=error)
                append_strategy_log(strategy_id, "error", "Order rejected because account risk could not be verified")
                return

        # Close/reduce: the strategy ledger is the ownership boundary.  The
        # exchange position/balance may only reduce the requested quantity.
        if reduce_only:
            try:
                amount, close_meta = resolve_reduce_only_quantity(
                    strategy_id=int(strategy_id),
                    symbol=str(symbol or ""),
                    pos_side=str(pos_side or ""),
                    requested_amount=float(amount or 0.0),
                    client=client,
                    market_type=str(market_type or "swap"),
                    exchange_config=exchange_config,
                    allow_exchange_fallback=False,
                )
                if close_meta:
                    phases["close_size_resolve"] = close_meta
            except Exception as e:
                logger.error(f"[RiskControl] Failed to resolve close quantity: {e}")
                phases["close_size_resolve_error"] = str(e)

        # Ensure ref price exists (used by maker pricing, fallbacks, and local DB snapshots).
        if ref_price <= 0:
            try:
                if isinstance(client, BinanceFuturesClient):
                    ref_price = float(client.get_mark_price(symbol=str(symbol)) or 0.0)
            except Exception:
                pass

        if market_type == "swap":
            try:
                from app.services.live_trading.account_configuration import configure_derivatives_account

                margin_mode = str(
                    payload.get("margin_mode")
                    or payload.get("marginMode")
                    or cfg.get("margin_mode")
                    or cfg.get("marginMode")
                    or "cross"
                )
                phases["account_configuration"] = configure_derivatives_account(
                    client,
                    exchange_id=exchange_id,
                    symbol=str(symbol),
                    leverage=float(leverage or 1.0),
                    margin_mode=margin_mode,
                )
            except Exception as e:
                err = f"derivatives_account_configuration_failed:{e}"
                logger.warning(f"live leverage set failed: pending_id={order_id}, strategy_id={strategy_id}, cfg={safe_cfg}, err={e}")
                self._mark_failed(order_id=order_id, error=err)
                _console_print(f"[worker] order rejected: strategy_id={strategy_id} pending_id={order_id} {err}")
                _notify_live_best_effort(status="failed", error=err, amount_hint=amount, price_hint=ref_price)
                append_strategy_log(strategy_id, "error", f"Leverage or margin-mode setup failed for {symbol}: {e}")
                return

        fills = FillAccumulator()

        # Spot close: cap to exchange free base (fees often make DB size > sellable free).
        if reduce_only and market_type == "spot" and side == "sell":
            try:
                from app.services.live_trading.spot_sizing import clamp_spot_close_quantity

                new_amt, spot_meta = clamp_spot_close_quantity(
                    client, symbol=str(symbol), requested_qty=float(amount or 0.0)
                )
                if spot_meta.get("adjusted"):
                    phases["spot_close_adjustment"] = spot_meta
                amount = new_amt
            except Exception as e:
                logger.warning(
                    "Spot close amount adjustment failed: pending_id=%s, err=%s", order_id, e
                )
                phases["spot_close_adjust_error"] = str(e)

        spot_quote_amt = 0.0
        spot_market_buy_uses_quote = False
        if market_type == "spot":
            try:
                from app.services.live_trading.spot_sizing import prepare_spot_live_order_sizes

                amount, spot_quote_amt, spot_market_buy_uses_quote = prepare_spot_live_order_sizes(
                    client,
                    symbol=str(symbol),
                    side=side,
                    reduce_only=reduce_only,
                    base_qty=float(amount or 0.0),
                    ref_price=ref_price,
                )
                phases["spot_prepare"] = {
                    "base_qty": amount,
                    "quote_amt": spot_quote_amt,
                    "market_buy_uses_quote": spot_market_buy_uses_quote,
                }
            except Exception as e:
                logger.warning(
                    "Spot size prepare failed: pending_id=%s, err=%s", order_id, e
                )
                phases["spot_prepare_error"] = str(e)

        self._log_live_order_sizing(
            strategy_id=strategy_id,
            client=client,
            symbol=symbol,
            signal_type=signal_type,
            reduce_only=reduce_only,
            amount=amount,
            ref_price=ref_price,
            leverage=leverage,
            payload=payload,
            phases=phases,
        )

        # Decide if we should use limit-first flow.
        use_limit_first = order_mode in ("maker", "limit", "limit_first", "maker_then_market")

        remaining = float(amount or 0.0)
        # Close/reduce: retry the strategy-ledger lookup once after a sync.  A
        # missing strategy position still resolves to zero; account inventory
        # is never adopted as strategy-owned quantity.
        if (
            remaining <= 0
            and reduce_only
            and not (spot_market_buy_uses_quote and spot_quote_amt > 0)
        ):
            phases["close_size_retry"] = {"trigger": "zero_after_first_resolve"}
            try:
                logger.info(
                    "[CloseRetry] Close qty is 0 for strategy=%s %s %s; re-syncing positions",
                    strategy_id,
                    symbol,
                    signal_type,
                )
                self._sync_positions_best_effort(target_strategy_id=strategy_id)
                amount, retry_meta = resolve_reduce_only_quantity(
                    strategy_id=int(strategy_id),
                    symbol=str(symbol or ""),
                    pos_side=str(pos_side or ""),
                    requested_amount=float(payload.get("amount") or order_row.get("amount") or 0.0),
                    client=client,
                    market_type=str(market_type or "swap"),
                    exchange_config=exchange_config,
                    allow_exchange_fallback=False,
                )
                if retry_meta:
                    phases["close_size_retry"].update(retry_meta)
                remaining = float(amount or 0.0)
                if remaining > 0:
                    logger.info(
                        "[CloseRetry] Resolved close qty=%s for strategy=%s %s %s",
                        remaining,
                        strategy_id,
                        symbol,
                        signal_type,
                    )
            except Exception as e:
                logger.warning(
                    "[CloseRetry] Re-sync/resolve failed: pending_id=%s strategy=%s err=%s",
                    order_id,
                    strategy_id,
                    e,
                )
                phases["close_size_retry"]["error"] = str(e)

        if remaining <= 0 and not (spot_market_buy_uses_quote and spot_quote_amt > 0):
            friendly_error = self._friendly_order_error(
                "invalid amount",
                client=client,
                exchange_id=exchange_id,
                symbol=symbol,
                signal_type=signal_type,
                amount=amount,
                price=ref_price,
                payload=payload,
            )
            self._mark_failed(order_id=order_id, error=friendly_error)
            _notify_live_best_effort(status="failed", error=friendly_error, amount_hint=amount)
            if reduce_only:
                append_strategy_log(
                    strategy_id,
                    "error",
                    f"Order rejected: {friendly_error} for {symbol} {signal_type} "
                    f"(no position after sync; check exchange/DB alignment)",
                )
            else:
                append_strategy_log(
                    strategy_id,
                    "error",
                    f"Order rejected: {friendly_error} for {symbol} {signal_type}",
                )
            return

        limit_order_id = ""
        limit_client_oid = ""
        market_order_id = ""
        market_client_oid = make_client_order_id(
            exchange_id=exchange_id,
            strategy_id=strategy_id,
            order_id=order_id,
            phase="mkt",
        )
        try:
            limit_price = 0.0
            if execution_algo == "limit" or use_limit_first:
                explicit_limit_price = float(payload.get("limit_price") or 0.0)
                limit_price = explicit_limit_price or maker_limit_price(
                    ref_price=ref_price,
                    side=side,
                    maker_offset=maker_offset,
                )
                limit_client_oid = make_client_order_id(
                    exchange_id=exchange_id,
                    strategy_id=strategy_id,
                    order_id=order_id,
                    phase="lmt",
                )
            adapter = LiveOrderPhaseAdapter(
                client=client,
                exchange_id=exchange_id,
                payload=payload,
                exchange_config=exchange_config,
                order_mode=order_mode,
                ref_price=ref_price,
                spot_quote_amt=spot_quote_amt,
                spot_market_buy_uses_quote=spot_market_buy_uses_quote,
            )
            intent = OrderIntent(
                symbol=str(symbol),
                side=side,
                quantity=float(remaining or 0.0),
                market_type=market_type,
                price=float(limit_price or 0.0),
                pos_side=pos_side,
                reduce_only=reduce_only,
                client_order_id=market_client_oid,
                fallback_client_order_id=market_client_oid,
                leverage=leverage,
                margin_mode=str(payload.get("margin_mode") or payload.get("td_mode") or "cross"),
                exchange_config=exchange_config,
            )
            if execution_algo == "limit":
                intent = OrderIntent(
                    symbol=intent.symbol,
                    side=intent.side,
                    quantity=intent.quantity,
                    market_type=intent.market_type,
                    price=intent.price,
                    pos_side=intent.pos_side,
                    reduce_only=intent.reduce_only,
                    client_order_id=limit_client_oid,
                    fallback_client_order_id=market_client_oid,
                    leverage=intent.leverage,
                    margin_mode=intent.margin_mode,
                    exchange_config=intent.exchange_config,
                )
                execution_result = RestingLimitExecutor(adapter).execute(intent)
                limit_order_id = str(execution_result.exchange_order_id or "")
            elif use_limit_first:
                intent = OrderIntent(
                    symbol=intent.symbol,
                    side=intent.side,
                    quantity=intent.quantity,
                    market_type=intent.market_type,
                    price=intent.price,
                    pos_side=intent.pos_side,
                    reduce_only=intent.reduce_only,
                    client_order_id=limit_client_oid,
                    fallback_client_order_id=market_client_oid,
                    leverage=intent.leverage,
                    margin_mode=intent.margin_mode,
                    exchange_config=intent.exchange_config,
                )
                execution_result = LimitThenMarketExecutor(
                    adapter,
                    max_wait_sec=maker_wait_sec,
                    fallback_to_market=bool(
                        payload.get(
                            "close_fallback_to_market" if reduce_only else "open_fallback_to_market",
                            True,
                        )
                    ),
                ).execute(intent)
            else:
                execution_result = MarketOrderExecutor(adapter).execute(intent)
            phases["executor"] = execution_result.raw
            if not execution_result.success:
                friendly_error = self._friendly_order_error(
                    execution_result.error,
                    client=client,
                    exchange_id=exchange_id,
                    symbol=symbol,
                    signal_type=signal_type,
                    amount=amount,
                    price=ref_price,
                    payload=payload,
                )
                self._mark_failed(order_id=order_id, error=friendly_error)
                _console_print(f"[worker] order failed: strategy_id={strategy_id} pending_id={order_id} err={friendly_error}")
                _notify_live_best_effort(status="failed", error=friendly_error, amount_hint=amount, price_hint=ref_price)
                append_strategy_log(strategy_id, "error", f"Exchange order failed ({exchange_id} {symbol} {signal_type}): {friendly_error}")
                return
            apply_execution_result(fills, execution_result)
            if execution_algo != "limit":
                market_order_id = str(execution_result.exchange_order_id or "")
        except LiveTradingError as e:
            logger.warning(f"live executor failed: pending_id={order_id}, strategy_id={strategy_id}, cfg={safe_cfg}, err={e}")
            friendly_error = self._friendly_order_error(
                e,
                client=client,
                exchange_id=exchange_id,
                symbol=symbol,
                signal_type=signal_type,
                amount=amount,
                price=ref_price,
                payload=payload,
            )
            self._mark_failed(order_id=order_id, error=friendly_error)
            _console_print(f"[worker] order failed: strategy_id={strategy_id} pending_id={order_id} err={friendly_error}")
            _notify_live_best_effort(status="failed", error=friendly_error, amount_hint=amount, price_hint=ref_price)
            append_strategy_log(strategy_id, "error", f"Exchange order failed ({exchange_id} {symbol} {signal_type}): {friendly_error}")
            return
        except Exception as e:
            logger.warning(f"live executor unexpected error: pending_id={order_id}, strategy_id={strategy_id}, cfg={safe_cfg}, err={e}")
            self._mark_failed(order_id=order_id, error=str(e))
            _console_print(f"[worker] order unexpected error: strategy_id={strategy_id} pending_id={order_id} err={e}")
            _notify_live_best_effort(status="failed", error=str(e), amount_hint=amount, price_hint=ref_price)
            append_strategy_log(strategy_id, "error", f"Unexpected order error ({exchange_id} {symbol} {signal_type}): {e}")
            return

        # Build final result (best-effort); live path never fabricates fill qty from request amount.
        filled_final = float(fills.total_base or 0.0)
        avg_final = float(fills.avg_price() or 0.0)

        ex_oid_for_recovery = str(market_order_id or limit_order_id or "")
        coid_for_recovery = str(
            market_client_oid if market_order_id else (limit_client_oid if limit_order_id else "")
        )
        if filled_final <= 0 and ex_oid_for_recovery:
            from app.services.live_trading.fill_recovery import try_recover_zero_fill

            rec_filled, rec_avg, rec_src = try_recover_zero_fill(
                client,
                symbol=str(symbol),
                market_type=str(market_type or "swap"),
                exchange_config=exchange_config if isinstance(exchange_config, dict) else {},
                exchange_order_id=ex_oid_for_recovery,
                client_order_id=coid_for_recovery,
                requested_qty=float(amount or 0.0),
                signal_type=str(signal_type or ""),
                pos_side=str(pos_side or ""),
                pre_position_qty=float(pre_position_qty or 0.0),
                ref_price=float(ref_price or 0.0),
            )
            if rec_filled > 0:
                filled_final = rec_filled
                avg_final = rec_avg if rec_avg > 0 else float(ref_price or 0.0)
                phases["fill_recovery"] = {
                    "source": rec_src,
                    "filled": rec_filled,
                    "avg_price": avg_final,
                    "exchange_order_id": ex_oid_for_recovery,
                }
                append_strategy_log(
                    strategy_id,
                    "info",
                    f"Fill recovered ({rec_src}): {signal_type} {symbol} qty={rec_filled:.6f} @ ~{avg_final:.4f}",
                )

        if filled_final > 0 and avg_final > 0:
            try:
                native_protection = self._attach_native_protection(
                    client=client,
                    payload=payload,
                    symbol=str(symbol),
                    signal_type=str(signal_type),
                    quantity=filled_final,
                    entry_price=avg_final,
                    exchange_config=exchange_config,
                    market_type=str(market_type),
                    client_order_id=f"qdprot{order_id}",
                )
                if native_protection:
                    phases["native_protection"] = native_protection
                    append_strategy_log(
                        strategy_id,
                        "info",
                        f"Native protection attached: {symbol} orders={len(native_protection)}",
                    )
            except Exception as e:
                phases["native_protection_error"] = str(e)
                logger.error(
                    "Native protection failed pending_id=%s strategy_id=%s symbol=%s: %s",
                    order_id,
                    strategy_id,
                    symbol,
                    e,
                )
                append_strategy_log(
                    strategy_id,
                    "error",
                    f"Native protection placement failed; runtime protection remains active: {symbol}: {e}",
                )

        res = type("Tmp", (), {"exchange_id": str(exchange_config.get("exchange_id") or ""), "exchange_order_id": str(market_order_id or limit_order_id), "raw": phases, "filled": filled_final, "avg_price": avg_final})()

        executed_at = int(time.time())
        filled = filled_final
        avg_price = avg_final
        post_query: Dict[str, Any] = {**phases, "fee_breakdown": dict(fills.fees_by_ccy)}

        # Persist queue result first (idempotency / observability).
        try:
            self._mark_sent(
                order_id=order_id,
                note="live_order_sent",
                exchange_id=res.exchange_id,
                exchange_order_id=res.exchange_order_id,
                exchange_response_json=json.dumps({"phases": (post_query or {})}, ensure_ascii=False),
                filled=filled,
                avg_price=avg_price,
                executed_at=executed_at if filled > 0 else None,
                final_filled=is_final_fill(amount, filled, avg_price, execution_result.status),
            )
            _console_print(f"[worker] order sent: strategy_id={strategy_id} pending_id={order_id} exchange={res.exchange_id} order_id={res.exchange_order_id} filled={filled} avg={avg_price}")
        except Exception as e:
            logger.warning(f"mark_sent failed: pending_id={order_id}, err={e}")

        # Record trade + update local position snapshot (best-effort).
        try:
            if filled > 0 and avg_price > 0:
                from app.services.live_trading.fee_quote import fee_to_quote

                commission_quote = 0.0
                commission_quote_known = True
                for fee_currency, fee_amount in fills.fees_by_ccy.items():
                    converted = fee_to_quote(
                        client,
                        symbol=str(symbol),
                        fee=float(fee_amount or 0.0),
                        fee_ccy="" if fee_currency == "UNKNOWN" else fee_currency,
                        fill_price=float(avg_price),
                    )
                    if converted is None:
                        commission_quote_known = False
                        break
                    commission_quote += converted
                if not commission_quote_known:
                    commission_quote = None
                logger.info(
                    f"live record begin: pending_id={order_id} strategy_id={strategy_id} symbol={symbol} "
                    f"signal={signal_type} filled={filled} avg_price={avg_price} fee={fills.total_fee} fee_ccy={fills.fee_ccy}"
                )
                _close_reason = trade_close_reason_from_payload(payload, str(signal_type))
                profit, matched_entry = persist_strategy_fill(
                        strategy_id=int(strategy_id),
                        symbol=str(symbol),
                        signal_type=str(signal_type),
                        filled=float(filled),
                        avg_price=float(avg_price),
                        exchange_config=exchange_config,
                        market_type=str(market_type or "swap"),
                        order_id=int(order_id),
                        fill_source="worker",
                        commission=float(fills.total_fee or 0.0),
                        commission_ccy=str(fills.fee_ccy or "").strip().upper(),
                        commission_quote=commission_quote,
                        close_reason=_close_reason,
                        strategy_run_id=int(payload.get("strategy_run_id") or order_row.get("strategy_run_id") or 0),
                        order_intent_id=int(payload.get("order_intent_id") or order_row.get("order_intent_id") or 0),
                        exchange_id=str(res.exchange_id or ""),
                        exchange_order_id=str(res.exchange_order_id or ""),
                        raw_fill=post_query or {},
                    )
                logger.info(f"live record done: pending_id={order_id} strategy_id={strategy_id} symbol={symbol} signal={signal_type}")
                _profit_str = f", profit={profit:.4f}" if profit is not None else ""
                _fee_str = f", fee={fills.total_fee:.6f} {fills.fee_ccy}" if fills.total_fee > 0 else ""
                _reason_parts = []
                _reason = str(payload.get("reason") or "").strip()
                if _reason:
                    _reason_parts.append(f"reason={_reason}")
                for _key, _label in (
                    ("stop_loss_price", "sl"),
                    ("take_profit_price", "tp"),
                    ("trailing_stop_price", "trail"),
                ):
                    try:
                        _v = float(payload.get(_key) or 0.0)
                    except Exception:
                        _v = 0.0
                    if _v > 0:
                        _reason_parts.append(f"{_label}={_v:.6f}")
                _reason_str = f", {', '.join(_reason_parts)}" if _reason_parts else ""
                append_strategy_log(
                    strategy_id, "trade",
                    f"Trade executed: {signal_type} {symbol} filled={filled:.6f} @ {avg_price:.6f}{_fee_str}{_profit_str}{_reason_str} (exchange={res.exchange_id})",
                )
        except Exception as e:
            logger.warning(f"record_trade/update_position failed: pending_id={order_id}, err={e}")

        # Notify live results (best-effort; does not affect execution).
        _notify_live_best_effort(
            status="sent",
            exchange_id=res.exchange_id,
            exchange_order_id=res.exchange_order_id,
            price_hint=avg_price if avg_price > 0 else ref_price,
            amount_hint=filled if filled > 0 else amount,
        )

    def _execute_ibkr_order(
        self,
        *,
        order_id: int,
        order_row: Dict[str, Any],
        payload: Dict[str, Any],
        client,  # IBKRClient instance
        strategy_id: int,
        exchange_config: Dict[str, Any],
        _notify_live_best_effort,
        _console_print,
    ) -> None:
        """
        Execute order via Interactive Brokers for US stocks.

        Supports market/limit entries and native attached protection orders.
        """
        signal_type = payload.get("signal_type") or order_row.get("signal_type")
        symbol = payload.get("symbol") or order_row.get("symbol")
        amount = float(payload.get("amount") or order_row.get("amount") or 0.0)
        ref_price = float(payload.get("ref_price") or payload.get("price") or order_row.get("price") or 0.0)

        sig = str(signal_type or "").strip().lower()

        if sig in ("open_long", "add_long", "close_short", "reduce_short", "close_short_stop", "close_short_profit", "close_short_trailing"):
            action = "buy"
        elif sig in ("close_long", "reduce_long", "close_long_stop", "close_long_profit", "close_long_trailing", "open_short", "add_short"):
            action = "sell"
        else:
            self._mark_failed(order_id=order_id, error=f"ibkr_unsupported_signal:{signal_type}")
            _console_print(f"[worker] IBKR order rejected: strategy_id={strategy_id} pending_id={order_id} unsupported signal {signal_type}")
            _notify_live_best_effort(status="failed", error=f"ibkr_unsupported_signal:{signal_type}")
            return

        # Get market type (USStock)
        market_type = str(
            payload.get("market_type") or
            payload.get("market_category") or
            exchange_config.get("market_type") or
            exchange_config.get("market_category") or
            "USStock"
        ).strip()

        try:
            order_type, limit_price = _broker_order_type(payload, ref_price)
            protection_ref = limit_price if order_type == "limit" else ref_price
            stop_price, take_price = _broker_protection_prices(
                payload,
                signal_type=str(signal_type or ""),
                entry_price=protection_ref,
            )
            if stop_price > 0 or take_price > 0:
                result = client.place_bracket_order(
                    symbol=symbol,
                    side=action,
                    quantity=amount,
                    take_profit_price=take_price,
                    stop_loss_price=stop_price,
                    limit_price=limit_price if order_type == "limit" else 0.0,
                    market_type=market_type,
                )
            elif order_type == "limit":
                result = client.place_limit_order(
                    symbol=symbol,
                    side=action,
                    quantity=amount,
                    price=limit_price,
                    market_type=market_type,
                )
            else:
                result = client.place_market_order(
                    symbol=symbol,
                    side=action,
                    quantity=amount,
                    market_type=market_type,
                )

            if not result.success:
                self._mark_failed(order_id=order_id, error=f"ibkr_order_failed:{result.message}")
                _console_print(f"[worker] IBKR order failed: strategy_id={strategy_id} pending_id={order_id} err={result.message}")
                _notify_live_best_effort(status="failed", error=f"ibkr_order_failed:{result.message}")
                append_strategy_log(strategy_id, "error", f"IBKR order failed ({symbol} {signal_type}): {result.message}")
                return

            filled = float(result.filled or 0.0)
            avg_price = float(result.avg_price or 0.0)
            exchange_order_id = str(result.order_id or "")
            commission, commission_ccy = _commission_snapshot(result.raw)
            from app.services.live_trading.fee_quote import fee_to_quote
            commission_quote = fee_to_quote(
                client,
                symbol=str(symbol),
                fee=commission,
                fee_ccy=commission_ccy,
                fill_price=avg_price,
            )

            executed_at = int(time.time())

            # Mark order as sent
            self._mark_sent(
                order_id=order_id,
                note="ibkr_order_sent",
                exchange_id="ibkr",
                exchange_order_id=exchange_order_id,
                exchange_response_json=json.dumps(result.raw or {}, ensure_ascii=False),
                filled=filled,
                avg_price=avg_price,
                executed_at=executed_at if filled > 0 else None,
                final_filled=is_final_fill(amount, filled, avg_price, result.status),
            )
            _console_print(f"[worker] IBKR order sent: strategy_id={strategy_id} pending_id={order_id} order_id={exchange_order_id} filled={filled} avg={avg_price}")

            # Record trade and update position
            try:
                if filled > 0 and avg_price > 0:
                    logger.info(
                        f"IBKR record begin: pending_id={order_id} strategy_id={strategy_id} symbol={symbol} "
                        f"signal={signal_type} filled={filled} avg_price={avg_price}"
                    )
                    profit, matched_entry = persist_strategy_fill(
                        strategy_id=int(strategy_id),
                        symbol=str(symbol),
                        signal_type=str(signal_type),
                        filled=float(filled),
                        avg_price=float(avg_price),
                        exchange_config=exchange_config,
                        market_type=str(market_type or "USStock"),
                        order_id=int(order_id),
                        fill_source="worker_ibkr",
                        commission=commission,
                        commission_ccy=commission_ccy,
                        commission_quote=commission_quote,
                        close_reason=trade_close_reason_from_payload(payload, str(signal_type)),
                        strategy_run_id=int(payload.get("strategy_run_id") or order_row.get("strategy_run_id") or 0),
                        order_intent_id=int(payload.get("order_intent_id") or order_row.get("order_intent_id") or 0),
                        exchange_id="ibkr",
                        exchange_order_id=str(exchange_order_id or ""),
                        raw_fill=result.raw or {},
                    )
                    logger.info(f"IBKR record done: pending_id={order_id} strategy_id={strategy_id} symbol={symbol}")
                    _pstr = f", profit={profit:.4f}" if profit is not None else ""
                    append_strategy_log(
                        strategy_id, "trade",
                        f"Trade executed: {signal_type} {symbol} filled={filled:.6f} @ {avg_price:.6f}{_pstr} (exchange=ibkr)",
                    )
            except Exception as e:
                logger.warning(f"IBKR record_trade/update_position failed: pending_id={order_id}, err={e}")

            # Notify success
            _notify_live_best_effort(
                status="sent",
                exchange_id="ibkr",
                exchange_order_id=exchange_order_id,
                price_hint=avg_price,
                amount_hint=filled,
            )

        except Exception as e:
            logger.error(f"IBKR order execution failed: pending_id={order_id}, strategy_id={strategy_id}, err={e}")
            self._mark_failed(order_id=order_id, error=f"ibkr_exception:{e}")
            _console_print(f"[worker] IBKR order exception: strategy_id={strategy_id} pending_id={order_id} err={e}")
            _notify_live_best_effort(status="failed", error=str(e))
            append_strategy_log(strategy_id, "error", f"IBKR order exception ({symbol} {signal_type}): {e}")
            if is_fatal_exchange_error(str(e)):
                auto_stop_live_strategy(int(strategy_id), str(e), source="ibkr_order")

    def _execute_alpaca_order(
        self,
        *,
        order_id: int,
        order_row: Dict[str, Any],
        payload: Dict[str, Any],
        client,  # AlpacaClient instance
        strategy_id: int,
        exchange_config: Dict[str, Any],
        market_category: str,
        _notify_live_best_effort,
        _console_print,
    ) -> None:
        """
        Execute order via Alpaca for US stocks (USStock) or crypto.

        Supports market/limit orders and Alpaca equity bracket protection.
        """
        signal_type = payload.get("signal_type") or order_row.get("signal_type")
        symbol = payload.get("symbol") or order_row.get("symbol")
        amount = float(payload.get("amount") or order_row.get("amount") or 0.0)
        ref_price = float(payload.get("ref_price") or payload.get("price") or order_row.get("price") or 0.0)

        sig = str(signal_type or "").strip().lower()

        if sig in ("open_long", "add_long", "close_short", "reduce_short", "close_short_stop", "close_short_profit", "close_short_trailing"):
            action = "buy"
        elif sig in ("close_long", "reduce_long", "close_long_stop", "close_long_profit", "close_long_trailing", "open_short", "add_short"):
            action = "sell"
        else:
            self._mark_failed(order_id=order_id, error=f"alpaca_unsupported_signal:{signal_type}")
            _console_print(f"[worker] Alpaca order rejected: strategy_id={strategy_id} pending_id={order_id} unsupported signal {signal_type}")
            _notify_live_best_effort(status="failed", error=f"alpaca_unsupported_signal:{signal_type}")
            return

        # Decide stock vs crypto leg of the Alpaca account based on the
        # strategy's market_category (USStock by default).
        mc = (market_category or "USStock").strip()
        market_type_for_client = "crypto" if mc.lower() in ("crypto", "cryptocurrency") else "USStock"
        if market_type_for_client == "crypto" and "short" in sig:
            self._mark_failed(order_id=order_id, error="alpaca_crypto_short_not_supported")
            _notify_live_best_effort(status="failed", error="alpaca_crypto_short_not_supported")
            return

        try:
            order_type, limit_price = _broker_order_type(payload, ref_price)
            protection_ref = limit_price if order_type == "limit" else ref_price
            stop_price, take_price = _broker_protection_prices(
                payload,
                signal_type=str(signal_type or ""),
                entry_price=protection_ref,
            )
            protection_kwargs = {}
            if market_type_for_client == "USStock" and (stop_price > 0 or take_price > 0):
                protection_kwargs = {
                    "stop_loss_price": stop_price,
                    "take_profit_price": take_price,
                }
            if order_type == "limit":
                result = client.place_limit_order(
                    symbol=symbol,
                    side=action,
                    quantity=amount,
                    price=limit_price,
                    market_type=market_type_for_client,
                    **protection_kwargs,
                )
            else:
                result = client.place_market_order(
                    symbol=symbol,
                    side=action,
                    quantity=amount,
                    market_type=market_type_for_client,
                    **protection_kwargs,
                )

            if not result.success:
                self._mark_failed(order_id=order_id, error=f"alpaca_order_failed:{result.message}")
                _console_print(f"[worker] Alpaca order failed: strategy_id={strategy_id} pending_id={order_id} err={result.message}")
                _notify_live_best_effort(status="failed", error=f"alpaca_order_failed:{result.message}")
                append_strategy_log(strategy_id, "error", f"Alpaca order failed ({symbol} {signal_type}): {result.message}")
                return

            filled = float(result.filled or 0.0)
            avg_price = float(result.avg_price or 0.0)
            exchange_order_id = str(result.order_id or "")
            commission, commission_ccy = _commission_snapshot(result.raw)
            from app.services.live_trading.fee_quote import fee_to_quote
            commission_quote = fee_to_quote(
                client,
                symbol=str(symbol),
                fee=commission,
                fee_ccy=commission_ccy,
                fill_price=avg_price,
            )

            if avg_price <= 0 and ref_price > 0:
                if filled > 0:
                    logger.warning(
                        f"[worker] Alpaca order avg_price=0, using ref_price={ref_price} as fallback: "
                        f"strategy_id={strategy_id} pending_id={order_id}"
                    )
                    avg_price = ref_price
                else:
                    logger.info(
                        f"[worker] Alpaca order submitted but not filled yet: "
                        f"strategy_id={strategy_id} pending_id={order_id} status={result.status}"
                    )

            executed_at = int(time.time())

            self._mark_sent(
                order_id=order_id,
                note="alpaca_order_sent",
                exchange_id="alpaca",
                exchange_order_id=exchange_order_id,
                exchange_response_json=json.dumps(result.raw or {}, ensure_ascii=False),
                filled=filled,
                avg_price=avg_price,
                executed_at=executed_at if filled > 0 else None,
                final_filled=is_final_fill(amount, filled, avg_price, result.status),
            )
            _console_print(
                f"[worker] Alpaca order sent: strategy_id={strategy_id} pending_id={order_id} "
                f"order_id={exchange_order_id} filled={filled} avg={avg_price}"
            )

            try:
                if filled > 0 and avg_price > 0:
                    profit, matched_entry = persist_strategy_fill(
                        strategy_id=int(strategy_id),
                        symbol=str(symbol),
                        signal_type=str(signal_type),
                        filled=float(filled),
                        avg_price=float(avg_price),
                        exchange_config=exchange_config,
                        market_type=str(market_type_for_client or "USStock"),
                        order_id=int(order_id),
                        fill_source="worker_alpaca",
                        commission=commission,
                        commission_ccy=commission_ccy,
                        commission_quote=commission_quote,
                        close_reason=trade_close_reason_from_payload(payload, str(signal_type)),
                        strategy_run_id=int(payload.get("strategy_run_id") or order_row.get("strategy_run_id") or 0),
                        order_intent_id=int(payload.get("order_intent_id") or order_row.get("order_intent_id") or 0),
                        exchange_id="alpaca",
                        exchange_order_id=str(exchange_order_id or ""),
                        raw_fill=result.raw or {},
                    )
                    logger.info(f"Alpaca record done: pending_id={order_id} strategy_id={strategy_id} symbol={symbol}")
                    _pstr = f", profit={profit:.4f}" if profit is not None else ""
                    append_strategy_log(
                        strategy_id, "trade",
                        f"Trade executed: {signal_type} {symbol} filled={filled:.6f} @ {avg_price:.6f}{_pstr} (exchange=alpaca)",
                    )
                else:
                    append_strategy_log(
                        strategy_id, "info",
                        f"Alpaca order submitted: {signal_type} {symbol} status={result.status or 'submitted'}, awaiting fill",
                    )
            except Exception as e:
                logger.warning(f"Alpaca record_trade/update_position failed: pending_id={order_id}, err={e}")

            _notify_live_best_effort(
                status="sent",
                exchange_id="alpaca",
                exchange_order_id=exchange_order_id,
                price_hint=avg_price,
                amount_hint=filled,
            )

        except Exception as e:
            logger.error(f"Alpaca order execution failed: pending_id={order_id}, strategy_id={strategy_id}, err={e}")
            self._mark_failed(order_id=order_id, error=f"alpaca_exception:{e}")
            _console_print(f"[worker] Alpaca order exception: strategy_id={strategy_id} pending_id={order_id} err={e}")
            _notify_live_best_effort(status="failed", error=str(e))
            append_strategy_log(strategy_id, "error", f"Alpaca order exception ({symbol} {signal_type}): {e}")

    def _mark_sent(
        self,
        order_id: int,
        note: str = "",
        exchange_id: str = "",
        exchange_order_id: str = "",
        exchange_response_json: str = "",
        filled: float = 0.0,
        avg_price: float = 0.0,
        executed_at: Optional[int] = None,
        final_filled: bool = False,
    ) -> None:
        exchange_response_json = _redact_exchange_json(exchange_response_json)
        with get_db_connection() as db:
            cur = db.cursor()
            # Use NOW() for timestamp fields; executed_at is set to NOW() if provided, else NULL
            cur.execute(
                """
                UPDATE pending_orders
                SET status = CASE WHEN %s THEN 'filled' ELSE 'sent' END,
                    last_error = %s,
                    dispatch_note = %s,
                    sent_at = NOW(),
                    executed_at = CASE WHEN %s THEN NOW() ELSE NULL END,
                    exchange_id = %s,
                    exchange_order_id = %s,
                    exchange_response_json = %s,
                    filled = %s,
                    avg_price = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (
                    bool(final_filled),
                    "",
                    str(note or ""),
                    executed_at is not None,  # Boolean flag for CASE WHEN
                    str(exchange_id or ""),
                    str(exchange_order_id or ""),
                    str(exchange_response_json or ""),
                    float(filled or 0.0),
                    float(avg_price or 0.0),
                    int(order_id),
                ),
            )
            cur.execute(
                """
                UPDATE strategy_order_intents soi
                SET status = CASE
                        WHEN %s THEN 'filled'
                        WHEN %s > 0 THEN 'partially_filled'
                        ELSE 'submitted'
                    END,
                    exchange_order_id = COALESCE(NULLIF(po.exchange_order_id, ''), soi.exchange_order_id),
                    updated_at = NOW()
                FROM pending_orders po
                WHERE po.id = %s
                  AND po.order_intent_id = soi.id
                """,
                (bool(final_filled), float(filled or 0.0), int(order_id)),
            )
            db.commit()
            cur.close()

    def _mark_failed(self, order_id: int, error: str) -> None:
        from app.services.live_trading.partner_attribution import redact_partner_attribution

        error = str(redact_partner_attribution(str(error or "failed")))
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE pending_orders
                SET status = 'failed',
                    last_error = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (str(error or "failed"), int(order_id)),
            )
            cur.execute(
                """
                UPDATE strategy_order_intents soi
                SET status = 'rejected',
                    updated_at = NOW()
                FROM pending_orders po
                WHERE po.id = %s
                  AND po.order_intent_id = soi.id
                """,
                (int(order_id),),
            )
            db.commit()
            cur.close()

    def _mark_deferred(self, order_id: int, reason: str) -> None:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                UPDATE pending_orders
                SET status = 'deferred',
                    last_error = %s,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (str(reason or "deferred"), int(order_id)),
            )
            db.commit()
            cur.close()
