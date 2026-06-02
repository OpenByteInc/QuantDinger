"""Grid engine: resting limit orders, cell pairing, initial position."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.grid.config import GridBotConfig
from app.services.grid.exchange_orders import (
    cancel_grid_order,
    execute_grid_market_order,
    make_grid_client_order_id,
    make_grid_initial_client_order_id,
    place_grid_limit_order,
    wait_grid_market_fill,
)
from app.services.grid.fill_handler import record_grid_market_fill
from app.services.grid.levels import GridCellSpec, generate_cells, generate_levels
from app.services.grid.resting_orders_repo import GridRestingOrder, GridRestingOrderRepository
from app.services.grid.runtime_state import load_grid_resting_state, persist_grid_resting_state
from app.services.live_trading.grid_cells import GridCellRepository, GridCellState
from app.utils.logger import get_logger
from app.utils.strategy_runtime_logs import append_strategy_log

logger = get_logger(__name__)

MarketSignalFn = Callable[[str, float, float, str], bool]
# (signal_type, usdt_amount, price, reason) -> success


class GridEngine:
    def __init__(
        self,
        strategy_id: int,
        symbol: str,
        trading_config: Dict[str, Any],
        exchange_config: Dict[str, Any],
        *,
        create_client_fn: Callable[[], Any],
        enqueue_market: MarketSignalFn,
    ) -> None:
        self.strategy_id = int(strategy_id)
        self.symbol = str(symbol or "")
        self.trading_config = trading_config if isinstance(trading_config, dict) else {}
        self.exchange_config = exchange_config if isinstance(exchange_config, dict) else {}
        self.cfg = GridBotConfig.from_trading_config(self.trading_config)
        self._create_client = create_client_fn
        self._enqueue_market = enqueue_market
        self._orders = GridRestingOrderRepository()
        self._cells = GridCellRepository()
        self._bootstrapped = False
        self._initial_done = False
        self._paused_entries = False
        self._runtime_params: Dict[str, Any] = {}
        gs = load_grid_resting_state(self.trading_config)
        if gs.get("initial_market_done"):
            self._initial_done = True
        self._consecutive_order_errors = 0
        self._stop_requested = False
        self._last_initial_attempt_ts = 0.0
        self._initial_retry_sec = 30.0
        self._initial_market_attempts = 0
        self._initial_market_max_attempts = 3

    @property
    def stop_requested(self) -> bool:
        return bool(self._stop_requested)

    def _record_order_error(self, purpose: str, exc: Exception) -> None:
        if self._stop_requested:
            return
        msg = str(exc or "")
        logger.warning(
            "Grid place limit failed sid=%s cell purpose=%s: %s",
            self.strategy_id,
            purpose,
            msg,
        )
        append_strategy_log(self.strategy_id, "error", f"Grid limit failed {purpose}: {msg}")
        self._consecutive_order_errors += 1
        try:
            from app.services.strategy_lifecycle import maybe_auto_stop_on_exchange_error

            threshold = 5
            try:
                import os

                threshold = max(1, int(os.getenv("GRID_ORDER_ERROR_STOP_THRESHOLD", "5")))
            except Exception:
                threshold = 5
            if maybe_auto_stop_on_exchange_error(
                self.strategy_id,
                msg,
                source="grid_order",
                consecutive_failures=self._consecutive_order_errors,
                consecutive_threshold=threshold,
            ):
                self._stop_requested = True
                self._paused_entries = True
        except Exception as e:
            logger.debug("grid auto-stop check sid=%s: %s", self.strategy_id, e)

    def _initial_capital_usdt(self) -> float:
        init_cap = float(self.trading_config.get("initial_capital") or 0)
        if init_cap <= 0:
            init_cap = float(self.trading_config.get("_grid_budget") or 0)
        if init_cap <= 0:
            init_cap = self.cfg.amount_per_grid * max(1, self.cfg.grid_count - 1) * 2
        return init_cap

    def set_runtime_params(self, params: Dict[str, Any]) -> None:
        self._runtime_params = dict(params or {})

    def _qty_from_usdt(self, usdt: float, price: float) -> float:
        if price <= 0 or usdt <= 0:
            return 0.0
        lev = self.cfg.leverage if self.cfg.market_type != "spot" else 1.0
        return float(usdt) * lev / float(price)

    def _levels_and_cells(self) -> Tuple[List[float], List[GridCellSpec]]:
        upper, lower = self.cfg.effective_bounds(self._runtime_params)
        levels = generate_levels(lower, upper, self.cfg.grid_count, self.cfg.grid_mode)
        return levels, generate_cells(levels)

    def bootstrap(self, current_price: float) -> Tuple[bool, str]:
        if current_price <= 0:
            return False, "invalid price"
        levels, cells = self._levels_and_cells()
        if not cells:
            return False, "failed to generate grid cells"
        self._cells.bootstrap_idle_cells(self.strategy_id, self.symbol, levels)
        self._bootstrapped = True
        append_strategy_log(
            self.strategy_id,
            "info",
            f"Grid resting bootstrap: {len(cells)} cells, {self.cfg.grid_direction}, "
            f"bounds [{levels[0]:.4f}, {levels[-1]:.4f}]",
        )
        return True, ""

    def _has_initial_market_trade(self) -> bool:
        """True when a verified grid initial market fill was recorded in L2."""
        try:
            from app.utils.db import get_db_connection

            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT 1 FROM qd_strategy_trades
                    WHERE strategy_id = %s
                      AND close_reason IN ('grid_initial_long', 'grid_initial_short')
                    LIMIT 1
                    """,
                    (int(self.strategy_id),),
                )
                row = cur.fetchone()
                cur.close()
                return bool(row)
        except Exception as e:
            logger.debug("grid initial trade check sid=%s: %s", self.strategy_id, e)
            return False

    def _leg_position_qty(self, pos_side: str) -> float:
        try:
            from app.services.live_trading.position_query import query_exchange_position_size

            client = self._create_client()
            return float(
                query_exchange_position_size(
                    client=client,
                    symbol=self.symbol,
                    pos_side=str(pos_side or ""),
                    market_type=str(self.cfg.market_type or "swap"),
                    exchange_config=self.exchange_config if isinstance(self.exchange_config, dict) else {},
                )
                or 0.0
            )
        except Exception as e:
            logger.debug("grid leg position qty sid=%s %s: %s", self.strategy_id, pos_side, e)
            return 0.0

    def _target_initial_usdt(self) -> float:
        return self._initial_capital_usdt() * self.cfg.initial_position_pct

    def _target_initial_base_qty(self, price: float) -> float:
        return self._qty_from_usdt(self._target_initial_usdt(), price)

    def _pos_side_for_signal(self, signal_type: str) -> str:
        sig = str(signal_type or "").strip().lower()
        if "short" in sig:
            return "short"
        return "long"

    def _probe_initial_client_order_fill(
        self,
        client: Any,
        client_order_id: str,
        signal_type: str,
        reason: str,
        price: float,
    ) -> bool:
        """If a prior initial market order already filled, sync local state without re-placing."""
        coid = str(client_order_id or "").strip()
        if not coid or client is None:
            return False
        try:
            filled, avg = wait_grid_market_fill(
                client,
                symbol=self.symbol,
                market_type=self.cfg.market_type,
                exchange_config=self.exchange_config,
                exchange_order_id="",
                client_order_id=coid,
                max_wait_sec=3.0,
            )
        except Exception as e:
            logger.debug("grid initial probe sid=%s: %s", self.strategy_id, e)
            return False
        if filled <= 0:
            return False
        px = float(avg or price or 0)
        record_grid_market_fill(
            self.strategy_id,
            self.symbol,
            signal_type,
            filled,
            px,
            self.trading_config,
            reason=reason,
        )
        append_strategy_log(
            self.strategy_id,
            "info",
            f"Grid initial market recovered from client order: {filled:.6f} @ {px:.4f}",
        )
        return True

    def _stop_initial_market_retries(self, *, reason: str = "") -> None:
        self._initial_done = True
        persist_grid_resting_state(self.strategy_id, {"initial_market_done": True})
        if reason:
            append_strategy_log(self.strategy_id, "warning", reason)

    def _try_recover_initial_from_exchange(
        self,
        current_price: float,
        signal_type: str,
        reason: str,
    ) -> bool:
        """When fill polling fails but the exchange already holds the initial leg, sync local state."""
        if current_price <= 0:
            return False
        pos_side = self._pos_side_for_signal(signal_type)
        target_qty = self._target_initial_base_qty(current_price)
        if target_qty <= 0:
            return False
        exch_qty = self._leg_position_qty(pos_side)
        if exch_qty <= 0:
            return False
        if exch_qty < target_qty * 0.85:
            return False
        record_qty = min(exch_qty, target_qty)
        if exch_qty > target_qty * 1.05:
            append_strategy_log(
                self.strategy_id,
                "error",
                f"Grid initial over-filled on exchange: {exch_qty:.6f} > target {target_qty:.6f}; "
                "stopping initial retries (check OKX position manually)",
            )
        if self._has_initial_market_trade():
            self._initial_done = True
            persist_grid_resting_state(self.strategy_id, {"initial_market_done": True})
            return True
        record_grid_market_fill(
            self.strategy_id,
            self.symbol,
            signal_type,
            record_qty,
            current_price,
            self.trading_config,
            reason=reason,
        )
        append_strategy_log(
            self.strategy_id,
            "info",
            f"Grid initial market recovered from exchange: {record_qty:.6f} {pos_side} @ ~{current_price:.4f}",
        )
        self._initial_done = True
        persist_grid_resting_state(self.strategy_id, {"initial_market_done": True})
        return True

    def _sync_initial_market_leg(
        self,
        signal_type: str,
        usdt: float,
        price: float,
        reason: str,
        *,
        client_order_id: Optional[str] = None,
    ) -> bool:
        lev = self.cfg.leverage if self.cfg.market_type != "spot" else 1.0
        qty = float(usdt or 0) * lev / float(price) if price > 0 else 0.0
        if qty <= 0:
            return False
        if self._try_recover_initial_from_exchange(price, signal_type, reason):
            return True
        coid = str(client_order_id or "").strip() or make_grid_initial_client_order_id(
            self.strategy_id,
            leg=self._pos_side_for_signal(signal_type),
        )
        pos_side = self._pos_side_for_signal(signal_type)
        exch_qty = self._leg_position_qty(pos_side)
        if qty > 0 and exch_qty >= qty * 0.85:
            return self._try_recover_initial_from_exchange(price, signal_type, reason)
        try:
            client = self._create_client()
            if self._probe_initial_client_order_fill(client, coid, signal_type, reason, price):
                return True
        except Exception as e:
            logger.debug("grid initial pre-place probe sid=%s: %s", self.strategy_id, e)
        try:
            client = self._create_client()
            ok, filled, avg = execute_grid_market_order(
                client,
                symbol=self.symbol,
                signal_type=signal_type,
                quantity=qty,
                market_type=self.cfg.market_type,
                exchange_config=self.exchange_config,
                leverage=self.cfg.leverage,
                client_order_id=coid,
            )
        except Exception as e:
            logger.warning("grid initial market sid=%s %s: %s", self.strategy_id, signal_type, e)
            append_strategy_log(self.strategy_id, "error", f"Grid initial market failed {signal_type}: {e}")
            return self._try_recover_initial_from_exchange(price, signal_type, reason)
        if not ok or filled <= 0:
            if self._try_recover_initial_from_exchange(price, signal_type, reason):
                return True
            append_strategy_log(
                self.strategy_id,
                "warning",
                f"Grid initial market {signal_type} not filled (qty={qty:.6f} @ {price:.4f})",
            )
            return False
        self._initial_market_attempts = 0
        record_grid_market_fill(
            self.strategy_id,
            self.symbol,
            signal_type,
            filled,
            avg,
            self.trading_config,
            reason=reason,
        )
        append_strategy_log(
            self.strategy_id,
            "info",
            f"Grid initial market {signal_type}: filled {filled:.6f} @ {avg:.4f}",
        )
        return True

    def run_initial_market_position(self, current_price: float) -> bool:
        if self.cfg.initial_position_pct <= 0:
            self._initial_done = True
            return True
        if self._initial_done:
            if self._has_initial_market_trade():
                return True
            # Stale flag from enqueue-only era — retry initial market.
            self._initial_done = False
            persist_grid_resting_state(self.strategy_id, {"initial_market_done": False})
        init_cap = self._initial_capital_usdt()
        usdt = init_cap * self.cfg.initial_position_pct
        if usdt <= 0:
            self._initial_done = True
            persist_grid_resting_state(self.strategy_id, {"initial_market_done": True})
            return True
        direction = self.cfg.grid_direction
        if direction == "short":
            sig = "open_short"
            reason = "grid_initial_short"
        elif direction == "neutral":
            sig = "open_long"
            reason = "grid_initial_long"
        else:
            sig = "open_long"
            reason = "grid_initial_long"

        if self._try_recover_initial_from_exchange(current_price, sig, reason):
            return True

        pos_side = self._pos_side_for_signal(sig)
        target_qty = self._target_initial_base_qty(current_price)
        exch_qty = self._leg_position_qty(pos_side)
        if target_qty > 0 and exch_qty >= target_qty * 0.85:
            if self._try_recover_initial_from_exchange(current_price, sig, reason):
                return True
        if target_qty > 0 and exch_qty > target_qty * 1.05:
            self._stop_initial_market_retries(
                reason=(
                    f"Grid initial market halted: exchange {pos_side} {exch_qty:.6f} "
                    f"exceeds target {target_qty:.6f}"
                ),
            )
            if not self._has_initial_market_trade():
                record_grid_market_fill(
                    self.strategy_id,
                    self.symbol,
                    sig,
                    min(exch_qty, target_qty),
                    current_price,
                    self.trading_config,
                    reason=reason,
                )
            return True

        now = time.time()
        if now - float(self._last_initial_attempt_ts or 0.0) < float(self._initial_retry_sec):
            return False
        self._last_initial_attempt_ts = now
        self._initial_market_attempts += 1
        if self._initial_market_attempts > self._initial_market_max_attempts:
            if self._try_recover_initial_from_exchange(current_price, sig, reason):
                return True
            if exch_qty > 0:
                self._stop_initial_market_retries(
                    reason=(
                        f"Grid initial market halted after {self._initial_market_max_attempts} attempts; "
                        f"exchange {pos_side}={exch_qty:.6f}"
                    ),
                )
                return True
            append_strategy_log(
                self.strategy_id,
                "error",
                f"Grid initial market abandoned after {self._initial_market_max_attempts} attempts",
            )
            self._stop_initial_market_retries()
            return False

        if direction == "short":
            ok = self._sync_initial_market_leg(
                "open_short",
                usdt,
                current_price,
                "grid_initial_short",
                client_order_id=make_grid_initial_client_order_id(self.strategy_id, leg="short"),
            )
        elif direction == "neutral":
            half = usdt / 2.0
            ok_long = self._sync_initial_market_leg(
                "open_long",
                half,
                current_price,
                "grid_initial_long",
                client_order_id=make_grid_initial_client_order_id(self.strategy_id, leg="long"),
            )
            ok_short = self._sync_initial_market_leg(
                "open_short",
                half,
                current_price,
                "grid_initial_short",
                client_order_id=make_grid_initial_client_order_id(self.strategy_id, leg="short"),
            )
            ok = ok_long or ok_short
            if ok:
                append_strategy_log(
                    self.strategy_id,
                    "info",
                    f"Grid initial neutral market: {usdt:.2f} USDT split @ {current_price:.4f}",
                )
        else:
            ok = self._sync_initial_market_leg(
                "open_long",
                usdt,
                current_price,
                "grid_initial_long",
                client_order_id=make_grid_initial_client_order_id(self.strategy_id, leg="long"),
            )
        if ok:
            self._initial_done = True
            persist_grid_resting_state(self.strategy_id, {"initial_market_done": True})
        return ok

    def _cell_state_by_index(self) -> Dict[int, GridCellState]:
        rows = self._cells.list_cells(self.strategy_id, self.symbol)
        out: Dict[int, GridCellState] = {}
        for row in rows or []:
            try:
                out[int(row.cell_index)] = GridCellState.parse(row.state)
            except Exception:
                continue
        return out

    def _cell_allows_entry(self, cell_index: int, purpose: str, cell_states: Dict[int, GridCellState]) -> bool:
        """Only IDLE cells without a paired exit/entry working order may get a new entry limit."""
        idx = int(cell_index)
        st = cell_states.get(idx, GridCellState.IDLE)
        if purpose == "long_entry":
            if st != GridCellState.IDLE:
                return False
            if self._orders.has_open_for_cell(self.strategy_id, idx, "long_entry"):
                return False
            if self._orders.has_open_for_cell(self.strategy_id, idx, "long_exit"):
                return False
            return True
        if purpose == "short_entry":
            if st != GridCellState.IDLE:
                return False
            if self._orders.has_open_for_cell(self.strategy_id, idx, "short_entry"):
                return False
            if self._orders.has_open_for_cell(self.strategy_id, idx, "short_exit"):
                return False
            return True
        return False

    def sync_grid_orders(self, current_price: float) -> int:
        if not self._bootstrapped or self._paused_entries or current_price <= 0:
            return 0
        if self._runtime_params.get("waterfall_pause"):
            return 0
        self._dedupe_open_entry_orders("long_entry")
        self._dedupe_open_entry_orders("short_entry")
        _, cells = self._levels_and_cells()
        cell_states = self._cell_state_by_index()
        placed = 0
        direction = self.cfg.grid_direction
        for cell in cells:
            if direction in ("long", "neutral") and cell.lower_price < current_price:
                if self._cell_allows_entry(cell.index, "long_entry", cell_states):
                    if self._place_limit(cell, "long_entry", "buy", cell.lower_price, reduce_only=False, pos_side="long"):
                        placed += 1
                        cell_states[int(cell.index)] = GridCellState.BUY_OPEN
            if direction in ("short", "neutral") and cell.upper_price > current_price:
                if self._cell_allows_entry(cell.index, "short_entry", cell_states):
                    if self._place_limit(cell, "short_entry", "sell", cell.upper_price, reduce_only=False, pos_side="short"):
                        placed += 1
                        cell_states[int(cell.index)] = GridCellState.SELL_OPEN
        return placed

    def _active_cell_for_price(
        self,
        cells: list,
        current_price: float,
        direction: str,
    ) -> Optional[GridCellSpec]:
        if not cells or current_price <= 0:
            return None
        for cell in cells:
            if cell.lower_price < current_price <= cell.upper_price:
                return cell
        if direction == "long":
            for cell in reversed(cells):
                if cell.lower_price < current_price:
                    return cell
        elif direction == "short":
            for cell in cells:
                if cell.upper_price > current_price:
                    return cell
        return None

    def _open_exit_qty(self, purpose: str) -> float:
        total = 0.0
        for order in self._orders.list_open(self.strategy_id):
            if str(order.purpose or "") != purpose:
                continue
            rem = float(order.quantity or 0) - float(order.processed_fill_qty or 0)
            if rem > 0:
                total += rem
        return total

    def _open_orders_for_cell(self, cell_index: int, purpose: str) -> List[GridRestingOrder]:
        out: List[GridRestingOrder] = []
        for order in self._orders.list_open(self.strategy_id):
            if int(order.cell_index) != int(cell_index):
                continue
            if str(order.purpose or "") != purpose:
                continue
            out.append(order)
        return out

    def _normalize_grid_base_qty(self, qty: float, price: float) -> float:
        """Floor to exchange lot/min; return 0 when below tradable minimum."""
        if qty <= 0 or price <= 0:
            return 0.0
        try:
            client = self._create_client()
            from app.services.live_trading.okx import OkxClient
            from app.services.live_trading.symbols import to_okx_spot_inst_id, to_okx_swap_inst_id

            if isinstance(client, OkxClient):
                mt = str(self.cfg.market_type or "swap").strip().lower()
                inst_id = (
                    to_okx_spot_inst_id(self.symbol)
                    if mt == "spot"
                    else to_okx_swap_inst_id(self.symbol)
                )
                norm, _prec = client._normalize_order_size(
                    inst_id=inst_id,
                    market_type=mt,
                    size=float(qty),
                )
                if float(norm or 0) <= 0:
                    return 0.0
                if mt != "spot":
                    inst = client.get_instrument(
                        inst_type="SWAP",
                        inst_id=inst_id,
                    )
                    ct_val = float((inst or {}).get("ctVal") or 0.0)
                    if ct_val > 0:
                        return float(norm) * ct_val
                return float(norm)
        except Exception as e:
            logger.debug("grid normalize qty sid=%s: %s", self.strategy_id, e)
        return float(qty)

    def _dedupe_open_entry_orders(self, purpose: str) -> None:
        """Cancel duplicate open entry limits on the same cell (recovery from prior stacking)."""
        by_cell: Dict[int, List[GridRestingOrder]] = {}
        for order in self._orders.list_open(self.strategy_id):
            if str(order.purpose or "") != purpose:
                continue
            by_cell.setdefault(int(order.cell_index), []).append(order)
        for cell_idx, orders in by_cell.items():
            if len(orders) <= 1:
                continue
            orders.sort(
                key=lambda o: float(o.quantity or 0) - float(o.processed_fill_qty or 0),
                reverse=True,
            )
            for extra in orders[1:]:
                try:
                    client = self._create_client()
                    cancel_grid_order(
                        client,
                        symbol=self.symbol,
                        market_type=self.cfg.market_type,
                        exchange_order_id=extra.exchange_order_id,
                        client_order_id=extra.client_order_id,
                        exchange_config=self.exchange_config,
                    )
                except Exception as e:
                    logger.debug("grid entry dedupe cancel sid=%s cell=%s: %s", self.strategy_id, cell_idx, e)
                if extra.id:
                    self._orders.update_status(int(extra.id), status="cancelled")
                append_strategy_log(
                    self.strategy_id,
                    "warning",
                    f"Grid deduped duplicate {purpose} on cell={cell_idx} "
                    f"(kept largest entry, cancelled oid={extra.exchange_order_id or extra.client_order_id})",
                )

    def _dedupe_open_exit_orders(self, purpose: str) -> None:
        """Cancel duplicate open exit limits on the same cell (recovery from prior stacking)."""
        by_cell: Dict[int, List[GridRestingOrder]] = {}
        for order in self._orders.list_open(self.strategy_id):
            if str(order.purpose or "") != purpose:
                continue
            by_cell.setdefault(int(order.cell_index), []).append(order)
        for cell_idx, orders in by_cell.items():
            if len(orders) <= 1:
                continue
            orders.sort(
                key=lambda o: float(o.quantity or 0) - float(o.processed_fill_qty or 0),
                reverse=True,
            )
            for extra in orders[1:]:
                try:
                    client = self._create_client()
                    cancel_grid_order(
                        client,
                        symbol=self.symbol,
                        market_type=self.cfg.market_type,
                        exchange_order_id=extra.exchange_order_id,
                        client_order_id=extra.client_order_id,
                        exchange_config=self.exchange_config,
                    )
                except Exception as e:
                    logger.debug("grid dedupe cancel sid=%s cell=%s: %s", self.strategy_id, cell_idx, e)
                if extra.id:
                    self._orders.update_status(int(extra.id), status="cancelled")
                append_strategy_log(
                    self.strategy_id,
                    "warning",
                    f"Grid deduped duplicate {purpose} on cell={cell_idx} "
                    f"(kept largest exit, cancelled oid={extra.exchange_order_id or extra.client_order_id})",
                )

    def _grid_base_qty(self, price: float) -> float:
        """One grid line's base quantity (amountPerGrid × leverage / price), exchange-normalized."""
        raw = self._qty_from_usdt(float(self.cfg.amount_per_grid), float(price or 0))
        return self._normalize_grid_base_qty(raw, price)

    def sync_held_cell_exits(self, current_price: float) -> int:
        """Re-hang grid-sized exits for cells that hold inventory but lost their working exit order."""
        if not self._bootstrapped or current_price <= 0:
            return 0
        direction = self.cfg.grid_direction
        if direction not in ("long", "short"):
            return 0
        placed = 0
        rows = self._cells.list_cells(self.strategy_id, self.symbol)
        for cell in rows or []:
            st = cell.state
            cell_idx = int(cell.cell_index)
            _, cells = self._levels_and_cells()
            cell_map = {c.index: c for c in cells}
            spec = cell_map.get(cell_idx)
            if not spec:
                continue
            if direction == "long" and st == GridCellState.LONG_HELD:
                if self._orders.has_open_for_cell(self.strategy_id, cell_idx, "long_exit"):
                    continue
                leg = float(cell.leg_size or 0.0)
                qty = self._normalize_grid_base_qty(leg, float(spec.upper_price or 0))
                if qty <= 0:
                    continue
                if self._place_limit(
                    spec,
                    "long_exit",
                    "sell",
                    spec.upper_price,
                    reduce_only=True,
                    pos_side="long",
                    quantity=qty,
                ):
                    placed += 1
            elif direction == "short" and st == GridCellState.SHORT_HELD:
                if self._orders.has_open_for_cell(self.strategy_id, cell_idx, "short_exit"):
                    continue
                leg = float(cell.leg_size or 0.0)
                qty = self._normalize_grid_base_qty(leg, float(spec.lower_price or 0))
                if qty <= 0:
                    continue
                if self._place_limit(
                    spec,
                    "short_exit",
                    "buy",
                    spec.lower_price,
                    reduce_only=True,
                    pos_side="short",
                    quantity=qty,
                ):
                    placed += 1
        return placed

    def sync_exit_coverage(self, current_price: float) -> int:
        """
        Hang **one grid-sized** take-profit on the active cell (long: upper / short: lower).

        Initial market inventory is NOT sold in one block — only ``amountPerGrid`` worth
        is offered at the next grid line, same as a normal filled entry cell.
        """
        if not self._bootstrapped or current_price <= 0:
            return 0
        direction = self.cfg.grid_direction
        if direction not in ("long", "short"):
            return 0

        _, cells = self._levels_and_cells()
        if not cells:
            return 0

        if direction == "long":
            pos_qty = self._leg_position_qty("long")
            purpose, side, pos_side = "long_exit", "sell", "long"
        else:
            pos_qty = self._leg_position_qty("short")
            purpose, side, pos_side = "short_exit", "buy", "short"

        if pos_qty <= 1e-10:
            return 0

        self._dedupe_open_exit_orders(purpose)

        target_cell = self._active_cell_for_price(cells, current_price, direction)
        if not target_cell:
            return 0

        px = float(
            (target_cell.upper_price if direction == "long" else target_cell.lower_price) or 0
        )
        if px <= 0:
            return 0

        if self._orders.has_open_for_cell(self.strategy_id, target_cell.index, purpose):
            return 0

        cell_states = self._cell_state_by_index()
        target_state = cell_states.get(int(target_cell.index), GridCellState.IDLE)
        if direction == "long" and target_state == GridCellState.LONG_HELD:
            return 0
        if direction == "short" and target_state == GridCellState.SHORT_HELD:
            return 0

        grid_qty = self._grid_base_qty(px)
        if grid_qty <= 0:
            return 0
        if pos_qty + 1e-8 < grid_qty:
            return 0

        ok = self._place_limit(
            target_cell,
            purpose,
            side,
            px,
            reduce_only=True,
            pos_side=pos_side,
            quantity=grid_qty,
        )
        placed = 0
        if ok:
            append_strategy_log(
                self.strategy_id,
                "info",
                f"Grid active-cell exit {purpose} cell={target_cell.index} @ {px:.4f} "
                f"qty={grid_qty:.6f} (one grid; pos={pos_qty:.6f})",
            )
            if direction == "long":
                self._cells.update_state(
                    self.strategy_id,
                    self.symbol,
                    target_cell.index,
                    state=GridCellState.LONG_HELD,
                    leg_size=grid_qty,
                    leg_entry_price=current_price,
                )
            else:
                self._cells.update_state(
                    self.strategy_id,
                    self.symbol,
                    target_cell.index,
                    state=GridCellState.SHORT_HELD,
                    leg_size=grid_qty,
                    leg_entry_price=current_price,
                )
            placed = 1
        placed += self.sync_held_cell_exits(current_price)
        return placed

    def sync_initial_exit_orders(self, current_price: float) -> int:
        """After initial market position, hang take-profit limits on the active cell."""
        if not self._bootstrapped or current_price <= 0:
            return 0
        if self.cfg.grid_direction not in ("long", "short"):
            return 0
        if self.cfg.initial_position_pct > 0 and not self._initial_done:
            return 0
        return self.sync_exit_coverage(current_price)

    def _place_limit(
        self,
        cell: GridCellSpec,
        purpose: str,
        side: str,
        price: float,
        *,
        reduce_only: bool,
        pos_side: str,
        quantity: Optional[float] = None,
    ) -> bool:
        px = float(price or 0)
        if px <= 0:
            return False
        usdt = float(self.cfg.amount_per_grid)
        qty = float(quantity) if quantity is not None else self._qty_from_usdt(usdt, px)
        qty = self._normalize_grid_base_qty(qty, px)
        if qty <= 0:
            return False
        coid = make_grid_client_order_id(self.strategy_id, cell.index, purpose)
        try:
            client = self._create_client()
            # Exit (reduce-only) orders may need to cross when price is above/below the grid line.
            post_only = (
                not reduce_only
                and self.cfg.order_mode in ("maker", "limit", "limit_first", "maker_then_market")
            )
            res = place_grid_limit_order(
                client,
                symbol=self.symbol,
                side=side,
                quantity=qty,
                price=px,
                market_type=self.cfg.market_type,
                exchange_config=self.exchange_config,
                pos_side=pos_side,
                reduce_only=reduce_only,
                client_order_id=coid,
                leverage=self.cfg.leverage,
                margin_mode=self.cfg.margin_mode,
                post_only=post_only,
            )
            ex_oid = str(res.exchange_order_id or "")
            row = GridRestingOrder(
                strategy_id=self.strategy_id,
                symbol=self.symbol,
                cell_index=cell.index,
                purpose=purpose,
                side=side,
                pos_side=pos_side,
                reduce_only=reduce_only,
                price=px,
                quantity=qty,
                quote_amount=usdt,
                client_order_id=coid,
                exchange_order_id=ex_oid,
                status="open",
            )
            oid = self._orders.insert(row)
            if not oid:
                if client and (ex_oid or coid):
                    try:
                        cancel_grid_order(
                            client,
                            symbol=self.symbol,
                            market_type=self.cfg.market_type,
                            exchange_order_id=ex_oid,
                            client_order_id=coid,
                            exchange_config=self.exchange_config,
                        )
                    except Exception as ce:
                        logger.warning(
                            "Grid insert failed; cancel orphan sid=%s cell=%s: %s",
                            self.strategy_id,
                            cell.index,
                            ce,
                        )
                append_strategy_log(
                    self.strategy_id,
                    "error",
                    f"Grid limit DB insert failed {purpose} cell={cell.index}",
                )
                return False
            st = GridCellState.BUY_OPEN if side == "buy" else GridCellState.SELL_OPEN
            if reduce_only and pos_side == "long":
                st = GridCellState.SELL_OPEN
            elif reduce_only and pos_side == "short":
                st = GridCellState.BUY_OPEN
            elif purpose == "long_entry":
                st = GridCellState.BUY_OPEN
            elif purpose == "short_entry":
                st = GridCellState.SELL_OPEN
            self._cells.update_state(
                self.strategy_id,
                self.symbol,
                cell.index,
                state=st,
                leg_size=qty,
                leg_entry_price=px,
                working_order_id=ex_oid or coid,
            )
            append_strategy_log(
                self.strategy_id,
                "info",
                f"Grid limit {purpose} {side} cell={cell.index} @ {px:.4f} qty={qty:.6f}",
            )
            self._consecutive_order_errors = 0
            return True
        except Exception as e:
            self._record_order_error(purpose, e)
        return False

    def on_order_filled(
        self,
        order: GridRestingOrder,
        filled_qty: float,
        avg_price: float,
    ) -> None:
        from app.services.grid.fill_handler import apply_grid_fill_to_local_state

        apply_grid_fill_to_local_state(
            self.strategy_id,
            self.symbol,
            order,
            filled_qty,
            avg_price,
            self.trading_config,
        )
        _, cells = self._levels_and_cells()
        cell_map = {c.index: c for c in cells}
        cell = cell_map.get(int(order.cell_index))
        if not cell:
            return
        purpose = str(order.purpose or "")
        fq = float(filled_qty or order.quantity or 0)
        px = float(avg_price or order.price or 0)
        append_strategy_log(
            self.strategy_id,
            "info",
            f"Grid fill {purpose} cell={order.cell_index} qty={fq:.6f} @ {px:.4f}",
        )
        if self._paused_entries or self._runtime_params.get("waterfall_pause"):
            if purpose.endswith("_exit"):
                pass
            elif self.cfg.boundary_action == "pause":
                return

        if purpose == "long_entry":
            exit_ok = False
            if not self._orders.has_open_for_cell(self.strategy_id, cell.index, "long_exit"):
                exit_ok = self._place_limit(
                    cell, "long_exit", "sell", cell.upper_price, reduce_only=True, pos_side="long", quantity=fq
                )
            self._cells.update_state(
                self.strategy_id, self.symbol, cell.index, state=GridCellState.LONG_HELD, leg_size=fq, leg_entry_price=px
            )
            if not exit_ok and not self._orders.has_open_for_cell(self.strategy_id, cell.index, "long_exit"):
                append_strategy_log(
                    self.strategy_id,
                    "warning",
                    f"Grid long_exit hang failed after entry fill cell={cell.index} @ {cell.upper_price:.4f}",
                )
        elif purpose == "long_exit":
            if not self._paused_entries:
                if self._cell_allows_entry(cell.index, "long_entry", self._cell_state_by_index()):
                    self._place_limit(
                        cell, "long_entry", "buy", cell.lower_price, reduce_only=False, pos_side="long", quantity=fq
                    )
            self._cells.update_state(self.strategy_id, self.symbol, cell.index, state=GridCellState.IDLE, leg_size=0)
        elif purpose == "short_entry":
            exit_ok = False
            if not self._orders.has_open_for_cell(self.strategy_id, cell.index, "short_exit"):
                exit_ok = self._place_limit(
                    cell, "short_exit", "buy", cell.lower_price, reduce_only=True, pos_side="short", quantity=fq
                )
            self._cells.update_state(
                self.strategy_id, self.symbol, cell.index, state=GridCellState.SHORT_HELD, leg_size=fq, leg_entry_price=px
            )
            if not exit_ok and not self._orders.has_open_for_cell(self.strategy_id, cell.index, "short_exit"):
                append_strategy_log(
                    self.strategy_id,
                    "warning",
                    f"Grid short_exit hang failed after entry fill cell={cell.index} @ {cell.lower_price:.4f}",
                )
        elif purpose == "short_exit":
            if not self._paused_entries:
                if self._cell_allows_entry(cell.index, "short_entry", self._cell_state_by_index()):
                    self._place_limit(
                        cell, "short_entry", "sell", cell.upper_price, reduce_only=False, pos_side="short", quantity=fq
                    )
            self._cells.update_state(self.strategy_id, self.symbol, cell.index, state=GridCellState.IDLE, leg_size=0)

    def handle_boundary(self, current_price: float) -> None:
        upper, lower = self.cfg.effective_bounds(self._runtime_params)
        if upper <= lower or current_price <= 0:
            return
        action = self.cfg.boundary_action
        out_of_low = current_price < lower
        out_of_high = current_price > upper
        if self.cfg.grid_direction == "long" and out_of_low:
            triggered = True
        elif self.cfg.grid_direction == "short" and out_of_high:
            triggered = True
        elif self.cfg.grid_direction == "neutral" and (out_of_low or out_of_high):
            triggered = True
        else:
            triggered = False
        if not triggered:
            return
        if action == "hold":
            append_strategy_log(self.strategy_id, "warning", "Grid out of bounds (hold)")
            return
        self.cancel_entry_orders_on_exchange()
        self._paused_entries = True
        append_strategy_log(self.strategy_id, "warning", f"Grid out of bounds -> {action}")
        if action == "stop_loss":
            self._enqueue_market("close_long", 0, current_price, "grid_boundary_stop")
            self._enqueue_market("close_short", 0, current_price, "grid_boundary_stop")

    def cancel_entry_orders_on_exchange(self) -> None:
        open_orders = self._orders.list_open(self.strategy_id)
        try:
            client = self._create_client()
        except Exception as e:
            logger.warning(
                "grid cancel_entry_orders create_client failed sid=%s: %s",
                self.strategy_id,
                e,
            )
            append_strategy_log(
                self.strategy_id,
                "error",
                f"Grid cancel entry orders failed (exchange client): {e}",
            )
            client = None
        for o in open_orders:
            if o.reduce_only or str(o.purpose or "").endswith("_exit"):
                continue
            if client:
                try:
                    cancel_grid_order(
                        client,
                        symbol=self.symbol,
                        market_type=self.cfg.market_type,
                        exchange_order_id=o.exchange_order_id,
                        client_order_id=o.client_order_id,
                        exchange_config=self.exchange_config,
                    )
                except Exception as e:
                    logger.debug("cancel grid entry: %s", e)
            if o.id:
                self._orders.update_status(int(o.id), status="cancelled")

    def cancel_all_orders_on_exchange(self) -> None:
        open_orders = self._orders.list_open(self.strategy_id)
        try:
            client = self._create_client()
        except Exception as e:
            logger.warning(
                "grid cancel_all_orders create_client failed sid=%s: %s",
                self.strategy_id,
                e,
            )
            append_strategy_log(
                self.strategy_id,
                "error",
                f"Grid cancel all orders failed (exchange client): {e}",
            )
            client = None
        for o in open_orders:
            if client:
                try:
                    cancel_grid_order(
                        client,
                        symbol=self.symbol,
                        market_type=self.cfg.market_type,
                        exchange_order_id=o.exchange_order_id,
                        client_order_id=o.client_order_id,
                        exchange_config=self.exchange_config,
                    )
                except Exception as e:
                    logger.debug("cancel grid order: %s", e)
            if o.id:
                self._orders.update_status(int(o.id), status="cancelled")

    def shutdown(self) -> None:
        self.cancel_all_orders_on_exchange()
        self._orders.cancel_all(self.strategy_id, self.symbol)
