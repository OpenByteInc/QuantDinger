"""Single-direction Turtle breakout with Donchian and ATR exits."""

# @param entry_period int 20 range=2:300:1
# @param exit_period int 10 range=2:300:1
# @param atr_period int 20 range=1:300:1
# @param atr_multiplier float 2.0 range=0.1:20:0.1
# @param short_direction bool false
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema_step(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _new_state(signature):
    return {
        "signature": signature,
        "highs": [],
        "lows": [],
        "previous_close": None,
        "atr": None,
        "atr_upper": None,
        "atr_lower": None,
        "previous_upper": None,
        "previous_lower": None,
        "previous_atr_upper": None,
        "previous_atr_lower": None,
    }


def _step(state, high, low, close, entry_period, exit_period, atr_period, atr_multiplier, short_direction):
    previous_close = state["previous_close"]
    true_range = high - low if previous_close is None else max(
        high - low,
        abs(high - float(previous_close)),
        abs(low - float(previous_close)),
    )
    state["atr"] = _ema_step(state["atr"], true_range, atr_period)
    state["atr_upper"] = _ema_step(state["atr_upper"], close + atr_multiplier * float(state["atr"]), 3)
    state["atr_lower"] = _ema_step(state["atr_lower"], close - atr_multiplier * float(state["atr"]), 3)
    state["highs"].append(high)
    state["lows"].append(low)
    maximum_period = max(entry_period, exit_period)
    if len(state["highs"]) > maximum_period:
        state["highs"].pop(0)
        state["lows"].pop(0)
    upper_period = exit_period if short_direction else entry_period
    lower_period = entry_period if short_direction else exit_period
    upper = max(state["highs"][-upper_period:]) if len(state["highs"]) >= upper_period else float("nan")
    lower = min(state["lows"][-lower_period:]) if len(state["lows"]) >= lower_period else float("nan")
    previous_upper = state["previous_upper"]
    previous_lower = state["previous_lower"]
    previous_atr_upper = state["previous_atr_upper"]
    previous_atr_lower = state["previous_atr_lower"]
    state["previous_upper"] = upper
    state["previous_lower"] = lower
    state["previous_atr_upper"] = float(state["atr_upper"])
    state["previous_atr_lower"] = float(state["atr_lower"])
    state["previous_close"] = close
    if any(value is None or float(value) != float(value) for value in (previous_upper, previous_lower, previous_atr_upper, previous_atr_lower)):
        return False, False
    if short_direction:
        return close <= float(previous_lower), close >= float(previous_upper) or close >= float(previous_atr_upper)
    return close >= float(previous_upper), close <= float(previous_lower) or close <= float(previous_atr_lower)


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.turtle_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(600)
    context.set_metadata(direction_mode="one_way", strategy_family="breakout")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    entry_period = int(context.params.get("entry_period", 20))
    exit_period = int(context.params.get("exit_period", 10))
    atr_period = int(context.params.get("atr_period", 20))
    atr_multiplier = float(context.params.get("atr_multiplier", 2.0))
    short_direction = bool(context.params.get("short_direction", False))
    target_pct = float(context.params.get("target_pct", 0.95))
    if min(entry_period, exit_period, atr_period) < 1 or atr_multiplier <= 0:
        return
    signature = [entry_period, exit_period, atr_period, atr_multiplier, short_direction]
    state = dict(g.turtle_state or {})
    if not state or list(state.get("signature") or []) != signature:
        required = max(15 * max(entry_period, exit_period, atr_period), 400)
        bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(signature)
        enter = False
        exit_signal = False
        for index in range(len(bars)):
            enter, exit_signal = _step(
                state,
                float(bars["high"].iloc[index]),
                float(bars["low"].iloc[index]),
                float(bars["close"].iloc[index]),
                entry_period,
                exit_period,
                atr_period,
                atr_multiplier,
                short_direction,
            )
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        enter, exit_signal = _step(
            state,
            float(bars["high"].iloc[-1]),
            float(bars["low"].iloc[-1]),
            float(bars["close"].iloc[-1]),
            entry_period,
            exit_period,
            atr_period,
            atr_multiplier,
            short_direction,
        )
    g.turtle_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if not short_direction and enter and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="turtle_breakout_long")
    elif not short_direction and exit_signal and amount > 0:
        order_target_percent(g.symbol, 0.0, reason="turtle_breakout_long_exit")
    elif short_direction and enter and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="turtle_breakout_short")
    elif short_direction and exit_signal and amount < 0:
        order_target_percent(g.symbol, 0.0, reason="turtle_breakout_short_exit")
