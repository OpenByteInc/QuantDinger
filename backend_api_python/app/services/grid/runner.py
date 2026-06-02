"""Grid resting runner — integrates GridEngine with TradingExecutor."""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from app.services.grid.config import GridBotConfig
from app.services.grid.engine import GridEngine
from app.services.grid.validator import validate_grid_config
from app.services.bot_scripts.grid_runtime import prepare_bot_market_guards
from app.utils.logger import get_logger
from app.utils.strategy_runtime_logs import append_strategy_log

logger = get_logger(__name__)

# Active runners for fill poller
_ACTIVE_RUNNERS: Dict[int, "GridRestingRunner"] = {}


def register_runner(runner: "GridRestingRunner") -> None:
    _ACTIVE_RUNNERS[int(runner.strategy_id)] = runner


def unregister_runner(strategy_id: int) -> None:
    _ACTIVE_RUNNERS.pop(int(strategy_id), None)


def get_runner(strategy_id: int) -> Optional["GridRestingRunner"]:
    return _ACTIVE_RUNNERS.get(int(strategy_id))


def all_runners() -> Dict[int, "GridRestingRunner"]:
    return dict(_ACTIVE_RUNNERS)


def shutdown_grid_for_strategy(strategy_id: int) -> None:
    """Cancel open grid limits on the exchange even when no runner thread is alive."""
    sid = int(strategy_id or 0)
    if sid <= 0:
        return
    gr = get_runner(sid)
    if gr is not None:
        gr.shutdown()
        return
    try:
        from app.services.exchange_execution import load_strategy_configs, resolve_exchange_config
        from app.services.live_trading.factory import create_client

        sc = load_strategy_configs(sid) or {}
        tc = sc.get("trading_config") if isinstance(sc.get("trading_config"), dict) else {}
        bot_type = str(sc.get("bot_type") or tc.get("bot_type") or "").strip().lower()
        if bot_type != "grid":
            return
        symbol = str(tc.get("symbol") or sc.get("symbol") or "").strip()
        if not symbol:
            return
        user_id = int(sc.get("user_id") or 1)
        ex_cfg = resolve_exchange_config(sc.get("exchange_config") or {}, user_id=user_id)
        mt = str(tc.get("market_type") or "swap").strip().lower()

        def _create_client():
            return create_client(ex_cfg, market_type=mt)

        engine = GridEngine(
            sid,
            symbol,
            tc,
            ex_cfg,
            create_client_fn=_create_client,
            enqueue_market=lambda *a, **k: False,
        )
        engine.shutdown()
        append_strategy_log(sid, "info", "Grid orders cancelled on strategy stop (no active runner)")
    except Exception as e:
        logger.warning("shutdown_grid_for_strategy sid=%s: %s", sid, e)
        append_strategy_log(sid, "warning", f"Grid stop cancel failed: {e}")


class GridRestingRunner:
    """Orchestrates professional resting grid for one live strategy."""

    def __init__(
        self,
        strategy_id: int,
        symbol: str,
        trading_config: Dict[str, Any],
        exchange_config: Dict[str, Any],
        *,
        user_id: int = 1,
        initial_capital: float,
        enqueue_market_fn: Callable[[str, float, float, str], bool],
        create_client_fn: Callable[[], Any],
        risk_exit_fn: Optional[Callable[[float], list]] = None,
    ) -> None:
        self.strategy_id = int(strategy_id)
        self.user_id = int(user_id or 1)
        self.symbol = str(symbol or "")
        self.trading_config = dict(trading_config or {})
        self.trading_config["initial_capital"] = float(initial_capital or 0)
        self.exchange_config = dict(exchange_config or {})
        self._risk_exit_fn = risk_exit_fn
        self._runtime_params: Dict[str, Any] = {}
        self._engine = GridEngine(
            strategy_id,
            symbol,
            self.trading_config,
            self.exchange_config,
            create_client_fn=create_client_fn,
            enqueue_market=enqueue_market_fn,
        )
        self._started = False
        self._last_sync_ts = 0.0
        self._last_exit_sync_ts = 0.0

    @property
    def engine(self) -> GridEngine:
        return self._engine

    @property
    def should_stop(self) -> bool:
        return self._engine.stop_requested

    def startup(self, current_price: float, *, bars_df: Any = None) -> tuple[bool, str]:
        cfg = GridBotConfig.from_trading_config(self.trading_config)
        ok, msg, warnings = validate_grid_config(cfg, initial_capital=float(self.trading_config.get("initial_capital") or 0))
        if not ok:
            return False, msg
        for w in warnings:
            append_strategy_log(self.strategy_id, "warning", f"Grid config: {w}")
        bp = self.trading_config.get("bot_params") if isinstance(self.trading_config.get("bot_params"), dict) else {}
        self._runtime_params = dict(bp)
        self._engine.set_runtime_params(self._runtime_params)
        ok2, msg2 = self._engine.bootstrap(current_price)
        if not ok2:
            return False, msg2
        try:
            client = self._engine._create_client()
        except Exception as e:
            msg = str(e or "exchange client failed")
            append_strategy_log(self.strategy_id, "error", f"Grid exchange client failed: {msg}")
            return False, msg
        from app.services.grid.exchange_requirements import (
            fetch_exchange_dual_leg_snapshot,
            validate_neutral_grid_exchange_support,
        )

        ok_mode, mode_msg = validate_neutral_grid_exchange_support(
            cfg,
            client,
            symbol=self.symbol,
            exchange_config=self.exchange_config,
        )
        if not ok_mode:
            append_strategy_log(self.strategy_id, "error", mode_msg)
            return False, mode_msg
        try:
            snap = fetch_exchange_dual_leg_snapshot(
                client,
                symbol=self.symbol,
                market_type=cfg.market_type,
                exchange_config=self.exchange_config,
            )
            append_strategy_log(
                self.strategy_id,
                "info",
                "Grid exchange snapshot before initial position: "
                f"long={snap.get('long_size', 0):.6f} short={snap.get('short_size', 0):.6f} "
                f"mode={snap.get('position_mode_label') or 'unknown'}",
            )
        except Exception as e:
            logger.debug("grid startup exchange snapshot sid=%s: %s", self.strategy_id, e)
        self._engine.run_initial_market_position(current_price)
        n = self._engine.sync_grid_orders(current_price)
        if self._engine.cfg.initial_position_pct <= 0 or self._engine._initial_done:
            self._engine.sync_exit_coverage(current_price)
        if self._engine.stop_requested:
            append_strategy_log(
                self.strategy_id,
                "error",
                "Grid startup aborted: resting limit orders failed (check exchange credentials)",
            )
            return False, "grid resting limit orders failed during startup"
        self._started = True
        register_runner(self)
        try:
            from app.services.grid.poller import sync_strategy_grid_orders

            polled = sync_strategy_grid_orders(self.strategy_id)
            if polled:
                append_strategy_log(
                    self.strategy_id,
                    "info",
                    f"Grid startup fill sync: polled {polled} resting order(s)",
                )
        except Exception as e:
            logger.warning("grid startup fill sync sid=%s: %s", self.strategy_id, e)
        append_strategy_log(self.strategy_id, "info", f"Grid resting live started, placed {n} entry limits")
        return True, ""

    def shutdown(self) -> None:
        try:
            self._engine.shutdown()
        finally:
            unregister_runner(self.strategy_id)
            self._started = False

    def tick(
        self,
        current_price: float,
        *,
        high: Optional[float] = None,
        low: Optional[float] = None,
        bars_df: Any = None,
        is_closed_bar: bool = False,
    ) -> None:
        if not self._started or current_price <= 0:
            return
        if self._engine.stop_requested:
            return
        hi = float(high if high is not None else current_price)
        lo = float(low if low is not None else current_price)
        prepare_bot_market_guards(
            "grid",
            self._runtime_params,
            price=current_price,
            high=hi,
            low=lo,
            bars_df=bars_df,
            is_closed_bar=is_closed_bar,
        )
        self._engine.set_runtime_params(self._runtime_params)

        if self._risk_exit_fn:
            try:
                exits = self._risk_exit_fn(current_price) or []
                if exits:
                    self._engine.cancel_entry_orders_on_exchange()
                    for ex in exits:
                        st = str(ex.get("type") or "").strip().lower()
                        if st:
                            self._engine._enqueue_market(
                                st, 0, current_price, str(ex.get("reason") or "grid_risk")
                            )
                    append_strategy_log(self.strategy_id, "warning", "Grid risk exit triggered")
                    return
            except Exception as e:
                logger.debug("grid risk exit: %s", e)

        self._engine.handle_boundary(current_price)

        if (
            self._engine.cfg.initial_position_pct > 0
            and not self._engine._initial_done
        ):
            self._engine.run_initial_market_position(current_price)

        if self._engine.cfg.grid_direction in ("long", "short"):
            if self._engine.cfg.initial_position_pct <= 0 or self._engine._initial_done:
                now_exit = time.time()
                if now_exit - self._last_exit_sync_ts >= 15.0:
                    self._engine.sync_exit_coverage(current_price)
                    self._last_exit_sync_ts = now_exit

        now = time.time()
        if now - self._last_sync_ts >= 15.0:
            self._engine.sync_grid_orders(current_price)
            self._last_sync_ts = now
