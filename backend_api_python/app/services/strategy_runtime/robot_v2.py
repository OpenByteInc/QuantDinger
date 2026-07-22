"""Strategy API V2 source generator for visual robot definitions."""

from __future__ import annotations

import ast
import re
from typing import Any


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
        return prices, amounts

    long_prices, long_amounts = leg_values("long")
    short_prices, short_amounts = leg_values("short")
    total_amount = sum(long_amounts) + sum(short_amounts)
    divisor = total_amount if total_amount > 0 else 1.0
    long_weights = [amount / divisor for amount in long_amounts]
    short_weights = [amount / divisor for amount in short_amounts]
    take_profit = float(config.get("take_profit_pct") or 0.0)
    hard_stop = float(config.get("hard_stop_pct") or 0.0)
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
    g.anchor_price = 0.0
    g.long_next_level = 0
    g.short_next_level = 0
    g.long_target_value = 0.0
    g.short_target_value = 0.0


def _leg_position(side):
    position = get_position(INSTRUMENT, position_side=side)
    return float(position.amount or 0.0), float(position.avg_cost or 0.0)


def _level_price(levels, index, current_price):
    if not DYNAMIC_ANCHOR:
        return float(levels[index] or 0.0)
    if g.anchor_price <= 0:
        g.anchor_price = float(current_price)
    return float(levels[index] or 0.0) * g.anchor_price


def _reset_leg(side):
    if side == "long":
        g.long_next_level = 0
        g.long_target_value = 0.0
    else:
        g.short_next_level = 0
        g.short_target_value = 0.0


def _risk_exit_leg(side, price):
    amount, average = _leg_position(side)
    if amount == 0 or average <= 0:
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

    long_changed = False
    while not long_exited and g.long_next_level < len(LONG_PRICE_LEVELS):
        target = _level_price(LONG_PRICE_LEVELS, g.long_next_level, price)
        if not float(current["low"]) <= target <= float(current["high"]):
            break
        weight = float(LONG_AMOUNT_WEIGHTS[g.long_next_level] or 0.0)
        g.long_target_value += float(context.portfolio.starting_cash) * weight
        g.long_next_level += 1
        long_changed = True
    if long_changed:
        order_target_value(
            INSTRUMENT,
            g.long_target_value,
            position_side="long",
            reason="neutral_grid_long_level",
        )

    short_changed = False
    while not short_exited and g.short_next_level < len(SHORT_PRICE_LEVELS):
        target = _level_price(SHORT_PRICE_LEVELS, g.short_next_level, price)
        if not float(current["low"]) <= target <= float(current["high"]):
            break
        weight = float(SHORT_AMOUNT_WEIGHTS[g.short_next_level] or 0.0)
        g.short_target_value += float(context.portfolio.starting_cash) * weight
        g.short_next_level += 1
        short_changed = True
    if short_changed:
        order_target_value(
            INSTRUMENT,
            -g.short_target_value,
            position_side="short",
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
    weights = [amount / total for amount in amounts] if total > 0 else [0.0 for _ in amounts]
    initial_match = re.search(r"(?m)^INITIAL_POSITION_PCT = (.+)$", source)
    try:
        initial_pct = float(ast.literal_eval(initial_match.group(1))) if initial_match else 0.0
    except (TypeError, ValueError, SyntaxError):
        initial_pct = 0.0
    level_fraction = max(0.0, 1.0 - initial_pct) if str(kind or "") == "grid" else 1.0
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
    total_budget_pct = min(
        1.0,
        max(0.0, float(config.get("dca_total_budget_pct") or 0.0)),
    )
    order_pct = min(
        total_budget_pct,
        max(0.0, float(config.get("dca_order_pct") or 0.0)),
    )
    dynamic_anchor = bool(config.get("dynamic_anchor"))
    reference_price = float(config.get("entry_price") or 0.0)
    price_filter_enabled = bool(config.get("dca_price_filter_enabled"))
    max_adverse_price_pct = max(
        0.0,
        float(config.get("dca_max_adverse_price_pct") or 0.0),
    )
    trailing_enabled = bool(config.get("trailing_take_profit_enabled"))
    trailing_activation = float(config.get("trailing_activation_pct") or 0.0)
    trailing_callback = float(config.get("trailing_callback_pct") or 0.0)
    take_profit = 0.0 if trailing_enabled else float(config.get("take_profit_pct") or 0.0)
    hard_stop = float(config.get("hard_stop_pct") or 0.0)
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
    )
    body = f'''

def initialize(context):
    context.set_universe([INSTRUMENT])
    context.subscribe(frequency=TIMEFRAME)
    context.set_metadata(direction_mode="long_only", market_type="spot")
    context.set_warmup(2)
    g.dca_order_count = 0
    g.dca_last_schedule_at = None
    g.dca_target_value = 0.0
    g.dca_anchor_price = 0.0


def _reset():
    g.dca_order_count = 0
    g.dca_last_schedule_at = None
    g.dca_target_value = 0.0
    g.dca_anchor_price = 0.0


def _position_state():
    position = get_position(INSTRUMENT)
    amount = float(position.amount or 0.0)
    average = float(position.avg_cost or 0.0)
    return amount, average


def _risk_exit(price):
    amount, average = _position_state()
    if amount == 0 or average <= 0:
        return False
    profit = (price - average) / average
    if TAKE_PROFIT > 0 and profit >= TAKE_PROFIT:
        order_target_value(INSTRUMENT, 0.0, reason="dca_take_profit")
        _reset()
        return True
    if HARD_STOP > 0 and -profit >= HARD_STOP:
        order_target_value(INSTRUMENT, 0.0, reason="dca_hard_stop")
        _reset()
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
    if _risk_exit(price):
        return
    amount, _ = _position_state()
    if amount == 0 and g.dca_order_count > 0:
        _reset()
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
    budget_value = float(context.portfolio.starting_cash) * DCA_TOTAL_BUDGET_PCT
    order_value = float(context.portfolio.starting_cash) * DCA_ORDER_PCT
    remaining_value = max(0.0, budget_value - g.dca_target_value)
    order_value = min(order_value, remaining_value)
    if order_value <= 0:
        return
    g.dca_target_value += order_value
    g.dca_order_count += 1
    order_target_value(
        INSTRUMENT,
        g.dca_target_value,
        reason="dca_scheduled_order",
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
    if kind == "grid" and side == "neutral":
        return _build_neutral_grid_v2_source(
            config,
            preview,
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
    amount_weights = (
        [max(0.0, amount) / total_amount for amount in amounts]
        if total_amount > 0
        else [0.0 for _ in amounts]
    )
    trailing_enabled = kind in {"dca", "martingale", "layered_martingale"} and bool(
        config.get("trailing_take_profit_enabled")
    )
    trailing_activation = float(config.get("trailing_activation_pct") or 0.0)
    trailing_callback = float(config.get("trailing_callback_pct") or 0.0)
    take_profit = 0.0 if trailing_enabled else float(config.get("take_profit_pct") or 0.0)
    hard_stop = float(config.get("hard_stop_pct") or 0.0)
    initial_position_pct = float(config.get("initial_position_pct") or 0.0)
    level_capital_fraction = max(0.0, 1.0 - initial_position_pct) if kind == "grid" else 1.0
    leverage_line = "    context.allow_leverage(max_leverage=100)\n" if market_type == "swap" else ""
    constants = (
        f"INSTRUMENT = {instrument!r}\n"
        f"TIMEFRAME = {timeframe!r}\n"
        f"PRICE_LEVELS = {price_levels!r}\n"
        f"DYNAMIC_ANCHOR = {dynamic_anchor!r}\n"
        f"AMOUNT_WEIGHTS = {amount_weights!r}\n"
        f"DIRECTION = {direction!r}\n"
        f"TAKE_PROFIT = {take_profit!r}\n"
        f"HARD_STOP = {hard_stop!r}\n"
        f"TRAILING_TAKE_PROFIT_ENABLED = {trailing_enabled!r}\n"
        f"TRAILING_ACTIVATION = {trailing_activation!r}\n"
        f"TRAILING_CALLBACK = {trailing_callback!r}\n"
        f"INITIAL_POSITION_PCT = {initial_position_pct!r}\n"
        f"LEVEL_CAPITAL_FRACTION = {level_capital_fraction!r}\n"
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
    )
    helpers = '''

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
    position = get_position(INSTRUMENT)
    amount = float(position.amount or 0.0)
    average = float(position.avg_cost or 0.0)
    return amount, average


def _risk_exit(price):
    amount, average = _position_state()
    if amount == 0 or average <= 0:
        return False
    profit = ((price - average) / average) * DIRECTION
    loss = -profit
    if TAKE_PROFIT > 0 and profit >= TAKE_PROFIT:
        order_target_value(INSTRUMENT, 0.0, reason="robot_take_profit")
        _reset()
        return True
    if HARD_STOP > 0 and loss >= HARD_STOP:
        order_target_value(INSTRUMENT, 0.0, reason="robot_hard_stop")
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

def handle_data(context, data):
    bars = get_history(2, TIMEFRAME, "close", INSTRUMENT)
    if len(bars) < 1 or not PRICE_LEVELS:
        return
    price = float(bars["close"].iloc[-1])
    if _risk_exit(price):
        return
    amount, _ = _position_state()
    if amount == 0 and g.next_level > 0:
        _reset()
    if g.next_level >= len(PRICE_LEVELS):
        return
    target = _level_price(g.next_level, price)
    due = price >= target if DIRECTION < 0 else price <= target
    if g.next_level == 0:
        due = True
    if not due:
        return
    g.target_value += float(context.portfolio.starting_cash) * LEVEL_CAPITAL_FRACTION * float(AMOUNT_WEIGHTS[g.next_level] or 0.0)
    g.next_level += 1
    order_target_value(
        INSTRUMENT,
        DIRECTION * g.target_value,
        reason="robot_level",
        stop_loss_pct=HARD_STOP,
        take_profit_pct=TAKE_PROFIT,
        trailing_stop_pct=TRAILING_CALLBACK if TRAILING_TAKE_PROFIT_ENABLED else 0.0,
        trailing_activation_pct=TRAILING_ACTIVATION if TRAILING_TAKE_PROFIT_ENABLED else 0.0,
    )
'''
    return constants + initialize + helpers + handler
