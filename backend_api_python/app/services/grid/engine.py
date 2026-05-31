"""Grid engine: resting limit orders, cell pairing, initial position."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.services.grid.config import GridBotConfig
from app.services.grid.exchange_orders import (
    cancel_grid_order,
    make_grid_client_order_id,
    place_grid_limit_order,
)
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

    def run_initial_market_position(self, current_price: float) -> bool:
        if self._initial_done or self.cfg.initial_position_pct <= 0:
            self._initial_done = True
            return True
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
            # Neutral grid: split initial budget across both legs (best-effort).
            half = usdt / 2.0
            ok_long = self._enqueue_market("open_long", half, current_price, "grid_initial_long")
            ok_short = self._enqueue_market("open_short", half, current_price, "grid_initial_short")
            ok = ok_long or ok_short
            if ok:
                append_strategy_log(
                    self.strategy_id,
                    "info",
                    f"Grid initial neutral market: {usdt:.2f} USDT split @ {current_price:.4f}",
                )
            if ok:
                self._initial_done = True
                persist_grid_resting_state(self.strategy_id, {"initial_market_done": True})
            return ok
        else:
            sig = "open_long"
            reason = "grid_initial_long"
        ok = self._enqueue_market(sig, usdt, current_price, reason)
        if ok:
            append_strategy_log(
                self.strategy_id,
                "info",
                f"Grid initial market {sig}: {usdt:.2f} USDT @ {current_price:.4f}",
            )
            self._initial_done = True
            persist_grid_resting_state(self.strategy_id, {"initial_market_done": True})
        return ok

    def sync_grid_orders(self, current_price: float) -> int:
        if not self._bootstrapped or self._paused_entries or current_price <= 0:
            return 0
        if self._runtime_params.get("waterfall_pause"):
            return 0
        _, cells = self._levels_and_cells()
        placed = 0
        direction = self.cfg.grid_direction
        for cell in cells:
            if direction in ("long", "neutral") and cell.lower_price < current_price:
                if not self._orders.has_open_for_cell(self.strategy_id, cell.index, "long_entry"):
                    if self._place_limit(cell, "long_entry", "buy", cell.lower_price, reduce_only=False, pos_side="long"):
                        placed += 1
            if direction in ("short", "neutral") and cell.upper_price > current_price:
                if not self._orders.has_open_for_cell(self.strategy_id, cell.index, "short_entry"):
                    if self._place_limit(cell, "short_entry", "sell", cell.upper_price, reduce_only=False, pos_side="short"):
                        placed += 1
        return placed

    def sync_initial_exit_orders(self, current_price: float) -> int:
        """After initial market position, hang one take-profit limit on the active cell."""
        if self.cfg.initial_position_pct <= 0 or current_price <= 0:
            return 0
        direction = self.cfg.grid_direction
        if direction == "neutral":
            return 0
        _, cells = self._levels_and_cells()
        if not cells:
            return 0
        init_cap = self._initial_capital_usdt()
        usdt = init_cap * self.cfg.initial_position_pct
        qty = self._qty_from_usdt(usdt, current_price)
        if qty <= 0:
            return 0

        target_cell: Optional[GridCellSpec] = None
        for cell in cells:
            if cell.lower_price < current_price <= cell.upper_price:
                target_cell = cell
                break

        placed = 0
        if direction == "long":
            if target_cell is None:
                for cell in reversed(cells):
                    if cell.lower_price < current_price:
                        target_cell = cell
                        break
            if target_cell and not self._orders.has_open_for_cell(
                self.strategy_id, target_cell.index, "long_exit"
            ):
                if self._place_limit(
                    target_cell,
                    "long_exit",
                    "sell",
                    target_cell.upper_price,
                    reduce_only=True,
                    pos_side="long",
                    quantity=qty,
                ):
                    placed += 1
        elif direction == "short":
            if target_cell is None:
                for cell in cells:
                    if cell.upper_price > current_price:
                        target_cell = cell
                        break
            if target_cell and not self._orders.has_open_for_cell(
                self.strategy_id, target_cell.index, "short_exit"
            ):
                if self._place_limit(
                    target_cell,
                    "short_exit",
                    "buy",
                    target_cell.lower_price,
                    reduce_only=True,
                    pos_side="short",
                    quantity=qty,
                ):
                    placed += 1
        return placed

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
        if qty <= 0:
            return False
        coid = make_grid_client_order_id(self.strategy_id, cell.index, purpose)
        try:
            client = self._create_client()
            post_only = self.cfg.order_mode in ("maker", "limit", "limit_first", "maker_then_market")
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
            ok = self._place_limit(
                cell, "long_exit", "sell", cell.upper_price, reduce_only=True, pos_side="long", quantity=fq
            )
            if ok:
                self._cells.update_state(
                    self.strategy_id, self.symbol, cell.index, state=GridCellState.LONG_HELD, leg_size=fq, leg_entry_price=px
                )
        elif purpose == "long_exit":
            if not self._paused_entries:
                self._place_limit(
                    cell, "long_entry", "buy", cell.lower_price, reduce_only=False, pos_side="long", quantity=fq
                )
            self._cells.update_state(self.strategy_id, self.symbol, cell.index, state=GridCellState.IDLE, leg_size=0)
        elif purpose == "short_entry":
            ok = self._place_limit(
                cell, "short_exit", "buy", cell.lower_price, reduce_only=True, pos_side="short", quantity=fq
            )
            if ok:
                self._cells.update_state(
                    self.strategy_id, self.symbol, cell.index, state=GridCellState.SHORT_HELD, leg_size=fq, leg_entry_price=px
                )
        elif purpose == "short_exit":
            if not self._paused_entries:
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
        except Exception:
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
                    )
                except Exception as e:
                    logger.debug("cancel grid entry: %s", e)
            if o.id:
                self._orders.update_status(int(o.id), status="cancelled")

    def cancel_all_orders_on_exchange(self) -> None:
        open_orders = self._orders.list_open(self.strategy_id)
        try:
            client = self._create_client()
        except Exception:
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
                    )
                except Exception as e:
                    logger.debug("cancel grid order: %s", e)
            if o.id:
                self._orders.update_status(int(o.id), status="cancelled")

    def shutdown(self) -> None:
        self.cancel_all_orders_on_exchange()
        self._orders.cancel_all(self.strategy_id, self.symbol)
