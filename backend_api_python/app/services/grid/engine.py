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
    normalize_grid_order_quantity,
    place_grid_limit_order,
    query_grid_order_fill,
    wait_grid_market_fill,
)
from app.services.grid.fill_handler import record_grid_market_fill
from app.services.grid.levels import GridCellSpec, generate_cells, generate_levels
from app.services.grid.resting_orders_repo import GridRestingOrder, GridRestingOrderRepository
from app.services.grid.runtime_state import load_grid_resting_state, persist_grid_resting_state
from app.services.live_trading.grid_cells import GridCellRepository, GridCellState
from app.services.live_trading.limit_price_safety import (
    is_marketable_limit_price,
    normalize_marketable_limit_price,
)
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
        user_id: int = 1,
        strategy_run_id: int = 0,
        create_client_fn: Callable[[], Any],
        enqueue_market: MarketSignalFn,
    ) -> None:
        self.strategy_id = int(strategy_id)
        self.strategy_run_id = max(0, int(strategy_run_id or 0))
        self.user_id = int(user_id or 1)
        self.symbol = str(symbol or "")
        self.trading_config = trading_config if isinstance(trading_config, dict) else {}
        self.exchange_config = exchange_config if isinstance(exchange_config, dict) else {}
        self.cfg = GridBotConfig.from_trading_config(self.trading_config)
        self._create_client = create_client_fn
        self._enqueue_market = enqueue_market
        self.order_guard = None
        self._orders = GridRestingOrderRepository()
        self._cells = GridCellRepository()
        self._bootstrapped = False
        self._initial_done = False
        self._paused_entries = False
        self._runtime_params: Dict[str, Any] = {}
        gs = load_grid_resting_state(self.trading_config)
        try:
            state_run_id = max(0, int(gs.get("strategy_run_id") or 0))
        except (TypeError, ValueError):
            state_run_id = 0
        self._runtime_state_matches_run = (
            self.strategy_run_id <= 0 or state_run_id == self.strategy_run_id
        )
        if self._runtime_state_matches_run and gs.get("initial_market_done"):
            self._initial_done = True
        baseline = gs.get("initial_exchange_baseline") if self._runtime_state_matches_run else None
        self._initial_exchange_baseline = (
            {
                "long": max(0.0, float((baseline or {}).get("long") or 0.0)),
                "short": max(0.0, float((baseline or {}).get("short") or 0.0)),
            }
            if isinstance(baseline, dict)
            else {}
        )
        seeded = gs.get("initial_seeded_cells") if self._runtime_state_matches_run else None
        self._initial_seeded_cells = {
            int(index)
            for index in (seeded if isinstance(seeded, list) else [])
            if str(index).lstrip("-").isdigit()
        }
        self._consecutive_order_errors = 0
        self._stop_requested = False
        self._stop_reason = ""
        self._last_initial_attempt_ts = 0.0
        self._initial_retry_sec = 30.0
        self._initial_market_attempts = 0
        self._initial_market_max_attempts = 3
        self._last_entry_order_ts = 0.0
        self._entry_ownership_cache: Dict[str, Tuple[float, bool, Dict[str, Any]]] = {}
        self._ownership_check_errors: set[str] = set()
        self._exchange_open_orders_cache: Optional[Tuple[float, List[Dict[str, Any]]]] = None
        self._exchange_reservation_logged: set[str] = set()
        self._last_reduce_only_conflict_ts = 0.0
        self._last_market_price = 0.0
        self._startup_initial_fills: List[Dict[str, Any]] = []

    @property
    def stop_requested(self) -> bool:
        return bool(self._stop_requested)

    @property
    def stop_reason(self) -> str:
        return str(self._stop_reason or "")

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
        from app.services.strategy_lifecycle import is_recoverable_position_error

        recoverable = is_recoverable_position_error(msg)
        lower_msg = msg.lower()
        if "-2022" in lower_msg or "reduceonly order is rejected" in lower_msg:
            self._exchange_open_orders_cache = None
            self._last_reduce_only_conflict_ts = time.time()
        append_strategy_log(self.strategy_id, "warning" if recoverable else "error", f"Grid limit failed {purpose}: {msg}")
        if recoverable:
            self._consecutive_order_errors = 0
            return
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
                perform_stop=False,
            ):
                self._stop_requested = True
                self._paused_entries = True
                self._stop_reason = "exchange error while placing grid resting order"
        except Exception as e:
            logger.debug("grid auto-stop check sid=%s: %s", self.strategy_id, e)

    def _initial_capital_usdt(self) -> float:
        init_cap = float(self.trading_config.get("initial_capital") or 0)
        if init_cap <= 0:
            init_cap = float(self.trading_config.get("_grid_budget") or 0)
        if init_cap <= 0:
            cell_budget = self._grid_budget_usdt() * self.cfg.tradable_cell_count
            init_cap = cell_budget if self.cfg.grid_count_unit == "cells" else cell_budget * 2
        return init_cap

    def _grid_budget_usdt(self, cell_index: Optional[int] = None) -> float:
        if cell_index is not None and 0 <= int(cell_index) < len(self.cfg.cell_budget_pcts):
            pct = max(0.0, float(self.cfg.cell_budget_pcts[int(cell_index)] or 0.0))
            if pct <= 0:
                return 0.0
        else:
            pct = max(0.0, float(self.cfg.amount_per_grid_pct or 0.0))
        if pct > 0:
            capital = float(
                self.trading_config.get("initial_capital")
                or self.trading_config.get("_grid_budget")
                or 0.0
            )
            if capital > 0:
                return capital * pct
        return max(0.0, float(self.cfg.amount_per_grid or 0.0))

    def set_runtime_params(self, params: Dict[str, Any]) -> None:
        self._runtime_params = dict(params or {})

    def actor_snapshot(self) -> Dict[str, Any]:
        return {
            "bootstrapped": bool(self._bootstrapped),
            "initial_done": bool(self._initial_done),
            "paused_entries": bool(self._paused_entries),
            "runtime_params": dict(self._runtime_params),
            "stop_requested": bool(self._stop_requested),
            "stop_reason": str(self._stop_reason or ""),
            "last_market_price": float(self._last_market_price or 0.0),
            "consecutive_order_errors": int(self._consecutive_order_errors or 0),
        }

    def restore_actor_snapshot(self, state: Dict[str, Any]) -> None:
        if not isinstance(state, dict) or not state:
            return
        self._bootstrapped = bool(state.get("bootstrapped", self._bootstrapped))
        self._initial_done = bool(state.get("initial_done", self._initial_done))
        self._paused_entries = bool(state.get("paused_entries", self._paused_entries))
        runtime_params = state.get("runtime_params")
        if isinstance(runtime_params, dict):
            self._runtime_params = dict(runtime_params)
        self._stop_requested = bool(state.get("stop_requested", self._stop_requested))
        self._stop_reason = str(state.get("stop_reason") or self._stop_reason or "")
        try:
            self._last_market_price = max(
                0.0,
                float(state.get("last_market_price") or self._last_market_price or 0.0),
            )
        except (TypeError, ValueError):
            pass
        try:
            self._consecutive_order_errors = max(
                0,
                int(state.get("consecutive_order_errors") or 0),
            )
        except (TypeError, ValueError):
            pass

    def _observe_market_price(self, price: float) -> None:
        value = float(price or 0.0)
        if value > 0:
            self._last_market_price = value

    def _qty_from_usdt(self, usdt: float, price: float) -> float:
        if price <= 0 or usdt <= 0:
            return 0.0
        lev = self.cfg.leverage if self.cfg.market_type != "spot" else 1.0
        return float(usdt) * lev / float(price)

    def _levels_and_cells(self) -> Tuple[List[float], List[GridCellSpec]]:
        upper, lower = self.cfg.effective_bounds(self._runtime_params)
        levels = generate_levels(lower, upper, self.cfg.grid_line_count, self.cfg.grid_mode)
        return levels, generate_cells(levels)

    def bootstrap(self, current_price: float) -> Tuple[bool, str]:
        if current_price <= 0:
            return False, "invalid price"
        self._observe_market_price(current_price)
        levels, cells = self._levels_and_cells()
        if not cells:
            return False, "failed to generate grid cells"
        self._cells.bootstrap_idle_cells(self.strategy_id, self.symbol, levels)
        bot_params = (
            self.trading_config.get("bot_params")
            if isinstance(self.trading_config.get("bot_params"), dict)
            else {}
        )
        try:
            materialized_anchor = float(bot_params.get("_dynamicAnchorPrice") or 0.0)
        except (TypeError, ValueError):
            materialized_anchor = 0.0
        if materialized_anchor > 0:
            persist_grid_resting_state(
                self.strategy_id,
                {"dynamic_anchor_price": materialized_anchor},
            )
        self._bootstrapped = True
        append_strategy_log(
            self.strategy_id,
            "info",
            f"Grid resting bootstrap: {len(cells)} cells, {self.cfg.grid_direction}, "
            f"bounds [{levels[0]:.4f}, {levels[-1]:.4f}]",
        )
        return True, ""

    def reconcile_grid_ladder_orders(self) -> int:
        """Cancel confirmed working orders that belong to a stale price ladder."""
        if not self._bootstrapped:
            return 0
        _, cells = self._levels_and_cells()
        cell_map = {int(cell.index): cell for cell in cells}
        mismatched: List[GridRestingOrder] = []
        for order in self._orders.list_open(self.strategy_id):
            cell = cell_map.get(int(order.cell_index))
            purpose = str(order.purpose or "")
            if cell is None:
                mismatched.append(order)
                continue
            if purpose in {"long_entry", "short_exit"}:
                expected_price = float(cell.lower_price or 0.0)
            elif purpose in {"long_exit", "short_entry"}:
                expected_price = float(cell.upper_price or 0.0)
            else:
                continue
            actual_price = float(order.price or 0.0)
            tolerance = max(1e-8, expected_price * 1e-8)
            if expected_price <= 0 or abs(actual_price - expected_price) > tolerance:
                mismatched.append(order)
        if not mismatched:
            return 0
        try:
            client = self._create_client()
        except Exception as exc:
            logger.warning(
                "grid stale-ladder reconciliation unavailable sid=%s: %s",
                self.strategy_id,
                exc,
            )
            return 0
        cancelled = 0
        for order in mismatched:
            if self._cancel_confirmed_order(client, order):
                cancelled += 1
        if cancelled:
            self._cells.release_cancelled_working_orders(
                self.strategy_id,
                self.symbol,
            )
            logger.info(
                "Grid reconciled stale ladder sid=%s cancelled=%s mismatched=%s",
                self.strategy_id,
                cancelled,
                len(mismatched),
            )
        return cancelled

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
                      AND (%s <= 0 OR strategy_run_id = %s)
                    LIMIT 1
                    """,
                    (
                        int(self.strategy_id),
                        int(self.strategy_run_id),
                        int(self.strategy_run_id),
                    ),
                )
                row = cur.fetchone()
                cur.close()
                return bool(row)
        except Exception as e:
            logger.debug("grid initial trade check sid=%s: %s", self.strategy_id, e)
            return False

    def _leg_position_qty(self, pos_side: str) -> float:
        """Return the whole exchange-account leg (includes non-strategy holdings)."""
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

    def _strategy_leg_position_qty(self, pos_side: str) -> float:
        """Return only this strategy's L3-owned leg.

        Resting-grid exits must never be sized from the exchange account's
        aggregate position because that can include manually opened holdings
        or another strategy sharing the credential.
        """
        try:
            from app.services.live_trading.records import fetch_position_size_for_side

            return max(
                0.0,
                float(
                    fetch_position_size_for_side(
                        int(self.strategy_id),
                        self.symbol,
                        str(pos_side or ""),
                    )
                    or 0.0
                ),
            )
        except Exception as e:
            logger.debug(
                "grid strategy leg position qty sid=%s %s: %s",
                self.strategy_id,
                pos_side,
                e,
            )
            return 0.0

    def _credential_id(self) -> int:
        from app.services.live_trading.leg_context import credential_id_from_exchange_config

        return int(credential_id_from_exchange_config(self.exchange_config) or 0)

    def _grid_entry_ownership_allowed(
        self,
        client: Any,
        pos_side: str,
        *,
        force: bool = False,
        reference_price: float = 0.0,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Run the same account/L3 ownership guard used by queued strategies."""
        from app.services.live_trading.position_ownership import supports_position_coexistence

        side = str(pos_side or "").strip().lower()
        exchange_id = str(self.exchange_config.get("exchange_id") or "").strip().lower()
        market_type = str(self.cfg.market_type or "swap")
        credential_id = self._credential_id()
        if not supports_position_coexistence(market_type, exchange_id):
            return True, {}

        # Unit/offline grid engines have neither an exchange nor credential.
        # A resolved live exchange without a credential must fail closed.
        if credential_id <= 0:
            if not exchange_id:
                return True, {}
            error_key = f"credential:{side}"
            if error_key not in self._ownership_check_errors:
                append_strategy_log(
                    self.strategy_id,
                    "error",
                    f"Grid entry rejected because position ownership has no credential: {self.symbol} {side}",
                )
                self._ownership_check_errors.add(error_key)
            return False, {"reason": "position_ownership_credential_missing"}

        now = time.time()
        cached = self._entry_ownership_cache.get(side)
        if not force and cached and now - float(cached[0]) <= 1.0:
            return bool(cached[1]), dict(cached[2])

        try:
            from app.services.live_trading.position_query import query_exchange_position_size
            from app.services.pending_orders.entry_position_guard import evaluate_entry_position_guard

            account_qty = query_exchange_position_size(
                client=client,
                symbol=self.symbol,
                pos_side=side,
                market_type=market_type,
                exchange_config=self.exchange_config,
                strict=True,
            )
            guard = evaluate_entry_position_guard(
                client=client,
                strategy_id=self.strategy_id,
                user_id=self.user_id,
                credential_id=credential_id,
                exchange_id=exchange_id,
                market_type=market_type,
                symbol=self.symbol,
                side=side,
                strategy_config={
                    "bot_type": "grid",
                    "trading_config": self.trading_config,
                },
                exchange_config=self.exchange_config,
                account_qty=float(account_qty or 0.0),
                reference_price=float(reference_price or 0.0),
            )
            metadata = dict(guard.ownership or {})
            allowed = not bool(guard.error)
            self._entry_ownership_cache[side] = (now, allowed, metadata)
            self._ownership_check_errors.discard(f"snapshot:{side}")
            if guard.log_message:
                append_strategy_log(
                    self.strategy_id,
                    guard.log_level or "error",
                    guard.log_message,
                )
            return allowed, metadata
        except Exception as exc:
            error_key = f"snapshot:{side}"
            if error_key not in self._ownership_check_errors:
                append_strategy_log(
                    self.strategy_id,
                    "error",
                    f"Grid entry rejected because position ownership could not be verified: "
                    f"{self.symbol} {side}; {exc}",
                )
                self._ownership_check_errors.add(error_key)
            metadata = {"reason": "position_ownership_snapshot_failed", "error": str(exc)}
            self._entry_ownership_cache[side] = (now, False, metadata)
            return False, metadata

    @staticmethod
    def _truthy_exchange_flag(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "y"}

    def _binance_reserved_exit_quantity(self, client: Any, pos_side: str) -> Optional[float]:
        """Return quantity already reserved by Binance orders for this position leg."""
        exchange_id = str(self.exchange_config.get("exchange_id") or "").strip().lower()
        market_type = str(self.cfg.market_type or "swap").strip().lower()
        if exchange_id != "binance" or market_type == "spot" or not hasattr(client, "get_open_orders"):
            return None

        now = time.time()
        cached = self._exchange_open_orders_cache
        rows: List[Dict[str, Any]]
        if cached and now - float(cached[0] or 0.0) <= 2.0:
            rows = cached[1]
        else:
            try:
                raw = client.get_open_orders(symbol=self.symbol)
                if isinstance(raw, dict):
                    raw = raw.get("raw") or raw.get("data") or []
                rows = [dict(row) for row in (raw or []) if isinstance(row, dict)]
                self._exchange_open_orders_cache = (now, rows)
                self._ownership_check_errors.discard("binance_open_orders")
            except Exception as exc:
                if "binance_open_orders" not in self._ownership_check_errors:
                    logger.warning(
                        "Binance open-order sync unavailable sid=%s symbol=%s: %s",
                        self.strategy_id,
                        self.symbol,
                        exc,
                    )
                    self._ownership_check_errors.add("binance_open_orders")
                return None

        wanted_position = "LONG" if str(pos_side or "").lower() == "long" else "SHORT"
        wanted_side = "SELL" if wanted_position == "LONG" else "BUY"
        wanted_symbol = self.symbol.replace("/", "").replace("-", "").upper()
        reserved = 0.0
        for row in rows:
            symbol = str(row.get("symbol") or "").replace("/", "").replace("-", "").upper()
            if symbol and symbol != wanted_symbol:
                continue
            if str(row.get("side") or "").upper() != wanted_side:
                continue
            status = str(row.get("status") or "NEW").upper()
            if status not in {"NEW", "PARTIALLY_FILLED", "PENDING_NEW"}:
                continue
            position_side = str(row.get("positionSide") or "BOTH").upper()
            reduce_only = self._truthy_exchange_flag(row.get("reduceOnly"))
            if position_side in {"LONG", "SHORT"}:
                if position_side != wanted_position:
                    continue
            elif not reduce_only:
                continue
            try:
                remaining = max(
                    0.0,
                    float(row.get("origQty") or row.get("quantity") or 0.0)
                    - float(row.get("executedQty") or row.get("filled") or 0.0),
                )
            except (TypeError, ValueError):
                continue
            reserved += remaining
        return reserved

    def _resolve_grid_exit_quantity(
        self,
        client: Any,
        *,
        pos_side: str,
        requested_qty: float,
    ) -> float:
        """Cap a resting exit to strategy inventory and the protected floor."""
        from app.services.live_trading.position_ownership import supports_position_coexistence
        from app.services.live_trading.position_query import resolve_reduce_only_quantity

        exchange_id = str(self.exchange_config.get("exchange_id") or "").strip().lower()
        market_type = str(self.cfg.market_type or "swap")
        credential_id = self._credential_id()
        if (
            supports_position_coexistence(market_type, exchange_id)
            and exchange_id
            and credential_id <= 0
        ):
            append_strategy_log(
                self.strategy_id,
                "error",
                f"Grid exit rejected because protected inventory has no credential: {self.symbol} {pos_side}",
            )
            return 0.0

        try:
            resolved, meta = resolve_reduce_only_quantity(
                strategy_id=self.strategy_id,
                symbol=self.symbol,
                pos_side=str(pos_side or ""),
                requested_amount=float(requested_qty or 0.0),
                client=client,
                market_type=market_type,
                exchange_config=self.exchange_config,
                allow_exchange_fallback=False,
                user_id=self.user_id,
                credential_id=credential_id,
            )

            # Existing resting exits already reserve part of the safe strategy
            # quantity. Binance may also retain orders that were placed before
            # a worker restart but were not committed locally.
            existing = 0.0
            try:
                for order in self._orders.list_open(self.strategy_id):
                    if not order.reduce_only and not str(order.purpose or "").endswith("_exit"):
                        continue
                    if str(order.pos_side or "") != str(pos_side or ""):
                        continue
                    existing += max(
                        0.0,
                        float(order.quantity or 0.0) - float(order.processed_fill_qty or 0.0),
                    )
            except Exception:
                existing = 0.0
            budgets = [max(0.0, float(meta.get("db_size") or 0.0))]
            if "exchange_strategy_available" in meta:
                budgets.append(max(0.0, float(meta.get("exchange_strategy_available") or 0.0)))
            elif float(meta.get("exchange_size") or 0.0) > 0:
                budgets.append(max(0.0, float(meta.get("exchange_size") or 0.0)))
            safe_total = min(budgets) if budgets else 0.0
            exchange_reserved = self._binance_reserved_exit_quantity(client, pos_side)
            reserved = max(existing, float(exchange_reserved or 0.0)) if exchange_reserved is not None else existing
            if exchange_reserved is not None and exchange_reserved > existing + 1e-10:
                log_key = f"binance_reservation:{pos_side}"
                if log_key not in self._exchange_reservation_logged:
                    logger.info(
                        "Binance open exits synchronized sid=%s symbol=%s side=%s reserved=%.8f",
                        self.strategy_id,
                        self.symbol,
                        pos_side,
                        exchange_reserved,
                    )
                    self._exchange_reservation_logged.add(log_key)
            amount = min(max(0.0, float(resolved or 0.0)), max(0.0, safe_total - reserved))

            if market_type == "spot" and amount > 0:
                from app.services.live_trading.spot_sizing import clamp_spot_close_quantity

                amount, _spot_meta = clamp_spot_close_quantity(
                    client,
                    symbol=self.symbol,
                    requested_qty=amount,
                )
            self._ownership_check_errors.discard(f"exit:{pos_side}")
            return max(0.0, float(amount or 0.0))
        except Exception as exc:
            error_key = f"exit:{pos_side}"
            if error_key not in self._ownership_check_errors:
                append_strategy_log(
                    self.strategy_id,
                    "error",
                    f"Grid exit rejected because protected inventory could not be verified: "
                    f"{self.symbol} {pos_side}; {exc}",
                )
                self._ownership_check_errors.add(error_key)
            return 0.0

    def set_initial_exchange_baseline(self, *, long_size: float = 0.0, short_size: float = 0.0) -> None:
        """Freeze the pre-start account position so recovery only claims new delta."""
        self.prepare_runtime_state()
        if self._initial_exchange_baseline:
            return
        self._initial_exchange_baseline = {
            "long": max(0.0, float(long_size or 0.0)),
            "short": max(0.0, float(short_size or 0.0)),
        }
        persist_grid_resting_state(
            self.strategy_id,
            {"initial_exchange_baseline": dict(self._initial_exchange_baseline)},
        )

    def prepare_runtime_state(self) -> None:
        """Reset run-scoped startup flags before a newly created strategy run starts."""
        if self.strategy_run_id <= 0 or self._runtime_state_matches_run:
            return
        persist_grid_resting_state(
            self.strategy_id,
            {
                "strategy_run_id": self.strategy_run_id,
                "initial_market_done": False,
                "initial_exchange_baseline": {},
                "initial_seeded_cells": [],
            },
        )
        self._runtime_state_matches_run = True

    def _initial_exchange_delta(self, pos_side: str) -> float:
        total = self._leg_position_qty(pos_side)
        baseline = max(
            0.0,
            float(self._initial_exchange_baseline.get(str(pos_side or ""), 0.0) or 0.0),
        )
        return max(0.0, total - baseline)

    def _persist_initial_seeded_cells(self) -> None:
        persist_grid_resting_state(
            self.strategy_id,
            {"initial_seeded_cells": sorted(self._initial_seeded_cells)},
        )

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
        details: Dict[str, Any] = {}
        try:
            filled, avg = wait_grid_market_fill(
                client,
                symbol=self.symbol,
                market_type=self.cfg.market_type,
                exchange_config=self.exchange_config,
                exchange_order_id="",
                client_order_id=coid,
                max_wait_sec=3.0,
                details=details,
            )
        except Exception as e:
            logger.debug("grid initial probe sid=%s: %s", self.strategy_id, e)
            return False
        if filled <= 0:
            return False
        px = float(avg or 0)
        if px <= 0:
            return False
        pos_side = self._pos_side_for_signal(signal_type)
        exchange_delta = self._initial_exchange_delta(pos_side)
        recovery_tolerance = max(1e-12, float(filled) * 0.02)
        if exchange_delta <= 0 or float(filled) > exchange_delta + recovery_tolerance:
            logger.warning(
                "grid initial recovery rejected sid=%s run=%s coid=%s filled=%s exchange_delta=%s",
                self.strategy_id,
                self.strategy_run_id,
                coid,
                filled,
                exchange_delta,
            )
            return False
        from app.services.pending_orders.fee_reconciliation import (
            fee_breakdown_snapshot,
            fee_breakdown_to_quote,
            fee_storage_values,
        )

        fees = fee_breakdown_snapshot(details)
        commission, commission_ccy = fee_storage_values(fees)
        commission_quote = (
            fee_breakdown_to_quote(
                client,
                symbol=self.symbol,
                fees=fees,
                fill_price=px,
            )
            if fees and px > 0
            else None
        )
        record_grid_market_fill(
            self.strategy_id,
            self.symbol,
            signal_type,
            filled,
            px,
            self.trading_config,
            reason=reason,
            commission=commission,
            fees_by_ccy=fees,
            commission_ccy=commission_ccy,
            commission_quote=commission_quote,
            client_order_id=coid,
            exchange_id=str(self.exchange_config.get("exchange_id") or ""),
            user_id=self.user_id,
            strategy_run_id=int(getattr(self, "strategy_run_id", 0) or 0),
            fee_status=str(
                details.get("fee_status")
                or ("actual" if fees else "pending")
            ),
            fee_source="rest",
        )
        append_strategy_log(
            self.strategy_id,
            "info",
            f"Grid initial market recovered from client order: {filled:.6f} @ {px:.4f}",
        )
        return True

    def _remember_startup_initial_fill(
        self,
        signal_type: str,
        filled: float,
        fees_by_ccy: Optional[Dict[str, float]],
    ) -> None:
        from app.services.pending_orders.fill_records import spot_position_fill_quantity

        owned_quantity = spot_position_fill_quantity(
            market_type=self.cfg.market_type,
            symbol=self.symbol,
            signal_type=signal_type,
            gross_quantity=filled,
            fees_by_ccy=fees_by_ccy or {},
        )
        self._startup_initial_fills.append(
            {
                "signal_type": signal_type,
                "quantity": max(0.0, float(owned_quantity or 0.0)),
            }
        )

    @property
    def has_new_startup_initial_fills(self) -> bool:
        return any(float(item.get("quantity") or 0.0) > 0 for item in self._startup_initial_fills)

    def rollback_startup_initial_fills(self, current_price: float) -> bool:
        """Close only initial inventory opened by this startup attempt."""
        pending = list(reversed(self._startup_initial_fills))
        if not pending:
            return True
        try:
            client = self._create_client()
        except Exception as exc:
            logger.error("Grid startup rollback client failed sid=%s: %s", self.strategy_id, exc)
            append_strategy_log(
                self.strategy_id,
                "error",
                "strategyRuntime.gridStartupRollbackClientFailed",
            )
            return False

        all_closed = True
        remaining: List[Dict[str, Any]] = []
        for item in pending:
            opened_signal = str(item.get("signal_type") or "").strip().lower()
            quantity = max(0.0, float(item.get("quantity") or 0.0))
            close_signal = "close_long" if opened_signal == "open_long" else "close_short"
            if quantity <= 0 or opened_signal not in {"open_long", "open_short"}:
                continue
            leg = "long" if close_signal == "close_long" else "short"
            run_segment = f"r{self.strategy_run_id}" if self.strategy_run_id > 0 else ""
            client_order_id = f"grb{self.strategy_id}{run_segment}{leg}"[:32]
            execution = execute_grid_market_order(
                client,
                symbol=self.symbol,
                signal_type=close_signal,
                quantity=quantity,
                market_type=self.cfg.market_type,
                exchange_config=self.exchange_config,
                leverage=self.cfg.leverage,
                client_order_id=client_order_id,
            )
            if not execution.ok or execution.filled <= 0:
                all_closed = False
                remaining.append(item)
                logger.error(
                    "Grid startup rollback failed sid=%s signal=%s qty=%s",
                    self.strategy_id,
                    close_signal,
                    quantity,
                )
                append_strategy_log(
                    self.strategy_id,
                    "error",
                    "strategyRuntime.gridStartupRollbackFailed",
                )
                continue
            record_grid_market_fill(
                self.strategy_id,
                self.symbol,
                close_signal,
                execution.filled,
                execution.avg_price or current_price,
                self.trading_config,
                reason="grid_startup_rollback",
                commission=execution.commission,
                fees_by_ccy=execution.fees_by_ccy,
                commission_ccy=execution.commission_ccy,
                commission_quote=execution.commission_quote,
                client_order_id=execution.client_order_id or client_order_id,
                exchange_order_id=execution.exchange_order_id,
                exchange_id=str(self.exchange_config.get("exchange_id") or ""),
                user_id=self.user_id,
                strategy_run_id=self.strategy_run_id,
                fee_status=execution.fee_status,
                fee_source="rest",
            )
            remainder = max(0.0, quantity - float(execution.filled or 0.0))
            if remainder > max(1e-12, quantity * 1e-6):
                all_closed = False
                remaining.append(
                    {
                        "signal_type": opened_signal,
                        "quantity": remainder,
                    }
                )
                logger.error(
                    "Grid startup rollback partial sid=%s signal=%s remaining=%s",
                    self.strategy_id,
                    close_signal,
                    remainder,
                )
                append_strategy_log(
                    self.strategy_id,
                    "error",
                    "strategyRuntime.gridStartupRollbackPartial",
                )
            append_strategy_log(
                self.strategy_id,
                "warning",
                "strategyRuntime.gridStartupRollbackCompleted",
            )
        self._startup_initial_fills = list(reversed(remaining))
        if all_closed:
            self._initial_done = False
            persist_grid_resting_state(self.strategy_id, {"initial_market_done": False})
        return all_closed

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
        """Recover only from the strategy's order, never from an account balance."""
        pos_side = self._pos_side_for_signal(signal_type)
        if self._initial_exchange_delta(pos_side) <= 0:
            return False
        if self._has_initial_market_trade():
            self._initial_done = True
            persist_grid_resting_state(self.strategy_id, {"initial_market_done": True})
            return True
        coid = make_grid_initial_client_order_id(
            self.strategy_id,
            leg=pos_side,
            strategy_run_id=int(getattr(self, "strategy_run_id", 0) or 0),
        )
        try:
            recovered = self._probe_initial_client_order_fill(
                self._create_client(), coid, signal_type, reason, current_price)
        except Exception:
            logger.warning("Grid initial fill recovery deferred sid=%s", self.strategy_id, exc_info=True)
            return False
        if recovered:
            self._initial_done = True
            persist_grid_resting_state(self.strategy_id, {"initial_market_done": True})
        return recovered

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
            strategy_run_id=self.strategy_run_id,
        )
        pos_side = self._pos_side_for_signal(signal_type)
        exch_qty = self._initial_exchange_delta(pos_side)
        if exch_qty > 0:
            return False
        try:
            client = self._create_client()
            if self.order_guard and not self.order_guard():
                return False
            execution = execute_grid_market_order(
                client,
                symbol=self.symbol,
                signal_type=signal_type,
                quantity=qty,
                market_type=self.cfg.market_type,
                exchange_config=self.exchange_config,
                leverage=self.cfg.leverage,
                client_order_id=coid,
            )
            ok, filled, avg = execution
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
            commission=float(getattr(execution, "commission", 0.0) or 0.0),
            fees_by_ccy=getattr(execution, "fees_by_ccy", None),
            commission_ccy=str(getattr(execution, "commission_ccy", "") or ""),
            commission_quote=getattr(execution, "commission_quote", None),
            exchange_order_id=str(getattr(execution, "exchange_order_id", "") or ""),
            client_order_id=str(getattr(execution, "client_order_id", coid) or coid),
            exchange_id=str(self.exchange_config.get("exchange_id") or ""),
            user_id=self.user_id,
            strategy_run_id=self.strategy_run_id,
            fee_status=str(getattr(execution, "fee_status", "pending") or "pending"),
            fee_source="rest",
        )
        self._remember_startup_initial_fill(
            signal_type,
            filled,
            getattr(execution, "fees_by_ccy", None),
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
        exch_qty = self._initial_exchange_delta(pos_side)
        if exch_qty > 0:
            # Unattributed inventory blocks another initial order until its fill is reconciled.
            return False

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
                client_order_id=make_grid_initial_client_order_id(
                    self.strategy_id,
                    leg="short",
                    strategy_run_id=self.strategy_run_id,
                ),
            )
        elif direction == "neutral":
            half = usdt / 2.0
            ok_long = self._sync_initial_market_leg(
                "open_long",
                half,
                current_price,
                "grid_initial_long",
                client_order_id=make_grid_initial_client_order_id(
                    self.strategy_id,
                    leg="long",
                    strategy_run_id=self.strategy_run_id,
                ),
            )
            ok_short = self._sync_initial_market_leg(
                "open_short",
                half,
                current_price,
                "grid_initial_short",
                client_order_id=make_grid_initial_client_order_id(
                    self.strategy_id,
                    leg="short",
                    strategy_run_id=self.strategy_run_id,
                ),
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
                client_order_id=make_grid_initial_client_order_id(
                    self.strategy_id,
                    leg="long",
                    strategy_run_id=self.strategy_run_id,
                ),
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
        if self._stop_requested or not self._bootstrapped or self._paused_entries or current_price <= 0:
            return 0
        self._observe_market_price(current_price)
        if self._runtime_params.get("waterfall_pause"):
            return 0
        direction = self.cfg.grid_direction
        entry_sides = (
            ("long", "short")
            if direction == "neutral"
            else ((direction,) if direction in ("long", "short") else ())
        )
        allowed_sides: Dict[str, bool] = {}
        if entry_sides:
            try:
                ownership_client = self._create_client()
            except Exception as exc:
                append_strategy_log(
                    self.strategy_id,
                    "error",
                    f"Grid entries rejected because the exchange position could not be verified: {exc}",
                )
                return 0
            for pos_side in entry_sides:
                allowed, metadata = self._grid_entry_ownership_allowed(
                    ownership_client,
                    pos_side,
                    force=True,
                    reference_price=current_price,
                )
                allowed_sides[pos_side] = allowed
                if allowed:
                    continue
                self.cancel_entry_orders_on_exchange(pos_side=pos_side)
                if str(metadata.get("reason") or "") in {
                    "account_below_protected_allocation",
                    "account_below_strategy_allocation",
                    "position_ownership_snapshot_failed",
                    "position_ownership_credential_missing",
                }:
                    # A negative/unknown snapshot can make already-resting exits
                    # exceed strategy-safe inventory.  Cancel them and let exit
                    # coverage rebuild from the verified budget.
                    self.cancel_exit_orders_on_exchange(pos_side=pos_side)
        if entry_sides and not any(allowed_sides.values()):
            return 0
        now = time.time()
        if self.cfg.order_frequency > 0 and self._last_entry_order_ts > 0:
            if now - self._last_entry_order_ts < float(self.cfg.order_frequency):
                return 0
        self._dedupe_open_entry_orders("long_entry")
        self._dedupe_open_entry_orders("short_entry")
        _, cells = self._levels_and_cells()
        cell_states = self._cell_state_by_index()
        placed = 0
        open_entries = sum(
            1
            for order in self._orders.list_open(self.strategy_id)
            if str(order.purpose or "") in ("long_entry", "short_entry")
        )
        remaining_slots = max(0, int(self.cfg.max_open_orders or 0) - int(open_entries or 0))
        if remaining_slots <= 0:
            return 0
        if direction == "neutral":
            open_by_purpose = {"long_entry": 0, "short_entry": 0}
            for order in self._orders.list_open(self.strategy_id):
                purpose = str(order.purpose or "")
                if purpose in open_by_purpose:
                    open_by_purpose[purpose] += 1

            long_cells = sorted(
                (cell for cell in cells if cell.lower_price < current_price),
                key=lambda cell: cell.lower_price,
                reverse=True,
            )
            short_cells = sorted(
                (cell for cell in cells if cell.upper_price > current_price),
                key=lambda cell: cell.upper_price,
            )
            candidates = {
                "long_entry": long_cells,
                "short_entry": short_cells,
            }
            next_index = {"long_entry": 0, "short_entry": 0}

            while placed < remaining_slots:
                if self._stop_requested:
                    break
                purposes = sorted(
                    ("long_entry", "short_entry"),
                    key=lambda purpose: (
                        open_by_purpose[purpose],
                        0 if purpose == "long_entry" else 1,
                    ),
                )
                placed_one = False
                for purpose in purposes:
                    if self._stop_requested:
                        break
                    side = "buy" if purpose == "long_entry" else "sell"
                    pos_side = "long" if purpose == "long_entry" else "short"
                    if not allowed_sides.get(pos_side, True):
                        continue
                    target_price = "lower_price" if purpose == "long_entry" else "upper_price"
                    pool = candidates[purpose]
                    while next_index[purpose] < len(pool):
                        cell = pool[next_index[purpose]]
                        next_index[purpose] += 1
                        if not self._cell_allows_entry(cell.index, purpose, cell_states):
                            continue
                        if self._place_limit(
                            cell,
                            purpose,
                            side,
                            getattr(cell, target_price),
                            reduce_only=False,
                            pos_side=pos_side,
                        ):
                            placed += 1
                            open_by_purpose[purpose] += 1
                            cell_states[int(cell.index)] = (
                                GridCellState.BUY_OPEN
                                if purpose == "long_entry"
                                else GridCellState.SELL_OPEN
                            )
                            self._last_entry_order_ts = now
                            placed_one = True
                        break
                    if placed_one or placed >= remaining_slots:
                        break
                if not placed_one:
                    break
            return placed

        for cell in cells:
            if self._stop_requested or placed >= remaining_slots:
                break
            if (
                direction in ("long", "neutral")
                and allowed_sides.get("long", True)
                and cell.lower_price < current_price
            ):
                if self._cell_allows_entry(cell.index, "long_entry", cell_states):
                    if self._place_limit(cell, "long_entry", "buy", cell.lower_price, reduce_only=False, pos_side="long"):
                        placed += 1
                        cell_states[int(cell.index)] = GridCellState.BUY_OPEN
                        self._last_entry_order_ts = now
                        if placed >= remaining_slots:
                            break
            if (
                direction in ("short", "neutral")
                and allowed_sides.get("short", True)
                and cell.upper_price > current_price
            ):
                if self._cell_allows_entry(cell.index, "short_entry", cell_states):
                    if self._place_limit(cell, "short_entry", "sell", cell.upper_price, reduce_only=False, pos_side="short"):
                        placed += 1
                        cell_states[int(cell.index)] = GridCellState.SELL_OPEN
                        self._last_entry_order_ts = now
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

    def _held_cell_qty(self, direction: str) -> float:
        """Quantity already assigned to held grid cells for the given leg."""
        target_state = GridCellState.LONG_HELD if direction == "long" else GridCellState.SHORT_HELD
        total = 0.0
        try:
            rows = self._cells.list_cells(self.strategy_id, self.symbol)
        except Exception:
            rows = []
        for cell in rows or []:
            if GridCellState.parse(getattr(cell, "state", "")) != target_state:
                continue
            try:
                qty = float(getattr(cell, "leg_size", 0.0) or 0.0)
            except Exception:
                qty = 0.0
            if qty > 0:
                total += qty
        return total

    def _cell_cost_basis(self, cell_index: int, purpose: str, fallback: float) -> float:
        """Preserve a held cell's entry price when re-hanging reduce-only exits."""
        try:
            rows = self._cells.list_cells(self.strategy_id, self.symbol)
        except Exception:
            rows = []
        for cell in rows or []:
            try:
                if int(getattr(cell, "cell_index", -1)) != int(cell_index):
                    continue
                entry = float(getattr(cell, "leg_entry_price", 0.0) or 0.0)
                if entry > 0:
                    return entry
                if purpose == "long_exit":
                    return float(getattr(cell, "lower_price", 0.0) or fallback or 0.0)
                if purpose == "short_exit":
                    return float(getattr(cell, "upper_price", 0.0) or fallback or 0.0)
            except Exception:
                continue
        return float(fallback or 0.0)

    def _open_orders_for_cell(self, cell_index: int, purpose: str) -> List[GridRestingOrder]:
        out: List[GridRestingOrder] = []
        try:
            orders = self._orders.list_open(self.strategy_id)
        except Exception:
            orders = []
        for order in orders:
            if int(order.cell_index) != int(cell_index):
                continue
            if str(order.purpose or "") != purpose:
                continue
            out.append(order)
        return out

    def _cell_record(self, cell_index: int):
        try:
            rows = self._cells.list_cells(self.strategy_id, self.symbol)
        except Exception:
            rows = []
        for row in rows or []:
            if int(getattr(row, "cell_index", -1)) == int(cell_index):
                return row
        return None

    def _ensure_cell_exit_coverage(
        self,
        cell: GridCellSpec,
        *,
        purpose: str,
        side: str,
        price: float,
        pos_side: str,
        quantity: float,
    ) -> bool:
        """Keep one reduce-only exit equal to the cell's confirmed held size."""
        desired = self._normalize_grid_base_qty(float(quantity or 0.0), float(price or 0.0))
        if desired <= 0:
            return False
        open_orders = self._open_orders_for_cell(cell.index, purpose)
        covered = sum(
            max(0.0, float(item.quantity or 0.0) - float(item.processed_fill_qty or 0.0))
            for item in open_orders
        )
        tolerance = max(1e-10, desired * 1e-8)
        if abs(covered - desired) <= tolerance:
            return True

        # A later partial entry can increase the held quantity. Replace the
        # prior child exit only after its cancellation and fills are reconciled.
        client = None
        try:
            client = self._create_client()
        except Exception as exc:
            logger.warning("grid exit coverage client sid=%s: %s", self.strategy_id, exc)
            return False
        if client is None:
            return False
        for item in open_orders:
            if not self._cancel_confirmed_order(client, item):
                return False
        return self._place_limit(
            cell,
            purpose,
            side,
            price,
            reduce_only=True,
            pos_side=pos_side,
            quantity=desired,
        )

    def _cancel_confirmed_order(self, client: Any, order: GridRestingOrder) -> bool:
        """Retain uncertain orders for polling; never discard unprocessed fills."""
        if client is None or not order.id:
            return False
        try:
            cancel_grid_order(
                client,
                symbol=self.symbol,
                market_type=self.cfg.market_type,
                exchange_order_id=order.exchange_order_id,
                client_order_id=order.client_order_id,
                exchange_config=self.exchange_config,
            )
        except Exception as exc:
            logger.warning("grid cancel request unconfirmed sid=%s oid=%s: %s", self.strategy_id, order.id, exc)
        try:
            filled, _, status = query_grid_order_fill(
                client,
                symbol=self.symbol,
                market_type=self.cfg.market_type,
                exchange_order_id=order.exchange_order_id,
                client_order_id=order.client_order_id,
                exchange_config=self.exchange_config,
            )
            # A cancel acknowledgement is not a terminal order snapshot. The
            # poller must post any racing fill before coverage is recalculated.
            if status != "cancelled":
                return False
            if max(float(filled or 0.0), float(order.filled_quantity or 0.0)) > float(order.processed_fill_qty or 0.0) + 1e-12:
                return False
            return self._orders.update_status(int(order.id), status="cancelled") is True
        except Exception as exc:
            logger.warning("grid cancel unconfirmed sid=%s oid=%s: %s", self.strategy_id, order.id, exc)
            return False

    def _normalize_grid_base_qty(self, qty: float, price: float) -> float:
        """Floor to exchange lot/min; return 0 when below tradable minimum."""
        if qty <= 0 or price <= 0:
            return 0.0
        try:
            client = self._create_client()
            return normalize_grid_order_quantity(
                client,
                symbol=self.symbol,
                quantity=float(qty),
                market_type=self.cfg.market_type,
                exchange_config=self.exchange_config,
                price=float(price),
            )
        except Exception as e:
            logger.debug("grid normalize qty sid=%s: %s", self.strategy_id, e)
        return 0.0

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
                except Exception as e:
                    logger.debug("grid entry dedupe cancel sid=%s cell=%s: %s", self.strategy_id, cell_idx, e)
                    continue
                if not self._cancel_confirmed_order(client, extra):
                    continue
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
                except Exception as e:
                    logger.debug("grid dedupe cancel sid=%s cell=%s: %s", self.strategy_id, cell_idx, e)
                    continue
                if not self._cancel_confirmed_order(client, extra):
                    continue
                append_strategy_log(
                    self.strategy_id,
                    "warning",
                    f"Grid deduped duplicate {purpose} on cell={cell_idx} "
                    f"(kept largest exit, cancelled oid={extra.exchange_order_id or extra.client_order_id})",
                )

    def _grid_base_qty(self, price: float, cell_index: Optional[int] = None) -> float:
        """One grid line's base quantity (amountPerGrid × leverage / price), exchange-normalized."""
        raw = self._qty_from_usdt(
            self._grid_budget_usdt(cell_index),
            float(price or 0),
        )
        return self._normalize_grid_base_qty(raw, price)

    def sync_held_cell_exits(self, current_price: float) -> int:
        """Re-hang grid-sized exits for cells that hold inventory but lost their working exit order."""
        if self._stop_requested or not self._bootstrapped or current_price <= 0:
            return 0
        self._observe_market_price(current_price)
        direction = self.cfg.grid_direction
        if direction not in ("long", "short", "neutral"):
            return 0
        placed = 0
        rows = self._cells.list_cells(self.strategy_id, self.symbol)
        for cell in rows or []:
            if self._stop_requested:
                break
            st = cell.state
            cell_idx = int(cell.cell_index)
            _, cells = self._levels_and_cells()
            cell_map = {c.index: c for c in cells}
            spec = cell_map.get(cell_idx)
            if not spec:
                continue
            if direction in ("long", "neutral") and st == GridCellState.LONG_HELD:
                if self._orders.has_open_for_cell(self.strategy_id, cell_idx, "long_exit"):
                    self._ensure_cell_exit_coverage(
                        spec, purpose="long_exit", side="sell", price=spec.upper_price,
                        pos_side="long", quantity=float(cell.leg_size or 0.0),
                    )
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
            elif direction in ("short", "neutral") and st == GridCellState.SHORT_HELD:
                if self._orders.has_open_for_cell(self.strategy_id, cell_idx, "short_exit"):
                    self._ensure_cell_exit_coverage(
                        spec, purpose="short_exit", side="buy", price=spec.lower_price,
                        pos_side="short", quantity=float(cell.leg_size or 0.0),
                    )
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
        if self._stop_requested or not self._bootstrapped or current_price <= 0:
            return 0
        self._observe_market_price(current_price)
        direction = self.cfg.grid_direction
        if direction not in ("long", "short"):
            return 0

        _, cells = self._levels_and_cells()
        if not cells:
            return 0

        if direction == "long":
            pos_qty = self._strategy_leg_position_qty("long")
            purpose, side, pos_side = "long_exit", "sell", "long"
        else:
            pos_qty = self._strategy_leg_position_qty("short")
            purpose, side, pos_side = "short_exit", "buy", "short"

        if pos_qty <= 1e-10:
            return 0

        self._dedupe_open_exit_orders(purpose)
        placed = self.sync_held_cell_exits(current_price)

        cell_states = self._cell_state_by_index()
        # Initial inventory is distributed across distinct cells whose take
        # profit is strictly beyond the current market. Never reuse an active
        # cell after its seed exit fills, and never share a cell with a working
        # entry. This prevents immediate-crossing exits and repeated sells at
        # one price while preserving the normal entry -> adjacent-exit cycle.
        if direction == "long":
            candidates = sorted(
                (cell for cell in cells if float(cell.upper_price or 0) > current_price),
                key=lambda cell: float(cell.upper_price or 0),
            )
        else:
            candidates = sorted(
                (cell for cell in cells if float(cell.lower_price or 0) < current_price),
                key=lambda cell: float(cell.lower_price or 0),
                reverse=True,
            )

        covered_qty = max(self._held_cell_qty(direction), self._open_exit_qty(purpose))
        uncovered_qty = max(0.0, float(pos_qty or 0.0) - float(covered_qty or 0.0))
        for target_cell in candidates:
            if self._stop_requested:
                break
            idx = int(target_cell.index)
            if idx in self._initial_seeded_cells:
                continue
            if cell_states.get(idx, GridCellState.IDLE) != GridCellState.IDLE:
                continue
            if self._orders.has_open_for_cell(self.strategy_id, idx, purpose):
                continue
            if self._orders.has_open_for_cell(
                self.strategy_id,
                idx,
                "long_entry" if direction == "long" else "short_entry",
            ):
                continue
            px = float(
                (target_cell.upper_price if direction == "long" else target_cell.lower_price)
                or 0
            )
            grid_qty = self._grid_base_qty(px, idx)
            if grid_qty <= 0 or uncovered_qty + 1e-8 < grid_qty:
                continue
            if not self._place_limit(
                target_cell,
                purpose,
                side,
                px,
                reduce_only=True,
                pos_side=pos_side,
                quantity=grid_qty,
            ):
                continue
            self._initial_seeded_cells.add(idx)
            self._persist_initial_seeded_cells()
            uncovered_qty = max(0.0, uncovered_qty - grid_qty)
            cell_states[idx] = (
                GridCellState.LONG_HELD
                if direction == "long"
                else GridCellState.SHORT_HELD
            )
            append_strategy_log(
                self.strategy_id,
                "info",
                f"Grid initial seed exit {purpose} cell={idx} @ {px:.4f} "
                f"qty={grid_qty:.6f} (strategy-owned={pos_qty:.6f})",
            )
            placed += 1
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
        if self._stop_requested:
            return False
        px = float(price or 0)
        if px <= 0:
            return False
        reference_price = float(self._last_market_price or 0.0)
        crossed_market = False
        if reference_price > 0:
            crossed_market = is_marketable_limit_price(
                side=side,
                limit_price=px,
                reference_price=reference_price,
            )
            if crossed_market:
                px = normalize_marketable_limit_price(
                    side=side,
                    limit_price=px,
                    reference_price=reference_price,
                )
        if reduce_only and time.time() - float(self._last_reduce_only_conflict_ts or 0.0) < 5.0:
            return False
        usdt = self._grid_budget_usdt(cell.index)
        qty = float(quantity) if quantity is not None else self._qty_from_usdt(usdt, px)
        qty = self._normalize_grid_base_qty(qty, px)
        if qty <= 0:
            return False
        coid = make_grid_client_order_id(self.strategy_id, cell.index, purpose)
        try:
            client = self._create_client()
            if not reduce_only:
                allowed, metadata = self._grid_entry_ownership_allowed(
                    client,
                    pos_side,
                    reference_price=px,
                )
                if not allowed:
                    self.cancel_entry_orders_on_exchange(pos_side=pos_side)
                    if str(metadata.get("reason") or "") in {
                        "account_below_protected_allocation",
                        "account_below_strategy_allocation",
                        "position_ownership_snapshot_failed",
                        "position_ownership_credential_missing",
                    }:
                        self.cancel_exit_orders_on_exchange(pos_side=pos_side)
                    return False
            else:
                qty = self._resolve_grid_exit_quantity(
                    client,
                    pos_side=pos_side,
                    requested_qty=qty,
                )
                qty = self._normalize_grid_base_qty(qty, px)
                if qty <= 0:
                    return False
            # Exit (reduce-only) orders may need to cross when price is above/below the grid line.
            post_only = (
                not reduce_only
                and not crossed_market
                and self.cfg.order_mode in ("maker", "limit", "limit_first", "maker_then_market")
            )
            if self.order_guard and not self.order_guard():
                return False
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
            paired_cell = self._cell_record(cell.index) if reduce_only else None
            paired_extra = getattr(paired_cell, "extra", {}) or {}
            paired_ids = paired_extra.get("entry_grid_order_ids", []) if isinstance(paired_extra, dict) else []
            row = GridRestingOrder(
                extra={"entry_grid_order_ids": paired_ids} if reduce_only else {},
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
            if oid:
                try:
                    from app.services.execution_streams.repository import ExecutionEventRepository

                    ExecutionEventRepository().register_binding(
                        credential_id=int(
                            self.exchange_config.get("credential_id")
                            or self.exchange_config.get("credentials_id")
                            or 0
                        ),
                        exchange_id=str(self.exchange_config.get("exchange_id") or ""),
                        market_type=str(self.cfg.market_type or "swap"),
                        owner_type="grid",
                        owner_id=int(oid),
                        user_id=int(getattr(self, "user_id", 0) or 1),
                        strategy_id=int(self.strategy_id),
                        symbol=str(self.symbol),
                        signal_type=str(purpose),
                        client_order_id=coid,
                        exchange_order_id=ex_oid,
                    )
                except Exception:
                    logger.debug("grid execution binding failed oid=%s", oid, exc_info=True)
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
                st = GridCellState.LONG_HELD
            elif reduce_only and pos_side == "short":
                st = GridCellState.SHORT_HELD
            elif purpose == "long_entry":
                st = GridCellState.BUY_OPEN
            elif purpose == "short_entry":
                st = GridCellState.SELL_OPEN
            leg_entry_price = px
            if reduce_only:
                leg_entry_price = self._cell_cost_basis(cell.index, purpose, fallback=px)
            self._cells.update_state(
                self.strategy_id,
                self.symbol,
                cell.index,
                state=st,
                leg_size=qty,
                leg_entry_price=leg_entry_price,
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
            context = (
                f"{e} [cell={cell.index} side={side} price={px:.12g} "
                f"qty={qty:.12g} client_order_id={coid}]"
            )
            self._record_order_error(purpose, RuntimeError(context))
        return False

    def on_order_filled(
        self,
        order: GridRestingOrder,
        filled_qty: float,
        avg_price: float,
        *,
        commission: float = 0.0,
        commission_ccy: str = "",
        commission_quote: float | None = None,
        fee_status: str = "pending",
        fee_source: str = "",
        exchange_fill_id: str = "",
        execution_event_id: int = 0,
        fees_by_ccy: Dict[str, float] | None = None,
    ) -> None:
        from app.services.grid.fill_handler import apply_grid_fill_to_local_state
        from app.utils.db_postgres import run_after_commit

        apply_grid_fill_to_local_state(
            self.strategy_id,
            self.symbol,
            order,
            filled_qty,
            avg_price,
            self.trading_config,
            commission=commission,
            commission_ccy=commission_ccy,
            commission_quote=commission_quote,
            fee_status=fee_status,
            fee_source=fee_source,
            exchange_fill_id=exchange_fill_id,
            execution_event_id=execution_event_id,
            fees_by_ccy=fees_by_ccy,
            strategy_run_id=int(getattr(self, "strategy_run_id", 0) or 0),
        )
        _, cells = self._levels_and_cells()
        cell_map = {c.index: c for c in cells}
        cell = cell_map.get(int(order.cell_index))
        if not cell:
            return
        purpose = str(order.purpose or "")
        fq = float(filled_qty or 0)
        from app.services.pending_orders.fill_records import spot_position_fill_quantity
        from app.services.grid.fill_handler import _PURPOSE_TO_SIGNAL
        fq = spot_position_fill_quantity(market_type=self.trading_config.get('market_type') or 'swap',
            symbol=self.symbol, signal_type=_PURPOSE_TO_SIGNAL.get(purpose, ''),
            gross_quantity=fq, fees_by_ccy=fees_by_ccy or {})
        px = float(avg_price or 0)
        self._observe_market_price(px)
        persisted_cell = self._cell_record(cell.index)
        persisted_state = GridCellState.parse(
            getattr(persisted_cell, "state", GridCellState.IDLE)
        )
        persisted_qty = max(0.0, float(getattr(persisted_cell, "leg_size", 0.0) or 0.0))
        persisted_entry = max(
            0.0,
            float(getattr(persisted_cell, "leg_entry_price", 0.0) or 0.0),
        )
        append_strategy_log(
            self.strategy_id,
            "info",
            f"Grid fill {purpose} cell={order.cell_index} qty={fq:.6f} @ {px:.4f}",
        )
        def update_cell(*args, **kwargs):
            if not self._cells.update_state(*args, **kwargs):
                raise RuntimeError("strategyRuntime.fillSnapshotNotReady")

        saved_extra = getattr(persisted_cell, "extra", {}) or {}
        saved_extra = dict(saved_extra) if isinstance(saved_extra, dict) else {}
        entry_ids = list(saved_extra.get("entry_grid_order_ids") or [])
        if purpose in {"long_entry", "short_entry"}:
            expected = GridCellState.LONG_HELD if purpose == "long_entry" else GridCellState.SHORT_HELD
            if persisted_state != expected or persisted_qty <= 1e-10:
                entry_ids = []
            if order.id and int(order.id) not in entry_ids:
                entry_ids.append(int(order.id))
            saved_extra["entry_grid_order_ids"] = entry_ids

        if purpose == "long_entry":
            prior_qty = persisted_qty if persisted_state == GridCellState.LONG_HELD else 0.0
            held_qty = prior_qty + fq
            held_entry = (
                ((persisted_entry * prior_qty) + (px * fq)) / held_qty
                if held_qty > 0
                else px
            )
            update_cell(
                self.strategy_id,
                self.symbol,
                cell.index,
                state=GridCellState.LONG_HELD,
                leg_size=held_qty,
                leg_entry_price=held_entry,
                extra=saved_extra,
            )
            exit_ok = run_after_commit(self._ensure_cell_exit_coverage,
                cell,
                purpose="long_exit",
                side="sell",
                price=cell.upper_price,
                pos_side="long",
                quantity=held_qty,
            )
            if not exit_ok and not self._orders.has_open_for_cell(self.strategy_id, cell.index, "long_exit"):
                append_strategy_log(
                    self.strategy_id,
                    "warning",
                    f"Grid long_exit hang failed after entry fill cell={cell.index} @ {cell.upper_price:.4f}",
                )
        elif purpose == "long_exit":
            remaining = max(0.0, persisted_qty - fq)
            state = GridCellState.LONG_HELD if remaining > 1e-10 else GridCellState.IDLE
            update_cell(
                self.strategy_id,
                self.symbol,
                cell.index,
                state=state,
                leg_size=remaining,
                extra={**saved_extra, "entry_grid_order_ids": entry_ids if remaining > 1e-10 else []},
            )
            if remaining <= 1e-10 and not self._paused_entries:
                if self._cell_allows_entry(cell.index, "long_entry", self._cell_state_by_index()):
                    run_after_commit(self._place_limit,
                        cell, "long_entry", "buy", cell.lower_price, reduce_only=False, pos_side="long", quantity=fq
                    )
        elif purpose == "short_entry":
            prior_qty = persisted_qty if persisted_state == GridCellState.SHORT_HELD else 0.0
            held_qty = prior_qty + fq
            held_entry = (
                ((persisted_entry * prior_qty) + (px * fq)) / held_qty
                if held_qty > 0
                else px
            )
            update_cell(
                self.strategy_id,
                self.symbol,
                cell.index,
                state=GridCellState.SHORT_HELD,
                leg_size=held_qty,
                leg_entry_price=held_entry,
                extra=saved_extra,
            )
            exit_ok = run_after_commit(self._ensure_cell_exit_coverage,
                cell,
                purpose="short_exit",
                side="buy",
                price=cell.lower_price,
                pos_side="short",
                quantity=held_qty,
            )
            if not exit_ok and not self._orders.has_open_for_cell(self.strategy_id, cell.index, "short_exit"):
                append_strategy_log(
                    self.strategy_id,
                    "warning",
                    f"Grid short_exit hang failed after entry fill cell={cell.index} @ {cell.lower_price:.4f}",
                )
        elif purpose == "short_exit":
            remaining = max(0.0, persisted_qty - fq)
            state = GridCellState.SHORT_HELD if remaining > 1e-10 else GridCellState.IDLE
            update_cell(
                self.strategy_id,
                self.symbol,
                cell.index,
                state=state,
                leg_size=remaining,
                extra={**saved_extra, "entry_grid_order_ids": entry_ids if remaining > 1e-10 else []},
            )
            if remaining <= 1e-10 and not self._paused_entries:
                if self._cell_allows_entry(cell.index, "short_entry", self._cell_state_by_index()):
                    run_after_commit(self._place_limit,
                        cell, "short_entry", "sell", cell.upper_price, reduce_only=False, pos_side="short", quantity=fq
                    )

    def handle_boundary(self, current_price: float) -> bool:
        upper, lower = self.cfg.effective_bounds(self._runtime_params)
        if upper <= lower or current_price <= 0:
            return False
        self._observe_market_price(current_price)
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
            return False
        side = "below lower" if out_of_low else "above upper"
        detail = (
            f"price={current_price:.4f} is {side} bound "
            f"[{lower:.4f}, {upper:.4f}], direction={self.cfg.grid_direction}"
        )
        if action == "hold":
            append_strategy_log(self.strategy_id, "warning", f"Grid out of bounds (hold): {detail}")
            return True
        self.cancel_entry_orders_on_exchange()
        self._paused_entries = True
        append_strategy_log(self.strategy_id, "warning", f"Grid out of bounds -> {action}: {detail}")
        if action == "stop_loss":
            self._enqueue_market("close_long", 0, current_price, "grid_boundary_stop")
            self._enqueue_market("close_short", 0, current_price, "grid_boundary_stop")
            self._stop_requested = True
            self._stop_reason = f"grid out of bounds ({detail}); stop_loss requested"
            try:
                from app.services.strategy_lifecycle import auto_stop_live_strategy

                auto_stop_live_strategy(self.strategy_id, self._stop_reason, source="grid_boundary")
            except Exception as e:
                logger.debug("grid boundary auto-stop sid=%s: %s", self.strategy_id, e)
        return True

    def _cancel_position_orders_on_exchange(
        self,
        *,
        pos_side: str = "",
        exits: bool,
    ) -> None:
        open_orders = self._orders.list_open(self.strategy_id)
        try:
            client = self._create_client()
        except Exception as e:
            logger.warning(
                "grid cancel ownership orders create_client failed sid=%s exits=%s: %s",
                self.strategy_id,
                exits,
                e,
            )
            append_strategy_log(
                self.strategy_id,
                "error",
                f"Grid cancel protected orders failed (exchange client): {e}",
            )
            client = None
        for o in open_orders:
            is_exit = bool(o.reduce_only or str(o.purpose or "").endswith("_exit"))
            if is_exit != bool(exits):
                continue
            if pos_side and str(o.pos_side or "") != str(pos_side):
                continue
            if not client:
                continue
            self._cancel_confirmed_order(client, o)

    def cancel_entry_orders_on_exchange(self, *, pos_side: str = "") -> None:
        self._cancel_position_orders_on_exchange(pos_side=pos_side, exits=False)

    def cancel_exit_orders_on_exchange(self, *, pos_side: str = "") -> None:
        self._cancel_position_orders_on_exchange(pos_side=pos_side, exits=True)

    def cancel_all_orders_on_exchange(self) -> None:
        open_orders = self._orders.list_open(self.strategy_id)
        if not open_orders:
            return
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
            self._cancel_confirmed_order(client, o)

    def shutdown(self, *, preserve_exit_orders: bool = False) -> None:
        if preserve_exit_orders:
            self.cancel_entry_orders_on_exchange()
            append_strategy_log(
                self.strategy_id,
                "warning",
                "strategyRuntime.gridOrderErrorExitsPreserved",
            )
        else:
            self.cancel_all_orders_on_exchange()
        released = self._cells.release_cancelled_working_orders(self.strategy_id, self.symbol)
        if released:
            append_strategy_log(self.strategy_id, "info", f"Grid released {released} local cell working state(s)")
