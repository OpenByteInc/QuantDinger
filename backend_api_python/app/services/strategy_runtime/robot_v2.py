"""Strategy API V2 source generator for visual robot definitions."""

from __future__ import annotations

import ast
import re
from typing import Any

from app.utils.numeric_precision import clean_generated_number


MAX_GRID_CELLS = 200


def _clean_number(value: Any) -> float:
    return clean_generated_number(value, decimal_places=12)


def _clean_numbers(values: list[float]) -> list[float]:
    return [_clean_number(value) for value in values]


def _clean_weights(values: list[float]) -> list[float]:
    """Bound source precision without changing the aggregate allocation."""
    raw = [max(0.0, float(value or 0.0)) for value in values]
    cleaned = _clean_numbers(raw)
    target = _clean_number(sum(raw))
    delta = _clean_number(target - sum(cleaned))
    if delta:
        index = next(
            (position for position in range(len(cleaned) - 1, -1, -1) if raw[position] > 0),
            -1,
        )
        if index >= 0:
            cleaned[index] = _clean_number(cleaned[index] + delta)
    return cleaned


def _equity_risk_constants(config: dict[str, Any]) -> str:
    """Emit the common, strategy-wide equity risk configuration."""
    take_profit = _clean_number(
        max(0.0, float(config.get("equity_take_profit_pct") or 0.0))
    )
    stop_loss = _clean_number(
        max(0.0, float(config.get("equity_stop_loss_pct") or 0.0))
    )
    trailing_enabled = bool(config.get("equity_trailing_enabled"))
    trailing_activation = _clean_number(max(
        0.0,
        float(config.get("equity_trailing_activation_pct") or 0.0),
    ))
    trailing_callback = _clean_number(max(
        0.0,
        float(config.get("equity_trailing_callback_pct") or 0.0),
    ))
    return (
        f"EQUITY_TAKE_PROFIT = {take_profit!r}\n"
        f"EQUITY_STOP_LOSS = {stop_loss!r}\n"
        f"EQUITY_TRAILING_ENABLED = {trailing_enabled!r}\n"
        f"EQUITY_TRAILING_ACTIVATION = {trailing_activation!r}\n"
        f"EQUITY_TRAILING_CALLBACK = {trailing_callback!r}\n"
    )


EQUITY_RISK_HELPERS = '''

def _equity_risk_reason(context):
    starting_equity = float(context.portfolio.starting_cash or 0.0)
    if starting_equity <= 0:
        return ""
    current_equity = float(context.portfolio.total_value or 0.0)
    total_return = (current_equity / starting_equity) - 1.0
    g.equity_peak_return = max(float(g.equity_peak_return or 0.0), total_return)
    if EQUITY_TAKE_PROFIT > 0 and total_return >= EQUITY_TAKE_PROFIT:
        return "equity_take_profit"
    if EQUITY_STOP_LOSS > 0 and total_return <= -EQUITY_STOP_LOSS:
        return "equity_stop_loss"
    if EQUITY_TRAILING_ENABLED:
        if total_return >= EQUITY_TRAILING_ACTIVATION:
            g.equity_trailing_armed = True
        drawdown_from_peak = float(g.equity_peak_return or 0.0) - total_return
        if g.equity_trailing_armed and drawdown_from_peak >= EQUITY_TRAILING_CALLBACK:
            return "equity_trailing_stop"
    return ""
'''


def _grid_points(start: float, end: float, count: int, mode: str) -> list[float]:
    cells = max(1, int(count or 1))
    low, high = sorted((float(start or 0.0), float(end or 0.0)))
    if low <= 0 or high <= low:
        return []
    if str(mode or "").strip().lower() == "geometric":
        ratio = (high / low) ** (1.0 / cells)
        return [_clean_number(low * (ratio**index)) for index in range(cells + 1)]
    step = (high - low) / cells
    return [_clean_number(low + (step * index)) for index in range(cells + 1)]


def _build_grid_v2_source(
    config: dict[str, Any],
    *,
    instrument: str,
    timeframe: str,
) -> str:
    """Build a bar-replay grid with the same per-cell lifecycle as live resting grid."""
    side = str(config.get("side") or "long").strip().lower()
    if side not in {"long", "short", "neutral"}:
        side = "long"
    dynamic_anchor = bool(config.get("dynamic_anchor"))
    start = float(config.get("start_price") or 0.0)
    end = float(config.get("end_price") or 0.0)
    count = max(2, int(config.get("grid_count") or 2))
    if count > MAX_GRID_CELLS:
        raise ValueError("GRID_COUNT_EXCEEDS_SAFE_LIMIT")
    mode = str(config.get("grid_mode") or "arithmetic").strip().lower()
    points = _grid_points(start, end, count, mode)
    reference = (min(start, end) + max(start, end)) / 2.0
    initial_pct = 0.0 if side == "neutral" else min(
        1.0,
        max(0.0, float(config.get("initial_position_pct") or 0.0)),
    )
    max_open_orders = max(1, int(config.get("max_open_orders") or 4))

    roles: list[str] = []
    lower_prices: list[float] = []
    upper_prices: list[float] = []
    for lower, upper in zip(points[:-1], points[1:]):
        midpoint = (lower + upper) / 2.0
        if side == "neutral":
            role = "long_entry" if midpoint < reference else "short_entry"
        elif side == "short":
            role = "short_entry" if midpoint >= reference else "short_seed"
        else:
            role = "long_entry" if midpoint < reference else "long_seed"
        roles.append(role)
        lower_prices.append(lower)
        upper_prices.append(upper)

    entry_indexes = [
        index
        for index, role in enumerate(roles)
        if role in {"long_entry", "short_entry"}
    ]
    seed_indexes = [
        index
        for index, role in enumerate(roles)
        if role in {"long_seed", "short_seed"}
    ]
    # More entry slots than entry cells cannot change trading behavior, but it
    # increases generated state and makes workload estimates misleading.
    max_open_orders = min(max_open_orders, max(1, len(entry_indexes)))
    entry_budget = 1.0 if side == "neutral" else max(0.0, 1.0 - initial_pct)
    budget_pcts = [0.0 for _ in roles]
    for index in entry_indexes:
        budget_pcts[index] = entry_budget / max(1, len(entry_indexes))
    for index in seed_indexes:
        budget_pcts[index] = initial_pct / max(1, len(seed_indexes))

    # Arm orders nearest to the anchor first, matching the live max-open-order policy.
    arm_order = sorted(
        entry_indexes,
        key=lambda index: abs(
            (
                lower_prices[index]
                if roles[index] == "long_entry"
                else upper_prices[index]
            )
            - reference
        ),
    )
    if dynamic_anchor and reference > 0:
        lower_prices = [price / reference for price in lower_prices]
        upper_prices = [price / reference for price in upper_prices]
    lower_prices = _clean_numbers(lower_prices)
    upper_prices = _clean_numbers(upper_prices)
    budget_pcts = _clean_weights(budget_pcts)
    initial_pct = _clean_number(initial_pct)

    leverage_line = "    context.allow_leverage(max_leverage=100)\n" if instrument.endswith("@swap") else ""
    direction_mode = (
        "neutral"
        if side == "neutral"
        else ("short_only" if side == "short" else "long_only")
    )
    constants = (
        f"INSTRUMENT = {instrument!r}\n"
        f"TIMEFRAME = {timeframe!r}\n"
        f"GRID_SIDE = {side!r}\n"
        f"DYNAMIC_ANCHOR = {dynamic_anchor!r}\n"
        f"CELL_LOWER = {lower_prices!r}\n"
        f"CELL_UPPER = {upper_prices!r}\n"
        f"CELL_ROLES = {roles!r}\n"
        f"CELL_BUDGET_PCTS = {budget_pcts!r}\n"
        f"ARM_ORDER = {arm_order!r}\n"
        f"INITIAL_POSITION_PCT = {initial_pct!r}\n"
        f"MAX_OPEN_ENTRY_ORDERS = {max_open_orders!r}\n"
        f"{_equity_risk_constants(config)}"
        "PERSIST_RUNTIME_STATE = True\n"
        "GRID_TEMPLATE_VERSION = 6\n"
    )
    body = f'''

def initialize(context):
    context.set_universe([INSTRUMENT])
    context.subscribe(frequency=TIMEFRAME)
    context.set_metadata(direction_mode={direction_mode!r})
    context.set_warmup(2)
{leverage_line}    g.anchor_price = 0.0
    g.initial_ref = ""
    g.initial_allocated = False
    g.cell_states = ["disabled" if role.endswith("_seed") else "entry_ready" for role in CELL_ROLES]
    g.cell_refs = ["" for _ in CELL_ROLES]
    g.cell_quantities = [0.0 for _ in CELL_ROLES]
    g.cell_cycles = [1 for _ in CELL_ROLES]
    g.halted = False
    g.equity_peak_return = 0.0
    g.equity_trailing_armed = False
    g.equity_exit_pending = False
    g.equity_stop_reason = ""


def _price(raw):
    if not DYNAMIC_ANCHOR:
        return float(raw or 0.0)
    return float(raw or 0.0) * float(g.anchor_price or 0.0)


def _status(ref):
    return get_order_status(ref) if ref else {{"status": "unknown"}}


def _status_name(ref):
    return str(_status(ref).get("status") or "unknown").strip().lower()


def _cancel_working_orders():
    if g.initial_ref and _status_name(g.initial_ref) in ("queued", "deferred", "submitted", "open", "partial"):
        cancel_order(g.initial_ref)
    for ref in g.cell_refs:
        if ref and _status_name(ref) in ("queued", "deferred", "submitted", "open", "partial"):
            cancel_order(ref)


def _close_all(reason):
    _cancel_working_orders()
    if GRID_SIDE in ("long", "neutral"):
        position = get_position(INSTRUMENT, position_side="long")
        if abs(float(position.amount or 0.0)) > 1e-12:
            order_target_value(
                INSTRUMENT,
                0.0,
                position_side="long",
                reason=reason,
                client_order_id="grid-risk-long",
            )
    if GRID_SIDE in ("short", "neutral"):
        position = get_position(INSTRUMENT, position_side="short")
        if abs(float(position.amount or 0.0)) > 1e-12:
            order_target_value(
                INSTRUMENT,
                0.0,
                position_side="short",
                reason=reason,
                client_order_id="grid-risk-short",
            )
    g.halted = True


{EQUITY_RISK_HELPERS}


def _risk_exit(context):
    if g.equity_exit_pending:
        return False
    reason = _equity_risk_reason(context)
    if not reason:
        return False
    full_reason = "grid_" + reason
    g.equity_exit_pending = True
    g.equity_stop_reason = full_reason
    _close_all(full_reason)
    return True


def _equity_risk_exit(context):
    return _risk_exit(context)


def _release_equity_risk_exit():
    g.equity_exit_pending = False
    g.equity_stop_reason = ""
    g.halted = False


def _reconcile_initial():
    if not g.initial_ref or g.initial_allocated:
        return
    status = _status(g.initial_ref)
    name = str(status.get("status") or "unknown").strip().lower()
    if name == "filled":
        filled = abs(float(status.get("filled_quantity") or 0.0))
        seed_indexes = [index for index, role in enumerate(CELL_ROLES) if role.endswith("_seed")]
        total_seed_pct = sum(float(CELL_BUDGET_PCTS[index] or 0.0) for index in seed_indexes)
        for index in seed_indexes:
            share = float(CELL_BUDGET_PCTS[index] or 0.0) / total_seed_pct if total_seed_pct > 0 else 0.0
            g.cell_quantities[index] = filled * share
            g.cell_states[index] = "exit_ready" if g.cell_quantities[index] > 1e-12 else "disabled"
        g.initial_allocated = True
    elif name in ("rejected", "cancelled"):
        g.halted = True


def _reconcile_cells():
    for index in range(len(CELL_ROLES)):
        ref = g.cell_refs[index]
        if not ref:
            continue
        status = _status(ref)
        name = str(status.get("status") or "unknown").strip().lower()
        if name == "filled":
            state = g.cell_states[index]
            if state == "entry_pending":
                g.cell_quantities[index] = abs(float(status.get("filled_quantity") or 0.0))
                g.cell_states[index] = "exit_ready"
            elif state == "exit_pending":
                g.cell_states[index] = "entry_ready"
                g.cell_cycles[index] += 1
            g.cell_refs[index] = ""
        elif name in ("rejected", "cancelled"):
            state = g.cell_states[index]
            g.cell_states[index] = "entry_ready" if state == "entry_pending" else "exit_ready"
            g.cell_refs[index] = ""


def _place_entry(context, index):
    role = CELL_ROLES[index]
    side = "long" if role.startswith("long") else "short"
    entry_price = _price(CELL_LOWER[index] if side == "long" else CELL_UPPER[index])
    cycle = int(g.cell_cycles[index] or 1)
    ref = "grid-%s-%s-entry-%s" % (index, side, cycle)
    quantity = abs(float(g.cell_quantities[index] or 0.0))
    if quantity > 1e-12:
        signed_quantity = quantity if side == "long" else -quantity
        order(
            INSTRUMENT,
            signed_quantity,
            position_side=side,
            order_type="limit",
            limit_price=entry_price,
            reason=side + "_entry",
            client_order_id=ref,
        )
    else:
        quote_value = float(context.portfolio.starting_cash) * float(CELL_BUDGET_PCTS[index] or 0.0)
        signed_value = quote_value if side == "long" else -quote_value
        order_value(
            INSTRUMENT,
            signed_value,
            position_side=side,
            order_type="limit",
            limit_price=entry_price,
            reason=side + "_entry",
            client_order_id=ref,
        )
    g.cell_refs[index] = ref
    g.cell_states[index] = "entry_pending"


def _place_exit(index):
    role = CELL_ROLES[index]
    side = "long" if role.startswith("long") else "short"
    quantity = abs(float(g.cell_quantities[index] or 0.0))
    if quantity <= 1e-12:
        g.cell_states[index] = "entry_ready"
        return
    exit_price = _price(CELL_UPPER[index] if side == "long" else CELL_LOWER[index])
    cycle = int(g.cell_cycles[index] or 1)
    ref = "grid-%s-%s-exit-%s" % (index, side, cycle)
    signed_quantity = -quantity if side == "long" else quantity
    order(
        INSTRUMENT,
        signed_quantity,
        position_side=side,
        order_type="limit",
        limit_price=exit_price,
        reason=side + "_exit",
        client_order_id=ref,
    )
    g.cell_refs[index] = ref
    g.cell_states[index] = "exit_pending"


def _arm_orders(context):
    for index in range(len(CELL_ROLES)):
        if g.cell_states[index] == "exit_ready":
            _place_exit(index)
    active_entries = sum(1 for state in g.cell_states if state == "entry_pending")
    for index in ARM_ORDER:
        if active_entries >= MAX_OPEN_ENTRY_ORDERS:
            break
        if g.cell_states[index] == "entry_ready":
            _place_entry(context, index)
            active_entries += 1


def handle_data(context, data):
    bars = get_history(2, TIMEFRAME, ["high", "low", "close"], INSTRUMENT)
    if len(bars) < 1 or g.halted:
        return
    price = float(bars["close"].iloc[-1])
    if g.anchor_price <= 0:
        g.anchor_price = price
    _reconcile_initial()
    _reconcile_cells()
    if _risk_exit(context):
        return
    if INITIAL_POSITION_PCT > 0 and not g.initial_ref:
        side = "short" if GRID_SIDE == "short" else "long"
        value = float(context.portfolio.starting_cash) * INITIAL_POSITION_PCT
        if side == "short":
            value = -value
        g.initial_ref = "grid-initial-" + side
        order_value(
            INSTRUMENT,
            value,
            position_side=side,
            reason="grid_initial_" + side,
            client_order_id=g.initial_ref,
        )
    _arm_orders(context)
'''
    return constants + body


def _build_neutral_grid_v2_source(
    config: dict[str, Any],
    preview: dict[str, Any],
    *,
    instrument: str,
    timeframe: str,
) -> str:
    levels = list(preview.get("levels") or [])
    dynamic_anchor = bool(config.get("dynamic_anchor"))
    reference_price = (
        float(config.get("start_price") or 0.0)
        + float(config.get("end_price") or 0.0)
    ) / 2.0

    def leg_values(side: str) -> tuple[list[float], list[float]]:
        rows = [item for item in levels if str((item or {}).get("side") or "") == side]
        rows.sort(key=lambda item: float((item or {}).get("price") or 0.0), reverse=side == "long")
        prices = [float((item or {}).get("price") or 0.0) for item in rows]
        if dynamic_anchor and reference_price > 0:
            prices = [price / reference_price for price in prices]
        amounts = [max(0.0, float((item or {}).get("amount_quote") or 0.0)) for item in rows]
        return _clean_numbers(prices), _clean_numbers(amounts)

    long_prices, long_amounts = leg_values("long")
    short_prices, short_amounts = leg_values("short")
    total_amount = sum(long_amounts) + sum(short_amounts)
    divisor = total_amount if total_amount > 0 else 1.0
    long_weights = _clean_weights([amount / divisor for amount in long_amounts])
    short_weights = _clean_weights([amount / divisor for amount in short_amounts])
    take_profit = _clean_number(config.get("take_profit_pct") or 0.0)
    hard_stop = _clean_number(config.get("hard_stop_pct") or 0.0)
    constants = (
        f"INSTRUMENT = {instrument!r}\n"
        f"TIMEFRAME = {timeframe!r}\n"
        f"DYNAMIC_ANCHOR = {dynamic_anchor!r}\n"
        f"LONG_PRICE_LEVELS = {long_prices!r}\n"
        f"SHORT_PRICE_LEVELS = {short_prices!r}\n"
        f"LONG_AMOUNT_WEIGHTS = {long_weights!r}\n"
        f"SHORT_AMOUNT_WEIGHTS = {short_weights!r}\n"
        f"TAKE_PROFIT = {take_profit!r}\n"
        f"HARD_STOP = {hard_stop!r}\n"
    )
    body = '''

def initialize(context):
    context.set_universe([INSTRUMENT])
    context.subscribe(frequency=TIMEFRAME)
    context.set_metadata(direction_mode="neutral")
    context.set_warmup(2)
    context.allow_leverage(max_leverage=100)
    g.long_anchor_price = 0.0
    g.short_anchor_price = 0.0
    g.long_next_level = 0
    g.short_next_level = 0


def _leg_position(side):
    position = get_position(INSTRUMENT, position_side=side)
    return float(position.amount or 0.0), float(position.avg_cost or 0.0)


def _level_price(side, levels, index, current_price):
    if not DYNAMIC_ANCHOR:
        return float(levels[index] or 0.0)
    if side == "long":
        if g.long_anchor_price <= 0:
            g.long_anchor_price = float(current_price)
        anchor = g.long_anchor_price
    else:
        if g.short_anchor_price <= 0:
            g.short_anchor_price = float(current_price)
        anchor = g.short_anchor_price
    return float(levels[index] or 0.0) * anchor


def _reset_leg(side):
    if side == "long":
        g.long_next_level = 0
        g.long_anchor_price = 0.0
    else:
        g.short_next_level = 0
        g.short_anchor_price = 0.0


def _risk_exit_leg(side, price):
    amount, average = _leg_position(side)
    if abs(amount) <= 1e-12 or average <= 0:
        return False
    direction = 1.0 if side == "long" else -1.0
    profit = ((price - average) / average) * direction
    if TAKE_PROFIT > 0 and profit >= TAKE_PROFIT:
        order_target_value(INSTRUMENT, 0.0, position_side=side, reason="neutral_grid_take_profit")
        _reset_leg(side)
        return True
    if HARD_STOP > 0 and -profit >= HARD_STOP:
        order_target_value(INSTRUMENT, 0.0, position_side=side, reason="neutral_grid_hard_stop")
        _reset_leg(side)
        return True
    return False


def handle_data(context, data):
    bars = get_history(2, TIMEFRAME, ["high", "low", "close"], INSTRUMENT)
    if len(bars) < 1:
        return
    current = bars.iloc[-1]
    price = float(current["close"])
    long_exited = _risk_exit_leg("long", price)
    short_exited = _risk_exit_leg("short", price)

    while not long_exited and g.long_next_level < len(LONG_PRICE_LEVELS):
        index = g.long_next_level
        target = _level_price("long", LONG_PRICE_LEVELS, index, price)
        if float(current["low"]) > target:
            break
        weight = float(LONG_AMOUNT_WEIGHTS[index] or 0.0)
        g.long_next_level += 1
        if weight > 0:
            order_value(
                INSTRUMENT,
                float(context.portfolio.starting_cash) * weight,
                position_side="long",
                order_type="limit",
                limit_price=target,
                reason="neutral_grid_long_level",
            )

    while not short_exited and g.short_next_level < len(SHORT_PRICE_LEVELS):
        index = g.short_next_level
        target = _level_price("short", SHORT_PRICE_LEVELS, index, price)
        if float(current["high"]) < target:
            break
        weight = float(SHORT_AMOUNT_WEIGHTS[index] or 0.0)
        g.short_next_level += 1
        if weight > 0:
            order_value(
                INSTRUMENT,
                -float(context.portfolio.starting_cash) * weight,
                position_side="short",
                order_type="limit",
                limit_price=target,
                reason="neutral_grid_short_level",
            )
'''
    return constants + body


def migrate_legacy_robot_v2_source(code: str, kind: str) -> str:
    """Convert legacy absolute robot allocations to run-capital weights."""
    source = str(code or "")
    if not source or "AMOUNT_WEIGHTS =" in source:
        return source
    amount_match = re.search(r"(?m)^AMOUNTS = (.+)$", source)
    if not amount_match:
        return source
    try:
        amounts = [max(0.0, float(value)) for value in ast.literal_eval(amount_match.group(1))]
    except (TypeError, ValueError, SyntaxError):
        return source
    total = sum(amounts)
    weights = _clean_weights(
        [amount / total for amount in amounts]
        if total > 0
        else [0.0 for _ in amounts]
    )
    initial_match = re.search(r"(?m)^INITIAL_POSITION_PCT = (.+)$", source)
    try:
        initial_pct = _clean_number(
            float(ast.literal_eval(initial_match.group(1))) if initial_match else 0.0
        )
    except (TypeError, ValueError, SyntaxError):
        initial_pct = 0.0
    level_fraction = _clean_number(
        max(0.0, 1.0 - initial_pct) if str(kind or "") == "grid" else 1.0
    )
    source = source.replace(amount_match.group(0), f"AMOUNT_WEIGHTS = {weights!r}", 1)
    if initial_match:
        source = source.replace(
            initial_match.group(0),
            f"INITIAL_POSITION_PCT = {initial_pct!r}\nLEVEL_CAPITAL_FRACTION = {level_fraction!r}",
            1,
        )
    source = source.replace(
        "initial_value = sum(AMOUNTS) * INITIAL_POSITION_PCT",
        "initial_value = float(context.portfolio.starting_cash) * INITIAL_POSITION_PCT",
    )
    source = source.replace(
        "g.target_value += float(AMOUNTS[g.next_level] or 0.0)",
        "g.target_value += float(context.portfolio.starting_cash) * LEVEL_CAPITAL_FRACTION * float(AMOUNT_WEIGHTS[g.next_level] or 0.0)",
    )
    return source


def _build_dca_v2_source(
    config: dict[str, Any],
    *,
    instrument: str,
    timeframe: str,
) -> str:
    interval_minutes = max(
        1,
        int(config.get("dca_interval_minutes") or 1),
    )
    max_orders = max(1, int(config.get("dca_max_orders") or 1))
    total_budget_pct = _clean_number(min(
        1.0,
        max(0.0, float(config.get("dca_total_budget_pct") or 0.0)),
    ))
    order_pct = _clean_number(min(
        total_budget_pct,
        max(0.0, float(config.get("dca_order_pct") or 0.0)),
    ))
    dynamic_anchor = bool(config.get("dynamic_anchor"))
    reference_price = _clean_number(config.get("entry_price") or 0.0)
    price_filter_enabled = bool(config.get("dca_price_filter_enabled"))
    max_adverse_price_pct = _clean_number(max(
        0.0,
        float(config.get("dca_max_adverse_price_pct") or 0.0),
    ))
    trailing_enabled = bool(config.get("trailing_take_profit_enabled"))
    trailing_activation = _clean_number(config.get("trailing_activation_pct") or 0.0)
    trailing_callback = _clean_number(config.get("trailing_callback_pct") or 0.0)
    take_profit = _clean_number(
        0.0 if trailing_enabled else float(config.get("take_profit_pct") or 0.0)
    )
    hard_stop = _clean_number(config.get("hard_stop_pct") or 0.0)
    constants = (
        f"INSTRUMENT = {instrument!r}\n"
        f"TIMEFRAME = {timeframe!r}\n"
        f"DCA_INTERVAL_MINUTES = {interval_minutes!r}\n"
        f"DCA_MAX_ORDERS = {max_orders!r}\n"
        f"DCA_TOTAL_BUDGET_PCT = {total_budget_pct!r}\n"
        f"DCA_ORDER_PCT = {order_pct!r}\n"
        f"DYNAMIC_ANCHOR = {dynamic_anchor!r}\n"
        f"DCA_REFERENCE_PRICE = {reference_price!r}\n"
        f"DCA_PRICE_FILTER_ENABLED = {price_filter_enabled!r}\n"
        f"DCA_MAX_ADVERSE_PRICE_PCT = {max_adverse_price_pct!r}\n"
        f"TAKE_PROFIT = {take_profit!r}\n"
        f"HARD_STOP = {hard_stop!r}\n"
        f"TRAILING_TAKE_PROFIT_ENABLED = {trailing_enabled!r}\n"
        f"TRAILING_ACTIVATION = {trailing_activation!r}\n"
        f"TRAILING_CALLBACK = {trailing_callback!r}\n"
        f"{_equity_risk_constants(config)}"
        "PERSIST_RUNTIME_STATE = True\n"
        "DCA_TEMPLATE_VERSION = 6\n"
    )
    body = f'''

def initialize(context):
    context.set_universe([INSTRUMENT])
    context.subscribe(frequency=TIMEFRAME)
    context.set_metadata(direction_mode="long_only", market_type="spot")
    context.set_warmup(2)
    g.dca_order_count = 0
    g.dca_last_schedule_at = None
    g.dca_spent_value = 0.0
    g.dca_cycle_capital = 0.0
    g.dca_anchor_price = 0.0
    g.dca_pending_ref = ""
    g.dca_pending_value = 0.0
    g.dca_attempt = 0
    g.dca_cycle_no = 1
    g.dca_exit_pending = False
    g.equity_peak_return = 0.0
    g.equity_trailing_armed = False
    g.equity_halted = False
    g.equity_exit_pending = False
    g.equity_stop_reason = ""


def _reset():
    g.dca_order_count = 0
    g.dca_last_schedule_at = None
    g.dca_spent_value = 0.0
    g.dca_cycle_capital = 0.0
    g.dca_anchor_price = 0.0
    g.dca_pending_ref = ""
    g.dca_pending_value = 0.0
    g.dca_attempt = 0
    g.dca_exit_pending = False
    g.dca_cycle_no += 1


def _position_state():
    position = get_position(INSTRUMENT)
    amount = float(position.amount or 0.0)
    average = float(position.avg_cost or 0.0)
    return amount, average


def _status_name(value):
    return str((value or {{}}).get("status") or "unknown").strip().lower()


def _reconcile_purchase():
    reference = str(g.dca_pending_ref or "")
    if not reference:
        return
    status = get_order_status(reference)
    name = _status_name(status)
    if name == "filled":
        settled_value = (
            float(status.get("filled_notional") or 0.0)
            + float(status.get("fee") or 0.0)
        )
        if settled_value <= 0:
            settled_value = float(g.dca_pending_value or 0.0)
        g.dca_spent_value += settled_value
        g.dca_order_count += 1
        g.dca_pending_ref = ""
        g.dca_pending_value = 0.0
        g.dca_attempt = 0
    elif name in ("rejected", "failed", "cancelled", "canceled", "expired"):
        g.dca_pending_ref = ""
        g.dca_pending_value = 0.0
        g.dca_attempt += 1


{EQUITY_RISK_HELPERS}


def _equity_risk_exit(context):
    if g.equity_exit_pending:
        return False
    reason = _equity_risk_reason(context)
    if not reason:
        return False
    full_reason = "dca_" + reason
    order_target_value(INSTRUMENT, 0.0, reason=full_reason)
    g.equity_halted = True
    g.equity_exit_pending = True
    g.dca_exit_pending = True
    g.equity_stop_reason = full_reason
    return True


def _release_equity_risk_exit():
    g.equity_halted = False
    g.equity_exit_pending = False
    g.dca_exit_pending = False
    g.equity_stop_reason = ""


def _risk_exit(price):
    amount, average = _position_state()
    if amount == 0 or average <= 0:
        return False
    profit = (price - average) / average
    if TAKE_PROFIT > 0 and profit >= TAKE_PROFIT:
        order_target_value(INSTRUMENT, 0.0, reason="dca_take_profit")
        g.dca_exit_pending = True
        return True
    if HARD_STOP > 0 and -profit >= HARD_STOP:
        order_target_value(INSTRUMENT, 0.0, reason="dca_hard_stop")
        g.dca_exit_pending = True
        return True
    return False


def _price_filter_allows(price):
    if g.dca_anchor_price <= 0:
        if DYNAMIC_ANCHOR or DCA_REFERENCE_PRICE <= 0:
            g.dca_anchor_price = float(price)
        else:
            g.dca_anchor_price = float(DCA_REFERENCE_PRICE)
    if not DCA_PRICE_FILTER_ENABLED:
        return True
    return price <= g.dca_anchor_price * (1.0 + DCA_MAX_ADVERSE_PRICE_PCT)


def handle_data(context, data):
    bars = get_history(2, TIMEFRAME, "close", INSTRUMENT)
    if len(bars) < 1:
        return
    price = float(bars["close"].iloc[-1])
    _reconcile_purchase()
    amount, _ = _position_state()
    if amount == 0 and not g.dca_pending_ref:
        exit_reason = ""
        if g.dca_exit_pending or g.dca_order_count > 0:
            exit_reason = consume_last_exit_reason(INSTRUMENT) or ""
        if g.dca_exit_pending or exit_reason:
            _reset()
            return
    if g.equity_halted or g.dca_pending_ref or g.dca_exit_pending:
        return
    if _equity_risk_exit(context):
        return
    if _risk_exit(price):
        return
    if g.dca_order_count >= DCA_MAX_ORDERS:
        return
    now = context.current_dt
    if now is None:
        return
    if g.dca_last_schedule_at is not None:
        elapsed_minutes = (now - g.dca_last_schedule_at).total_seconds() / 60.0
        if elapsed_minutes < DCA_INTERVAL_MINUTES:
            return
    g.dca_last_schedule_at = now
    if not _price_filter_allows(price):
        return
    if g.dca_cycle_capital <= 0:
        g.dca_cycle_capital = max(0.0, float(context.portfolio.starting_cash))
    budget_value = g.dca_cycle_capital * DCA_TOTAL_BUDGET_PCT
    purchase_value = g.dca_cycle_capital * DCA_ORDER_PCT
    remaining_value = max(0.0, budget_value - g.dca_spent_value)
    purchase_value = min(purchase_value, remaining_value)
    if purchase_value <= 0:
        return
    reference = "dca:%s:order:%s:attempt:%s" % (
        g.dca_cycle_no,
        g.dca_order_count + 1,
        g.dca_attempt,
    )
    g.dca_pending_value = purchase_value
    g.dca_pending_ref = order_value(
        INSTRUMENT,
        purchase_value,
        reason="dca_scheduled_order",
        client_order_id=reference,
        stop_loss_pct=HARD_STOP,
        take_profit_pct=TAKE_PROFIT,
        trailing_stop_pct=TRAILING_CALLBACK if TRAILING_TAKE_PROFIT_ENABLED else 0.0,
        trailing_activation_pct=TRAILING_ACTIVATION if TRAILING_TAKE_PROFIT_ENABLED else 0.0,
    )
'''
    return constants + body


def build_robot_v2_source(
    kind: str,
    config: dict[str, Any],
    preview: dict[str, Any],
    *,
    symbol: str,
    market_type: str,
    timeframe: str,
) -> str:
    if kind == "dca":
        market_type = "spot"
    instrument = f"Crypto:{str(symbol or 'BTC/USDT').strip()}@{market_type}"
    side = str(config.get("side") or "long").strip().lower()
    if kind == "dca":
        return _build_dca_v2_source(
            config,
            instrument=instrument,
            timeframe=timeframe,
        )
    if kind == "grid":
        return _build_grid_v2_source(
            config,
            instrument=instrument,
            timeframe=timeframe,
        )
    levels = list(preview.get("levels") or [])
    prices = [float((item or {}).get("price") or 0.0) for item in levels]
    amounts = [float((item or {}).get("amount_quote") or 0.0) for item in levels]
    dynamic_anchor = bool(config.get("dynamic_anchor"))
    if kind == "grid":
        reference_price = (
            float(config.get("start_price") or 0.0)
            + float(config.get("end_price") or 0.0)
        ) / 2.0
    else:
        reference_price = float(config.get("entry_price") or 0.0)
    price_levels = (
        [price / reference_price for price in prices]
        if dynamic_anchor and reference_price > 0
        else prices
    )
    price_levels = _clean_numbers(price_levels)
    direction = -1.0 if side == "short" else 1.0
    if kind == "grid" and dynamic_anchor:
        actionable = [
            (price, amount)
            for price, amount in zip(price_levels, amounts)
            if (direction > 0 and price < 1.0) or (direction < 0 and price > 1.0)
        ]
        if actionable:
            price_levels = [item[0] for item in actionable]
            amounts = [item[1] for item in actionable]
    total_amount = sum(max(0.0, amount) for amount in amounts)
    amount_weights = _clean_weights(
        [max(0.0, amount) / total_amount for amount in amounts]
        if total_amount > 0
        else [0.0 for _ in amounts]
    )
    trailing_enabled = kind in {"dca", "martingale", "layered_martingale"} and bool(
        config.get("trailing_take_profit_enabled")
    )
    trailing_activation = _clean_number(config.get("trailing_activation_pct") or 0.0)
    trailing_callback = _clean_number(config.get("trailing_callback_pct") or 0.0)
    take_profit = _clean_number(
        0.0 if trailing_enabled else float(config.get("take_profit_pct") or 0.0)
    )
    hard_stop = _clean_number(config.get("hard_stop_pct") or 0.0)
    initial_position_pct = _clean_number(config.get("initial_position_pct") or 0.0)
    level_capital_fraction = _clean_number(
        max(0.0, 1.0 - initial_position_pct) if kind == "grid" else 1.0
    )
    restart_after_stop = bool(config.get("restart_after_stop"))
    leverage_line = "    context.allow_leverage(max_leverage=100)\n" if market_type == "swap" else ""
    constants = (
        f"INSTRUMENT = {instrument!r}\n"
        f"TIMEFRAME = {timeframe!r}\n"
        f"PRICE_LEVELS = {price_levels!r}\n"
        f"DYNAMIC_ANCHOR = {dynamic_anchor!r}\n"
        f"AMOUNT_WEIGHTS = {amount_weights!r}\n"
        f"DIRECTION = {direction!r}\n"
        f"POSITION_SIDE = {'short' if direction < 0 else 'long'!r}\n"
        f"TAKE_PROFIT = {take_profit!r}\n"
        f"HARD_STOP = {hard_stop!r}\n"
        f"TRAILING_TAKE_PROFIT_ENABLED = {trailing_enabled!r}\n"
        f"TRAILING_ACTIVATION = {trailing_activation!r}\n"
        f"TRAILING_CALLBACK = {trailing_callback!r}\n"
        f"{_equity_risk_constants(config)}"
        f"INITIAL_POSITION_PCT = {initial_position_pct!r}\n"
        f"LEVEL_CAPITAL_FRACTION = {level_capital_fraction!r}\n"
        f"RESTART_AFTER_STOP = {restart_after_stop!r}\n"
        "ENTRY_TRIGGER_MODE = 'realtime_price'\n"
        "PERSIST_RUNTIME_STATE = True\n"
        "ROBOT_TEMPLATE_VERSION = 6\n"
        "FINAL_SWEEP_MIN_QUOTE = 1.0\n"
    )
    initialize = (
        "\ndef initialize(context):\n"
        "    context.set_universe([INSTRUMENT])\n"
        "    context.subscribe(frequency=TIMEFRAME)\n"
        f"    context.set_metadata(direction_mode={'short_only' if direction < 0 else 'long_only'!r})\n"
        "    context.set_warmup(2)\n"
        f"{leverage_line}"
        "    g.next_level = 0\n"
        "    g.target_value = 0.0\n"
        "    g.anchor_price = 0.0\n"
        "    g.initialized = False\n"
        "    g.level_statuses = ['ready' for _ in PRICE_LEVELS]\n"
        "    g.level_refs = ['' for _ in PRICE_LEVELS]\n"
        "    g.level_spend = [0.0 for _ in PRICE_LEVELS]\n"
        "    g.level_planned = [0.0 for _ in PRICE_LEVELS]\n"
        "    g.level_attempts = [0 for _ in PRICE_LEVELS]\n"
        "    g.cycle_no = 1\n"
        "    g.halted_after_stop = False\n"
        "    g.recovery_required = False\n"
        "    g.exit_pending_reason = ''\n"
        "    g.cooldown_bar = ''\n"
        "    g.final_sweep_ref = ''\n"
        "    g.final_sweep_done = False\n"
        "    g.equity_peak_return = 0.0\n"
        "    g.equity_trailing_armed = False\n"
        "    g.equity_halted = False\n"
        "    g.equity_exit_pending = False\n"
        "    g.equity_stop_reason = ''\n"
        "    g.price_tick_seen = False\n"
    )
    helpers = f'''

def _reset():
    g.next_level = 0
    g.target_value = 0.0
    g.anchor_price = 0.0
    g.initialized = False


def _level_price(index, current_price):
    if not DYNAMIC_ANCHOR:
        return float(PRICE_LEVELS[index] or 0.0)
    if g.anchor_price <= 0:
        g.anchor_price = float(current_price)
    return float(PRICE_LEVELS[index] or 0.0) * g.anchor_price


def _position_state():
    position = get_position(INSTRUMENT, position_side=POSITION_SIDE)
    amount = float(position.amount or 0.0)
    average = float(position.avg_cost or 0.0)
    return amount, average


{EQUITY_RISK_HELPERS}


def _equity_risk_exit(context):
    if g.exit_pending_reason or g.equity_exit_pending:
        return False
    reason = _equity_risk_reason(context)
    if not reason:
        return False
    full_reason = "robot_" + reason
    _request_exit(full_reason)
    g.equity_halted = True
    g.equity_exit_pending = True
    g.equity_stop_reason = full_reason
    return True


def _release_equity_risk_exit():
    if g.equity_exit_pending:
        g.exit_pending_reason = ""
    g.equity_halted = False
    g.equity_exit_pending = False
    g.equity_stop_reason = ""


def _risk_exit(price):
    amount, average = _position_state()
    if amount == 0 or average <= 0:
        return False
    profit = ((price - average) / average) * DIRECTION
    loss = -profit
    if TAKE_PROFIT > 0 and profit >= TAKE_PROFIT:
        order_target_value(INSTRUMENT, 0.0, position_side=POSITION_SIDE, reason="robot_take_profit")
        _reset()
        return True
    if HARD_STOP > 0 and loss >= HARD_STOP:
        order_target_value(INSTRUMENT, 0.0, position_side=POSITION_SIDE, reason="robot_hard_stop")
        _reset()
        return True
    return False
'''
    if kind == "grid":
        handler = '''

def handle_data(context, data):
    bars = get_history(2, TIMEFRAME, ["high", "low", "close"], INSTRUMENT)
    if len(bars) < 1:
        return
    current = bars.iloc[-1]
    price = float(current["close"])
    if _risk_exit(price):
        return
    if not g.initialized:
        g.initialized = True
        amount, average = _position_state()
        initial_value = float(context.portfolio.starting_cash) * INITIAL_POSITION_PCT
        if amount != 0:
            g.target_value = abs(amount) * price
            g.anchor_price = average if average > 0 else price
            restored_value = max(0.0, g.target_value - initial_value)
            while g.next_level < len(AMOUNT_WEIGHTS):
                level_value = float(context.portfolio.starting_cash) * LEVEL_CAPITAL_FRACTION * float(AMOUNT_WEIGHTS[g.next_level] or 0.0)
                if restored_value + 1e-8 < level_value:
                    break
                restored_value -= level_value
                g.next_level += 1
        elif initial_value > 0:
            g.target_value = initial_value
            order_target_value(INSTRUMENT, DIRECTION * g.target_value, reason="grid_initial")
    changed = False
    while g.next_level < len(PRICE_LEVELS):
        target = _level_price(g.next_level, price)
        crossed = float(current["low"]) <= target <= float(current["high"])
        if not crossed:
            break
        g.target_value += float(context.portfolio.starting_cash) * LEVEL_CAPITAL_FRACTION * float(AMOUNT_WEIGHTS[g.next_level] or 0.0)
        g.next_level += 1
        changed = True
    if changed:
        order_target_value(INSTRUMENT, DIRECTION * g.target_value, reason="grid_level")
'''
    else:
        handler = '''

def _new_cycle():
    g.next_level = 0
    g.target_value = 0.0
    g.anchor_price = 0.0
    g.level_statuses = ["ready" for _ in PRICE_LEVELS]
    g.level_refs = ["" for _ in PRICE_LEVELS]
    g.level_spend = [0.0 for _ in PRICE_LEVELS]
    g.level_planned = [0.0 for _ in PRICE_LEVELS]
    g.level_attempts = [0 for _ in PRICE_LEVELS]
    g.exit_pending_reason = ""
    g.final_sweep_ref = ""
    g.final_sweep_done = False
    g.cycle_no += 1


def _status_name(value):
    return str((value or {}).get("status") or "unknown").strip().lower()


def _reconcile_orders():
    processed = []
    for index in range(len(PRICE_LEVELS)):
        reference = str(g.level_refs[index] or "")
        if not reference or reference in processed:
            continue
        processed.append(reference)
        indexes = [
            item
            for item in range(len(PRICE_LEVELS))
            if str(g.level_refs[item] or "") == reference
        ]
        status = get_order_status(reference)
        name = _status_name(status)
        filled_spend = (
            float(status.get("filled_notional") or 0.0)
            + float(status.get("fee") or 0.0)
        )
        planned_total = sum(
            float(g.level_planned[item] or 0.0)
            for item in indexes
        )
        if filled_spend > 0 and planned_total > 0:
            for item in indexes:
                share = float(g.level_planned[item] or 0.0) / planned_total
                g.level_spend[item] = max(
                    float(g.level_spend[item] or 0.0),
                    filled_spend * share,
                )
        if name == "filled":
            for item in indexes:
                g.level_statuses[item] = "filled"
        elif name in ("rejected", "failed", "cancelled", "canceled", "expired"):
            # A rejected batch leaves every included level eligible. No level
            # in the batch is allowed to advance independently.
            for item in indexes:
                g.level_statuses[item] = "ready"
                g.level_refs[item] = ""
                g.level_spend[item] = 0.0
                g.level_planned[item] = 0.0
                g.level_attempts[item] += 1
        else:
            for item in indexes:
                g.level_statuses[item] = "pending"
    g.next_level = 0
    while (
        g.next_level < len(PRICE_LEVELS)
        and g.level_statuses[g.next_level] == "filled"
    ):
        g.next_level += 1


def _fee_rate(context):
    return max(
        0.0,
        float(
            context.params.get(
                "commission",
                context.params.get("fee_rate", 0.001),
            )
            or 0.0
        ),
    )


def _planned_quote(context, index):
    budget = float(context.portfolio.starting_cash)
    if index < len(PRICE_LEVELS) - 1:
        return budget * float(AMOUNT_WEIGHTS[index] or 0.0)
    spent = sum(float(value or 0.0) for value in g.level_spend)
    fee_rate = _fee_rate(context)
    reserved = 0.0
    for pending_index in range(len(PRICE_LEVELS) - 1):
        if g.level_statuses[pending_index] == "pending":
            planned = float(g.level_planned[pending_index] or 0.0)
            unfilled = max(0.0, planned - float(g.level_spend[pending_index] or 0.0))
            reserved += unfilled * (1.0 + fee_rate)
    # The final level absorbs all unallocated cycle capital. Its fee is part
    # of the same fixed run budget rather than an amount above that budget.
    return max(0.0, budget - spent - reserved) / (1.0 + fee_rate)


def _submit_levels(context, indexes):
    batch = []
    quote_total = 0.0
    for index in indexes:
        quote = _planned_quote(context, index)
        if quote <= 0:
            continue
        g.level_statuses[index] = "pending"
        g.level_planned[index] = quote
        batch.append(index)
        quote_total += quote
    if not batch or quote_total <= 0:
        return
    attempt = max(int(g.level_attempts[index] or 0) for index in batch)
    reference = "martingale:%s:levels:%s-%s:attempt:%s" % (
        g.cycle_no,
        batch[0],
        batch[-1],
        attempt,
    )
    submitted_reference = order_value(
        INSTRUMENT,
        DIRECTION * quote_total,
        position_side=POSITION_SIDE,
        reason="robot_level",
        client_order_id=reference,
        stop_loss_pct=HARD_STOP,
        take_profit_pct=TAKE_PROFIT,
        trailing_stop_pct=TRAILING_CALLBACK if TRAILING_TAKE_PROFIT_ENABLED else 0.0,
        trailing_activation_pct=TRAILING_ACTIVATION if TRAILING_TAKE_PROFIT_ENABLED else 0.0,
        trailing_rebase_on_scale_in=False,
    )
    for index in batch:
        g.level_refs[index] = submitted_reference


def _submit_final_sweep(context):
    if g.final_sweep_done or g.final_sweep_ref:
        return
    if not PRICE_LEVELS or any(value != "filled" for value in g.level_statuses):
        return
    spent = sum(float(value or 0.0) for value in g.level_spend)
    remaining = max(0.0, float(context.portfolio.starting_cash) - spent)
    if remaining < FINAL_SWEEP_MIN_QUOTE:
        g.final_sweep_done = True
        return
    quote = remaining / (1.0 + _fee_rate(context))
    reference = "martingale:%s:final_sweep" % g.cycle_no
    g.final_sweep_ref = order_value(
        INSTRUMENT,
        DIRECTION * quote,
        position_side=POSITION_SIDE,
        reason="robot_final_sweep",
        client_order_id=reference,
        stop_loss_pct=HARD_STOP,
        take_profit_pct=TAKE_PROFIT,
        trailing_stop_pct=TRAILING_CALLBACK if TRAILING_TAKE_PROFIT_ENABLED else 0.0,
        trailing_activation_pct=TRAILING_ACTIVATION if TRAILING_TAKE_PROFIT_ENABLED else 0.0,
        trailing_rebase_on_scale_in=False,
    )


def _reconcile_final_sweep():
    if not g.final_sweep_ref:
        return
    status = get_order_status(g.final_sweep_ref)
    name = _status_name(status)
    if name == "filled":
        g.final_sweep_done = True
    elif name in ("rejected", "failed", "cancelled", "canceled", "expired"):
        # Do not retry an untradeable dust remainder forever.
        g.final_sweep_done = True
    if g.final_sweep_done:
        g.final_sweep_ref = ""


def _request_exit(reason):
    order_target_value(INSTRUMENT, 0.0, position_side=POSITION_SIDE, reason=reason)
    g.exit_pending_reason = reason


def _risk_exit(price):
    amount, average = _position_state()
    if abs(amount) <= 1e-12 or average <= 0 or g.exit_pending_reason:
        return False
    profit = ((price - average) / average) * DIRECTION
    loss = -profit
    if TAKE_PROFIT > 0 and profit >= TAKE_PROFIT:
        _request_exit("robot_take_profit")
        return True
    if HARD_STOP > 0 and loss >= HARD_STOP:
        _request_exit("robot_hard_stop")
        return True
    return False


def _handle_flat_transition(context):
    amount, _ = _position_state()
    had_cycle = any(
        value in ("pending", "filled")
        for value in g.level_statuses
    )
    if abs(amount) > 1e-12 or not had_cycle:
        return False
    reason = consume_last_exit_reason(INSTRUMENT) or g.exit_pending_reason
    # Fill/order status can become visible before the separately synchronized
    # position snapshot.  Without a confirmed exit reason that short window is
    # not a completed cycle and must never start a duplicate initial order.
    if not reason:
        return False
    stopped = reason in ("stop_loss", "robot_hard_stop")
    current_bar = str(context.current_dt or "")
    _new_cycle()
    g.cooldown_bar = current_bar
    if stopped and not RESTART_AFTER_STOP:
        g.halted_after_stop = True
    return True


def _prepare_cycle(context, price):
    amount, average = _position_state()
    if not g.initialized:
        g.initialized = True
        if abs(amount) > 1e-12 and not any(
            value in ("pending", "filled")
            for value in g.level_statuses
        ):
            g.recovery_required = True
            log.warning("martingale_recovery_required: live position exists without cycle state")
            return False
    if g.recovery_required or g.halted_after_stop:
        return False
    _reconcile_orders()
    _reconcile_final_sweep()
    if abs(amount) > 1e-12 and average > 0 and g.anchor_price <= 0:
        g.anchor_price = average
    if _handle_flat_transition(context):
        return False
    if g.equity_halted:
        return False
    if g.cooldown_bar:
        if str(context.current_dt or "") == g.cooldown_bar:
            return False
        g.cooldown_bar = ""
    return True


def _due_levels_at_price(price):
    due_indexes = []
    for index in range(len(PRICE_LEVELS)):
        if g.level_statuses[index] != "ready":
            continue
        target = _level_price(index, price)
        due = price >= target if DIRECTION < 0 else price <= target
        if index == 0:
            due = True
        if not due:
            break
        due_indexes.append(index)
    return due_indexes


def on_price_tick(context, prices):
    price = float((prices or {{}}).get(INSTRUMENT) or 0.0)
    if price <= 0 or not PRICE_LEVELS:
        return
    g.price_tick_seen = True
    if not _prepare_cycle(context, price):
        return
    # Position and portfolio exits are evaluated by the supervisor before this
    # entry hook.  This callback only scales the active martingale cycle.
    due_indexes = _due_levels_at_price(price)
    _submit_levels(context, due_indexes)
    _submit_final_sweep(context)


def handle_data(context, data):
    bars = get_history(2, TIMEFRAME, ["high", "low", "close"], INSTRUMENT)
    if len(bars) < 1 or not PRICE_LEVELS:
        return
    current = bars.iloc[-1]
    price = float(current["close"])
    current_low = float(current["low"])
    current_high = float(current["high"])
    if not _prepare_cycle(context, price):
        return
    # Live deployments receive price ticks independently of bar events.  Once
    # that hook is active, the closed-bar handler must never emit duplicates.
    if g.price_tick_seen:
        return
    if _equity_risk_exit(context):
        return
    if _risk_exit(price):
        return
    due_indexes = []
    for index in range(len(PRICE_LEVELS)):
        if g.level_statuses[index] != "ready":
            continue
        target = _level_price(index, price)
        due = (
            current_high >= target
            if DIRECTION < 0
            else current_low <= target
        )
        if index == 0:
            due = True
        if not due:
            # Levels are ordered away from the anchor, so deeper levels cannot
            # be due once the first untouched level has not been crossed.
            break
        due_indexes.append(index)
    _submit_levels(context, due_indexes)
    _submit_final_sweep(context)
'''
    return constants + initialize + helpers + handler
