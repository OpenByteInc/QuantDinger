"""Composable capability contracts for Strategy API V2 AI authoring.

The base prompt describes rules shared by every strategy.  This module owns
task-specific rules so prompt construction, post-generation validation, and
the external authoring contract all use the same source of truth.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from app.services.factors.registry import list_factors
from app.services.strategy_direction import DIRECTION_MODES, normalize_direction_mode
from app.services.strategy_v2.contract import StrategyV2ContractError


ORDER_CALLS = {
    "order",
    "order_value",
    "order_target",
    "order_target_value",
    "order_target_percent",
}
ORDER_VALUE_ARGUMENTS = {
    "order": "amount",
    "order_value": "value",
    "order_target": "amount",
    "order_target_value": "value",
    "order_target_percent": "percent",
}
PROTECTION_ARGUMENTS = {
    "stop_loss_pct",
    "take_profit_pct",
    "trailing_stop_pct",
    "trailing_activation_pct",
    "time_limit_seconds",
}
INITIALIZE_RUNTIME_CALLS = ORDER_CALLS | {
    "cancel_order",
    "consume_last_exit_reason",
    "current",
    "factor",
    "get_factors",
    "get_fundamentals",
    "get_history",
    "get_order_status",
    "get_position",
    "get_positions",
    "history",
    "indicator",
    "set_default_protection",
}


@dataclass(frozen=True)
class StrategyAICapability:
    name: str
    summary: str
    rules: dict[str, Any]
    contract: str
    repair: str

    def metadata(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "rules": self.rules,
            "contract": self.contract.strip(),
            "repair": self.repair.strip(),
        }


@dataclass(frozen=True)
class StrategyAIGenerationIntent:
    capabilities: tuple[str, ...]
    requested_direction_mode: str = ""
    factor_ids: tuple[str, ...] = ()
    required_factor_ids: tuple[str, ...] = ()

    def metadata(self) -> dict[str, Any]:
        return {
            "capabilities": list(self.capabilities),
            "requested_direction_mode": self.requested_direction_mode,
            "active_factor_ids": list(self.factor_ids),
            "requested_factor_ids": list(self.required_factor_ids),
        }


_DIRECTION_VALUES = ", ".join(f"`{value}`" for value in sorted(DIRECTION_MODES))


CAPABILITY_PACKS: dict[str, StrategyAICapability] = {
    "advanced_execution": StrategyAICapability(
        name="advanced_execution",
        summary="Limit and maker-first execution parameters.",
        rules={
            "order_types": ["market", "limit"],
            "execution_algorithms": ["market", "limit", "maker_then_market"],
            "limit_price_required_for": ["limit"],
        },
        contract="""
## Advanced order execution
- Supported execution keywords are `order_type`, `limit_price`, `execution_algo`, `maker_wait_sec`, and `maker_offset_bps`.
- `order_type` accepts `market` or `limit`. `execution_algo` accepts `market`, `limit`, or `maker_then_market`.
- A limit order requires a positive `limit_price`. Maker waiting time and offset must remain non-negative and bounded.
""",
        repair="""
- Use only supported order/execution literals and add a positive `limit_price` to every limit order.
""",
    ),
    "crypto_swap": StrategyAICapability(
        name="crypto_swap",
        summary="Crypto perpetual net-position, hedge-leg, and leverage semantics.",
        rules={
            "direction_modes": sorted(DIRECTION_MODES),
            "position_sides": ["long", "short"],
            "direction_mode_required": True,
            "position_side_required_for": ["long_only", "short_only", "both", "neutral"],
            "position_side_forbidden_for": ["one_way"],
        },
        contract=f"""
## Crypto perpetual contract
- Every new Crypto `@swap` strategy must declare `context.set_metadata(direction_mode=...)` in `initialize`; accepted values are exactly {_DIRECTION_VALUES}.
- `direction_mode` is strategy capability metadata. `position_side` is the concrete `long` or `short` hedge leg on a position read or order. They are not interchangeable.
- `one_way` means one signed net position. Use `get_position(symbol)` and omit `position_side` from every order. Positive targets open or maintain long exposure; negative targets open or maintain short exposure; close the current position before opening the opposite side.
- For `long_only`, `short_only`, `both`, and `neutral`, every `get_position(...)` and order call for a swap instrument must explicitly pass `position_side="long"` or `position_side="short"` (a variable resolving to one of those values is also valid).
- For `long_only`, `short_only`, `both`, and `neutral`, hedge-account positions remain explicit legs. A `one_way` strategy is the exception: the runtime exposes its single active strategy-owned leg through the signed `get_position(symbol)` view on either account mode.
- Short targets use negative quantity, value, or percent while still declaring `position_side="short"`. Closing either leg uses a zero target for that same `position_side`.
- `one_way` is account-mode adaptive in live trading: the runtime uses a net position on one-way accounts and routes the active leg explicitly on hedge accounts while preventing simultaneous strategy-owned legs. `both` and `neutral` still require exchange hedge mode. `allow_leverage` remains a separate source permission and must never be multiplied into order sizing.
""",
        repair="""
- For `one_way`, remove `position_side` and implement signed net-position reversal. For all hedge-leg modes, add an explicit valid `position_side` to every Crypto swap position read and order.
- Add the canonical `direction_mode` declaration and keep it consistent with the implemented legs.
""",
    ),
    "supertrend": StrategyAICapability(
        name="supertrend",
        summary="Canonical platform Supertrend series and reversal semantics.",
        rules={
            "indicator_name": "supertrend",
            "parameters": ["period", "multiplier", "output"],
            "direction_output": "direction",
            "warmup_handling": "dropna",
        },
        contract="""
## Canonical Supertrend
- Never reimplement Supertrend bands from ATR in generated strategy source. Use the platform series: `indicator("supertrend", symbol, period=period, multiplier=multiplier, output="direction", frequency=frequency)`.
- The returned pandas Series contains warmup `NaN` values. First call `valid_trend = trend.dropna()` and return unless `len(valid_trend) >= 2`.
- A change from a negative previous value to a positive current value is a bullish reversal. A positive-to-negative change is a bearish reversal.
- Read `valid_trend.iloc[-2]` and `valid_trend.iloc[-1]` only after the completed-data length guard. Do not rebuild rolling bands independently on every bar.
""",
        repair="""
- Replace custom ATR-band Supertrend implementations with the canonical `indicator("supertrend", ..., output="direction")` series.
- Drop warmup `NaN` values before reading the final two direction values.
""",
    ),
    "technical_factors": StrategyAICapability(
        name="technical_factors",
        summary="Canonical registered factor APIs selected for this strategy.",
        rules={
            "series_api": "indicator",
            "scalar_api": "factor",
            "warmup_handling": "dropna",
            "registry_source": "app.services.factors.registry",
        },
        contract="""
## Registered technical factors
- When adding or changing a requested factor, use the canonical factor ID and parameters in its active definition. Do not reimplement it with ad-hoc rolling math.
- Use `indicator(factor_id, symbol, **params)` when previous/current series values are needed for signals. Use `factor(factor_id, symbol, **params)` only when one current scalar value is sufficient.
- `indicator(...)` returns a pandas Series for one output or a DataFrame for multiple outputs. Warmup values may be `NaN`; call `.dropna()` on the selected series and check its length before `.iloc` access.
- Multi-output indicators must select a documented `output` or use the returned DataFrame columns. Never guess output names or parameter aliases.
- For series APIs, use returned DataFrame columns for MACD (`macd`, `macdsignal`, `macdhist`), STOCH (`slowk`, `slowd`), and KDJ (`k`, `d`, `j`); an `output` argument is not a universal Series selector. For scalar `factor`, use the active definition's output enum.
- A definition marked `requested` is a hard requirement for this turn. A definition marked only `existing` is context: preserve a working legacy or TA-Lib call when the user did not ask to change that factor.
""",
        repair="""
- Replace manual implementations of requested registered factors with `indicator(...)` or `factor(...)` using the active factor definition.
- Select the documented output, remove warmup `NaN` values, and guard completed-data length before reading signal values.
""",
    ),
    "bidirectional": StrategyAICapability(
        name="bidirectional",
        summary="Independent hedge legs for direction_mode=both.",
        rules={
            "requested_direction_mode": "both",
            "required_order_sides": ["long", "short"],
        },
        contract="""
## Bidirectional swap behavior
- `direction_mode="both"` means independent long and short hedge legs and requires exchange hedge mode.
- Implement independent long entry, long exit, short entry, and short exit behavior. A long exit is not a short entry, and a short exit is not a long entry.
- Read both legs explicitly with `get_position(symbol, position_side="long")` and `get_position(symbol, position_side="short")` when position state is needed.
- Orders for the long leg pass `position_side="long"`; orders for the short leg pass `position_side="short"`. Target-style short entries use a negative target and short exits use zero.
- A signal reversal may close one leg and open the other, but the source must express those as separate leg-specific intents and must not assume either order filled immediately.
""",
        repair="""
- Do not repair a bidirectional request by changing metadata alone.
- Ensure order calls visibly cover both `long` and `short` position sides with independent entry/exit conditions.
""",
    ),
    "one_way_reversal": StrategyAICapability(
        name="one_way_reversal",
        summary="Long/short reversal on one signed net position.",
        rules={
            "requested_direction_mode": "one_way",
            "position_side": "forbidden",
        },
        contract="""
## One-way long/short reversal
- A general long-and-short request means `direction_mode="one_way"` unless the user explicitly requests hedge mode or simultaneous independent legs.
- Read the signed position with `get_position(symbol)` and omit `position_side` from all orders.
- Express reversal as four intents: close short, open long, close long, and open short. Submit the close first and do not assume it filled immediately before opening the opposite side.
- Positive target quantities, values, or percentages represent long exposure; negative targets represent short exposure; zero closes the current net position.
""",
        repair="""
- Remove hedge-leg `position_side` arguments and use the signed net position.
- Keep close and opposite-side entry as separate order intents so the live executor can serialize the reversal safely.
""",
    ),
    "order_lifecycle": StrategyAICapability(
        name="order_lifecycle",
        summary="Asynchronous, idempotent order tracking and reconciliation.",
        rules={
            "client_order_id_max_length": 100,
            "client_order_id_unique_per_logical_order": True,
            "active_statuses": ["unknown", "queued", "deferred", "submitted", "open", "partial"],
            "terminal_statuses": ["filled", "rejected", "failed", "cancelled", "canceled", "expired"],
            "requires_status_check": True,
            "status_result": {
                "type": "mapping",
                "status_field": "status",
                "filled_quantity_field": "filled_quantity",
                "filled_notional_field": "filled_notional",
                "fee_field": "fee",
            },
        },
        contract="""
## Stateful order lifecycle
- An order function submits intent; it does not prove a fill. Active states include `unknown`, `queued`, `deferred`, `submitted`, `open`, and `partial`; `partial` is not a completed fill.
- Orders that may be retried, cancelled, reconciled, or used to advance a cycle must pass a stable `client_order_id` of at most 100 characters and retain the returned reference. The ID must be stable for retries of the same logical order but unique across later trading cycles; derive it from the completed-bar timestamp plus side/action instead of reusing one literal forever.
- `get_order_status(reference)` returns a mapping, not a status string. Read `result["status"]` (and, when needed, `filled_quantity`, `filled_notional`, or `fee`). Query with the retained reference and cancel with `cancel_order(reference)`. Treat cancellation as asynchronous.
- Advance strategy state, reuse capital, or open the opposite leg only after both a terminal order state and the synchronized position confirm the result.
- A target crossing zero is close-then-open. Never model it as one immediately filled reversal.
- Supported execution keywords include `reason`, `position_side`, `client_order_id`, `order_type`, `limit_price`, `execution_algo`, `maker_wait_sec`, and `maker_offset_bps`. A limit order requires a positive `limit_price`.
""",
        repair="""
- Add stable, per-logical-order `client_order_id` values to tracked orders and use `get_order_status(reference)["status"]` before advancing state.
- Do not treat `partial`, a submitted order, or a cancel request as a terminal fill.
""",
    ),
    "protection": StrategyAICapability(
        name="protection",
        summary="Native stop-loss, take-profit, trailing, and time protection.",
        rules={
            "ratio_limits": {
                "stop_loss_pct": 1.0,
                "take_profit_pct": 5.0,
                "trailing_stop_pct": 1.0,
                "trailing_activation_pct": 5.0,
            },
            "non_negative": ["time_limit_seconds"],
            "requires": {"trailing_activation_pct": "trailing_stop_pct"},
        },
        contract="""
## Native position protection
- Attach protection to an entry order with `stop_loss_pct`, `take_profit_pct`, `trailing_stop_pct`, `trailing_activation_pct`, and/or `time_limit_seconds`, or call `set_default_protection(...)` inside an executable handler/callback before later entries.
- When protection is requested for Crypto swaps, each required entry leg must carry effective protection directly on its order call (inline keywords or a literal `protection` mapping). The AI validator does not accept `set_default_protection` alone as proof that each swap entry leg is protected.
- Percentage values are decimal ratios: `0.03` means 3%, not 0.03% or 3.
- Use ratios within their bounds: stop loss and trailing stop 0..1; take profit and trailing activation 0..5; time limits are non-negative seconds. Zero disables a rule; activation alone is not protection. `trailing_rebase_on_scale_in` defaults to true and resets the trail and holding clock on scale-in; false preserves an already active trail.
- Signal exits and native protections are different mechanisms. Do not claim fixed stop-loss or take-profit behavior unless executable protection arguments are present.
- Backtest native protection evaluates each bar's open/high/low and can close at the trigger price or a gap opening price, including on an entry fill bar. It is not a close-only signal exit. The default conservative conflict policy prioritizes stop loss, trailing stop, time limit, then take profit. Live protection uses a separate price clock; do not mutate strategy candles to implement it.
""",
        repair="""
- Implement requested stops or take profit with native protection keywords or `set_default_protection`; explanatory comments or parameters alone are insufficient.
- For Crypto swaps, put effective protection directly on every required entry leg; default protection alone does not pass per-leg AI validation.
- Keep percentage values as bounded decimal ratios.
""",
    ),
    "persistent_state": StrategyAICapability(
        name="persistent_state",
        summary="Restart-safe state for grids, DCA, martingale, and order cycles.",
        rules={
            "module_flag": "PERSIST_RUNTIME_STATE",
            "module_flag_value": True,
            "state_namespace": "g",
        },
        contract="""
## Restart-safe strategy state
- If behavior cannot be reconstructed solely from synchronized positions and order status, declare `PERSIST_RUNTIME_STATE = True` at module scope.
- Store only JSON-like values on `g`; do not use files, databases, network services, or hidden module state.
- After restart, reconcile restored state with actual positions and tracked order status before placing another order. A snapshot never replaces the exchange ledger.
- Grid, DCA, martingale, and multi-step order-cycle strategies must have explicit layer/cycle bounds and stable client order IDs.
""",
        repair="""
- Enable `PERSIST_RUNTIME_STATE = True` for restart-sensitive state machines and keep `g` values serializable.
- Reconcile restored state before resuming a bounded grid, DCA, martingale, or order cycle.
""",
    ),
    "scheduling": StrategyAICapability(
        name="scheduling",
        summary="Schedule callbacks, timezone, and bar visibility semantics.",
        rules={
            "weekday_range": [1, 7],
            "live_after_trading_end": False,
            "scheduled_data_visibility": "previous_completed_bar",
            "handle_data_fill": "next_bar_open",
        },
        contract="""
## Scheduling and execution timing
- `weekday` uses 1 through 7 with Monday equal to 1. A monthly day beyond the month end resolves to the final day of that month.
- Scheduled callbacks see the previous completed bar and may submit for the current open. `handle_data` sees the newly completed bar and submits for the next bar open.
- Live schedule times use the configured user timezone. Current live runtime does not call `after_trading_end`; critical close behavior belongs in `handle_data` or an explicit schedule.
""",
        repair="""
- Keep schedule helpers global, use valid weekday/monthday values, and do not assume a scheduled callback can see the unfinished current bar.
""",
    ),
}


_ONE_WAY_TERMS = (
    "多空双向",
    "双向/多空",
    "多空都",
    "多空交易",
    "单向持仓",
    "净持仓",
    "开多平空",
    "开空平多",
    "long and short",
    "long/short",
    "bidirectional",
    "both directions",
    "one way",
    "one-way",
    "one_way",
    "net position",
    "single position",
    'direction_mode="one_way"',
    "direction_mode='one_way'",
)
_BIDIRECTIONAL_TERMS = (
    "双向持仓",
    "对冲模式",
    "独立多空腿",
    "hedge mode",
    "hedged",
    "independent long and short legs",
    "both",
    'direction_mode="both"',
    "direction_mode='both'",
)
_NEUTRAL_TERMS = (
    "中性双腿",
    "market neutral",
    "neutral",
    'direction_mode="neutral"',
    "direction_mode='neutral'",
)
_SHORT_ONLY_TERMS = (
    "仅做空",
    "只做空",
    "short only",
    "short-only",
    "short_only",
    'direction_mode="short_only"',
)
_LONG_ONLY_TERMS = (
    "仅做多",
    "只做多",
    "long only",
    "long-only",
    "long_only",
    'direction_mode="long_only"',
)
_DIRECTION_TERM_MAP = {
    **{term: "neutral" for term in _NEUTRAL_TERMS},
    **{term: "both" for term in _BIDIRECTIONAL_TERMS},
    **{term: "one_way" for term in _ONE_WAY_TERMS},
    **{term: "short_only" for term in _SHORT_ONLY_TERMS},
    **{term: "long_only" for term in _LONG_ONLY_TERMS},
}
_PROTECTION_TERMS = (
    "风控",
    "風控",
    "risk control",
    "risk management",
    "止损",
    "止盈",
    "移动止损",
    "保护订单",
    "stop loss",
    "stop-loss",
    "take profit",
    "take-profit",
    "trailing stop",
    "stop_loss_pct",
    "take_profit_pct",
    "trailing_stop_pct",
    "set_default_protection",
)
_ORDER_LIFECYCLE_TERMS = (
    "订单状态",
    "部分成交",
    "撤单",
    "重试订单",
    "订单重试",
    "对账",
    "client_order_id",
    "get_order_status",
    "cancel_order",
    "partial fill",
    "order status",
    "idempotent order",
    "reconcile order",
)
_ADVANCED_EXECUTION_TERMS = (
    "限价单",
    "限价",
    "maker",
    "limit order",
    "order_type",
    "execution_algo",
    "maker_wait_sec",
    "maker_offset_bps",
)
_SUPERTREND_TERMS = (
    "supertrend",
    "super trend",
    "超级趋势",
)
_FACTOR_ALIASES = {
    "布林": "bollinger_bands",
    "唐奇安": "donchian_channels",
    "肯特纳": "keltner_channels",
    "超级趋势": "supertrend",
    "威廉指标": "williams_r",
    "资金流量指标": "mfi",
    "商品通道指标": "cci",
}
_LEGACY_FACTOR_ALIASES = {
    "stoch": "stochastic",
}
_TECHNICAL_FACTOR_DEFINITIONS = {
    str(item["factor_id"]): item
    for item in list_factors(factor_type="technical")
}
_PERSISTENCE_TERMS = (
    "跨重启",
    "状态恢复",
    "持久化状态",
    "网格",
    "马丁",
    "dca",
    "grid",
    "martingale",
    "persist_runtime_state",
)
_SCHEDULE_TERMS = (
    "定时",
    "调度",
    "每天",
    "每周",
    "每月",
    "run_daily",
    "run_weekly",
    "run_monthly",
    "scheduled callback",
)


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _requested_factor_ids(text: str) -> tuple[str, ...]:
    lowered = str(text or "").lower()
    selected = {
        factor_id
        for factor_id in _TECHNICAL_FACTOR_DEFINITIONS
        if any(
            re.search(
                rf"(?<![a-z0-9_]){re.escape(alias)}(?![a-z0-9_])",
                lowered,
            )
            for alias in {
                factor_id,
                factor_id.replace("_", " "),
                factor_id.replace("_", "-"),
            }
        )
    }
    selected.update(
        factor_id
        for alias, factor_id in _FACTOR_ALIASES.items()
        if alias in lowered
    )
    return tuple(sorted(selected))


def _source_factor_ids(source: str) -> tuple[str, ...]:
    try:
        tree = ast.parse(str(source or ""))
    except SyntaxError:
        return ()
    selected: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = (
            node.func.id
            if isinstance(node.func, ast.Name)
            else node.func.attr
            if isinstance(node.func, ast.Attribute)
            else ""
        )
        factor_node: ast.AST | None = None
        if call_name in {"indicator", "factor"}:
            factor_node = node.args[0] if node.args else next(
                (item.value for item in node.keywords if item.arg == "name"),
                None,
            )
        elif call_name == "get_factors":
            factor_node = node.args[1] if len(node.args) > 1 else next(
                (item.value for item in node.keywords if item.arg == "names"),
                None,
            )
        values = factor_node.elts if isinstance(factor_node, (ast.List, ast.Tuple, ast.Set)) else [factor_node]
        for value_node in values:
            if not isinstance(value_node, ast.Constant) or not isinstance(value_node.value, str):
                continue
            factor_id = value_node.value.strip().lower().replace("-", "_").replace(" ", "_")
            factor_id = _LEGACY_FACTOR_ALIASES.get(factor_id, factor_id)
            if factor_id in _TECHNICAL_FACTOR_DEFINITIONS:
                selected.add(factor_id)
    return tuple(sorted(selected))


def _requested_direction(prompt: str, existing_code: str) -> str:
    request = str(prompt or "").lower()
    mentions = [
        (request.rfind(term.lower()), len(term), mode)
        for term, mode in _DIRECTION_TERM_MAP.items()
        if request.rfind(term.lower()) >= 0
    ]
    if mentions:
        return max(mentions, key=lambda item: (item[0], item[1]))[2]
    matches = list(re.finditer(
        r"(?:direction_mode\s*=|['\"]direction_mode['\"]\s*[:,])\s*"
        r"['\"](long_only|short_only|one_way|both|neutral)['\"]",
        str(existing_code or ""),
        flags=re.IGNORECASE,
    ))
    return normalize_direction_mode(matches[-1].group(1)) if matches else ""


def resolve_strategy_generation_intent(
    *,
    prompt: str,
    existing_code: str = "",
    context: dict | None = None,
) -> StrategyAIGenerationIntent:
    """Select capability packs from explicit request, source, and IDE context."""
    context = dict(context or {})
    if context.get("source") == "indicator_ide_conversion":
        conversion_request = context.pop("conversionRequest", None)
        if conversion_request is None:
            # Older clients embed the request between template and indicator source.
            match = re.search(
                r"User conversion request:\s*\n(.*?)\nIndicator source code:",
                str(prompt or ""),
                flags=re.DOTALL,
            )
            conversion_request = match.group(1) if match else prompt
        prompt = str(conversion_request or "")
    serialized_context = json.dumps(context, ensure_ascii=False, sort_keys=True)
    combined = "\n".join((str(prompt or ""), str(existing_code or ""), serialized_context))
    capabilities: set[str] = set()
    direction = _requested_direction(prompt, existing_code)
    required_factor_ids = _requested_factor_ids(
        "\n".join((str(prompt or ""), serialized_context))
    )
    factor_ids = tuple(sorted({
        *required_factor_ids,
        *_source_factor_ids(existing_code),
    }))

    if re.search(r"@(?:[a-z0-9_-]+:)?swap\b|usdtswap\b|usdt[ /_-]*swap\b|\bperpetual\b|永续", combined, re.IGNORECASE):
        capabilities.add("crypto_swap")
    if _contains_any(combined, _SUPERTREND_TERMS):
        capabilities.add("supertrend")
    if factor_ids:
        capabilities.add("technical_factors")
    if direction in {"both", "neutral"}:
        capabilities.update(("crypto_swap", "bidirectional"))
    if direction == "one_way":
        capabilities.update(("crypto_swap", "one_way_reversal"))
    if _contains_any(combined, _PROTECTION_TERMS):
        capabilities.add("protection")
    if _contains_any(combined, _ORDER_LIFECYCLE_TERMS):
        capabilities.update(("advanced_execution", "order_lifecycle", "persistent_state"))
    if _contains_any(combined, _ADVANCED_EXECUTION_TERMS):
        capabilities.add("advanced_execution")
    if _contains_any(combined, _PERSISTENCE_TERMS):
        capabilities.update(("persistent_state", "order_lifecycle"))
    if _contains_any(combined, _SCHEDULE_TERMS):
        capabilities.add("scheduling")

    explicit = context.get("strategyCapabilities") or context.get("strategy_capabilities") or []
    if isinstance(explicit, str):
        explicit = [explicit]
    if isinstance(explicit, (list, tuple, set)):
        capabilities.update(str(item).strip() for item in explicit if str(item).strip() in CAPABILITY_PACKS)

    return StrategyAIGenerationIntent(
        tuple(sorted(capabilities)),
        direction,
        factor_ids,
        required_factor_ids,
    )


def render_strategy_capability_contract(intent: StrategyAIGenerationIntent) -> str:
    if not intent.capabilities:
        return ""
    sections = [
        "# Active capability contracts",
        "Only the capability packs selected for this request/source are included below.",
        json.dumps(intent.metadata(), ensure_ascii=False, sort_keys=True),
    ]
    sections.extend(CAPABILITY_PACKS[name].contract.strip() for name in intent.capabilities)
    if intent.factor_ids:
        active_definitions = {
            factor_id: {
                "required_fields": definition["required_fields"],
                "default_params": definition["default_params"],
                "parameter_schema": definition["parameter_schema"],
                "direction_hint": definition["direction_hint"],
                "default_warmup_bars": definition["default_warmup_bars"],
                "scope": (
                    "requested"
                    if factor_id in intent.required_factor_ids
                    else "existing"
                ),
            }
            for factor_id in intent.factor_ids
            if (definition := _TECHNICAL_FACTOR_DEFINITIONS.get(factor_id))
        }
        sections.extend(
            (
                "## Active factor definitions",
                json.dumps(active_definitions, ensure_ascii=False, sort_keys=True),
            )
        )
    return "\n\n".join(sections)


def render_strategy_capability_repairs(intent: StrategyAIGenerationIntent) -> str:
    if not intent.capabilities:
        return ""
    lines = ["# Active capability repair rules"]
    lines.extend(CAPABILITY_PACKS[name].repair.strip() for name in intent.capabilities)
    return "\n".join(lines)


def strategy_ai_capability_catalog() -> dict[str, dict[str, Any]]:
    return {name: capability.metadata() for name, capability in CAPABILITY_PACKS.items()}


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in {"context", "data"}
    ):
        return node.func.attr
    return ""


def _keyword(node: ast.Call, name: str) -> ast.AST | None:
    return next((item.value for item in node.keywords if item.arg == name), None)


def _literal_text(node: ast.AST | None) -> str:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return ""
    return node.value.strip().lower()


def _static_string_key(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "g"
    ):
        return f"g.{node.attr}"
    return ""


def _collect_static_strings(tree: ast.AST) -> dict[str, str]:
    assignments: list[tuple[str, ast.AST]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        assignments.extend(
            (key, node.value)
            for target in targets
            if (key := _static_string_key(target))
        )
    values: dict[str, str] = {}
    for _ in range(len(assignments) + 1):
        changed = False
        for key, value_node in assignments:
            value = _literal_text(value_node)
            if not value:
                reference = _static_string_key(value_node)
                value = values.get(reference, "")
            if value and values.get(key) != value:
                values[key] = value
                changed = True
        if not changed:
            break
    return values


def _collect_assignment_expressions(tree: ast.AST) -> dict[str, ast.AST]:
    values: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            key = _static_string_key(target)
            if key:
                values[key] = node.value
    return values


def _resolved_text(node: ast.AST | None, static_strings: dict[str, str]) -> str:
    if node is None:
        return ""
    return _literal_text(node) or static_strings.get(_static_string_key(node), "")


def _enclosing_function(
    tree: ast.AST,
    target: ast.AST,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    matches = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        descendants = list(ast.walk(node))
        if any(item is target for item in descendants):
            matches.append((len(descendants), node))
    return min(matches, key=lambda item: item[0])[1] if matches else None


def _possible_text_values(
    node: ast.AST | None,
    tree: ast.AST,
    static_strings: dict[str, str],
) -> set[str]:
    resolved = _resolved_text(node, static_strings)
    if resolved:
        return {resolved}
    if isinstance(node, ast.IfExp):
        return _possible_text_values(node.body, tree, static_strings) | _possible_text_values(
            node.orelse, tree, static_strings
        )
    if not isinstance(node, ast.Name):
        return set()
    function = _enclosing_function(tree, node)
    if function is None:
        return set()
    parameters = [*function.args.posonlyargs, *function.args.args]
    parameter_index = next(
        (index for index, item in enumerate(parameters) if item.arg == node.id),
        None,
    )
    if parameter_index is None:
        return set()
    invocations = [
        call
        for call in ast.walk(tree)
        if isinstance(call, ast.Call) and _call_name(call) == function.name
    ]
    if not invocations:
        return set()
    values: set[str] = set()
    for call in invocations:
        argument = (
            call.args[parameter_index]
            if len(call.args) > parameter_index
            else _keyword(call, node.id)
        )
        value = _resolved_text(argument, static_strings)
        if not value:
            return set()
        values.add(value)
    return values


def _literal_text_values(
    node: ast.AST | None,
    static_strings: dict[str, str],
) -> set[str]:
    resolved = _resolved_text(node, static_strings)
    if resolved:
        return {resolved}
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values: set[str] = set()
        for item in node.elts:
            value = _resolved_text(item, static_strings)
            if value:
                values.add(value)
        return values
    return set()


def _literal_number(node: ast.AST | None) -> float | None:
    try:
        value = ast.literal_eval(node) if node is not None else None
    except (TypeError, ValueError):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _numeric_values(
    node: ast.AST | None,
    assignments: dict[str, ast.AST],
    seen: set[str] | None = None,
) -> set[float]:
    literal = _literal_number(node)
    if literal is not None:
        return {literal}
    if node is None:
        return set()
    if isinstance(node, ast.IfExp):
        return _numeric_values(node.body, assignments, seen) | _numeric_values(
            node.orelse, assignments, seen
        )
    key = _static_string_key(node)
    visited = set(seen or ())
    if key and key in assignments and key not in visited:
        visited.add(key)
        return _numeric_values(assignments[key], assignments, visited)
    return set()


def _is_definitely_zero(node: ast.AST | None, assignments: dict[str, ast.AST]) -> bool:
    values = _numeric_values(node, assignments)
    return bool(values) and all(abs(value) <= 1e-12 for value in values)


def _order_value_node(node: ast.Call) -> ast.AST | None:
    argument = ORDER_VALUE_ARGUMENTS[_call_name(node)]
    return node.args[1] if len(node.args) > 1 else _keyword(node, argument)


def _instrument_argument(node: ast.Call) -> ast.AST | None:
    if node.args:
        return node.args[0]
    return _keyword(node, "symbol") or _keyword(node, "instrument")


def _assigned_key_for_call(tree: ast.AST, target_call: ast.Call) -> str:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is not target_call:
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if len(targets) == 1:
            return _static_string_key(targets[0])
    return ""


def _status_result_field_is_read(
    tree: ast.AST,
    target_call: ast.Call,
    static_strings: dict[str, str],
) -> bool:
    assigned_key = _assigned_key_for_call(tree, target_call)
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript):
            same_result = node.value is target_call
            assigned_result = (
                bool(assigned_key)
                and _static_string_key(node.value) == assigned_key
            )
            if (same_result or assigned_result) and _resolved_text(
                node.slice, static_strings
            ) == "status":
                return True
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "get" or not node.args:
            continue
        same_result = node.func.value is target_call
        assigned_result = (
            bool(assigned_key)
            and _static_string_key(node.func.value) == assigned_key
        )
        if same_result or assigned_result:
            if _resolved_text(node.args[0], static_strings) == "status":
                return True
    return False


def _has_effective_protection(node: ast.Call) -> bool:
    if _call_name(node) not in ORDER_CALLS | {"set_default_protection"}:
        return False
    protection_node = _keyword(node, "protection")
    if protection_node is not None and not isinstance(protection_node, ast.Dict):
        if not (isinstance(protection_node, ast.Constant) and protection_node.value is None):
            return True
    for value_node in _protection_items(node).values():
        literal = _literal_number(value_node)
        if literal is None or literal > 0:
            return True
    return False


def _protection_items(node: ast.Call) -> dict[str, ast.AST]:
    items = {
        name: value
        for name in PROTECTION_ARGUMENTS
        if (value := _keyword(node, name)) is not None
    }
    protection_node = _keyword(node, "protection")
    if isinstance(protection_node, ast.Dict):
        for key, value in zip(protection_node.keys, protection_node.values):
            name = _literal_text(key)
            if name in PROTECTION_ARGUMENTS:
                items.setdefault(name, value)
    return items


def _assignment_is_true(tree: ast.AST, name: str) -> bool:
    for node in tree.body if isinstance(tree, ast.Module) else ():
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == name for target in targets):
            continue
        if isinstance(node.value, ast.Constant) and node.value.value is True:
            return True
    return False


def _function_calls(function: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    calls: list[ast.Call] = []

    class FunctionBodyVisitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            calls.append(node)
            self.generic_visit(node)

        def visit_FunctionDef(self, _node: ast.FunctionDef) -> None:  # noqa: N802
            return

        def visit_AsyncFunctionDef(self, _node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            return

        def visit_Lambda(self, _node: ast.Lambda) -> None:  # noqa: N802
            return

    visitor = FunctionBodyVisitor()
    for statement in function.body:
        visitor.visit(statement)
    return calls


def _metadata_direction_from_call(
    node: ast.Call,
    static_strings: dict[str, str],
) -> str:
    keyword_value = _keyword(node, "direction_mode")
    if keyword_value is not None:
        return normalize_direction_mode(_resolved_text(keyword_value, static_strings))
    if len(node.args) == 2 and _resolved_text(node.args[0], static_strings) == "direction_mode":
        return normalize_direction_mode(_resolved_text(node.args[1], static_strings))
    if len(node.args) == 1 and isinstance(node.args[0], ast.Dict):
        for key, value in zip(node.args[0].keys, node.args[0].values):
            if _resolved_text(key, static_strings) == "direction_mode":
                return normalize_direction_mode(_resolved_text(value, static_strings))
    return ""


def _declared_direction_mode(tree: ast.AST, static_strings: dict[str, str]) -> str:
    for function in tree.body if isinstance(tree, ast.Module) else ():
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if function.name != "initialize":
            continue
        positional = [*function.args.posonlyargs, *function.args.args]
        context_name = positional[0].arg if positional else "context"
        for node in _function_calls(function):
            if not (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "set_metadata"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == context_name
            ):
                continue
            direction = _metadata_direction_from_call(node, static_strings)
            if direction:
                return direction
    return ""


def _validate_history_window_clock(tree: ast.AST) -> None:
    for function in (node for node in tree.body if isinstance(node, ast.FunctionDef)):
        assignments = _collect_assignment_expressions(function)

        def origin(node: ast.AST | None, seen: frozenset[str] = frozenset()) -> str:
            key = _static_string_key(node)
            if key:
                return origin(assignments.get(key), seen | {key}) if key not in seen else ""
            if isinstance(node, ast.Call):
                if _call_name(node) in {"get_history", "history"}:
                    return "history"
                if _call_name(node) == "len" and node.args and origin(node.args[0], seen) == "history":
                    return "window_length"
            if isinstance(node, ast.Subscript):
                return origin(node.value, seen)
            if isinstance(node, ast.BinOp):
                if "window_length" in {origin(node.left, seen), origin(node.right, seen)}:
                    return "window_length"
            return ""

        for node in ast.walk(function):
            if not isinstance(node, ast.Compare):
                continue
            persisted = any(
                _static_string_key(child).startswith("g.") and origin(child) == "window_length"
                for child in ast.walk(node)
            )
            current = any(
                isinstance(child, (ast.Name, ast.Call)) and origin(child) == "window_length"
                for child in ast.walk(node)
            )
            if persisted and current:
                raise StrategyV2ContractError("strategyV2.aiHistoryWindowClockUnsupported")


def validate_strategy_ai_semantics(
    code: str,
    manifest: Any,
    intent: StrategyAIGenerationIntent,
) -> None:
    """Enforce high-value authoring semantics that compilation cannot prove."""
    try:
        tree = ast.parse(str(code or ""))
    except SyntaxError:
        return

    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    static_strings = _collect_static_strings(tree)
    assignment_expressions = _collect_assignment_expressions(tree)
    _validate_history_window_clock(tree)
    order_calls = [node for node in calls if _call_name(node) in ORDER_CALLS]
    position_calls = [node for node in calls if _call_name(node) == "get_position"]
    direction_mode = normalize_direction_mode(getattr(manifest, "direction_mode", ""))
    declared_direction_mode = _declared_direction_mode(tree, static_strings)
    requested_direction = normalize_direction_mode(intent.requested_direction_mode)

    for node in tree.body if isinstance(tree, ast.Module) else ():
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != "initialize":
            continue
        positional = [*node.args.posonlyargs, *node.args.args]
        context_name = positional[0].arg if positional else "context"
        for child in _function_calls(node):
            call_name = ""
            if isinstance(child.func, ast.Name):
                call_name = child.func.id
            elif (
                isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id in {context_name, "data"}
            ):
                call_name = child.func.attr
            if call_name == "set_default_protection":
                raise StrategyV2ContractError(
                    "strategyV2.aiDefaultProtectionInitializeUnsupported"
                )
            if call_name in INITIALIZE_RUNTIME_CALLS:
                raise StrategyV2ContractError(
                    f"strategyV2.aiInitializeRuntimeApiUnsupported:{call_name}"
                )
    if requested_direction and direction_mode != requested_direction:
        raise StrategyV2ContractError(
            f"strategyV2.aiDirectionModeMismatch:{requested_direction}:{direction_mode or 'missing'}"
        )

    universe = getattr(manifest, "universe", None)
    instruments = list(getattr(universe, "instruments", ()) or ())
    all_swap = bool(instruments) and all(
        getattr(item, "market", "") == "Crypto" and getattr(item, "market_type", "") == "swap"
        for item in instruments
    )
    any_swap = any(
        getattr(item, "market", "") == "Crypto" and getattr(item, "market_type", "") == "swap"
        for item in instruments
    )
    swap_scope = any_swap or "crypto_swap" in intent.capabilities

    if swap_scope and not declared_direction_mode:
        raise StrategyV2ContractError("strategyV2.aiSwapDirectionModeRequired")
    if declared_direction_mode and direction_mode != declared_direction_mode:
        raise StrategyV2ContractError(
            f"strategyV2.aiDirectionModeMismatch:{declared_direction_mode}:{direction_mode or 'missing'}"
        )
    if not swap_scope and direction_mode in {"short_only", "one_way", "both", "neutral"}:
        raise StrategyV2ContractError("strategyV2.aiShortRequiresCryptoSwap")

    swap_instrument_keys = {
        str(getattr(item, "key", "")).strip().lower()
        for item in instruments
        if getattr(item, "market", "") == "Crypto"
        and getattr(item, "market_type", "") == "swap"
    }
    swap_position_calls: list[ast.Call] = []
    swap_order_calls: list[ast.Call] = []
    for node in [*position_calls, *order_calls]:
        instrument_key = _resolved_text(_instrument_argument(node), static_strings)
        is_swap_call = (
            all_swap
            or instrument_key in swap_instrument_keys
            or (not instruments and "crypto_swap" in intent.capabilities)
        )
        # In a mixed universe, an unresolved instrument could select a swap at
        # runtime. Require an explicit leg instead of silently accepting an
        # ambiguous order/read that compilation cannot classify.
        if swap_scope and not instrument_key:
            is_swap_call = True
        if not is_swap_call:
            continue
        if node in position_calls:
            swap_position_calls.append(node)
        else:
            swap_order_calls.append(node)

    order_sides: set[str] = set()
    valid_position_sides = set(CAPABILITY_PACKS["crypto_swap"].rules["position_sides"])
    if swap_scope:
        for node in [*swap_position_calls, *swap_order_calls]:
            side_node = _keyword(node, "position_side")
            if direction_mode == "one_way":
                if side_node is not None:
                    raise StrategyV2ContractError(
                        f"strategyV2.aiOneWayPositionSideForbidden:{_call_name(node)}"
                    )
                continue
            if side_node is None:
                raise StrategyV2ContractError(
                    f"strategyV2.aiSwapPositionSideRequired:{_call_name(node)}"
                )
            possible_sides = _possible_text_values(side_node, tree, static_strings)
            if not possible_sides or not possible_sides.issubset(valid_position_sides):
                raise StrategyV2ContractError("strategyV2.aiPositionSideInvalid")
        for node in swap_order_calls:
            order_sides.update(
                _possible_text_values(
                    _keyword(node, "position_side"), tree, static_strings
                )
                & valid_position_sides
            )

    required_bidirectional_sides = set(
        CAPABILITY_PACKS["bidirectional"].rules["required_order_sides"]
    )
    open_sides: set[str] = set()
    protected_open_sides: set[str] = set()
    for node in order_calls:
        possible_sides = _possible_text_values(
            _keyword(node, "position_side"), tree, static_strings
        )
        value_node = _order_value_node(node)
        static_values = _numeric_values(value_node, assignment_expressions)
        nonzero_values = {value for value in static_values if abs(value) > 1e-12}
        opening_sides: set[str] = set()
        if "long" in possible_sides and any(value > 0 for value in nonzero_values):
            opening_sides.add("long")
        if "short" in possible_sides and any(value < 0 for value in nonzero_values):
            opening_sides.add("short")
        if direction_mode == "one_way" and not possible_sides:
            if any(value > 0 for value in nonzero_values):
                opening_sides.add("long")
            if any(value < 0 for value in nonzero_values):
                opening_sides.add("short")
            if not static_values and not _is_definitely_zero(
                value_node, assignment_expressions
            ):
                opening_sides.update(valid_position_sides)
        if possible_sides and not static_values and not _is_definitely_zero(
            value_node, assignment_expressions
        ):
            opening_sides.update(possible_sides & valid_position_sides)
        open_sides.update(opening_sides)
        if opening_sides and _has_effective_protection(node):
            protected_open_sides.update(opening_sides)
        target_order = _call_name(node).startswith("order_target")
        if target_order and "long" in possible_sides and any(
            value < 0 for value in nonzero_values
        ):
            raise StrategyV2ContractError(
                "strategyV2.aiDirectionModeOrderMismatch:long:negativeTarget"
            )
        if target_order and "short" in possible_sides and any(
            value > 0 for value in nonzero_values
        ):
            raise StrategyV2ContractError(
                "strategyV2.aiDirectionModeOrderMismatch:short:positiveTarget"
            )

    if direction_mode in {"both", "neutral"} and not required_bidirectional_sides.issubset(
        open_sides
    ):
        missing = ",".join(sorted(required_bidirectional_sides - open_sides))
        raise StrategyV2ContractError(f"strategyV2.aiBidirectionalOrderLegsRequired:{missing}")
    if direction_mode == "long_only" and "short" in order_sides:
        raise StrategyV2ContractError("strategyV2.aiDirectionModeOrderMismatch:long_only:short")
    if direction_mode == "short_only" and "long" in order_sides:
        raise StrategyV2ContractError("strategyV2.aiDirectionModeOrderMismatch:short_only:long")

    if "protection" in intent.capabilities and swap_scope:
        if direction_mode == "long_only":
            required_protected_sides = {"long"}
        elif direction_mode == "short_only":
            required_protected_sides = {"short"}
        elif direction_mode in {"both", "neutral", "one_way"}:
            required_protected_sides = set(required_bidirectional_sides)
        else:
            required_protected_sides = set()
        missing_protection = required_protected_sides - protected_open_sides
        if missing_protection:
            raise StrategyV2ContractError(
                "strategyV2.aiProtectionEntryLegsRequired:"
                + ",".join(sorted(missing_protection))
            )

    for node in order_calls:
        reason = _keyword(node, "reason")
        if reason is None or (isinstance(reason, ast.Constant) and not str(reason.value or "").strip()):
            raise StrategyV2ContractError(f"strategyV2.aiOrderReasonRequired:{_call_name(node)}")

        order_type = _resolved_text(
            _keyword(node, "order_type") or _keyword(node, "type"),
            static_strings,
        )
        execution_algo = _resolved_text(_keyword(node, "execution_algo"), static_strings)
        if order_type and order_type not in {"market", "limit"}:
            raise StrategyV2ContractError(f"strategyV2.orderTypeUnsupported:{order_type}")
        if execution_algo and execution_algo not in {"market", "limit", "maker_then_market"}:
            raise StrategyV2ContractError(
                f"strategyV2.executionAlgoUnsupported:{execution_algo}"
            )
        if order_type == "limit" or execution_algo == "limit":
            limit_price_node = _keyword(node, "limit_price") or _keyword(node, "price")
            limit_price = _literal_number(limit_price_node)
            if limit_price_node is None or (limit_price is not None and limit_price <= 0):
                raise StrategyV2ContractError("strategyV2.limitPriceRequired")
        client_id = _resolved_text(_keyword(node, "client_order_id"), static_strings)
        max_client_id_length = CAPABILITY_PACKS["order_lifecycle"].rules[
            "client_order_id_max_length"
        ]
        if len(client_id) > max_client_id_length:
            raise StrategyV2ContractError("strategyV2.aiClientOrderIdTooLong")

    protection_limits = CAPABILITY_PACKS["protection"].rules["ratio_limits"]
    for node in calls:
        if _call_name(node) not in ORDER_CALLS | {"set_default_protection"}:
            continue
        for name, maximum in protection_limits.items():
            value_node = _protection_items(node).get(name)
            value = _literal_number(value_node)
            if isinstance(value_node, ast.Constant) and value is None:
                raise StrategyV2ContractError(
                    f"strategyV2.aiProtectionArgumentInvalid:{name}"
                )
            if value_node is not None and value is not None and not 0 <= value <= maximum:
                raise StrategyV2ContractError(
                    f"strategyV2.aiProtectionArgumentInvalid:{name}"
                )
        protection_items = _protection_items(node)
        time_node = protection_items.get("time_limit_seconds")
        time_value = _literal_number(time_node)
        if isinstance(time_node, ast.Constant) and time_value is None:
            raise StrategyV2ContractError(
                "strategyV2.aiProtectionArgumentInvalid:time_limit_seconds"
            )
        if time_node is not None and time_value is not None and time_value < 0:
            raise StrategyV2ContractError(
                "strategyV2.aiProtectionArgumentInvalid:time_limit_seconds"
            )
        activation = protection_items.get("trailing_activation_pct")
        activation_value = _literal_number(activation)
        activation_enabled = activation is not None and (
            activation_value is None or activation_value > 0
        )
        if activation_enabled and protection_items.get("trailing_stop_pct") is None:
            raise StrategyV2ContractError("strategyV2.aiProtectionCombinationInvalid")

    if "protection" in intent.capabilities:
        has_protection = any(_has_effective_protection(node) for node in calls)
        if not has_protection:
            raise StrategyV2ContractError("strategyV2.aiProtectionImplementationRequired")

    if "supertrend" in intent.capabilities:
        native_supertrend = any(
            _call_name(node) == "indicator"
            and bool(node.args)
            and _resolved_text(node.args[0], static_strings) == "supertrend"
            and _resolved_text(_keyword(node, "output"), static_strings) == "direction"
            for node in calls
        )
        if not native_supertrend:
            raise StrategyV2ContractError("strategyV2.aiSupertrendNativeRequired")

    used_factor_ids: set[str] = set()
    requested_factor_calls: list[tuple[str, ast.Call]] = []
    for node in calls:
        call_name = _call_name(node)
        factor_node: ast.AST | None = None
        if call_name in {"indicator", "factor"}:
            factor_node = node.args[0] if node.args else _keyword(node, "name")
        elif call_name == "get_factors":
            factor_node = node.args[1] if len(node.args) > 1 else _keyword(node, "names")
        for value in _literal_text_values(factor_node, static_strings):
            factor_id = value.strip().replace("-", "_").replace(" ", "_")
            used_factor_ids.add(factor_id)
            if factor_id in intent.required_factor_ids and call_name in {"indicator", "factor"}:
                requested_factor_calls.append((factor_id, node))
    missing_factor_ids = set(intent.required_factor_ids) - used_factor_ids
    if missing_factor_ids:
        raise StrategyV2ContractError(
            "strategyV2.aiRequestedFactorMissing:"
            + ",".join(sorted(missing_factor_ids))
        )
    for factor_id, node in requested_factor_calls:
        definition = _TECHNICAL_FACTOR_DEFINITIONS[factor_id]
        schema = definition["parameter_schema"]
        for keyword in node.keywords:
            name = str(keyword.arg or "")
            if name in {"name", "symbol", "frequency"}:
                continue
            if name not in schema:
                raise StrategyV2ContractError(
                    f"strategyV2.aiFactorParameterUnsupported:{factor_id}:{name or '**kwargs'}"
                )
            try:
                value = ast.literal_eval(keyword.value)
            except Exception:
                continue
            parameter = schema[name]
            parameter_type = parameter.get("type")
            invalid = (
                (parameter_type == "integer" and (isinstance(value, bool) or not isinstance(value, int)))
                or (parameter_type == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))))
                or (parameter_type == "boolean" and not isinstance(value, bool))
                or (parameter_type == "enum" and value not in parameter.get("options", ()))
                or (isinstance(value, (int, float)) and not isinstance(value, bool) and parameter.get("minimum") is not None and value < parameter["minimum"])
                or (isinstance(value, (int, float)) and not isinstance(value, bool) and parameter.get("maximum") is not None and value > parameter["maximum"])
            )
            if invalid:
                raise StrategyV2ContractError(
                    f"strategyV2.aiFactorParameterInvalid:{factor_id}:{name}"
                )

    persistence_flag = CAPABILITY_PACKS["persistent_state"].rules["module_flag"]
    if "persistent_state" in intent.capabilities and not _assignment_is_true(
        tree, persistence_flag
    ):
        raise StrategyV2ContractError("strategyV2.aiPersistentStateRequired")

    if "order_lifecycle" in intent.capabilities and order_calls:
        if any(_keyword(node, "client_order_id") is None for node in order_calls):
            raise StrategyV2ContractError("strategyV2.aiClientOrderIdRequired")
        client_ids = {
            value
            for node in order_calls
            if (value := _resolved_text(_keyword(node, "client_order_id"), static_strings))
        }
        if direction_mode in {"both", "neutral"} and client_ids:
            raise StrategyV2ContractError("strategyV2.aiClientOrderIdMustVary")
        order_reference_keys = {
            key for node in order_calls if (key := _assigned_key_for_call(tree, node))
        }
        valid_status_check = False
        for node in calls:
            if _call_name(node) not in {"get_order_status", "cancel_order"}:
                continue
            if len(node.args) != 1 or node.keywords:
                raise StrategyV2ContractError("strategyV2.aiOrderStatusCheckRequired")
            reference_node = node.args[0]
            reference_key = _static_string_key(reference_node)
            reference_value = _resolved_text(reference_node, static_strings)
            if reference_key not in order_reference_keys and reference_value not in client_ids:
                raise StrategyV2ContractError("strategyV2.aiOrderStatusCheckRequired")
            if _call_name(node) == "get_order_status":
                if not _status_result_field_is_read(tree, node, static_strings):
                    raise StrategyV2ContractError("strategyV2.aiOrderStatusFieldRequired")
                valid_status_check = True
        if not valid_status_check:
            raise StrategyV2ContractError("strategyV2.aiOrderStatusCheckRequired")
