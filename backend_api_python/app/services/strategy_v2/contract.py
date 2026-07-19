"""Strategy API V2 compilation and manifest discovery."""

from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Iterable

from app.utils.safe_exec import build_safe_builtins, safe_exec_with_validation
from app.services.factors import FactorError, get_factor
from app.services.strategy_direction import (
    direction_mode_from_manifest,
    infer_direction_mode_from_code,
    normalize_direction_mode,
)

from .instruments import (
    is_index_reference,
    normalize_frequency,
    normalize_index_reference,
    normalize_pool_reference,
    parse_instrument,
)
from .models import (
    InstrumentSpec,
    ScheduleSpec,
    StrategyManifest,
    SubscriptionSpec,
    UniverseSpec,
)


V2_HANDLER_NAMES = (
    "initialize",
    "before_trading_start",
    "handle_data",
    "after_trading_end",
    "on_rebalance",
)


class StrategyV2ContractError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class StateNamespace(SimpleNamespace):
    """Per-run user state for Strategy API V2 code."""


class _DiscoveryLogger:
    """No-op logger used while the strategy manifest is being discovered."""

    def __call__(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def debug(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def info(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def warning(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    warn = warning

    def error(self, *_args: Any, **_kwargs: Any) -> None:
        return None


@dataclass
class CompiledStrategyV2:
    code: str
    namespace: dict[str, Any]
    state: StateNamespace
    manifest: StrategyManifest

    def handler(self, name: str) -> Callable[..., Any] | None:
        value = self.namespace.get(name)
        return value if callable(value) else None


class DiscoveryContext:
    def __init__(self) -> None:
        self.universe_reference = ""
        self.instruments: list[InstrumentSpec] = []
        self.subscriptions: list[SubscriptionSpec] = []
        self.schedules: list[ScheduleSpec] = []
        self.benchmark: InstrumentSpec | None = None
        self.warmup_bars = 0
        self.leverage_allowed = False
        self.max_leverage = 1.0
        self.metadata: dict[str, Any] = {}
        self.current_dt = None
        self.previous_trading_date = None
        self.portfolio = SimpleNamespace(
            starting_cash=0.0,
            available_cash=0.0,
            total_value=0.0,
            positions={},
        )

    def set_universe(
        self,
        values: object = None,
        *,
        index: object = None,
        pool: object = None,
    ) -> None:
        if pool is not None:
            self.universe_reference = normalize_pool_reference(pool)
            self.instruments = []
            return
        source = index if index is not None else values
        if source is None:
            raise StrategyV2ContractError("strategyV2.universeRequired")
        if isinstance(source, str) and is_index_reference(source):
            self.universe_reference = normalize_index_reference(source)
            return
        self.instruments = _parse_many(source)

    def set_benchmark(self, value: object) -> None:
        self.benchmark = parse_instrument(value)

    def subscribe(
        self,
        symbols: object = None,
        *,
        frequency: object = "1d",
        fields: Iterable[object] | None = None,
    ) -> None:
        instruments = _parse_many(symbols) if symbols is not None else list(self.instruments)
        reference = "" if instruments else self.universe_reference
        self.subscriptions.append(SubscriptionSpec(
            instruments=tuple(instruments),
            universe_reference=reference,
            frequency=normalize_frequency(frequency),
            fields=tuple(str(item).strip().lower() for item in (fields or ("open", "high", "low", "close", "volume"))),
        ))

    def set_warmup(self, bars: object) -> None:
        self.warmup_bars = max(0, int(bars or 0))

    def allow_leverage(self, max_leverage: object = 1) -> None:
        value = max(1.0, float(max_leverage or 1.0))
        self.leverage_allowed = value > 1.0
        self.max_leverage = value

    def set_metadata(self, **values: Any) -> None:
        self.metadata.update(values)


class _ScheduleBindings:
    def __init__(self, context: DiscoveryContext):
        self.context = context

    def daily(self, *args: Any, **kwargs: Any) -> None:
        callback, time = _schedule_callback(args, kwargs)
        self.context.schedules.append(ScheduleSpec("daily", callback, time=time))

    def weekly(self, *args: Any, **kwargs: Any) -> None:
        callback, time = _schedule_callback(args, kwargs)
        weekday = kwargs.get("weekday", 1)
        self.context.schedules.append(ScheduleSpec("weekly", callback, time=time, weekday=int(weekday)))

    def monthly(self, *args: Any, **kwargs: Any) -> None:
        callback, time = _schedule_callback(args, kwargs)
        monthday = kwargs.get("monthday", 1)
        self.context.schedules.append(ScheduleSpec("monthly", callback, time=time, monthday=int(monthday)))


def compile_strategy_v2(code: str) -> CompiledStrategyV2:
    raw = str(code or "").strip()
    if not raw:
        raise StrategyV2ContractError("strategyV2.codeRequired")
    _validate_dataframe_truthiness(raw)
    _validate_strategy_api_calls(raw)

    context = DiscoveryContext()
    state = StateNamespace()
    schedules = _ScheduleBindings(context)
    discovery_log = _DiscoveryLogger()
    namespace: dict[str, Any] = {
        "__builtins__": build_safe_builtins(),
        "g": state,
        "run_daily": schedules.daily,
        "run_weekly": schedules.weekly,
        "run_monthly": schedules.monthly,
        "get_index_stocks": lambda value, **_: [normalize_index_reference(value)],
        "get_universe_stocks": lambda *_args, **_kwargs: [],
        "get_position": lambda *_args, **_kwargs: SimpleNamespace(
            amount=0.0,
            avg_cost=0.0,
            last_price=0.0,
        ),
        "get_positions": lambda *_args, **_kwargs: {},
        "get_history": lambda *_args, **_kwargs: [],
        "history": lambda *_args, **_kwargs: [],
        "is_trade": lambda *_args, **_kwargs: False,
        "log": discovery_log,
    }
    result = safe_exec_with_validation(raw, namespace, namespace, timeout=10)
    if not result.get("success"):
        raise StrategyV2ContractError(str(result.get("error") or "strategyV2.compileFailed"))

    initialize = namespace.get("initialize")
    if not callable(initialize):
        raise StrategyV2ContractError("strategyV2.initializeRequired")
    try:
        initialize(context)
    except Exception as exc:
        raise StrategyV2ContractError(f"strategyV2.initializeFailed:{exc}") from exc

    if not context.instruments and not context.universe_reference:
        raise StrategyV2ContractError("strategyV2.universeRequired")
    if not context.subscriptions:
        context.subscribe(frequency=context.metadata.get("frequency") or "1d")

    handlers = tuple(name for name in V2_HANDLER_NAMES if callable(namespace.get(name)))
    if not any(name in handlers for name in ("handle_data", "on_rebalance")) and not context.schedules:
        raise StrategyV2ContractError("strategyV2.handlerRequired")

    factors, fundamentals = _discover_dependencies(raw)
    if context.leverage_allowed:
        if context.universe_reference or not context.instruments:
            raise StrategyV2ContractError("strategyV2.leverageCryptoSwapOnly")
        if any(item.market != "Crypto" or item.market_type != "swap" for item in context.instruments):
            raise StrategyV2ContractError("strategyV2.leverageCryptoSwapOnly")
    strategy_type = "portfolio" if (
        context.universe_reference
        or len(context.instruments) > 1
        or "on_rebalance" in handlers
    ) else "cta"
    metadata_manifest = {"metadata": dict(context.metadata)}
    declared_direction_mode = direction_mode_from_manifest(metadata_manifest)
    direction_keys = {
        "direction_mode",
        "directionMode",
        "trade_direction",
        "position_side",
        "side",
    }
    if any(key in context.metadata for key in direction_keys) and not declared_direction_mode:
        raise StrategyV2ContractError("strategyV2.directionModeInvalid")
    direction_mode = declared_direction_mode or infer_direction_mode_from_code(raw)
    if not direction_mode and context.instruments and all(
        item.market_type != "swap" for item in context.instruments
    ):
        direction_mode = "long_only"
    direction_mode = normalize_direction_mode(direction_mode)
    manifest = StrategyManifest(
        api_version=2,
        code_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        strategy_type=strategy_type,
        universe=UniverseSpec(
            kind="dynamic" if context.universe_reference else "static",
            reference=context.universe_reference,
            instruments=tuple(context.instruments),
        ),
        subscriptions=tuple(context.subscriptions),
        schedules=tuple(context.schedules),
        benchmark=context.benchmark,
        handlers=handlers,
        factor_dependencies=tuple(sorted(factors)),
        fundamental_dependencies=tuple(sorted(fundamentals)),
        warmup_bars=context.warmup_bars,
        leverage_allowed=context.leverage_allowed,
        max_leverage=context.max_leverage,
        direction_mode=direction_mode,
        metadata_fields=dict(context.metadata),
    )
    return CompiledStrategyV2(raw, namespace, state, manifest)


def canonical_source_metadata(
    code: str,
    metadata: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compile source code and persist only the current runtime contract."""
    manifest = compile_strategy_v2(code).manifest.metadata()
    canonical = dict(metadata or {})
    canonical.pop("apiVersion", None)
    canonical.pop("api_version", None)
    canonical.pop("strategyManifest", None)
    canonical["strategy_manifest"] = manifest
    return canonical, manifest


_DATAFRAME_API_NAMES = {
    "get_history",
    "history",
    "get_factors",
    "get_fundamentals",
    "indicator",
    "factor",
}

_ORDER_API_ARGUMENTS = {
    "order": ("symbol", "amount"),
    "order_value": ("symbol", "value"),
    "order_target": ("symbol", "amount"),
    "order_target_value": ("symbol", "value"),
    "order_target_percent": ("symbol", "percent"),
}

_CANONICAL_SYMBOL_PREFIXES = (
    "CNStock:",
    "Crypto:",
    "Forex:",
    "Future:",
    "Futures:",
    "USStock:",
)


def _validate_dataframe_truthiness(code: str) -> None:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return
    dataframe_names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        call_name = ""
        if isinstance(value.func, ast.Name):
            call_name = value.func.id
        elif isinstance(value.func, ast.Attribute):
            call_name = value.func.attr
        if call_name not in _DATAFRAME_API_NAMES:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                dataframe_names.add(target.id)
    if not dataframe_names:
        return
    for node in ast.walk(tree):
        test = node.test if isinstance(node, (ast.If, ast.While, ast.IfExp)) else None
        if test is not None and _uses_direct_boolean_name(test, dataframe_names):
            raise StrategyV2ContractError("strategyV2.dataframeTruthAmbiguous")


def _uses_direct_boolean_name(node: ast.AST, names: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _uses_direct_boolean_name(node.operand, names)
    if isinstance(node, ast.BoolOp):
        return any(_uses_direct_boolean_name(value, names) for value in node.values)
    return False


def _validate_strategy_api_calls(code: str) -> None:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return
    static_values = _collect_static_api_values(tree)
    single_frame_names: set[str] = set()
    position_names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name, call_owner = _api_call_identity(node)
        if call_name in _ORDER_API_ARGUMENTS and call_owner in {"", "context"}:
            _validate_order_call(node, call_name, static_values)
        elif call_name in {"get_history", "history"} and call_owner == "":
            _validate_get_history_call(node, call_name, static_values)
        elif call_name == "history" and call_owner == "data":
            _validate_data_history_call(node, static_values)
        if _history_call_returns_single_frame(node, call_name, call_owner, static_values):
            parent_target = _assigned_name_for_call(tree, node)
            if parent_target:
                single_frame_names.add(parent_target)
        if call_name == "get_position" and call_owner in {"", "context"}:
            parent_target = _assigned_name_for_call(tree, node)
            if parent_target:
                position_names.add(parent_target)
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            if isinstance(node.value, ast.Call):
                call_name, call_owner = _api_call_identity(node.value)
                if _history_call_returns_single_frame(
                    node.value,
                    call_name,
                    call_owner,
                    static_values,
                ):
                    kind, _ = _static_api_value(node.slice, static_values)
                    if kind == "symbol":
                        raise StrategyV2ContractError(
                            "strategyV2.apiCallInvalid:history:singleSymbolResultIsDataFrame"
                        )
            if not isinstance(node.value, ast.Name):
                continue
            if node.value.id in position_names:
                raise StrategyV2ContractError(
                    "strategyV2.apiCallInvalid:get_position:returnsPositionObject"
                )
            if node.value.id not in single_frame_names:
                continue
            kind, _ = _static_api_value(node.slice, static_values)
            if kind != "symbol":
                continue
            raise StrategyV2ContractError(
                "strategyV2.apiCallInvalid:history:singleSymbolResultIsDataFrame"
            )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in position_names
            and node.func.attr in {"get", "items", "keys", "values"}
        ):
            raise StrategyV2ContractError(
                "strategyV2.apiCallInvalid:get_position:returnsPositionObject"
            )
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for index, operator in enumerate(node.ops):
                if not isinstance(operator, (ast.In, ast.NotIn)):
                    continue
                container = operands[index + 1]
                if isinstance(container, ast.Name) and container.id in position_names:
                    raise StrategyV2ContractError(
                        "strategyV2.apiCallInvalid:get_position:returnsPositionObject"
                    )


def _collect_static_api_values(tree: ast.AST) -> dict[str, tuple[str, int | None]]:
    assignments: list[tuple[str, ast.AST]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            key = _static_target_key(target)
            if key:
                assignments.append((key, node.value))
    values: dict[str, tuple[str, int | None]] = {}
    for _ in range(len(assignments) + 1):
        changed = False
        for key, value in assignments:
            resolved = _static_api_value(value, values)
            if resolved[0] != "unknown" and values.get(key) != resolved:
                values[key] = resolved
                changed = True
        if not changed:
            break
    return values


def _static_target_key(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "g"
    ):
        return f"g.{node.attr}"
    return ""


def _static_api_value(
    node: ast.AST | None,
    values: dict[str, tuple[str, int | None]],
) -> tuple[str, int | None]:
    if node is None:
        return "unknown", None
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool):
            return "boolean", None
        if isinstance(node.value, (int, float)):
            return "number", None
        if isinstance(node.value, str):
            raw = node.value.strip()
            if raw.startswith(_CANONICAL_SYMBOL_PREFIXES):
                return "symbol", None
            return "string", None
    if isinstance(node, ast.Name):
        if node.id == "context":
            return "context", None
        return values.get(node.id, ("unknown", None))
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "g"
    ):
        return values.get(f"g.{node.attr}", ("unknown", None))
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        items = [_static_api_value(item, values) for item in node.elts]
        if items and all(kind == "symbol" for kind, _ in items):
            return "symbols", len(items)
        return "collection", len(items)
    return "unknown", None


def _api_call_identity(node: ast.Call) -> tuple[str, str]:
    if isinstance(node.func, ast.Name):
        return node.func.id, ""
    if isinstance(node.func, ast.Attribute):
        owner = node.func.value.id if isinstance(node.func.value, ast.Name) else ""
        return node.func.attr, owner
    return "", ""


def _call_keywords(node: ast.Call) -> dict[str, ast.AST]:
    return {item.arg: item.value for item in node.keywords if item.arg}


def _validate_order_call(
    node: ast.Call,
    call_name: str,
    static_values: dict[str, tuple[str, int | None]],
) -> None:
    required = _ORDER_API_ARGUMENTS[call_name]
    keywords = _call_keywords(node)
    if len(node.args) > 2:
        raise StrategyV2ContractError(
            f"strategyV2.apiCallInvalid:{call_name}:expectedSymbolAndValue"
        )
    for index, argument_name in enumerate(required):
        if index < len(node.args) and argument_name in keywords:
            raise StrategyV2ContractError(
                f"strategyV2.apiCallInvalid:{call_name}:duplicateArgument:{argument_name}"
            )
        if index >= len(node.args) and argument_name not in keywords:
            raise StrategyV2ContractError(
                f"strategyV2.apiCallInvalid:{call_name}:missingArgument:{argument_name}"
            )
    symbol_node = node.args[0] if node.args else keywords.get("symbol")
    value_node = node.args[1] if len(node.args) > 1 else keywords.get(required[1])
    symbol_kind, _ = _static_api_value(symbol_node, static_values)
    value_kind, _ = _static_api_value(value_node, static_values)
    if symbol_kind in {"context", "number", "boolean"} or value_kind in {
        "context",
        "symbol",
        "symbols",
    }:
        raise StrategyV2ContractError(
            f"strategyV2.apiCallInvalid:{call_name}:expectedSymbolAndValue"
        )


def _validate_get_history_call(
    node: ast.Call,
    call_name: str,
    static_values: dict[str, tuple[str, int | None]],
) -> None:
    keywords = _call_keywords(node)
    if "fields" in keywords:
        raise StrategyV2ContractError(
            f"strategyV2.apiCallInvalid:{call_name}:unsupportedArgument:fields"
        )
    if len(node.args) > 4:
        raise StrategyV2ContractError(
            f"strategyV2.apiCallInvalid:{call_name}:expectedCountFirst"
        )
    positional_names = ("count", "frequency", "field", "security_list")
    for index, name in enumerate(positional_names):
        if len(node.args) > index and name in keywords:
            raise StrategyV2ContractError(
                f"strategyV2.apiCallInvalid:{call_name}:duplicateArgument:{name}"
            )
    count_node = node.args[0] if node.args else keywords.get("count")
    if count_node is None:
        raise StrategyV2ContractError(
            f"strategyV2.apiCallInvalid:{call_name}:missingArgument:count"
        )
    count_kind, _ = _static_api_value(count_node, static_values)
    if count_kind in {"context", "string", "symbol", "symbols", "collection", "boolean"}:
        raise StrategyV2ContractError(
            f"strategyV2.apiCallInvalid:{call_name}:expectedCountFirst"
        )
    security_node = node.args[3] if len(node.args) > 3 else keywords.get("security_list")
    security_kind, _ = _static_api_value(security_node, static_values)
    if security_kind in {"context", "number", "boolean"}:
        raise StrategyV2ContractError(
            f"strategyV2.apiCallInvalid:{call_name}:invalidSecurityList"
        )


def _validate_data_history_call(
    node: ast.Call,
    static_values: dict[str, tuple[str, int | None]],
) -> None:
    keywords = _call_keywords(node)
    unsupported = set(keywords) - {"symbols", "count", "fields"}
    if unsupported:
        name = sorted(unsupported)[0]
        raise StrategyV2ContractError(
            f"strategyV2.apiCallInvalid:data.history:unsupportedArgument:{name}"
        )
    if len(node.args) > 3:
        raise StrategyV2ContractError(
            "strategyV2.apiCallInvalid:data.history:expectedSymbolsThenCount"
        )
    positional_names = ("symbols", "count", "fields")
    for index, name in enumerate(positional_names):
        if len(node.args) > index and name in keywords:
            raise StrategyV2ContractError(
                f"strategyV2.apiCallInvalid:data.history:duplicateArgument:{name}"
            )
    symbols_node = node.args[0] if node.args else keywords.get("symbols")
    count_node = node.args[1] if len(node.args) > 1 else keywords.get("count")
    if symbols_node is None or count_node is None:
        raise StrategyV2ContractError(
            "strategyV2.apiCallInvalid:data.history:expectedSymbolsThenCount"
        )
    symbols_kind, _ = _static_api_value(symbols_node, static_values)
    count_kind, _ = _static_api_value(count_node, static_values)
    if symbols_kind in {"context", "number", "boolean"} or count_kind in {
        "context",
        "string",
        "symbol",
        "symbols",
        "collection",
        "boolean",
    }:
        raise StrategyV2ContractError(
            "strategyV2.apiCallInvalid:data.history:expectedSymbolsThenCount"
        )


def _history_call_returns_single_frame(
    node: ast.Call,
    call_name: str,
    call_owner: str,
    static_values: dict[str, tuple[str, int | None]],
) -> bool:
    keywords = _call_keywords(node)
    target_node: ast.AST | None = None
    if call_name in {"get_history", "history"} and call_owner == "":
        target_node = node.args[3] if len(node.args) > 3 else keywords.get("security_list")
    elif call_name == "history" and call_owner == "data":
        target_node = node.args[0] if node.args else keywords.get("symbols")
    kind, count = _static_api_value(target_node, static_values)
    return kind == "symbol" or (kind == "symbols" and count == 1)


def _assigned_name_for_call(tree: ast.AST, target_call: ast.Call) -> str:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is not target_call:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if len(targets) == 1 and isinstance(targets[0], ast.Name):
            return targets[0].id
    return ""


def is_strategy_v2_code(code: str) -> bool:
    raw = str(code or "")
    return "def initialize(" in raw and "context.set_universe(" in raw and any(
        token in raw for token in ("context.subscribe(", "run_daily(", "run_weekly(", "run_monthly(")
    )


def _parse_many(values: object) -> list[InstrumentSpec]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if isinstance(values, dict):
        values = list(values.keys())
    try:
        raw_values = list(values)
    except TypeError:
        raw_values = [values]
    unique: dict[str, InstrumentSpec] = {}
    for value in raw_values:
        if is_index_reference(value):
            continue
        item = parse_instrument(value)
        unique[item.key] = item
    return list(unique.values())


def _schedule_callback(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[str, str]:
    callback = kwargs.get("callback")
    for value in args:
        if callable(value):
            callback = value
            break
    if not callable(callback):
        raise StrategyV2ContractError("strategyV2.scheduleCallbackRequired")
    return str(getattr(callback, "__name__", "scheduled")), str(kwargs.get("time") or "")


def _discover_dependencies(code: str) -> tuple[set[str], set[str]]:
    factors: set[str] = set()
    fundamentals: set[str] = set()
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return factors, fundamentals
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name not in {"indicator", "factor", "get_factors", "get_fundamentals"}:
            continue
        dependency_arg = 1 if name == "get_factors" else 0
        literals = _literal_strings(node.args[dependency_arg]) if len(node.args) > dependency_arg else []
        if name == "get_fundamentals":
            fundamentals.update(literals)
            continue
        for literal in literals:
            try:
                definition = get_factor(literal.lower())
            except FactorError:
                factors.add(literal)
                continue
            if definition.factor_type == "fundamental":
                fundamentals.update(field.upper() for field in definition.required_fields)
            else:
                factors.add(literal)
    return factors, fundamentals


def _literal_strings(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value.strip().upper()]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values: list[str] = []
        for item in node.elts:
            values.extend(_literal_strings(item))
        return values
    return []
