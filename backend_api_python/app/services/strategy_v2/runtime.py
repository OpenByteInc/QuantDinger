"""Unified event-driven simulation runtime for Strategy API V2."""

from __future__ import annotations

import inspect
import math
import calendar
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
from .frequencies import normalize_frequency
from .protection import ProtectionDecision, ProtectionEngine, ProtectionSpec, ProtectionState


def _backtest_time_iso(value: Any) -> str:
    """Serialize the UTC-naive market index as an unambiguous UTC instant."""
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.floor("s").isoformat().replace("+00:00", "Z")


@dataclass
class Position:
    symbol: str
    amount: float = 0.0
    avg_cost: float = 0.0
    last_price: float = 0.0
    position_side: str = ""

    @property
    def market_value(self) -> float:
        return self.amount * self.last_price

@dataclass
class PortfolioState:
    starting_cash: float
    available_cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    total_value: float = 0.0


def _normalize_position_side(value: object) -> str:
    side = str(value or "").strip().lower()
    return side if side in {"long", "short"} else ""


def _position_key(symbol: object, position_side: object = "") -> str:
    base = str(symbol or "")
    side = _normalize_position_side(position_side)
    return f"{base}::{side}" if side else base


def _grid_order_identity(client_order_id: object) -> tuple[int, str, str, int] | None:
    """Return the stable grid-cell identity encoded by the V2 robot template."""
    parts = str(client_order_id or "").strip().split("-")
    if len(parts) != 5 or parts[0] != "grid":
        return None
    try:
        cell_index = int(parts[1])
        cycle = int(parts[4])
    except (TypeError, ValueError):
        return None
    position_side = _normalize_position_side(parts[2])
    phase = str(parts[3] or "").strip().lower()
    if (
        cell_index < 0
        or cycle < 1
        or not position_side
        or phase not in {"entry", "exit"}
    ):
        return None
    return cell_index, position_side, phase, cycle


def _snapshot_state_value(value: Any) -> Any:
    if isinstance(value, pd.Timestamp):
        return {
            "__strategy_v2_type__": "timestamp",
            "value": value.isoformat(),
        }
    if isinstance(value, Mapping):
        return {
            str(key): _snapshot_state_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_snapshot_state_value(item) for item in value]
    return value


def _restore_state_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        if value.get("__strategy_v2_type__") == "timestamp":
            return pd.Timestamp(value.get("value"))
        return {
            str(key): _restore_state_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_restore_state_value(item) for item in value]
    return value


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
    position_side: str = ""
    client_order_id: str = ""


class StrategyDataView:
    def __init__(self, portal: MultiAssetDataPortal):
        self.portal = portal

    def history(
        self,
        symbols: object,
        count: int,
        fields: object = None,
        *,
        frequency: object = None,
        **_: Any,
    ):
        return self.portal.history(symbols, count=count, fields=fields, frequency=frequency)

    def current(
        self,
        symbol: object,
        field: str = "close",
        *,
        frequency: object = None,
    ) -> float:
        return self.portal.current(symbol, field, frequency=frequency)

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
        self._order_sequence = 0
        self._order_statuses: dict[str, dict[str, Any]] = {}
        self._cancelled_order_ids: set[str] = set()
        self._last_exit_reasons: dict[str, str] = {}
        self.logger = StrategyRuntimeLogger(self.log)

    def set_default_protection(self, **values: Any) -> None:
        self._default_protection = ProtectionSpec.from_value(values)

    def order(self, symbol: object, amount: object, **kwargs: Any) -> str | None:
        return self._queue(symbol, "quantity", amount, kwargs)

    def order_value(self, symbol: object, value: object, **kwargs: Any) -> str | None:
        return self._queue(symbol, "value", value, kwargs)

    def order_target(self, symbol: object, amount: object, **kwargs: Any) -> str | None:
        return self._queue(symbol, "target_quantity", amount, kwargs)

    def order_target_value(self, symbol: object, value: object, **kwargs: Any) -> str | None:
        return self._queue(symbol, "target_value", value, kwargs)

    def order_target_percent(self, symbol: object, percent: object, **kwargs: Any) -> str | None:
        return self._queue(symbol, "target_percent", percent, kwargs)

    def get_order_status(self, client_order_id: object) -> dict[str, Any]:
        reference = str(client_order_id or "").strip()
        return dict(self._order_statuses.get(reference) or {
            "client_order_id": reference,
            "status": "unknown",
            "filled_quantity": 0.0,
            "filled_notional": 0.0,
            "fee": 0.0,
        })

    def cancel_order(self, client_order_id: object) -> bool:
        reference = str(client_order_id or "").strip()
        if not reference:
            return False
        current = dict(self._order_statuses.get(reference) or {})
        if str(current.get("status") or "").strip().lower() == "filled":
            return False
        current.update({
            "client_order_id": reference,
            "status": "cancelled",
            "reason": "cancelled_by_strategy",
        })
        self._order_statuses[reference] = current
        self._cancelled_order_ids.add(reference)
        return True

    def flush_cancelled_order_ids(self) -> set[str]:
        references = set(self._cancelled_order_ids)
        self._cancelled_order_ids.clear()
        return references

    def update_order_statuses(self, values: Mapping[str, Mapping[str, Any]] | None) -> None:
        for reference, raw in (values or {}).items():
            key = str(reference or "").strip()
            if not key or not isinstance(raw, Mapping):
                continue
            self._order_statuses[key] = dict(raw)

    def order_references(self) -> set[str]:
        return set(self._order_statuses)

    def order_status_snapshot(self) -> dict[str, dict[str, Any]]:
        return {key: dict(value) for key, value in self._order_statuses.items()}

    def set_last_exit_reason(self, symbol: object, reason: object) -> None:
        self._last_exit_reasons[str(symbol or "")] = str(reason or "")

    def consume_last_exit_reason(self, symbol: object) -> str:
        return self._last_exit_reasons.pop(str(symbol or ""), "")

    def exit_reason_snapshot(self) -> dict[str, str]:
        return dict(self._last_exit_reasons)

    def restore_exit_reasons(self, values: Mapping[str, object] | None) -> None:
        self._last_exit_reasons = {
            str(key): str(value or "")
            for key, value in (values or {}).items()
        }

    def get_position(
        self,
        symbols: object = None,
        *,
        position_side: str = "",
    ) -> dict[str, Position] | Position:
        if symbols is None:
            return dict(self.portfolio.positions)
        if isinstance(symbols, str):
            try:
                key = self.portal.resolve_key(symbols)
            except Exception:
                key = str(symbols)
            leg_key = _position_key(key, position_side)
            current = self.portfolio.positions.get(leg_key)
            if current is not None:
                return current
            try:
                last_price = self.portal.current(key, "close", 0.0)
            except Exception:
                last_price = 0.0
            return Position(
                key,
                last_price=last_price,
                position_side=_normalize_position_side(position_side),
            )
        output: dict[str, Position] = {}
        for symbol in symbols:
            key = self.portal.resolve_key(symbol)
            leg_key = _position_key(key, position_side)
            if leg_key in self.portfolio.positions:
                output[leg_key] = self.portfolio.positions[leg_key]
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
        frames = self.portal.frames_for_frequency(frequency)
        symbols = security_list or list(frames.keys())
        return self.portal.history(
            symbols,
            count=int(count),
            fields=field,
            frequency=frequency,
        )

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
        frequency = normalize_frequency(
            params.pop("frequency", None),
            self.portal.driving_frequency,
        )
        frame = self.portal.visible_frame(target, frequency=frequency)
        library_id = str(name or "").strip()
        if is_talib_available():
            try:
                return compute_talib_indicator(library_id, frame, params)
            except Exception:
                pass
        return self._compute_builtin_indicator(
            library_id,
            target,
            frame,
            params,
            frequency=frequency,
        )

    def _compute_builtin_indicator(
        self,
        library_id: str,
        target: object,
        frame: pd.DataFrame,
        params: Mapping[str, Any],
        *,
        frequency: str,
    ) -> pd.Series | pd.DataFrame:
        factor_id, normalized, outputs = _builtin_indicator_contract(library_id, params)
        resolved_target = self.portal.resolve_key(target, frequency=frequency)
        cache_key = (
            resolved_target,
            frequency,
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
        frequency = normalize_frequency(
            params.pop("frequency", None),
            self.portal.driving_frequency,
        )
        frame = self.portal.visible_frame(target, frequency=frequency)
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

    def _queue(
        self,
        symbol: object,
        kind: str,
        value: object,
        kwargs: Mapping[str, Any],
    ) -> str | None:
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
                "trailing_rebase_on_scale_in",
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
        self._order_sequence += 1
        client_order_id = str(kwargs.get("client_order_id") or "").strip()
        return_reference = bool(client_order_id)
        if not client_order_id:
            timestamp = (
                pd.Timestamp(self.current_dt).isoformat()
                if self.current_dt is not None
                else "discovery"
            )
            client_order_id = f"strategy-v2:{timestamp}:{self._order_sequence}"
        client_order_id = client_order_id[:100]
        self._order_statuses.setdefault(client_order_id, {
            "client_order_id": client_order_id,
            "status": "queued",
            "filled_quantity": 0.0,
            "filled_notional": 0.0,
            "fee": 0.0,
        })
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
            position_side=_normalize_position_side(kwargs.get("position_side")),
            client_order_id=client_order_id,
        ))
        # Strategy API V2 historically returned None from order helpers.
        # Preserve that contract for existing user code; callers that opt into
        # a stable client_order_id receive the reference for status tracking.
        return client_order_id if return_reference else None

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
        self._grid_entries: dict[str, dict[str, Any]] = {}
        self._protections: dict[str, ProtectionState] = {}
        self.protection_events: list[dict[str, Any]] = []
        self.order_ledger: list[dict[str, Any]] = []
        self._resting_deferred_events: dict[str, int] = {}
        self.rebalance_records: list[dict[str, Any]] = []
        self.holding_snapshots: list[dict[str, Any]] = []
        self.protection_engine = ProtectionEngine()
        self.equity_curve: list[dict[str, Any]] = []
        self._order_sequence = 0
        self.bankrupt = False
        self.liquidation_events: list[dict[str, Any]] = []
        self.liquidation_adjustment = 0.0

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
            forced_liquidation = order.reason == "margin_liquidation"
            self._order_sequence += 1
            order_id = f"{pd.Timestamp(timestamp).isoformat()}:{self._order_sequence}"
            override = (price_overrides or {}).get(order.symbol)
            bar = portal.bar_at(order.symbol, timestamp)
            open_price = float(override) if override is not None else (float(bar["open"]) if bar else None)
            blocked_reason = "" if forced_liquidation else self._execution_block_reason(order, bar, open_price)
            if blocked_reason:
                status = "rejected" if order.attempts >= 4 else "deferred"
                event = self._order_event(order_id, order, timestamp, status, blocked_reason)
                batch_event_indexes.append(self._append_order_event(event))
                if status == "deferred":
                    deferred.append(replace(order, attempts=order.attempts + 1))
                continue
            position_key = _position_key(order.symbol, order.position_side)
            current = self.portfolio.positions.get(position_key) or Position(
                order.symbol,
                position_side=_normalize_position_side(order.position_side),
            )
            equity = self.mark_to_market(portal, timestamp)
            is_limit_order = order.order_type == "limit" or order.execution_algo == "limit"
            sizing_price = (
                float(order.limit_price)
                if is_limit_order and float(order.limit_price or 0.0) > 0
                else open_price
            )
            target_qty = self._target_quantity(order, current, sizing_price, equity)
            target_weights[position_key] = target_qty * sizing_price / equity if equity else 0.0
            delta = target_qty - current.amount
            direction = 1 if delta > 0 else -1 if delta < 0 else 0
            if order.pending_direction and direction != order.pending_direction:
                batch_event_indexes.append(self._append_order_event(self._order_event(
                    order_id, order, timestamp, "rejected", "target_already_met",
                    requested_quantity=0.0,
                )))
                continue
            closes_position = (
                order.kind in {"target_quantity", "target_value", "target_percent"}
                and abs(target_qty) <= 1e-12
                and abs(current.amount) > 1e-12
            )
            if abs(delta) <= 1e-12 or (abs(delta * sizing_price) < 0.01 and not closes_position):
                batch_event_indexes.append(self._append_order_event(self._order_event(
                    order_id, order, timestamp, "rejected", "target_already_met",
                    requested_quantity=0.0,
                )))
                continue
            fill_reference = "bar_open"
            if is_limit_order:
                limit_price = float(order.limit_price or 0.0)
                high_price = float((bar or {}).get("high") or open_price)
                low_price = float((bar or {}).get("low") or open_price)
                if limit_price <= 0:
                    batch_event_indexes.append(self._append_order_event(self._order_event(
                        order_id,
                        order,
                        timestamp,
                        "rejected",
                        "limit_price_required",
                        requested_quantity=abs(delta),
                    )))
                    continue
                if delta > 0 and open_price <= limit_price:
                    fill_price = open_price
                    fill_reference = "gap_open"
                elif delta > 0 and low_price <= limit_price:
                    fill_price = limit_price
                    fill_reference = "limit"
                elif delta < 0 and open_price >= limit_price:
                    fill_price = open_price
                    fill_reference = "gap_open"
                elif delta < 0 and high_price >= limit_price:
                    fill_price = limit_price
                    fill_reference = "limit"
                else:
                    batch_event_indexes.append(self._append_order_event(self._order_event(
                        order_id,
                        order,
                        timestamp,
                        "deferred",
                        "limit_not_reached",
                        requested_quantity=abs(delta),
                        price=limit_price,
                    ), coalesce_resting=True))
                    # Resting limits remain active until filled; unlike missing
                    # market data they must not expire after four bars.
                    deferred.append(order)
                    continue
            else:
                fill_price = open_price * (
                    1.0 + self.slippage if delta > 0 else 1.0 - self.slippage
                )
            requested_delta = delta
            lot_size = self._lot_size(order.symbol, bar)
            delta = self._round_to_lot(delta, lot_size)
            if abs(delta) < lot_size - 1e-12:
                batch_event_indexes.append(self._append_order_event(self._order_event(
                    order_id, order, timestamp, "rejected", "minimum_trade_unit",
                    requested_quantity=abs(requested_delta),
                )))
                continue
            liquidity_cap = None if forced_liquidation else self._liquidity_cap(bar, lot_size)
            if liquidity_cap is not None and abs(delta) > liquidity_cap:
                delta = math.copysign(liquidity_cap, delta)
            if forced_liquidation:
                feasible_delta, constraint_reason = delta, ""
            else:
                feasible_delta, constraint_reason = self._feasible_delta(
                    delta=delta,
                    current=current,
                    fill_price=fill_price,
                    equity=equity,
                    lot_size=lot_size,
                    position_key=position_key,
                )
            if abs(feasible_delta) < lot_size - 1e-12:
                batch_event_indexes.append(self._append_order_event(self._order_event(
                    order_id,
                    order,
                    timestamp,
                    "rejected",
                    constraint_reason or "position_limit",
                    requested_quantity=abs(requested_delta),
                )))
                continue
            delta = feasible_delta
            target_qty = current.amount + delta
            remaining_quantity = max(0.0, abs(requested_delta) - abs(delta))
            has_tradable_remainder = (
                remaining_quantity + 1e-12 >= lot_size
                and remaining_quantity * fill_price + 1e-12 >= 0.01
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
                self.portfolio.positions.pop(position_key, None)
                self._protections.pop(position_key, None)
            else:
                self.portfolio.positions[position_key] = current
                new_side = "long" if current.amount > 0 else "short"
                existing = self._protections.get(position_key)
                side_changed = existing is not None and existing.side != new_side
                if order.protection is not None or side_changed:
                    spec = order.protection or (existing.spec if existing else None)
                    if spec is not None:
                        if existing is not None and not side_changed:
                            existing.apply_scale_in(
                                entry_price=current.avg_cost,
                                fill_price=fill_price,
                                spec=spec,
                                scaled_at=timestamp,
                            )
                        else:
                            self._protections[position_key] = ProtectionState.open(
                                symbol=order.symbol,
                                side=new_side,
                                entry_price=current.avg_cost,
                                spec=spec,
                                opened_at=timestamp,
                            )
            self.portfolio.total_value = self.portfolio.available_cash + sum(
                position.market_value for position in self.portfolio.positions.values()
            )
            execution_type, inferred_position_side = _execution_identity(old_amount, target_qty, delta)
            position_side = _normalize_position_side(order.position_side) or inferred_position_side
            execution = {
                "order_id": order_id,
                "symbol": order.symbol,
                "time": _backtest_time_iso(timestamp),
                "side": "buy" if delta > 0 else "sell",
                "type": execution_type,
                "position_side": position_side,
                "position_key": position_key,
                "quantity": abs(delta),
                "price": fill_price,
                "notional": notional,
                "commission": fee,
                "balance": self.portfolio.total_value,
                "reason": order.reason,
                "client_order_id": str(order.client_order_id or ""),
                "signal_time": _backtest_time_iso(order.signal_time if order.signal_time is not None else timestamp),
                "fill_reference": fill_reference,
                "reference_price": open_price,
                "order_type": "limit" if is_limit_order else "market",
                "limit_price": float(order.limit_price or 0.0) if is_limit_order else 0.0,
                "status": execution_status,
                "requested_quantity": abs(requested_delta),
            }
            self.executions.append(execution)
            reason = "margin_liquidation" if forced_liquidation else "filled"
            if execution_status == "partial":
                reason = constraint_reason or (
                    "insufficient_liquidity"
                    if liquidity_cap is not None and abs(requested_delta) > liquidity_cap
                    else "partial_fill"
                )
            batch_event_indexes.append(self._append_order_event(self._order_event(
                order_id,
                order,
                timestamp,
                execution["status"],
                reason,
                requested_quantity=abs(requested_delta),
                filled_quantity=abs(delta),
                price=fill_price,
                commission=fee,
            )))
            self._record_closed_trade(
                execution=execution,
                old_amount=old_amount,
                old_cost=old_cost,
                target_amount=target_qty,
            )
            if execution["status"] == "partial" and (is_limit_order or order.attempts < 4):
                next_attempts = order.attempts if is_limit_order else order.attempts + 1
                if order.kind == "quantity":
                    remaining_value = math.copysign(
                        max(0.0, abs(requested_delta) - abs(delta)),
                        requested_delta,
                    )
                    deferred.append(replace(
                        order,
                        value=remaining_value,
                        attempts=next_attempts,
                        pending_direction=1 if requested_delta > 0 else -1,
                    ))
                elif order.kind == "value":
                    remaining_value = math.copysign(
                        max(0.0, abs(requested_delta) - abs(delta)) * sizing_price,
                        requested_delta,
                    )
                    deferred.append(replace(
                        order,
                        value=remaining_value,
                        attempts=next_attempts,
                        pending_direction=1 if requested_delta > 0 else -1,
                    ))
                else:
                    deferred.append(replace(
                        order,
                        attempts=next_attempts,
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

    def liquidate_if_insolvent(
        self,
        portal: MultiAssetDataPortal,
        timestamp: Any,
    ) -> bool:
        """Force-close an insolvent leveraged account and stop further strategy orders."""
        if self.bankrupt:
            return True
        equity_before = self.mark_to_market(portal, timestamp)
        if equity_before > 0:
            return False

        orders: list[OrderIntent] = []
        price_overrides: dict[str, float] = {}
        for position in list(self.portfolio.positions.values()):
            price = portal.close_at(position.symbol, timestamp)
            if price is None or not math.isfinite(float(price)) or float(price) <= 0:
                price = position.last_price or position.avg_cost
            if price is None or not math.isfinite(float(price)) or float(price) <= 0:
                continue
            orders.append(OrderIntent(
                position.symbol,
                "target_quantity",
                0.0,
                "margin_liquidation",
                signal_time=pd.Timestamp(timestamp),
                position_side=position.position_side,
            ))
            price_overrides[position.symbol] = float(price)

        if orders:
            self.execute(orders, portal, timestamp, price_overrides=price_overrides)

        # A backtest has no exchange insurance-fund model. Absorb any residual
        # deficit after forced liquidation so equity cannot continue below zero.
        deficit = max(0.0, -float(self.portfolio.available_cash))
        if deficit:
            self.liquidation_adjustment += deficit
            self.portfolio.available_cash += deficit
        self.portfolio.total_value = max(0.0, self.mark_to_market(portal, timestamp))
        self.bankrupt = True
        self.liquidation_events.append({
            "time": _backtest_time_iso(timestamp),
            "equityBefore": equity_before,
            "deficitAbsorbed": deficit,
            "positionsClosed": len(orders),
        })
        return True

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
        position_key = _position_key(order.symbol, order.position_side)
        current = self.portfolio.positions.get(position_key) or Position(
            order.symbol,
            position_side=_normalize_position_side(order.position_side),
        )
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
        position_key: str,
    ) -> tuple[float, str]:
        requested_direction = 1 if delta > 0 else -1 if delta < 0 else 0
        feasible = delta
        reason = ""
        if feasible > 0:
            borrowing_floor = -equity * (self.leverage - 1.0)
            spendable = max(0.0, self.portfolio.available_cash - borrowing_floor)
            cash_cap = spendable / max(fill_price * (1.0 + self.commission), 1e-12)
            if feasible > cash_cap:
                feasible = cash_cap
                reason = "insufficient_cash"
        gross_limit = max(0.0, equity * self.leverage - self._gross_value(exclude=position_key))
        max_target_abs = gross_limit / max(fill_price, 1e-12)
        desired_target = current.amount + feasible
        if abs(desired_target) > max_target_abs + 1e-12:
            capped_target = math.copysign(max_target_abs, desired_target)
            feasible = capped_target - current.amount
            reason = "position_limit"
        # A risk cap may reduce the admissible target below the current
        # position after fees/slippage are applied. It must never turn an
        # incremental buy into a sell (or vice versa); forced deleveraging is
        # handled by the explicit liquidation path.
        feasible_direction = 1 if feasible > 0 else -1 if feasible < 0 else 0
        if requested_direction and feasible_direction not in {0, requested_direction}:
            return 0.0, reason or "position_limit"
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
            "clientOrderId": str(order.client_order_id or ""),
            "symbol": order.symbol,
            "positionSide": _normalize_position_side(order.position_side),
            "kind": order.kind,
            "value": order.value,
            "reason": order.reason,
            "status": status,
            "statusReason": reason,
            "signalTime": _backtest_time_iso(order.signal_time if order.signal_time is not None else timestamp),
            "eventTime": _backtest_time_iso(timestamp),
            "attempt": order.attempts + 1,
            "requestedQuantity": requested_quantity,
            "filledQuantity": filled_quantity,
            "price": price,
            "commission": commission,
        }

    def _append_order_event(
        self,
        event: dict[str, Any],
        *,
        coalesce_resting: bool = False,
    ) -> int:
        """Append an economic order event, compacting repeated resting polls.

        A limit order that remains outside the bar range has not changed
        economically.  Persist one audit row for that continuous resting
        period and retain its first/last timestamps plus exact poll count.
        Filled, partial, rejected, and other deferred transitions always get
        their own rows and terminate the previous resting period.
        """
        reference = str(event.get("clientOrderId") or "").strip()
        if coalesce_resting and reference:
            existing_index = self._resting_deferred_events.get(reference)
            if existing_index is not None and 0 <= existing_index < len(self.order_ledger):
                existing = self.order_ledger[existing_index]
                if (
                    existing.get("status") == "deferred"
                    and existing.get("statusReason") == "limit_not_reached"
                ):
                    requested = float(event.get("requestedQuantity") or 0.0)
                    existing["lastEventTime"] = event.get("eventTime")
                    existing["lastOrderId"] = event.get("orderId")
                    existing["occurrenceCount"] = int(existing.get("occurrenceCount") or 1) + 1
                    existing["requestedQuantity"] = requested
                    existing["minRequestedQuantity"] = min(
                        float(existing.get("minRequestedQuantity") or requested),
                        requested,
                    )
                    existing["maxRequestedQuantity"] = max(
                        float(existing.get("maxRequestedQuantity") or requested),
                        requested,
                    )
                    return existing_index

            requested = float(event.get("requestedQuantity") or 0.0)
            event["firstEventTime"] = event.get("eventTime")
            event["lastEventTime"] = event.get("eventTime")
            event["occurrenceCount"] = 1
            event["minRequestedQuantity"] = requested
            event["maxRequestedQuantity"] = requested
        elif reference:
            self._resting_deferred_events.pop(reference, None)

        self.order_ledger.append(event)
        index = len(self.order_ledger) - 1
        if coalesce_resting and reference:
            self._resting_deferred_events[reference] = index
        return index

    def release_resting_order_references(self, references: Iterable[str]) -> None:
        """End deferred-ledger compaction windows for strategy cancellations."""
        for reference in references:
            self._resting_deferred_events.pop(str(reference or "").strip(), None)

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
            "time": _backtest_time_iso(timestamp),
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
        for position_key, state in list(self._protections.items()):
            position = self.portfolio.positions.get(position_key)
            symbol = position.symbol if position is not None else str(state.symbol or "")
            bar = portal.bar_at(symbol, timestamp)
            if position is None or bar is None:
                if position is None:
                    self._protections.pop(position_key, None)
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
                    position_side=position.position_side,
                )],
                portal,
                timestamp,
                price_overrides={symbol: decision.price},
            )
            if len(self.executions) == execution_count:
                latest = self.order_ledger[-1] if self.order_ledger else {}
                if latest.get("statusReason") == "target_already_met":
                    self._protections.pop(position_key, None)
                continue
            decisions.append(decision)
            self.protection_events.append({
                "symbol": symbol,
                "side": decision.side,
                "reason": decision.reason,
                "triggerPrice": decision.trigger_price,
                "fillReferencePrice": decision.price,
                "time": _backtest_time_iso(decision.timestamp),
            })
        return decisions

    def mark_to_market(self, portal: MultiAssetDataPortal, timestamp: Any) -> float:
        total = float(self.portfolio.available_cash)
        for _, position in self.portfolio.positions.items():
            symbol = position.symbol
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
            "time": _backtest_time_iso(timestamp),
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
        symbol = str(execution.get("position_key") or execution["symbol"])
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
            gross_profit = (
                float(execution["price"]) - float(entry.get("price") or old_cost)
            ) * closing_quantity * direction
            profit = gross_profit - entry_fee - close_fee
            account_entry_price = float(entry.get("price") or old_cost)
            account_entry_time = str(entry.get("time") or execution["time"])
            grid_match = self._consume_grid_entry(execution, closing_quantity, close_fee)
            trade = {
                "symbol": symbol,
                "side": str(entry.get("side") or ("long" if old_amount > 0 else "short")),
                "entry_time": account_entry_time,
                "exit_time": str(execution["time"]),
                "entry_price": account_entry_price,
                "exit_price": float(execution["price"]),
                "quantity": closing_quantity,
                "amount": closing_quantity,
                "profit": profit,
                "gross_profit": gross_profit,
                "entry_commission": entry_fee,
                "exit_commission": close_fee,
                "commission": entry_fee + close_fee,
                "balance": float(execution.get("balance") or 0.0),
                "close_reason": str(execution.get("reason") or "strategy"),
                "profit_basis": "account_average",
            }
            if grid_match is not None:
                trade.update({
                    "entry_time": grid_match["entry_time"],
                    "entry_price": grid_match["entry_price"],
                    "gross_profit": grid_match["gross_profit"],
                    "entry_commission": grid_match["entry_commission"],
                    "commission": grid_match["commission"],
                    "profit": grid_match["profit"],
                    "matched_entry_price": grid_match["entry_price"],
                    "grid_matched_profit": grid_match["profit"],
                    "grid_cell_index": grid_match["cell_index"],
                    "grid_cycle": grid_match["cycle"],
                    "profit_basis": "grid_cell",
                    "account_entry_time": account_entry_time,
                    "account_avg_entry_price": account_entry_price,
                    "account_gross_profit": gross_profit,
                    "account_realized_profit": profit,
                })
            self.closed_trades.append(trade)
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
            self._record_grid_entry(execution, opening_quantity, open_fee)

    @staticmethod
    def _grid_entry_key(
        position_key: str,
        identity: tuple[int, str, str, int],
    ) -> str:
        cell_index, position_side, _, cycle = identity
        return f"{position_key}|{cell_index}|{position_side}|{cycle}"

    def _record_grid_entry(
        self,
        execution: Mapping[str, Any],
        quantity: float,
        commission: float,
    ) -> None:
        identity = _grid_order_identity(execution.get("client_order_id"))
        if identity is None or identity[2] != "entry" or quantity <= 1e-12:
            return
        position_key = str(execution.get("position_key") or execution.get("symbol") or "")
        key = self._grid_entry_key(position_key, identity)
        current = self._grid_entries.get(key)
        if current is None:
            self._grid_entries[key] = {
                "cell_index": identity[0],
                "position_side": identity[1],
                "cycle": identity[3],
                "entry_time": str(execution.get("time") or ""),
                "entry_price": float(execution.get("price") or 0.0),
                "quantity": float(quantity),
                "commission": float(commission),
            }
            return
        previous_quantity = float(current.get("quantity") or 0.0)
        combined_quantity = previous_quantity + float(quantity)
        if combined_quantity <= 1e-12:
            return
        current["entry_price"] = (
            float(current.get("entry_price") or 0.0) * previous_quantity
            + float(execution.get("price") or 0.0) * float(quantity)
        ) / combined_quantity
        current["quantity"] = combined_quantity
        current["commission"] = float(current.get("commission") or 0.0) + float(commission)

    def _consume_grid_entry(
        self,
        execution: Mapping[str, Any],
        closing_quantity: float,
        close_commission: float,
    ) -> dict[str, Any] | None:
        identity = _grid_order_identity(execution.get("client_order_id"))
        if identity is None or identity[2] != "exit" or closing_quantity <= 1e-12:
            return None
        position_key = str(execution.get("position_key") or execution.get("symbol") or "")
        key = self._grid_entry_key(position_key, identity)
        entry = self._grid_entries.get(key)
        available = float((entry or {}).get("quantity") or 0.0)
        if entry is None or available + 1e-10 < closing_quantity:
            return None

        entry_commission_total = float(entry.get("commission") or 0.0)
        entry_commission = entry_commission_total * closing_quantity / max(available, 1e-12)
        remaining = max(0.0, available - closing_quantity)
        if remaining <= 1e-12:
            self._grid_entries.pop(key, None)
        else:
            entry["quantity"] = remaining
            entry["commission"] = max(0.0, entry_commission_total - entry_commission)

        entry_price = float(entry.get("entry_price") or 0.0)
        exit_price = float(execution.get("price") or 0.0)
        direction = 1.0 if identity[1] == "long" else -1.0
        gross_profit = (exit_price - entry_price) * closing_quantity * direction
        commission = entry_commission + float(close_commission)
        return {
            "cell_index": identity[0],
            "cycle": identity[3],
            "entry_time": str(entry.get("entry_time") or execution.get("time") or ""),
            "entry_price": entry_price,
            "entry_commission": entry_commission,
            "commission": commission,
            "gross_profit": gross_profit,
            "profit": gross_profit - commission,
        }


class StrategyV2BacktestRunner:
    VERSION = "quantdinger-strategy-api-v2"

    def __init__(
        self,
        *,
        code: str,
        frames: Mapping[str, pd.DataFrame],
        frequency_frames: Mapping[str, Mapping[str, pd.DataFrame]] | None = None,
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
        self.portal = MultiAssetDataPortal(
            frames,
            frequency_frames=frequency_frames,
            driving_frequency=self.program.manifest.driving_frequency,
            universe_resolver=universe_resolver,
        )
        self.broker = MultiAssetSimulationBroker(
            initial_capital=initial_capital,
            leverage=requested_leverage,
            commission=commission,
            slippage=slippage,
        )
        runtime_params = dict(params or {})
        runtime_params.setdefault("commission", self.broker.commission)
        runtime_params.setdefault("slippage", self.broker.slippage)
        self.context = StrategyRuntimeContext(
            portal=self.portal,
            portfolio=self.broker.portfolio,
            params=runtime_params,
        )
        self.logs: list[str] = []
        self._order_status_cursor = 0
        self._order_status_summaries: dict[str, dict[str, Any]] = {}
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
            if self.broker.bankrupt:
                self.portal.set_clock(timestamp, include_current=True)
                self.broker.record_equity(self.portal, timestamp)
                previous = pd.Timestamp(timestamp)
                continue
            self.portal.set_clock(timestamp, include_current=False)
            if pending_orders:
                pending_orders = self.broker.execute(pending_orders, self.portal, timestamp)
                self._sync_order_statuses()
            protection_decisions = self.broker.process_protections(self.portal, timestamp)
            for decision in protection_decisions:
                self.context.set_last_exit_reason(decision.symbol, decision.reason)

            self._invoke("before_trading_start", self.context, self.context.data)
            pending_orders = self._remove_cancelled_orders(pending_orders)
            opening_orders = self.context.flush_orders()
            for schedule in self.program.manifest.schedules:
                if self._schedule_due(
                    schedule,
                    timestamp,
                    previous,
                    self.program.manifest.driving_frequency,
                ):
                    self._invoke(schedule.callback, self.context, self.context.data)
                    pending_orders = self._remove_cancelled_orders(pending_orders)
                    opening_orders.extend(self.context.flush_orders())
            if self.program.manifest.strategy_type == "portfolio" and not self.program.manifest.schedules:
                self._invoke("on_rebalance", self.context, self.portal.panel())
                pending_orders = self._remove_cancelled_orders(pending_orders)
                opening_orders.extend(self.context.flush_orders())
            if opening_orders:
                pending_orders = _merge_pending(
                    pending_orders,
                    self.broker.execute(opening_orders, self.portal, timestamp),
                )
                self._sync_order_statuses()

            self.portal.set_clock(timestamp, include_current=True)
            if self.broker.liquidate_if_insolvent(self.portal, timestamp):
                pending_orders = []
                self.context.flush_orders()
                self.logs.extend(self.context.flush_logs())
                self.broker.record_equity(self.portal, timestamp)
                previous = pd.Timestamp(timestamp)
                continue
            self._invoke("handle_data", self.context, self.context.data)
            pending_orders = self._remove_cancelled_orders(pending_orders)
            pending_orders = _merge_pending(pending_orders, self.context.flush_orders())
            self._invoke("after_trading_end", self.context, self.context.data)
            pending_orders = self._remove_cancelled_orders(pending_orders)
            pending_orders = _merge_pending(pending_orders, self.context.flush_orders())
            self.logs.extend(self.context.flush_logs())
            self.broker.record_equity(self.portal, timestamp)
            previous = pd.Timestamp(timestamp)

        return self._result()

    def _remove_cancelled_orders(self, orders: list[OrderIntent]) -> list[OrderIntent]:
        cancelled = self.context.flush_cancelled_order_ids()
        if not cancelled:
            return orders
        self.broker.release_resting_order_references(cancelled)
        return [
            order
            for order in orders
            if str(order.client_order_id or "") not in cancelled
        ]

    def _sync_order_statuses(self) -> None:
        changed: dict[str, dict[str, Any]] = {}
        for event in self.broker.order_ledger[self._order_status_cursor:]:
            reference = str(event.get("clientOrderId") or "").strip()
            if not reference:
                continue
            current = self._order_status_summaries.setdefault(reference, {
                "client_order_id": reference,
                "status": "unknown",
                "filled_quantity": 0.0,
                "filled_notional": 0.0,
                "fee": 0.0,
                "reason": "",
            })
            quantity = max(0.0, float(event.get("filledQuantity") or 0.0))
            price = max(0.0, float(event.get("price") or 0.0))
            current["filled_quantity"] += quantity
            current["filled_notional"] += quantity * price
            current["fee"] += max(0.0, float(event.get("commission") or 0.0))
            current["status"] = str(event.get("status") or "unknown")
            current["reason"] = str(event.get("statusReason") or "")
            changed[reference] = current
        self._order_status_cursor = len(self.broker.order_ledger)
        self.context.update_order_statuses(changed)

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
            "get_order_status": ctx.get_order_status,
            "cancel_order": ctx.cancel_order,
            "consume_last_exit_reason": ctx.consume_last_exit_reason,
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
        equity_curve: list[dict[str, Any]] = []
        peak = initial
        peak_time = str(self.broker.equity_curve[0].get("time") or "") if self.broker.equity_curve else ""
        max_drawdown = 0.0
        max_drawdown_peak = initial
        max_drawdown_trough = initial
        max_drawdown_peak_time = ""
        max_drawdown_trough_time = ""
        for item in self.broker.equity_curve:
            value = float(item["value"])
            item_time = str(item.get("time") or "")
            if peak <= 0 or value > peak:
                peak = value
                peak_time = item_time
            drawdown = (value / peak - 1.0) * 100.0 if peak > 0 else 0.0
            equity_curve.append({**item, "drawdown": drawdown})
            if drawdown < max_drawdown:
                max_drawdown = drawdown
                max_drawdown_peak = peak
                max_drawdown_trough = value
                max_drawdown_peak_time = peak_time
                max_drawdown_trough_time = item_time
        values = [float(item["value"]) for item in equity_curve]
        closed_trades = list(self.broker.closed_trades)
        executions = list(self.broker.executions)
        profits = [float(item.get("profit") or 0.0) for item in closed_trades]
        account_realized_profits = [
            float(
                item.get("account_realized_profit")
                if item.get("account_realized_profit") is not None
                else item.get("profit") or 0.0
            )
            for item in closed_trades
        ]
        grid_matched_profits = [
            float(item.get("grid_matched_profit") or 0.0)
            for item in closed_trades
            if item.get("profit_basis") == "grid_cell"
        ]
        wins = [value for value in profits if value > 0]
        losses = [value for value in profits if value < 0]
        returns = pd.Series(values, dtype="float64").pct_change().dropna() if values else pd.Series(dtype="float64")
        volatility = float(returns.std(ddof=0)) if not returns.empty else 0.0
        periods_per_year = _periods_per_year(
            self.program.manifest.driving_frequency,
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
        annualized_return_available = elapsed_days >= 1.0 and initial > 0
        annualized_return_capped = False
        if annualized_return_available:
            annualized_return = (
                -100.0
                if final <= 0
                else math.expm1(
                    min(
                        math.log(max(final / initial, 1e-300)) * (365.25 / elapsed_days),
                        math.log(10_001.0),
                    )
                )
                * 100.0
            )
            annualized_return_capped = final > initial and annualized_return >= 1_000_000.0 - 1e-6
        win_rate = len(wins) / len(profits) * 100.0 if profits else 0.0
        average_win = sum(wins) / len(wins) if wins else 0.0
        average_loss = abs(sum(losses) / len(losses)) if losses else 0.0
        profit_loss_ratio = average_win / average_loss if average_loss > 0 else 0.0
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (gross_profit if gross_profit > 0 else 0.0)
        average_profit = sum(profits) / len(profits) if profits else 0.0
        attribution = self._attribution(initial)
        ledger_occurrences = sum(
            max(1, int(item.get("occurrenceCount") or 1))
            for item in self.broker.order_ledger
        )
        return {
            "initialCapital": initial,
            "totalReturn": total_return,
            "total_return": total_return,
            "finalEquity": final,
            "maxDrawdown": max_drawdown,
            "maxDrawdownPeakEquity": max_drawdown_peak,
            "maxDrawdownTroughEquity": max_drawdown_trough,
            "maxDrawdownPeakTime": max_drawdown_peak_time,
            "maxDrawdownTroughTime": max_drawdown_trough_time,
            "totalTrades": len(closed_trades),
            "totalExecutions": len(executions),
            "sampleCount": len(equity_curve),
            "equityCurve": equity_curve,
            "holdingSnapshots": list(self.broker.holding_snapshots),
            "rebalanceRecords": list(self.broker.rebalance_records),
            "orderLedger": list(self.broker.order_ledger),
            "orderLedgerStats": {
                "storedEvents": len(self.broker.order_ledger),
                "eventOccurrences": ledger_occurrences,
                "compactedOccurrences": max(
                    0,
                    ledger_occurrences - len(self.broker.order_ledger),
                ),
            },
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
            "accountRealizedProfit": sum(account_realized_profits),
            "gridMatchedProfit": sum(grid_matched_profits),
            "gridMatchedTradeCount": len(grid_matched_profits),
            "tradeProfitBasis": (
                "grid_cell_when_available"
                if grid_matched_profits
                else "account_average"
            ),
            "totalProfit": final - initial,
            "sharpeRatio": sharpe_ratio,
            "annualizedReturn": annualized_return,
            "annualizedReturnAvailable": annualized_return_available,
            "annualizedReturnCapped": annualized_return_capped,
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
            "liquidated": self.broker.bankrupt,
            "liquidationEvents": list(self.broker.liquidation_events),
            "liquidationAdjustment": self.broker.liquidation_adjustment,
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
            symbol = str(execution.get("position_key") or execution.get("symbol") or "")
            commission_by_symbol[symbol] = commission_by_symbol.get(symbol, 0.0) + float(execution.get("commission") or 0.0)
        for trade in self.broker.closed_trades:
            symbol = str(trade.get("symbol") or "")
            account_profit = (
                trade.get("account_realized_profit")
                if trade.get("account_realized_profit") is not None
                else trade.get("profit")
            )
            realized_by_symbol[symbol] = (
                realized_by_symbol.get(symbol, 0.0) + float(account_profit or 0.0)
            )
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
        statuses = {
            name: sum(
                max(1, int(item.get("occurrenceCount") or 1))
                for item in self.broker.order_ledger
                if item.get("status") == name
            )
            for name in ("filled", "partial", "deferred", "rejected")
        }
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
            symbol = str(execution.get("position_key") or execution.get("symbol") or "")
            quantities[symbol] = quantities.get(symbol, 0.0) + signed_quantity

            reference_price = float(execution.get("reference_price") or 0.0)
            fill_reference = str(execution.get("fill_reference") or "bar_open")
            if fill_reference == "limit":
                expected_price = float(execution.get("limit_price") or 0.0)
            elif fill_reference == "gap_open":
                expected_price = reference_price
            else:
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
        cash_before_liquidation_adjustment = cash
        cash += self.broker.liquidation_adjustment
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
            "cashLedgerBeforeLiquidationAdjustment": cash_before_liquidation_adjustment,
            "liquidationAdjustment": self.broker.liquidation_adjustment,
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
        frequency_frames: Mapping[str, Mapping[str, pd.DataFrame]] | None = None,
        initial_capital: float,
        params: Mapping[str, Any] | None = None,
        universe_resolver=None,
        schedule_timezone: str = "UTC",
    ) -> None:
        self.program = compile_strategy_v2(code)
        self._universe_resolver = universe_resolver
        self.portal = MultiAssetDataPortal(
            frames,
            frequency_frames=frequency_frames,
            driving_frequency=self.program.manifest.driving_frequency,
            universe_resolver=universe_resolver,
        )
        self.portfolio = PortfolioState(initial_capital, initial_capital, total_value=initial_capital)
        self.context = StrategyRuntimeContext(portal=self.portal, portfolio=self.portfolio, params=params)
        self.persist_strategy_state = (
            _truthy(self.program.namespace.get("PERSIST_RUNTIME_STATE"))
            or _truthy(self.context.params.get("persist_runtime_state"))
        )
        self.last_processed: pd.Timestamp | None = None
        self.schedule_timezone = _resolve_schedule_timezone(schedule_timezone)
        self.last_schedule_check: pd.Timestamp | None = None
        self.protection_engine = ProtectionEngine()
        self.protection_specs: dict[str, ProtectionSpec] = {}
        self.protection_states: dict[str, ProtectionState] = {}
        self._protection_exit_pending: set[str] = set()
        self._bind_runtime_api()

    def process(
        self,
        frames: Mapping[str, pd.DataFrame],
        *,
        frequency_frames: Mapping[str, Mapping[str, pd.DataFrame]] | None = None,
        schedule_time: Any = None,
    ) -> tuple[list[OrderIntent], list[str], pd.Timestamp]:
        portal = MultiAssetDataPortal(
            frames,
            frequency_frames=frequency_frames,
            driving_frequency=self.program.manifest.driving_frequency,
            universe_resolver=self._universe_resolver,
        )
        if portal.timestamps.empty:
            raise StrategyV2ContractError("strategyV2.noMarketData")
        timestamp = pd.Timestamp(portal.timestamps[-1])
        schedule_clock = self._schedule_clock(schedule_time)
        bar_advanced = self.last_processed is None or timestamp > self.last_processed

        self.portal = portal
        self.context.portal = portal
        self.context.data = StrategyDataView(portal)
        self.context.previous_trading_date = self.last_processed
        portal.set_clock(timestamp, include_current=True)

        if bar_advanced and (
            self.last_processed is None or timestamp.date() != self.last_processed.date()
        ):
            self.context.current_dt = timestamp
            self._invoke("before_trading_start", self.context, self.context.data)

        previous_schedule_check = self.last_schedule_check
        if previous_schedule_check is not None and schedule_clock > previous_schedule_check:
            self.context.current_dt = schedule_clock
            for schedule in self.program.manifest.schedules:
                if StrategyV2BacktestRunner._schedule_due(
                    schedule,
                    schedule_clock,
                    previous_schedule_check,
                    "1m",
                ):
                    self._invoke(schedule.callback, self.context, self.context.data)
        self.last_schedule_check = schedule_clock

        if bar_advanced:
            self.context.current_dt = timestamp
            if self.program.manifest.strategy_type == "portfolio" and not self.program.manifest.schedules:
                self._invoke("on_rebalance", self.context, portal.panel())
            self._invoke("handle_data", self.context, self.context.data)
        orders = self.context.flush_orders()
        self._capture_protection_intents(orders)
        logs = self.context.flush_logs()
        if bar_advanced:
            self.last_processed = timestamp
        return orders, logs, timestamp

    def _schedule_clock(self, value: Any = None) -> pd.Timestamp:
        if value is None:
            return pd.Timestamp.now(tz=self.schedule_timezone)
        current = pd.Timestamp(value)
        if current.tzinfo is None:
            return current.tz_localize(self.schedule_timezone)
        return current.tz_convert(self.schedule_timezone)

    def synchronize_positions(
        self,
        positions: Mapping[str, Mapping[str, Any]],
        *,
        available_cash: float | None = None,
        total_value: float | None = None,
    ) -> None:
        synced: dict[str, Position] = {}
        for raw_symbol, raw in positions.items():
            source_key = str(raw_symbol or "")
            source_symbol, separator, suffix = source_key.rpartition("::")
            suffix_side = _normalize_position_side(suffix) if separator else ""
            resolve_symbol = source_symbol if suffix_side else source_key
            try:
                symbol = self.portal.resolve_key(resolve_symbol)
            except Exception:
                symbol = str(resolve_symbol)
            amount = float(raw.get("amount") or 0.0)
            side = str(raw.get("side") or "long").strip().lower()
            if side == "short" and amount > 0:
                amount = -amount
            position_side = (
                suffix_side
                or _normalize_position_side(raw.get("position_side"))
            )
            key = _position_key(symbol, position_side)
            synced[key] = Position(
                symbol=symbol,
                amount=amount,
                avg_cost=float(raw.get("avg_cost") or 0.0),
                last_price=float(raw.get("last_price") or 0.0),
                position_side=position_side,
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
                new_average = float(position.avg_cost)
                if abs(new_average - state.entry_price) > max(1e-12, abs(state.entry_price) * 1e-10):
                    state.apply_scale_in(
                        entry_price=new_average,
                        fill_price=float(position.last_price or new_average),
                        spec=spec,
                        scaled_at=self.context.current_dt,
                    )
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
        for position_key, state in list(self.protection_states.items()):
            # A protection exit is dispatched asynchronously.  Do not enqueue
            # another close on every risk tick while the first one is still
            # pending; queued spot closes could otherwise consume unrelated
            # wallet inventory after the strategy-owned position is flat.
            if position_key in self._protection_exit_pending:
                continue
            position = self.portfolio.positions.get(position_key)
            if position is None or abs(position.amount) <= 1e-12:
                continue
            price = prices.get(position.symbol)
            if price is None:
                price = prices.get(position_key)
            if price is None:
                continue
            decision = self.protection_engine.evaluate_price(
                state,
                timestamp=ts,
                price=float(price or 0.0),
            )
            if decision is None:
                continue
            exits.append(OrderIntent(
                position.symbol,
                "target_quantity",
                0.0,
                decision.reason,
                position_side=position.position_side,
            ))
            self.context.set_last_exit_reason(position_key, decision.reason)
            self.context.set_last_exit_reason(position.symbol, decision.reason)
            self._protection_exit_pending.add(position_key)
        return exits

    def evaluate_equity_risk(
        self,
        *,
        timestamp: object = None,
    ) -> tuple[list[OrderIntent], list[str], str]:
        """Evaluate a generated robot's portfolio-wide risk on every live tick.

        Generated system templates expose ``_equity_risk_exit``.  Keeping this
        hook outside ``handle_data`` means a 1H robot can still stop immediately
        when its live account equity crosses a limit instead of waiting for the
        next hourly candle.  User-authored and previously purchased sources that
        do not expose the hook retain their existing behaviour.
        """
        callback = self.program.namespace.get("_equity_risk_exit")
        if not callable(callback):
            return [], [], ""
        self.context.current_dt = pd.Timestamp(timestamp or pd.Timestamp.utcnow())
        callback(self.context)
        orders = self.context.flush_orders()
        self._capture_protection_intents(orders)
        logs = self.context.flush_logs()
        reason = str(
            getattr(self.program.state, "equity_stop_reason", "") or ""
        ).strip()
        if not reason and orders:
            reason = str(orders[0].reason or "").strip()
        return orders, logs, reason

    def evaluate_price_tick(
        self,
        prices: Mapping[str, float],
        *,
        timestamp: object = None,
    ) -> tuple[list[OrderIntent], list[str]]:
        """Run the optional live-price hook without advancing the K-line clock.

        System robot templates use this hook for price-level strategies such as
        martingale.  Indicator strategies deliberately omit it and therefore
        remain closed-bar driven.  The caller is responsible for freshness
        checks before invoking the hook so stale REST/stream cache values can
        never open or scale a position.
        """
        callback = self.program.namespace.get("on_price_tick")
        if not callable(callback):
            return [], []
        normalized: dict[str, float] = {}
        for raw_symbol, raw_price in (prices or {}).items():
            try:
                symbol = self.portal.resolve_key(raw_symbol)
                price = float(raw_price or 0.0)
            except Exception:
                continue
            if price > 0 and math.isfinite(price):
                normalized[symbol] = price
        if not normalized:
            return [], []
        tick_time = pd.Timestamp(timestamp or pd.Timestamp.utcnow())
        self.context.current_dt = tick_time
        callback(self.context, normalized)
        orders = self.context.flush_orders()
        self._capture_protection_intents(orders)
        return orders, self.context.flush_logs()

    def release_equity_risk_exit(self) -> None:
        """Re-arm a generated equity exit after its async submission failed."""
        callback = self.program.namespace.get("_release_equity_risk_exit")
        if callable(callback):
            callback()

    def pending_protection_exit_symbols(self) -> set[str]:
        """Return symbols whose protection close has already been dispatched."""
        return set(self._protection_exit_pending)

    def release_protection_exit(
        self,
        symbol: object,
        *,
        position_side: object = "",
    ) -> None:
        """Allow a protection close to be retried after submission was rejected."""
        self._protection_exit_pending.discard(
            _position_key(symbol, position_side)
        )

    def session_snapshot(self) -> dict[str, Any]:
        snapshot = {
            "version": 3,
            "protection": self.protection_snapshot(),
        }
        if self.persist_strategy_state:
            snapshot.update({
                "strategyState": _snapshot_state_value(
                    dict(vars(self.program.state))
                ),
                "lastProcessed": (
                    pd.Timestamp(self.last_processed).isoformat()
                    if self.last_processed is not None
                    else None
                ),
                "lastScheduleCheck": (
                    pd.Timestamp(self.last_schedule_check).isoformat()
                    if self.last_schedule_check is not None
                    else None
                ),
                "orderStatuses": self.context.order_status_snapshot(),
                "exitReasons": self.context.exit_reason_snapshot(),
            })
        return snapshot

    def restore_session_snapshot(self, values: Mapping[str, Any] | None) -> None:
        raw = dict(values or {})
        if not raw:
            return
        protection = raw.get("protection")
        if isinstance(protection, Mapping):
            self.restore_protection_snapshot(protection)
        elif "states" in raw or "specs" in raw:
            # Backward compatibility with the old protection-only snapshot.
            self.restore_protection_snapshot(raw)
        if self.persist_strategy_state:
            strategy_state = raw.get("strategyState")
            if isinstance(strategy_state, Mapping):
                for name, value in _restore_state_value(strategy_state).items():
                    setattr(self.program.state, str(name), value)
            self.context.update_order_statuses(
                raw.get("orderStatuses")
                if isinstance(raw.get("orderStatuses"), Mapping)
                else {}
            )
            self.context.restore_exit_reasons(
                raw.get("exitReasons")
                if isinstance(raw.get("exitReasons"), Mapping)
                else {}
            )
            try:
                if raw.get("lastProcessed"):
                    self.last_processed = pd.Timestamp(raw["lastProcessed"])
                if raw.get("lastScheduleCheck"):
                    self.last_schedule_check = pd.Timestamp(raw["lastScheduleCheck"])
            except Exception:
                self.last_processed = None
                self.last_schedule_check = None

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
            position_key = _position_key(order.symbol, order.position_side)
            if order.protection is not None:
                self.protection_specs[position_key] = order.protection
            if order.kind in {"target_quantity", "target_value", "target_percent"} and abs(order.value) <= 1e-12:
                self._protection_exit_pending.add(position_key)

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
            "get_order_status": ctx.get_order_status,
            "cancel_order": ctx.cancel_order,
            "consume_last_exit_reason": ctx.consume_last_exit_reason,
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


def _resolve_schedule_timezone(value: str) -> ZoneInfo:
    name = str(value or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


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
            output = [
                item for item in output
                if not (
                    item.symbol == order.symbol
                    and _normalize_position_side(item.position_side)
                    == _normalize_position_side(order.position_side)
                    and item.kind.startswith("target_")
                )
            ]
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
