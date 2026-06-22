"""Poll exchange for grid resting order fills (rate-limited for multi-tenant scale)."""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from app.services.grid.exchange_orders import query_grid_order_fill
from app.services.grid.resting_orders_repo import GridRestingOrder, GridRestingOrderRepository
from app.services.grid.runner import get_runner
from app.services.exchange_execution import resolve_exchange_config
from app.services.live_trading.factory import create_client
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


class GridFillPoller:
    """
    Background thread: poll open grid resting orders and dispatch fills.

    Designed for hundreds/thousands of concurrent grid bots:
    - Caps orders processed per loop (GRID_FILL_MAX_ORDERS_PER_CYCLE)
    - Per-order minimum interval (GRID_FILL_MIN_ORDER_INTERVAL_SEC)
    - Per-credential request budget per minute (GRID_FILL_MAX_REQ_PER_CREDENTIAL_PER_MIN)
    - Reuses one exchange client per credential within each cycle
    """

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._repo = GridRestingOrderRepository()
        self._interval = max(1.0, _env_float("GRID_FILL_POLL_SEC", 3.0))
        self._min_order_interval = max(2.0, _env_float("GRID_FILL_MIN_ORDER_INTERVAL_SEC", 5.0))
        self._max_orders_per_cycle = max(10, _env_int("GRID_FILL_MAX_ORDERS_PER_CYCLE", 150))
        self._max_req_per_cred_per_min = max(10, _env_int("GRID_FILL_MAX_REQ_PER_CREDENTIAL_PER_MIN", 120))
        self._last_poll_by_order: Dict[int, float] = {}
        self._credential_req_ts: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="grid-fill-poller", daemon=True)
        self._thread.start()
        logger.info(
            "GridFillPoller started interval=%ss min_order=%ss max/cycle=%s max_req/cred/min=%s",
            self._interval,
            self._min_order_interval,
            self._max_orders_per_cycle,
            self._max_req_per_cred_per_min,
        )

    def stop(self) -> None:
        self._stop.set()

    def is_alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as e:
                logger.warning("GridFillPoller error: %s", e)
            self._stop.wait(self._interval)

    def _credential_key(self, runner) -> str:
        ec = runner.exchange_config if isinstance(runner.exchange_config, dict) else {}
        cid = ec.get("credential_id")
        if cid is not None and str(cid).strip():
            return f"cred:{cid}"
        ex = str(ec.get("exchange_id") or "").strip().lower()
        return f"sid:{runner.strategy_id}:{ex or 'unknown'}"

    def _allow_credential_request(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            window = self._credential_req_ts[key]
            window[:] = [t for t in window if now - t < 60.0]
            if len(window) >= self._max_req_per_cred_per_min:
                return False
            window.append(now)
            return True

    def _select_orders(self, open_orders: List[GridRestingOrder]) -> List[GridRestingOrder]:
        now = time.time()
        candidates: List[Tuple[int, float, GridRestingOrder]] = []
        for order in open_orders:
            sid = int(order.strategy_id)
            if not get_runner(sid):
                continue
            oid = int(order.id or 0)
            if not oid:
                continue
            last = self._last_poll_by_order.get(oid, 0.0)
            min_iv = self._min_order_interval * (0.5 if str(order.status or "") == "partial" else 1.0)
            if now - last < min_iv:
                continue
            prio = 0 if str(order.status or "") == "partial" else 1
            candidates.append((prio, last, order))
        candidates.sort(key=lambda x: (x[0], x[1]))
        return [o for _, _, o in candidates[: self._max_orders_per_cycle]]

    def _poll_once(self) -> None:
        open_orders = self._repo.list_open()
        if not open_orders:
            return
        selected = self._select_orders(open_orders)
        if not selected:
            return

        clients: Dict[str, object] = {}
        for order in selected:
            sid = int(order.strategy_id)
            runner = get_runner(sid)
            if not runner:
                continue
            cred_key = self._credential_key(runner)
            if not self._allow_credential_request(cred_key):
                continue
            cfg = runner.engine.cfg
            client = clients.get(cred_key)
            if client is None:
                try:
                    ex_cfg = resolve_exchange_config(
                        runner.exchange_config if isinstance(runner.exchange_config, dict) else {},
                        user_id=int(getattr(runner, "user_id", 0) or 1),
                    )
                    client = create_client(ex_cfg, market_type=cfg.market_type)
                    clients[cred_key] = client
                except Exception as e:
                    logger.debug("grid poller client sid=%s: %s", sid, e)
                    continue
            oid = int(order.id or 0)
            self._last_poll_by_order[oid] = time.time()
            self._poll_order(runner, client, order, cfg.market_type)

    def _poll_order(self, runner, client, order, market_type: str) -> None:
        filled, avg, status = query_grid_order_fill(
            client,
            symbol=order.symbol,
            market_type=market_type,
            exchange_order_id=order.exchange_order_id,
            client_order_id=order.client_order_id,
            exchange_config=runner.exchange_config if isinstance(runner.exchange_config, dict) else {},
        )
        if status == "unknown":
            return
        oid = int(order.id or 0)
        if not oid:
            return
        if status in ("open", "partial"):
            if filled > float(order.processed_fill_qty or 0):
                self._repo.update_status(
                    oid,
                    status="partial" if status == "partial" or filled < float(order.quantity or 0) else "open",
                    filled_quantity=filled,
                    avg_fill_price=avg,
                )
            return
        if status == "cancelled":
            self._repo.update_status(oid, status="cancelled", filled_quantity=filled, avg_fill_price=avg)
            return
        if status != "filled" and filled <= 0:
            return

        prev = float(order.processed_fill_qty or 0)
        total_filled = float(filled or order.filled_quantity or 0)
        if total_filled <= 0 and status == "filled":
            total_filled = float(order.quantity or 0)

        # Idempotent: already fully processed.
        if prev >= total_filled and total_filled > 0:
            if str(order.status or "") != "filled":
                self._repo.update_status(
                    oid,
                    status="filled",
                    filled_quantity=total_filled,
                    avg_fill_price=avg,
                    processed_fill_qty=prev,
                )
            return

        new_fill = total_filled - prev
        if new_fill <= 0:
            if status == "filled":
                self._repo.update_status(
                    oid,
                    status="filled",
                    filled_quantity=total_filled,
                    avg_fill_price=avg,
                    processed_fill_qty=prev,
                )
            return

        try:
            runner.engine.on_order_filled(order, new_fill, avg or float(order.price or 0))
        except Exception as e:
            logger.warning("grid on_order_filled sid=%s oid=%s: %s", order.strategy_id, oid, e)
            return

        self._repo.update_status(
            oid,
            status="filled",
            filled_quantity=total_filled,
            avg_fill_price=avg,
            processed_fill_qty=total_filled,
        )

    def sync_strategy(self, strategy_id: int) -> int:
        """Poll every open order for one strategy immediately (UI refresh)."""
        sid = int(strategy_id)
        open_orders = self._repo.list_open(sid)
        unprocessed = self._repo.list_unprocessed(sid)
        merged: Dict[int, GridRestingOrder] = {}
        for order in open_orders + unprocessed:
            oid = int(order.id or 0)
            if oid > 0:
                merged[oid] = order
        open_orders = list(merged.values())
        if not open_orders:
            return 0
        runner = get_runner(sid)
        if not runner:
            return 0
        cfg = runner.engine.cfg
        cred_key = self._credential_key(runner)
        try:
            from app.services.exchange_execution import resolve_exchange_config
            from app.services.live_trading.factory import create_client

            ex_cfg = resolve_exchange_config(
                runner.exchange_config if isinstance(runner.exchange_config, dict) else {},
                user_id=int(getattr(runner, "user_id", 0) or 1),
            )
            client = create_client(ex_cfg, market_type=cfg.market_type)
        except Exception as e:
            logger.debug("grid sync_strategy client sid=%s: %s", sid, e)
            return 0
        n = 0
        for order in open_orders:
            if not self._allow_credential_request(cred_key):
                break
            self._last_poll_by_order[int(order.id or 0)] = time.time()
            self._poll_order(runner, client, order, cfg.market_type)
            n += 1
        return n


_poller: Optional[GridFillPoller] = None


def get_grid_fill_poller() -> GridFillPoller:
    global _poller
    if _poller is None:
        _poller = GridFillPoller()
    return _poller


def sync_strategy_grid_orders(strategy_id: int) -> int:
    """
    Force one exchange poll for all open resting orders of a strategy.
    Used by the UI refresh button so status/filled qty update immediately.
    """
    poller = get_grid_fill_poller()
    return poller.sync_strategy(int(strategy_id))
