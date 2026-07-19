"""Unified event-driven simulation runtime for Strategy API V2."""

from __future__ import annotations

import inspect
import math
import calendar
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping

import pandas as pd

from app.services.factors import (
    FactorError,
    compute_factor,
    compute_talib_factor,
    compute_talib_indicator,
    get_factor,
    is_talib_available,
)

from .contract import CompiledStrategyV2, StrategyV2ContractError, compile_strategy_v2
from .data import MultiAssetDataPortal
from .protection import ProtectionDecision, ProtectionEngine, ProtectionSpec, ProtectionState


@dataclass
class Position:
    symbol: str
    amount: float = 0.0
    avg_cost: float = 0.0
    last_price: float = 0.0

    @property
    def market_value(self) -> float:
        return self.amount * self.last_price

@dataclass
class PortfolioState:
    starting_cash: float
    available_cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    total_value: float = 0.0

@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    kind: str
    value: float
    reason: str = "strategy"
    protection: ProtectionSpec | None = None
    signal_time: pd.Timestamp | None = None
    attempts: int = 0
    pending_direction: int = 0
    order_type: str = "market"
    limit_price: float = 0.0
    execution_algo: str = "market"
    maker_wait_sec: float = 0.0
    maker_offset_bps: float = 0.0


class StrategyDataView:
    def __init__(self, portal: MultiAssetDataPortal):
        self.portal = portal

    def history(self, symbols: object, count: int, fields: object = None, **_: Any):
        return self.portal.history(symbols, count=count, fields=fields)

    def current(self, symbol: object, field: str = "close") -> float:
        return self.portal.current(symbol, field)

    def __getitem__(self, symbol: object) -> pd.DataFrame:
        return self.portal.visible_frame(symbol)


class StrategyRuntimeLogger:
    def __init__(self, sink) -> None:
        self._sink = sink

    def __call__(self, message: object, *_args: Any, **_kwargs: Any) -> None:
        self.info(message)

    def debug(self, message: object, *_args: Any, **_kwargs: Any) -> None:
        self._write("debug", message)

    def info(self, message: object, *_args: Any, **_kwargs: Any) -> None:
        self._write("info", message)

    def warning(self, message: object, *_args: Any, **_kwargs: Any) -> None:
        self._write("warning", message)

    warn = warning

    def error(self, message: object, *_args: Any, **_kwargs: Any) -> None:
        self._write("error", message)

    def _write(self, level: str, message: object) -> None:
        self._sink(f"[{level}] {message}")


class StrategyRuntimeContext:
    def __init__(
        self,
        *,
        portal: MultiAssetDataPortal,
        portfolio: PortfolioState,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        self.portal = portal
        self.data = StrategyDataView(portal)
        self.portfolio = portfolio
        self.params = dict(params or {})
        self.current_dt: pd.Timestamp | None = None
        self.previous_trading_date: pd.Timestamp | None = None
        self._orders: list[OrderIntent] = []
        self._logs: list[str] = []
        self._default_protection: ProtectionSpec | None = None
        self._indicator_cache: dict[tuple[Any, ...], pd.Series | pd.DataFrame] = {}
        self.logger = StrategyRuntimeLogger(self.log)

    def set_default_protection(self, **values: Any) -> None:
        self._default_protection = ProtectionSpec.from_value(values)

    def order(self, symbol: object, amount: object, **kwargs: Any) -> None:
        self._queue(symbol, "quantity", amount, kwargs)

    def order_value(self, symbol: object, value: object, **kwargs: Any) -> None:
        self._queue(symbol, "value", value, kwargs)

    def order_target(self, symbol: object, amount: object, **kwargs: Any) -> None:
        self._queue(symbol, "target_quantity", amount, kwargs)

    def order_target_value(self, symbol: object, value: object, **kwargs: Any) -> None:
        self._queue(symbol, "target_value", value, kwargs)

    def order_target_percent(self, symbol: object, percent: object, **kwargs: Any) -> None:
        self._queue(symbol, "target_percent", percent, kwargs)

    def get_position(self, symbols: object = None) -> dict[str, Position] | Position:
        if symbols is None:
            return dict(self.portfolio.positions)
        if isinstance(symbols, str):
            try:
                key = self.portal.resolve_key(symbols)
            except Exception:
                key = str(symbols)
            current = self.portfolio.positions.get(key)
            if current is not None:
                return current
            try:
                last_price = self.portal.current(key, "close", 0.0)
            except Exception:
                last_price = 0.0
            return Position(key, last_price=last_price)
        output: dict[str, Position] = {}
        for symbol in symbols:
            key = self.portal.resolve_key(symbol)
            if key in self.portfolio.positions:
                output[key] = self.portfolio.positions[key]
        return output

    def get_positions(self, symbols: object = None) -> dict[str, Position]:
        positions = self.get_position(symbols)
        if isinstance(positions, Position):
            return {positions.symbol: positions}
        return positions

    def get_history(
        self,
        count: object,
        frequency: object = None,
        field: object = None,
        security_list: object = None,
        **_: Any,
    ):
        del frequency
        symbols = security_list or list(self.portal.frames.keys())
        return self.portal.history(symbols, count=int(count), fields=field)

    def get_index_stocks(self, reference: object, **_: Any) -> list[str]:
        return self.portal.universe(str(reference or ""))

    def get_universe_stocks(self, reference: object = None, **_: Any) -> list[str]:
        return self.portal.universe(str(reference or ""))

    def is_trade(self, *_args: Any, **_kwargs: Any) -> bool:
        return self.current_dt is not None and any(
            pd.Timestamp(self.current_dt) in frame.index
            for frame in self.portal.frames.values()
        )

    def indicator(self, name: object, symbol: object = None, **params: Any):
        target = symbol or self._default_symbol()
        frame = self.portal.visible_frame(target)
        library_id = str(name or "").strip()
        if is_talib_available():
            try:
                return compute_talib_indicator(library_id, frame, params)
            except Exception:
                pass
        return self._compute_builtin_indicator(library_id, target, frame, params)

    def _compute_builtin_indicator(
        self,
        library_id: str,
        target: object,
        frame: pd.DataFrame,
        params: Mapping[str, Any],
    ) -> pd.Series | pd.DataFrame:
        factor_id, normalized, outputs = _builtin_indicator_contract(library_id, params)
        resolved_target = self.portal.resolve_key(target)
        cache_key = (
            resolved_target,
            factor_id,
            tuple(sorted((str(key), repr(value)) for key, value in normalized.items())),
            tuple(outputs),
        )
        cached = self._indicator_cache.get(cache_key)
        cached_length = len(cached) if cached is not None else 0
        if cached is not None and (
            cached_length > len(frame)
            or not cached.index.equals(frame.index[:cached_length])
        ):
            cached = None
            cached_length = 0

        values = {
            column: (
                cached[column].tolist()
                if isinstance(cached, pd.DataFrame)
                else cached.tolist()
                if isinstance(cached, pd.Series) and len(outputs) == 1
                else []
            )
            for column, _ in outputs
        }
        for index in range(cached_length, len(frame)):
            visible = frame.iloc[:index + 1]
            for column, output in outputs:
                factor_params = dict(normalized)
                if output:
                    factor_params["output"] = output
                try:
                    value = compute_factor(factor_id, visible, factor_params)
                except FactorError as exc:
                    if exc.code not in {"factor.noData", "factor.insufficientHistory"}:
                        raise
                    value = float("nan")
                values[column].append(value)

        if len(outputs) == 1:
            column = outputs[0][0]
            result: pd.Series | pd.DataFrame = pd.Series(
                values[column], index=frame.index, name=column, dtype=float
            )
        else:
            result = pd.DataFrame(values, index=frame.index, dtype=float)
        self._indicator_cache[cache_key] = result
        return result

    def factor(self, name: object, symbol: object = None, **params: Any) -> float:
        target = symbol or self._default_symbol()
        frame = self.portal.visible_frame(target)
        factor_id = str(name or "").strip()
        try:
            get_factor(factor_id.lower())
            return compute_factor(factor_id.lower(), frame, params)
        except FactorError as exc:
            if exc.code != "factor.notFound":
                raise
        output = str(params.pop("output", "") or "")
        return compute_talib_factor(factor_id, frame, params, output=output)

    def get_factors(self, symbols: object, names: object, **params: Any) -> pd.DataFrame:
        requested_symbols = [symbols] if isinstance(symbols, str) else list(symbols or [])
        requested_names = [names] if isinstance(names, str) else list(names or [])
        rows = {}
        for symbol in requested_symbols:
            key = self.portal.resolve_key(symbol)
            rows[key] = {
                str(name): self.factor(name, key, **dict(params))
                for name in requested_names
            }
        return pd.DataFrame.from_dict(rows, orient="index")

    def get_fundamentals(self, fields: object, symbols: object = None, **_: Any) -> pd.DataFrame:
        requested_fields = [fields] if isinstance(fields, str) else list(fields or [])
        requested_symbols = (
            [symbols] if isinstance(symbols, str)
            else list(symbols or self.portal.frames.keys())
        )
        rows = {}
        for symbol in requested_symbols:
            key = self.portal.resolve_key(symbol)
            frame = self.portal.visible_frame(key, count=1)
            rows[key] = {
                str(field): frame.iloc[-1].get(_fundamental_column(field)) if not frame.empty else None
                for field in requested_fields
            }
        return pd.DataFrame.from_dict(rows, orient="index")

    def log(self, message: object) -> None:
        self._logs.append(str(message))

    def flush_orders(self) -> list[OrderIntent]:
        orders = list(self._orders)
        self._orders.clear()
        return orders

    def flush_logs(self) -> list[str]:
        logs = list(self._logs)
        self._logs.clear()
        return logs

    def _queue(self, symbol: object, kind: str, value: object, kwargs: Mapping[str, Any]) -> None:
        key = self.portal.resolve_key(symbol)
        try:
            number = float(value)
        except Exception as exc:
            raise StrategyV2ContractError("strategyV2.orderValueInvalid") from exc
        if not math.isfinite(number):
            raise StrategyV2ContractError("strategyV2.orderValueInvalid")
        protection_values = kwargs.get("protection")
        inline_values = {
            name: kwargs.get(name)
            for name in (
                "stop_loss_pct",
                "take_profit_pct",
                "trailing_stop_pct",
                "trailing_activation_pct",
                "time_limit_seconds",
            )
            if kwargs.get(name) is not None
        }
        protection = ProtectionSpec.from_value(protection_values, **inline_values)
        if protection is None:
            protection = self._default_protection
        order_type = str(kwargs.get("order_type") or kwargs.get("type") or "market").strip().lower()
        execution_algo = str(kwargs.get("execution_algo") or "").strip().lower()
        if order_type not in {"market", "limit"}:
            raise StrategyV2ContractError("strategyV2.orderTypeUnsupported")
        if not execution_algo:
            execution_algo = "limit" if order_type == "limit" else "market"
        if execution_algo in {"maker", "limit_then_market"}:
            execution_algo = "maker_then_market"
        if execution_algo not in {"market", "limit", "maker_then_market"}:
            raise StrategyV2ContractError("strategyV2.executionAlgoUnsupported")
        limit_price = kwargs.get("limit_price")
        if limit_price is None and order_type == "limit":
            limit_price = kwargs.get("price")
        try:
            limit_price_number = float(limit_price or 0.0)
            maker_wait_sec = max(0.0, float(kwargs.get("maker_wait_sec") or 0.0))
            maker_offset_bps = max(0.0, float(kwargs.get("maker_offset_bps") or 0.0))
        except Exception as exc:
            raise StrategyV2ContractError("strategyV2.invalidOrderPrice") from exc
        if execution_algo == "limit" and limit_price_number <= 0:
            raise StrategyV2ContractError("strategyV2.limitPriceRequired")
        self._orders.append(OrderIntent(
            key,
            kind,
            number,
            str(kwargs.get("reason") or "strategy"),
            protection,
            self.current_dt,
            order_type=order_type,
            limit_price=limit_price_number,
            execution_algo=execution_algo,
            maker_wait_sec=maker_wait_sec,
            maker_offset_bps=maker_offset_bps,
        ))

    def _default_symbol(self) -> str:
        if len(self.portal.frames) != 1:
            raise StrategyV2ContractError("strategyV2.symbolRequiredForMultiAssetFactor")
        return next(iter(self.portal.frames))


def _builtin_indicator_contract(
    library_id: str,
    params: Mapping[str, Any],
) -> tuple[str, dict[str, Any], tuple[tuple[str, str], ...]]:
    name = str(library_id or "").strip().lower().replace("talib:", "")
    normalized = dict(params)

    aliases: dict[str, str] = {}
    outputs: tuple[tuple[str, str], ...] = ((name, ""),)
    factor_id = name
    if name in {"atr", "rsi", "adx"}:
        aliases = {"timeperiod": "period"}
    elif name == "macd":
        aliases = {
            "fastperiod": "fast_period",
            "slowperiod": "slow_period",
            "signalperiod": "signal_period",
        }
        outputs = (
            ("macd", "line"),
            ("macdsignal", "signal"),
            ("macdhist", "histogram"),
        )
    elif name in {"stoch", "stochastic"}:
        factor_id = "stochastic"
        aliases = {
            "fastk_period": "period",
            "slowk_period": "smooth_k",
            "slowd_period": "smooth_d",
        }
        outputs = (("slowk", "k"), ("slowd", "d"))
    elif name == "kdj":
        aliases = {
            "fastk_period": "period",
            "slowk_period": "k_period",
            "slowd_period": "d_period",
        }
        outputs = (("k", "k"), ("d", "d"), ("j", "j"))

    for source, destination in aliases.items():
        if source in normalized:
            normalized[destination] = normalized.pop(source)
    get_factor(factor_id)
    return factor_id, normalized, outputs


class MultiAssetSimulationBroker:
    def __init__(
        self,
        *,
        initial_capital: float,
        leverage: float = 1.0,
        commission: float = 0.0005,
        slippage: float = 0.0005,
    ) -> None:
        self.portfolio = PortfolioState(initial_capital, initial_capital, total_value=initial_capital)
        self.leverage = max(1.0, float(leverage or 1.0))
        self.commission = max(0.0, float(commission or 0.0))
        self.slippage = max(0.0, float(slippage or 0.0))
        self.executions: list[dict[str, Any]] = []
        self.closed_trades: list[dict[str, Any]] = []
        self._entries: dict[str, dict[str, Any]] = {}
        self._protections: dict[str, ProtectionState] = {}
        self.protection_events: list[dict[str, Any]] = []
        self.order_ledger: list[dict[str, Any]] = []
        self.rebalance_records: list[dict[str, Any]] = []
        self.holding_snapshots: list[dict[str, Any]] = []
        self.protection_engine = ProtectionEngine()
        self.equity_curve: list[dict[str, Any]] = []
        self._order_sequence = 0

    def execute(
        self,
        orders: Iterable[OrderIntent],
        portal: MultiAssetDataPortal,
        timestamp: Any,
        *,
        price_overrides: Mapping[str, float] | None = None,
    ) -> list[OrderIntent]:
        deferred: list[OrderIntent] = []
        batch_orders = list(orders)
        if not batch_orders:
            return deferred
        equity_before = self.mark_to_market(portal, timestamp)
        cash_before = float(self.portfolio.available_cash)
        target_weights: dict[str, float] = {}
        batch_event_indexes: list[int] = []
        for order in batch_orders:
            self._order_sequence += 1
            order_id = f"{pd.Timestamp(timestamp).isoformat()}:{self._order_sequence}"
            override = (price_overrides or {}).get(order.symbol)
            bar = portal.bar_at(order.symbol, timestamp)
            open_price = float(override) if override is not None else (float(bar["open"]) if bar else None)
            blocked_reason = self._execution_block_reason(order, bar, open_price)
            if blocked_reason:
                status = "rejected" if order.attempts >= 4 else "deferred"
                event = self._order_event(order_id, order, timestamp, status, blocked_reason)
                self.order_ledger.append(event)
                batch_event_indexes.append(len(self.order_ledger) - 1)
                if status == "deferred":
                    deferred.append(replace(order, attempts=order.attempts + 1))
                continue
            current = self.portfolio.positions.get(order.symbol) or Position(order.symbol)
            equity = self.mark_to_market(portal, timestamp)
            target_qty = self._target_quantity(order, current, open_price, equity)
            target_weights[order.symbol] = target_qty * open_price / equity if equity else 0.0
            delta = target_qty - current.amount
            direction = 1 if delta > 0 else -1 if delta < 0 else 0
            if order.pending_direction and direction != order.pending_direction:
                self.order_ledger.append(self._order_event(
                    order_id, order, timestamp, "rejected", "target_already_met",
                    requested_quantity=0.0,
                ))
                batch_event_indexes.append(len(self.order_ledger) - 1)
                continue
            closes_position = (
                order.kind in {"target_quantity", "target_value", "target_percent"}
                and abs(target_qty) <= 1e-12
                and abs(current.amount) > 1e-12
            )
            if abs(delta) <= 1e-12 or (abs(delta * open_price) < 0.01 and not closes_position):
                self.order_ledger.append(self._order_event(
                    order_id, order, timestamp, "rejected", "target_already_met",
                    requested_quantity=0.0,
                ))
                batch_event_indexes.append(len(self.order_ledger) - 1)
                continue
            fill_price = open_price * (1.0 + self.slippage if delta > 0 else 1.0 - self.slippage)
            requested_delta = delta
            lot_size = self._lot_size(order.symbol, bar)
            delta = self._round_to_lot(delta, lot_size)
            if abs(delta) < lot_size - 1e-12:
                self.order_ledger.append(self._order_event(
                    order_id, order, timestamp, "rejected", "minimum_trade_unit",
                    requested_quantity=abs(requested_delta),
                ))
                batch_event_indexes.append(len(self.order_ledger) - 1)
                continue
            liquidity_cap = self._liquidity_cap(bar, lot_size)
            if liquidity_cap is not None and abs(delta) > liquidity_cap:
                delta = math.copysign(liquidity_cap, delta)
            feasible_delta, constraint_reason = self._feasible_delta(
                delta=delta,
                current=current,
                fill_price=fill_price,
                equity=equity,
                lot_size=lot_size,
            )
            if abs(feasible_delta) < lot_size - 1e-12:
                self.order_ledger.append(self._order_event(
                    order_id,
                    order,
                    timestamp,
                    "rejected",
                    constraint_reason or "position_limit",
                    requested_quantity=abs(requested_delta),
                ))
                batch_event_indexes.append(len(self.order_ledger) - 1)
                continue
            delta = feasible_delta
            target_qty = current.amount + delta
            remaining_quantity = max(0.0, abs(requested_delta) - abs(delta))
            has_tradable_remainder = (
                remaining_quantity + 1e-12 >= lot_size
                and remaining_quantity * open_price + 1e-12 >= 0.01
            )
            execution_status = "partial" if has_tradable_remainder else "filled"
            notional = abs(delta * fill_price)
            fee = notional * self.commission
            projected_cash = self.portfolio.available_cash - delta * fill_price - fee
            old_amount = current.amount
            old_cost = current.avg_cost
            current.amount = target_qty
            current.avg_cost = _next_average_cost(old_amount, current.avg_cost, delta, fill_price)
            current.last_price = fill_price
            self.portfolio.available_cash = projected_cash
            if abs(current.amount) <= 1e-12:
                self.portfolio.positions.pop(order.symbol, None)
                self._protections.pop(order.symbol, None)
            else:
                self.portfolio.positions[order.symbol] = current
                new_side = "long" if current.amount > 0 else "short"
                existing = self._protections.get(order.symbol)
                side_changed = existing is not None and existing.side != new_side
                if order.protection is not None or side_changed:
                    spec = order.protection or (existing.spec if existing else None)
                    if spec is not None:
                        self._protections[order.symbol] = ProtectionState.open(
                            symbol=order.symbol,
                            side=new_side,
                            entry_price=current.avg_cost,
                            spec=spec,
                            opened_at=timestamp,
                        )
            self.portfolio.total_value = self.portfolio.available_cash + sum(
                position.market_value for position in self.portfolio.positions.values()
            )
            execution_type, position_side = _execution_identity(old_amount, target_qty, delta)
            execution = {
                "order_id": order_id,
                "symbol": order.symbol,
                "time": str(pd.Timestamp(timestamp)),
                "side": "buy" if delta > 0 else "sell",
                "type": execution_type,
                "position_side": position_side,
                "quantity": abs(delta),
                "price": fill_price,
                "notional": notional,
                "commission": fee,
                "balance": self.portfolio.total_value,
                "reason": order.reason,
                "signal_time": str(order.signal_time) if order.signal_time is not None else str(pd.Timestamp(timestamp)),
                "fill_reference": "bar_open",
                "reference_price": open_price,
                "status": execution_status,
                "requested_quantity": abs(requested_delta),
            }
            self.executions.append(execution)
            reason = "filled"
            if execution_status == "partial":
                reason = constraint_reason or (
                    "insufficient_liquidity"
                    if liquidity_cap is not None and abs(requested_delta) > liquidity_cap
                    else "partial_fill"
                )
            self.order_ledger.append(self._order_event(
                order_id,
                order,
                timestamp,
                execution["status"],
                reason,
                requested_quantity=abs(requested_delta),
                filled_quantity=abs(delta),
                price=fill_price,
                commission=fee,
            ))
            batch_event_indexes.append(len(self.order_ledger) - 1)
            self._record_closed_trade(
                execution=execution,
                old_amount=old_amount,
                old_cost=old_cost,
                target_amount=target_qty,
            )
            if execution["status"] == "partial" and order.attempts < 4:
                if order.kind == "quantity":
                    remaining_value = math.copysign(
                        max(0.0, abs(requested_delta) - abs(delta)),
                        requested_delta,
                    )
                    deferred.append(replace(
                        order,
                        value=remaining_value,
                        attempts=order.attempts + 1,
                        pending_direction=1 if requested_delta > 0 else -1,
                    ))
                else:
                    deferred.append(replace(
                        order,
                        attempts=order.attempts + 1,
                        pending_direction=1 if requested_delta > 0 else -1,
                    ))
        self._record_rebalance(
            portal=portal,
            timestamp=timestamp,
            equity_before=equity_before,
            cash_before=cash_before,
            target_weights=target_weights,
            event_indexes=batch_event_indexes,
        )
        return deferred

    def _execution_block_reason(
        self,
        order: OrderIntent,
        bar: Mapping[str, Any] | None,
        open_price: float | None,
    ) -> str:
        if bar is None or open_price is None or not math.isfinite(open_price) or open_price <= 0:
            return "no_price"
        if _truthy(bar.get("suspended")) or _truthy(bar.get("is_suspended")):
            return "suspended"
        current = self.portfolio.positions.get(order.symbol) or Position(order.symbol)
        target = self._target_quantity(order, current, open_price, max(self.portfolio.total_value, 0.0))
        delta = target - current.amount
        if delta > 0 and (_truthy(bar.get("limit_up")) or _truthy(bar.get("is_limit_up"))):
            return "limit_up"
        if delta < 0 and (_truthy(bar.get("limit_down")) or _truthy(bar.get("is_limit_down"))):
            return "limit_down"
        return ""

    def _feasible_delta(
        self,
        *,
        delta: float,
        current: Position,
        fill_price: float,
        equity: float,
        lot_size: float,
    ) -> tuple[float, str]:
        feasible = delta
        reason = ""
        if feasible > 0:
            borrowing_floor = -equity * (self.leverage - 1.0)
            spendable = max(0.0, self.portfolio.available_cash - borrowing_floor)
            cash_cap = spendable / max(fill_price * (1.0 + self.commission), 1e-12)
            if feasible > cash_cap:
                feasible = cash_cap
                reason = "insufficient_cash"
        gross_limit = max(0.0, equity * self.leverage - self._gross_value(exclude=current.symbol))
        max_target_abs = gross_limit / max(fill_price, 1e-12)
        desired_target = current.amount + feasible
        if abs(desired_target) > max_target_abs + 1e-12:
            capped_target = math.copysign(max_target_abs, desired_target)
            feasible = capped_target - current.amount
            reason = "position_limit"
        return self._round_to_lot(feasible, lot_size), reason

    @staticmethod
    def _lot_size(symbol: str, bar: Mapping[str, Any] | None) -> float:
        explicit = float((bar or {}).get("lot_size") or 0.0)
        if explicit > 0:
            return explicit
        return 1e-8 if str(symbol).startswith("Crypto:") else 1.0

    @staticmethod
    def _round_to_lot(value: float, lot_size: float) -> float:
        if lot_size <= 0:
            return value
        units = math.floor(abs(value) / lot_size + 1e-8)
        return math.copysign(units * lot_size, value) if units else 0.0

    @staticmethod
    def _liquidity_cap(bar: Mapping[str, Any] | None, lot_size: float) -> float | None:
        volume = float((bar or {}).get("volume") or 0.0)
        if volume <= 0:
            return None
        cap = volume * 0.1
        units = math.floor(cap / lot_size + 1e-10)
        return units * lot_size if units else 0.0

    @staticmethod
    def _order_event(
        order_id: str,
        order: OrderIntent,
        timestamp: Any,
        status: str,
        reason: str,
        *,
        requested_quantity: float = 0.0,
        filled_quantity: float = 0.0,
        price: float = 0.0,
        commission: float = 0.0,
    ) -> dict[str, Any]:
        return {
            "orderId": order_id,
            "symbol": order.symbol,
            "kind": order.kind,
            "value": order.value,
            "reason": order.reason,
            "status": status,
            "statusReason": reason,
            "signalTime": str(order.signal_time) if order.signal_time is not None else str(pd.Timestamp(timestamp)),
            "eventTime": str(pd.Timestamp(timestamp)),
            "attempt": order.attempts + 1,
            "requestedQuantity": requested_quantity,
            "filledQuantity": filled_quantity,
            "price": price,
            "commission": commission,
        }

    def _record_rebalance(
        self,
        *,
        portal: MultiAssetDataPortal,
        timestamp: Any,
        equity_before: float,
        cash_before: float,
        target_weights: Mapping[str, float],
        event_indexes: list[int],
    ) -> None:
        equity_after = self.mark_to_market(portal, timestamp)
        actual_weights = {
            symbol: position.market_value / equity_after if equity_after else 0.0
            for symbol, position in self.portfolio.positions.items()
        }
        events = [self.order_ledger[index] for index in event_indexes]
        turnover = sum(float(item.get("filledQuantity") or 0.0) * float(item.get("price") or 0.0) for item in events)
        counts = {name: sum(1 for item in events if item.get("status") == name) for name in ("filled", "partial", "deferred", "rejected")}
        self.rebalance_records.append({
            "time": str(pd.Timestamp(timestamp)),
            "targetWeights": dict(target_weights),
            "actualWeights": actual_weights,
            "cashBefore": cash_before,
            "cashAfter": float(self.portfolio.available_cash),
            "equityBefore": equity_before,
            "equityAfter": equity_after,
            "turnover": turnover / equity_before if equity_before else 0.0,
            "orderCount": len(events),
            **counts,
        })

    def process_protections(
        self,
        portal: MultiAssetDataPortal,
        timestamp: Any,
    ) -> list[ProtectionDecision]:
        decisions: list[ProtectionDecision] = []
        for symbol, state in list(self._protections.items()):
            position = self.portfolio.positions.get(symbol)
            bar = portal.bar_at(symbol, timestamp)
            if position is None or bar is None:
                if position is None:
                    self._protections.pop(symbol, None)
                continue
            state.entry_price = float(position.avg_cost)
            decision = self.protection_engine.evaluate_bar(
                state,
                timestamp=timestamp,
                open_price=bar["open"],
                high_price=bar["high"],
                low_price=bar["low"],
            )
            if decision is None:
                continue
            execution_count = len(self.executions)
            self.execute(
                [OrderIntent(
                    symbol,
                    "target_quantity",
                    0.0,
                    decision.reason,
                    signal_time=pd.Timestamp(timestamp),
                )],
                portal,
                timestamp,
                price_overrides={symbol: decision.price},
            )
            if len(self.executions) == execution_count:
                latest = self.order_ledger[-1] if self.order_ledger else {}
                if latest.get("statusReason") == "target_already_met":
                    self._protections.pop(symbol, None)
                continue
            decisions.append(decision)
            self.protection_events.append({
                "symbol": symbol,
                "side": decision.side,
                "reason": decision.reason,
                "triggerPrice": decision.trigger_price,
                "fillReferencePrice": decision.price,
                "time": str(decision.timestamp),
            })
        return decisions

    def mark_to_market(self, portal: MultiAssetDataPortal, timestamp: Any) -> float:
        total = float(self.portfolio.available_cash)
        for symbol, position in self.portfolio.positions.items():
            price = portal.close_at(symbol, timestamp)
            if price is None:
                price = portal.current(symbol, "close", position.last_price)
            position.last_price = float(price or position.last_price or position.avg_cost)
            total += position.market_value
        self.portfolio.total_value = total
        return total

    def record_equity(self, portal: MultiAssetDataPortal, timestamp: Any) -> None:
        value = self.mark_to_market(portal, timestamp)
        gross = sum(abs(item.market_value) for item in self.portfolio.positions.values())
        net = sum(item.market_value for item in self.portfolio.positions.values())
        snapshot = {
            "time": str(pd.Timestamp(timestamp)),
            "value": round(value, 8),
            "cash": round(float(self.portfolio.available_cash), 8),
            "grossExposure": gross / value if value else 0.0,
            "netExposure": net / value if value else 0.0,
        }
        self.equity_curve.append(snapshot)
        self.holding_snapshots.append({
            **snapshot,
            "positions": {
                symbol: {
                    "quantity": position.amount,
                    "averageCost": position.avg_cost,
                    "lastPrice": position.last_price,
                    "marketValue": position.market_value,
                    "weight": position.market_value / value if value else 0.0,
                }
                for symbol, position in self.portfolio.positions.items()
            },
        })

    def _target_quantity(self, order: OrderIntent, current: Position, price: float, equity: float) -> float:
        notional_multiplier = self.leverage
        if order.kind == "quantity":
            return current.amount + order.value
        if order.kind == "value":
            return current.amount + order.value * notional_multiplier / price
        if order.kind == "target_quantity":
            return order.value
        if order.kind == "target_value":
            return order.value * notional_multiplier / price
        if order.kind == "target_percent":
            target_value = equity * order.value * notional_multiplier
            if target_value > 0:
                target_value /= (1.0 + self.slippage) * (1.0 + self.commission)
            return target_value / price
        raise StrategyV2ContractError(f"strategyV2.orderKindUnsupported:{order.kind}")

    def _gross_value(self, *, exclude: str = "") -> float:
        return sum(abs(item.market_value) for key, item in self.portfolio.positions.items() if key != exclude)

    def _record_closed_trade(
        self,
        *,
        execution: Mapping[str, Any],
        old_amount: float,
        old_cost: float,
        target_amount: float,
    ) -> None:
        symbol = str(execution["symbol"])
        delta = float(execution["quantity"]) * (1.0 if execution["side"] == "buy" else -1.0)
        closing_quantity = min(abs(old_amount), abs(delta)) if old_amount * delta < 0 else 0.0
        opening_quantity = max(0.0, abs(delta) - closing_quantity)
        total_quantity = max(abs(delta), 1e-12)
        fee = float(execution.get("commission") or 0.0)
        close_fee = fee * closing_quantity / total_quantity
        open_fee = fee - close_fee

        if closing_quantity > 1e-12:
            entry = self._entries.get(symbol) or {
                "time": execution["time"],
                "price": old_cost,
                "quantity": abs(old_amount),
                "commission": 0.0,
                "side": "long" if old_amount > 0 else "short",
            }
            entry_quantity = max(float(entry.get("quantity") or 0.0), closing_quantity)
            entry_fee = float(entry.get("commission") or 0.0) * closing_quantity / entry_quantity
            direction = 1.0 if old_amount > 0 else -1.0
            gross_profit = (float(execution["price"]) - float(entry.get("price") or old_cost)) * closing_quantity * direction
            profit = gross_profit - entry_fee - close_fee
            self.closed_trades.append({
                "symbol": symbol,
                "side": str(entry.get("side") or ("long" if old_amount > 0 else "short")),
                "entry_time": str(entry.get("time") or execution["time"]),
                "exit_time": str(execution["time"]),
                "entry_price": float(entry.get("price") or old_cost),
                "exit_price": float(execution["price"]),
                "quantity": closing_quantity,
                "amount": closing_quantity,
                "profit": profit,
                "commission": entry_fee + close_fee,
                "balance": float(execution.get("balance") or 0.0),
                "close_reason": str(execution.get("reason") or "strategy"),
            })
            remaining = max(0.0, entry_quantity - closing_quantity)
            if remaining > 1e-12 and old_amount * target_amount >= 0:
                entry["quantity"] = remaining
                entry["commission"] = max(0.0, float(entry.get("commission") or 0.0) - entry_fee)
                self._entries[symbol] = entry
            else:
                self._entries.pop(symbol, None)

        if opening_quantity > 1e-12:
            opening_side = "long" if target_amount > 0 else "short"
            existing = self._entries.get(symbol)
            if existing and existing.get("side") == opening_side:
                previous_quantity = float(existing.get("quantity") or 0.0)
                combined_quantity = previous_quantity + opening_quantity
                existing["price"] = (
                    float(existing.get("price") or execution["price"]) * previous_quantity
                    + float(execution["price"]) * opening_quantity
                ) / combined_quantity
                existing["quantity"] = combined_quantity
                existing["commission"] = float(existing.get("commission") or 0.0) + open_fee
            else:
                self._entries[symbol] = {
                    "time": execution["time"],
                    "price": float(execution["price"]),
                    "quantity": opening_quantity,
                    "commission": open_fee,
                    "side": opening_side,
                }


class StrategyV2BacktestRunner:
    VERSION = "quantdinger-strategy-api-v2"

    def __init__(
        self,
        *,
        code: str,
        frames: Mapping[str, pd.DataFrame],
        initial_capital: float,
        params: Mapping[str, Any] | None = None,
        leverage_enabled: bool = False,
        leverage: float = 1.0,
        commission: float = 0.0005,
        slippage: float = 0.0005,
        universe_resolver=None,
    ) -> None:
        self.program: CompiledStrategyV2 = compile_strategy_v2(code)
        requested_leverage = max(1.0, float(leverage or 1.0)) if leverage_enabled else 1.0
        if requested_leverage > 1.0 and not self.program.manifest.leverage_allowed:
            raise StrategyV2ContractError("strategyV2.leverageNotAllowed")
        if requested_leverage > self.program.manifest.max_leverage:
            raise StrategyV2ContractError("strategyV2.leverageExceedsStrategyLimit")
        self.portal = MultiAssetDataPortal(frames, universe_resolver=universe_resolver)
        self.broker = MultiAssetSimulationBroker(
            initial_capital=initial_capital,
            leverage=requested_leverage,
            commission=commission,
            slippage=slippage,
        )
        self.context = StrategyRuntimeContext(portal=self.portal, portfolio=self.broker.portfolio, params=params)
        self.logs: list[str] = []
        self._bind_runtime_api()

    def run(self, *, start_date: Any = None, end_date: Any = None) -> dict[str, Any]:
        timestamps = self.portal.timestamps
        if start_date is not None:
            timestamps = timestamps[timestamps >= pd.Timestamp(start_date)]
        if end_date is not None:
            timestamps = timestamps[timestamps <= pd.Timestamp(end_date)]
        if timestamps.empty:
            raise StrategyV2ContractError("strategyV2.backtestRangeEmpty")

        previous: pd.Timestamp | None = None
        pending_orders: list[OrderIntent] = []
        for timestamp in timestamps:
            self.context.current_dt = pd.Timestamp(timestamp)
            self.context.previous_trading_date = previous
            self.portal.set_clock(timestamp, include_current=False)
            if pending_orders:
                pending_orders = self.broker.execute(pending_orders, self.portal, timestamp)
            self.broker.process_protections(self.portal, timestamp)

            self._invoke("before_trading_start", self.context, self.context.data)
            opening_orders = self.context.flush_orders()
            for schedule in self.program.manifest.schedules:
                if self._schedule_due(
                    schedule,
                    timestamp,
                    previous,
                    self.program.manifest.primary_frequency,
                ):
                    self._invoke(schedule.callback, self.context, self.context.data)
                    opening_orders.extend(self.context.flush_orders())
            if self.program.manifest.strategy_type == "portfolio" and not self.program.manifest.schedules:
                self._invoke("on_rebalance", self.context, self.portal.panel())
                opening_orders.extend(self.context.flush_orders())
            if opening_orders:
                pending_orders = _merge_pending(
                    pending_orders,
                    self.broker.execute(opening_orders, self.portal, timestamp),
                )

            self.portal.set_clock(timestamp, include_current=True)
            self._invoke("handle_data", self.context, self.context.data)
            pending_orders = _merge_pending(pending_orders, self.context.flush_orders())
            self._invoke("after_trading_end", self.context, self.context.data)
            pending_orders = _merge_pending(pending_orders, self.context.flush_orders())
            self.logs.extend(self.context.flush_logs())
            self.broker.record_equity(self.portal, timestamp)
            previous = pd.Timestamp(timestamp)

        return self._result()

    def _bind_runtime_api(self) -> None:
        ctx = self.context
        bindings = {
            "order": ctx.order,
            "order_value": ctx.order_value,
            "order_target": ctx.order_target,
            "order_target_value": ctx.order_target_value,
            "order_target_percent": ctx.order_target_percent,
            "set_default_protection": ctx.set_default_protection,
            "get_position": ctx.get_position,
            "get_positions": ctx.get_positions,
            "get_history": ctx.get_history,
            "history": ctx.get_history,
            "get_index_stocks": ctx.get_index_stocks,
            "get_universe_stocks": ctx.get_universe_stocks,
            "indicator": ctx.indicator,
            "factor": ctx.factor,
            "get_factors": ctx.get_factors,
            "get_fundamentals": ctx.get_fundamentals,
            "is_trade": ctx.is_trade,
            "run_daily": lambda *args, **kwargs: None,
            "run_weekly": lambda *args, **kwargs: None,
            "run_monthly": lambda *args, **kwargs: None,
            "log": ctx.logger,
        }
        self.program.namespace.update(bindings)

    def _invoke(self, handler_name: str, *args: Any) -> Any:
        handler = self.program.handler(handler_name)
        if not callable(handler):
            return None
        try:
            signature = inspect.signature(handler)
            positional = [
                item for item in signature.parameters.values()
                if item.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ]
            if any(item.kind == inspect.Parameter.VAR_POSITIONAL for item in signature.parameters.values()):
                return handler(*args)
            return handler(*args[:len(positional)])
        except StrategyV2ContractError:
            raise
        except Exception as exc:
            raise StrategyV2ContractError(f"strategyV2.runtimeFailed:{handler_name}:{exc}") from exc

    @staticmethod
    def _schedule_due(
        schedule,
        current: pd.Timestamp,
        previous: pd.Timestamp | None,
        bar_frequency: str = "1d",
    ) -> bool:
        current = pd.Timestamp(current)
        previous = pd.Timestamp(previous) if previous is not None else None
        scheduled_date = current.normalize()
        if schedule.frequency == "weekly":
            target_weekday = max(1, min(7, int(schedule.weekday or 1))) - 1
            scheduled_date = current.normalize() + pd.Timedelta(days=target_weekday - current.weekday())
        elif schedule.frequency == "monthly":
            target_day = max(1, int(schedule.monthday or 1))
            last_day = calendar.monthrange(current.year, current.month)[1]
            scheduled_date = pd.Timestamp(
                year=current.year,
                month=current.month,
                day=min(target_day, last_day),
                tz=current.tz,
            )
        elif schedule.frequency != "daily":
            return False

        scheduled_at = scheduled_date
        if _is_intraday_frequency(bar_frequency) and schedule.time:
            scheduled_at += _parse_schedule_time(schedule.time)
        if current < scheduled_at:
            return False
        if previous is None:
            return True
        if schedule.frequency == "daily" and not _is_intraday_frequency(bar_frequency):
            return current.date() != previous.date()
        return previous < scheduled_at <= current

    def _result(self) -> dict[str, Any]:
        initial = float(self.broker.portfolio.starting_cash)
        final = float(self.broker.portfolio.total_value)
        total_return = (final / initial - 1.0) * 100.0 if initial else 0.0
        values = [float(item["value"]) for item in self.broker.equity_curve]
        peak = values[0] if values else initial
        max_drawdown = 0.0
        for value in values:
            peak = max(peak, value)
            if peak > 0:
                max_drawdown = min(max_drawdown, (value / peak - 1.0) * 100.0)
        closed_trades = list(self.broker.closed_trades)
        executions = list(self.broker.executions)
        profits = [float(item.get("profit") or 0.0) for item in closed_trades]
        wins = [value for value in profits if value > 0]
        losses = [value for value in profits if value < 0]
        returns = pd.Series(values, dtype="float64").pct_change().dropna() if values else pd.Series(dtype="float64")
        volatility = float(returns.std(ddof=0)) if not returns.empty else 0.0
        periods_per_year = _periods_per_year(
            self.program.manifest.primary_frequency,
            self.program.manifest.markets,
        )
        sharpe_ratio = float(returns.mean() / volatility * math.sqrt(periods_per_year)) if volatility > 0 else 0.0
        annualized_volatility = volatility * math.sqrt(periods_per_year) * 100.0
        elapsed_days = 0.0
        if len(self.broker.equity_curve) > 1:
            first_time = pd.Timestamp(self.broker.equity_curve[0]["time"])
            last_time = pd.Timestamp(self.broker.equity_curve[-1]["time"])
            elapsed_days = max(0.0, (last_time - first_time).total_seconds() / 86400.0)
        annualized_return = 0.0
        if elapsed_days > 0 and initial > 0 and final > 0:
            annualized_return = ((final / initial) ** (365.25 / elapsed_days) - 1.0) * 100.0
        win_rate = len(wins) / len(profits) * 100.0 if profits else 0.0
        average_win = sum(wins) / len(wins) if wins else 0.0
        average_loss = abs(sum(losses) / len(losses)) if losses else 0.0
        profit_loss_ratio = average_win / average_loss if average_loss > 0 else 0.0
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)
        average_profit = sum(profits) / len(profits) if profits else 0.0
        attribution = self._attribution(initial)
        return {
            "totalReturn": total_return,
            "total_return": total_return,
            "finalEquity": final,
            "maxDrawdown": max_drawdown,
            "totalTrades": len(closed_trades),
            "totalExecutions": len(executions),
            "sampleCount": len(self.broker.equity_curve),
            "equityCurve": list(self.broker.equity_curve),
            "holdingSnapshots": list(self.broker.holding_snapshots),
            "rebalanceRecords": list(self.broker.rebalance_records),
            "orderLedger": list(self.broker.order_ledger),
            "trades": closed_trades,
            "closedTrades": closed_trades,
            "rawTrades": executions,
            "executions": executions,
            "winRate": win_rate,
            "winningTrades": len(wins),
            "losingTrades": len(losses),
            "grossProfit": gross_profit,
            "grossLoss": gross_loss,
            "avgWin": average_win,
            "avgLoss": -average_loss if losses else 0.0,
            "profitFactor": profit_factor,
            "profitLossRatio": profit_loss_ratio,
            "bestTrade": max(profits) if profits else 0.0,
            "worstTrade": min(profits) if profits else 0.0,
            "avgTrade": average_profit,
            "averageProfit": average_profit,
            "totalProfit": final - initial,
            "sharpeRatio": sharpe_ratio,
            "annualizedReturn": annualized_return,
            "annualizedVolatility": annualized_volatility,
            "periodsPerYear": periods_per_year,
            "totalCommission": sum(float(item.get("commission") or 0.0) for item in executions),
            "positions": {
                key: {
                    "amount": value.amount,
                    "avgCost": value.avg_cost,
                    "lastPrice": value.last_price,
                    "marketValue": value.market_value,
                }
                for key, value in self.broker.portfolio.positions.items()
            },
            "protectionEvents": list(self.broker.protection_events),
            "attribution": attribution,
            "logs": list(self.logs),
            "manifest": self.program.manifest.metadata(),
            "engine": {"version": self.VERSION},
            "audit": self._reconcile(),
        }

    def _attribution(self, initial: float) -> dict[str, Any]:
        commission_by_symbol: dict[str, float] = {}
        realized_by_symbol: dict[str, float] = {}
        for execution in self.broker.executions:
            symbol = str(execution.get("symbol") or "")
            commission_by_symbol[symbol] = commission_by_symbol.get(symbol, 0.0) + float(execution.get("commission") or 0.0)
        for trade in self.broker.closed_trades:
            symbol = str(trade.get("symbol") or "")
            realized_by_symbol[symbol] = realized_by_symbol.get(symbol, 0.0) + float(trade.get("profit") or 0.0)
        rows = []
        for symbol in sorted(set(commission_by_symbol) | set(realized_by_symbol) | set(self.broker.portfolio.positions)):
            position = self.broker.portfolio.positions.get(symbol)
            unrealized = 0.0
            if position is not None:
                unrealized = (position.last_price - position.avg_cost) * position.amount
            realized = realized_by_symbol.get(symbol, 0.0)
            fee = commission_by_symbol.get(symbol, 0.0)
            rows.append({
                "symbol": symbol,
                "industry": "Unclassified",
                "realizedProfit": realized,
                "unrealizedProfit": unrealized,
                "commission": fee,
                "netContribution": (realized + unrealized) / initial if initial else 0.0,
            })
        statuses = {name: sum(1 for item in self.broker.order_ledger if item.get("status") == name) for name in ("filled", "partial", "deferred", "rejected")}
        total_commission = sum(commission_by_symbol.values())
        return {
            "symbols": rows,
            "industries": [{
                "industry": "Unclassified",
                "netContribution": sum(float(item["netContribution"]) for item in rows),
                "commission": total_commission,
            }],
            "feeDrag": total_commission / initial if initial else 0.0,
            "orderStatus": statuses,
        }

    def _reconcile(self) -> dict[str, Any]:
        initial = float(self.broker.portfolio.starting_cash)
        cash = initial
        quantities: dict[str, float] = {}
        fee_mismatches: list[int] = []
        fill_mismatches: list[int] = []
        timing_mismatches: list[int] = []
        for index, execution in enumerate(self.broker.executions):
            side = str(execution.get("side") or "")
            quantity = float(execution.get("quantity") or 0.0)
            notional = float(execution.get("notional") or 0.0)
            fee = float(execution.get("commission") or 0.0)
            expected_fee = notional * self.broker.commission
            if abs(fee - expected_fee) > max(1e-8, abs(expected_fee) * 1e-9):
                fee_mismatches.append(index)
            signed_quantity = quantity if side == "buy" else -quantity
            cash -= signed_quantity * float(execution.get("price") or 0.0) + fee
            symbol = str(execution.get("symbol") or "")
            quantities[symbol] = quantities.get(symbol, 0.0) + signed_quantity

            reference_price = float(execution.get("reference_price") or 0.0)
            expected_price = reference_price * (
                1.0 + self.broker.slippage if side == "buy" else 1.0 - self.broker.slippage
            )
            actual_price = float(execution.get("price") or 0.0)
            if reference_price <= 0 or abs(actual_price - expected_price) > max(1e-8, abs(expected_price) * 1e-9):
                fill_mismatches.append(index)
            signal_time = pd.Timestamp(execution.get("signal_time"))
            fill_time = pd.Timestamp(execution.get("time"))
            if fill_time < signal_time:
                timing_mismatches.append(index)

        position_mismatches = []
        for symbol in sorted(set(quantities) | set(self.broker.portfolio.positions)):
            actual = float((self.broker.portfolio.positions.get(symbol) or Position(symbol)).amount)
            if abs(quantities.get(symbol, 0.0) - actual) > 1e-8:
                position_mismatches.append(symbol)
        ledger_equity = cash + sum(position.market_value for position in self.broker.portfolio.positions.values())
        final_equity = float(self.broker.portfolio.total_value)
        equity_difference = ledger_equity - final_equity
        passed = not (
            fee_mismatches
            or fill_mismatches
            or timing_mismatches
            or position_mismatches
        ) and abs(equity_difference) <= 1e-6
        return {
            "passed": passed,
            "scope": ["fees", "fill_prices", "fill_timing", "positions", "cash", "final_equity"],
            "executionCount": len(self.broker.executions),
            "closedTradeCount": len(self.broker.closed_trades),
            "cashLedger": cash,
            "ledgerEquity": ledger_equity,
            "reportedEquity": final_equity,
            "equityDifference": equity_difference,
            "feeMismatchIndexes": fee_mismatches,
            "fillMismatchIndexes": fill_mismatches,
            "timingMismatchIndexes": timing_mismatches,
            "positionMismatchSymbols": position_mismatches,
        }


class StrategyV2LiveSession:
    """Stateful bar-event session shared by signal-only and live deployments."""

    def __init__(
        self,
        *,
        code: str,
        frames: Mapping[str, pd.DataFrame],
        initial_capital: float,
        params: Mapping[str, Any] | None = None,
        universe_resolver=None,
    ) -> None:
        self.program = compile_strategy_v2(code)
        self._universe_resolver = universe_resolver
        self.portal = MultiAssetDataPortal(frames, universe_resolver=universe_resolver)
        self.portfolio = PortfolioState(initial_capital, initial_capital, total_value=initial_capital)
        self.context = StrategyRuntimeContext(portal=self.portal, portfolio=self.portfolio, params=params)
        self.last_processed: pd.Timestamp | None = None
        self.protection_engine = ProtectionEngine()
        self.protection_specs: dict[str, ProtectionSpec] = {}
        self.protection_states: dict[str, ProtectionState] = {}
        self._protection_exit_pending: set[str] = set()
        self._bind_runtime_api()

    def process(self, frames: Mapping[str, pd.DataFrame]) -> tuple[list[OrderIntent], list[str], pd.Timestamp]:
        portal = MultiAssetDataPortal(frames, universe_resolver=self._universe_resolver)
        if portal.timestamps.empty:
            raise StrategyV2ContractError("strategyV2.noMarketData")
        timestamp = pd.Timestamp(portal.timestamps[-1])
        if self.last_processed is not None and timestamp <= self.last_processed:
            return [], [], timestamp

        self.portal = portal
        self.context.portal = portal
        self.context.data = StrategyDataView(portal)
        self.context.current_dt = timestamp
        self.context.previous_trading_date = self.last_processed
        portal.set_clock(timestamp, include_current=True)

        if self.last_processed is None or timestamp.date() != self.last_processed.date():
            self._invoke("before_trading_start", self.context, self.context.data)
        for schedule in self.program.manifest.schedules:
            if StrategyV2BacktestRunner._schedule_due(
                schedule,
                timestamp,
                self.last_processed,
                self.program.manifest.primary_frequency,
            ):
                self._invoke(schedule.callback, self.context, self.context.data)
        if self.program.manifest.strategy_type == "portfolio" and not self.program.manifest.schedules:
            self._invoke("on_rebalance", self.context, portal.panel())
        self._invoke("handle_data", self.context, self.context.data)
        orders = self.context.flush_orders()
        self._capture_protection_intents(orders)
        logs = self.context.flush_logs()
        self.last_processed = timestamp
        return orders, logs, timestamp

    def synchronize_positions(
        self,
        positions: Mapping[str, Mapping[str, Any]],
        *,
        available_cash: float | None = None,
        total_value: float | None = None,
    ) -> None:
        synced: dict[str, Position] = {}
        for raw_symbol, raw in positions.items():
            try:
                key = self.portal.resolve_key(raw_symbol)
            except Exception:
                key = str(raw_symbol)
            amount = float(raw.get("amount") or 0.0)
            side = str(raw.get("side") or "long").strip().lower()
            if side == "short" and amount > 0:
                amount = -amount
            synced[key] = Position(
                symbol=key,
                amount=amount,
                avg_cost=float(raw.get("avg_cost") or 0.0),
                last_price=float(raw.get("last_price") or 0.0),
            )
        self.portfolio.positions = synced
        for symbol, position in synced.items():
            spec = self.protection_specs.get(symbol)
            if spec is None or abs(position.amount) <= 1e-12 or position.avg_cost <= 0:
                continue
            side = "long" if position.amount > 0 else "short"
            state = self.protection_states.get(symbol)
            if state is None or state.side != side:
                self.protection_states[symbol] = ProtectionState.open(
                    symbol=symbol,
                    side=side,
                    entry_price=position.avg_cost,
                    spec=spec,
                    opened_at=self.context.current_dt or pd.Timestamp.utcnow(),
                )
            else:
                state.entry_price = float(position.avg_cost)
        for symbol in list(self._protection_exit_pending):
            if symbol not in synced:
                self._protection_exit_pending.discard(symbol)
                self.protection_specs.pop(symbol, None)
                self.protection_states.pop(symbol, None)
        if available_cash is not None:
            self.portfolio.available_cash = float(available_cash)
        if total_value is not None:
            self.portfolio.total_value = float(total_value)

    def evaluate_protections(
        self,
        prices: Mapping[str, float],
        *,
        timestamp: object = None,
    ) -> list[OrderIntent]:
        ts = pd.Timestamp(timestamp or pd.Timestamp.utcnow())
        exits: list[OrderIntent] = []
        for symbol, state in list(self.protection_states.items()):
            position = self.portfolio.positions.get(symbol)
            if position is None or abs(position.amount) <= 1e-12:
                continue
            price = prices.get(symbol)
            if price is None:
                continue
            decision = self.protection_engine.evaluate_price(
                state,
                timestamp=ts,
                price=float(price or 0.0),
            )
            if decision is None:
                continue
            exits.append(OrderIntent(symbol, "target_quantity", 0.0, decision.reason))
            self._protection_exit_pending.add(symbol)
        return exits

    def protection_snapshot(self) -> dict[str, Any]:
        return {
            "specs": {symbol: spec.metadata() for symbol, spec in self.protection_specs.items()},
            "states": {symbol: state.metadata() for symbol, state in self.protection_states.items()},
            "exitPending": sorted(self._protection_exit_pending),
        }

    def restore_protection_snapshot(self, values: Mapping[str, Any] | None) -> None:
        raw = values or {}
        specs: dict[str, ProtectionSpec] = {}
        for symbol, item in (raw.get("specs") or {}).items():
            spec = ProtectionSpec.from_value(item)
            if spec is not None:
                specs[str(symbol)] = spec
        states: dict[str, ProtectionState] = {}
        for symbol, item in (raw.get("states") or {}).items():
            if not isinstance(item, Mapping):
                continue
            state = ProtectionState.from_metadata(item)
            if state is not None:
                states[str(symbol)] = state
        self.protection_specs = specs
        self.protection_states = states
        self._protection_exit_pending = {str(item) for item in (raw.get("exitPending") or [])}

    def _capture_protection_intents(self, orders: Iterable[OrderIntent]) -> None:
        for order in orders:
            if order.protection is not None:
                self.protection_specs[order.symbol] = order.protection
                self._protection_exit_pending.discard(order.symbol)
            if order.kind in {"target_quantity", "target_value", "target_percent"} and abs(order.value) <= 1e-12:
                self._protection_exit_pending.add(order.symbol)

    def _bind_runtime_api(self) -> None:
        ctx = self.context
        self.program.namespace.update({
            "order": ctx.order,
            "order_value": ctx.order_value,
            "order_target": ctx.order_target,
            "order_target_value": ctx.order_target_value,
            "order_target_percent": ctx.order_target_percent,
            "set_default_protection": ctx.set_default_protection,
            "get_position": ctx.get_position,
            "get_positions": ctx.get_positions,
            "get_history": ctx.get_history,
            "history": ctx.get_history,
            "get_index_stocks": ctx.get_index_stocks,
            "get_universe_stocks": ctx.get_universe_stocks,
            "indicator": ctx.indicator,
            "factor": ctx.factor,
            "get_factors": ctx.get_factors,
            "get_fundamentals": ctx.get_fundamentals,
            "is_trade": ctx.is_trade,
            "run_daily": lambda *args, **kwargs: None,
            "run_weekly": lambda *args, **kwargs: None,
            "run_monthly": lambda *args, **kwargs: None,
            "log": ctx.logger,
        })

    def _invoke(self, handler_name: str, *args: Any) -> Any:
        handler = self.program.handler(handler_name)
        if not callable(handler):
            return None
        try:
            signature = inspect.signature(handler)
            positional = [
                item for item in signature.parameters.values()
                if item.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            ]
            if any(item.kind == inspect.Parameter.VAR_POSITIONAL for item in signature.parameters.values()):
                return handler(*args)
            return handler(*args[:len(positional)])
        except StrategyV2ContractError:
            raise
        except Exception as exc:
            raise StrategyV2ContractError(f"strategyV2.runtimeFailed:{handler_name}:{exc}") from exc


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value) if value is not None and not pd.isna(value) else False


def _is_intraday_frequency(frequency: str) -> bool:
    normalized = str(frequency or "1d").strip().lower()
    return normalized.endswith("m") or normalized.endswith("h")


def _parse_schedule_time(value: str) -> pd.Timedelta:
    try:
        hours, minutes = str(value or "00:00").split(":", 1)
        return pd.Timedelta(hours=int(hours), minutes=int(minutes))
    except (TypeError, ValueError):
        return pd.Timedelta(0)


def _merge_pending(current: Iterable[OrderIntent], incoming: Iterable[OrderIntent]) -> list[OrderIntent]:
    output = list(current)
    for order in incoming:
        if order.kind.startswith("target_"):
            output = [item for item in output if not (item.symbol == order.symbol and item.kind.startswith("target_"))]
        output.append(order)
    return output


def _next_average_cost(old_amount: float, old_cost: float, delta: float, fill_price: float) -> float:
    new_amount = old_amount + delta
    if abs(new_amount) <= 1e-12:
        return 0.0
    if old_amount == 0 or old_amount * delta > 0:
        return (old_amount * old_cost + delta * fill_price) / new_amount
    if old_amount * new_amount < 0:
        return fill_price
    return old_cost


def _periods_per_year(frequency: str, markets: Iterable[str]) -> float:
    normalized = str(frequency or "1d").strip().lower()
    is_crypto = "Crypto" in set(markets)
    trading_days = 365.25 if is_crypto else 252.0
    if normalized.endswith("m"):
        minutes = max(1, int(normalized[:-1] or 1))
        session_minutes = 1440.0 if is_crypto else 390.0
        return trading_days * session_minutes / minutes
    if normalized.endswith("h"):
        hours = max(1, int(normalized[:-1] or 1))
        session_hours = 24.0 if is_crypto else 6.5
        return trading_days * session_hours / hours
    if normalized.endswith("w"):
        return 52.0
    return trading_days


def _execution_identity(old_amount: float, target_amount: float, delta: float) -> tuple[str, str]:
    if old_amount >= 0 and target_amount >= 0:
        if delta > 0:
            return ("open_long" if abs(old_amount) <= 1e-12 else "add_long", "long")
        return ("close_long" if abs(target_amount) <= 1e-12 else "reduce_long", "long")
    if old_amount <= 0 and target_amount <= 0:
        if delta < 0:
            return ("open_short" if abs(old_amount) <= 1e-12 else "add_short", "short")
        return ("close_short" if abs(target_amount) <= 1e-12 else "reduce_short", "short")
    return ("reverse_to_long" if target_amount > 0 else "reverse_to_short", "long" if target_amount > 0 else "short")


def _fundamental_column(value: object) -> str:
    aliases = {
        "PE": "pe_ratio",
        "PB": "pb_ratio",
        "ROE": "return_on_equity",
        "MARKET_CAP": "market_cap",
        "REVENUE_GROWTH": "revenue_growth",
        "DEBT_TO_EQUITY": "debt_to_equity",
        "FREE_CASH_FLOW": "free_cash_flow",
    }
    raw = str(value or "").strip()
    return aliases.get(raw.upper(), raw.lower())
