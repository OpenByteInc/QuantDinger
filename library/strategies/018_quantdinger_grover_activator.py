"""
QuantDinger Grover Activator
One-way adaptive trend strategy using an ATR-scaled recursive activator.
"""

# @param length int 480 range=2:1000:1
# @param multiplier float 14.0 range=0.1:100:0.1
# @param target_pct float 0.95 range=0.05:1:0.05


def _seed_activator(close, high, low, length, multiplier):
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    previous_ts = float(close.iloc[0])
    previous_diff = 0.0
    last_value = 0.0
    bars_since_cross = 0
    latest_up = False
    latest_down = False
    for index in range(1, len(close)):
        atr_value = float(atr.iloc[index])
        if atr_value != atr_value:
            previous_ts = float(close.iloc[index])
            previous_diff = 0.0
            continue
        diff = float(close.iloc[index]) - previous_ts
        up = diff > 0 and previous_diff <= 0
        down = diff < 0 and previous_diff >= 0
        if up or down:
            last_value = atr_value / length
            bars_since_cross = 0
            current_ts = previous_ts - atr_value * multiplier if up else previous_ts + atr_value * multiplier
        else:
            bars_since_cross += 1
            sign = 1.0 if diff > 0 else (-1.0 if diff < 0 else 0.0)
            current_ts = previous_ts + sign * last_value * bars_since_cross
        latest_up = up
        latest_down = down
        previous_diff = diff
        previous_ts = current_ts
    return {
        "latest_up": latest_up,
        "latest_down": latest_down,
        "ts": previous_ts,
        "diff": previous_diff,
        "last_value": last_value,
        "bars_since_cross": bars_since_cross,
        "atr": float(atr.iloc[-1]),
        "previous_close": float(close.iloc[-1]),
    }


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.activator_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(3200)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    length = int(context.params.get("length", 480))
    multiplier = float(context.params.get("multiplier", 14.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    if length < 2 or multiplier <= 0:
        return
    state = dict(g.activator_state or {})
    reset = (
        not state
        or int(state.get("length") or 0) != length
        or float(state.get("multiplier") or 0.0) != multiplier
    )
    if reset:
        required = max(6 * length, 1200)
        bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < required:
            return
        state = _seed_activator(
            bars["close"],
            bars["high"],
            bars["low"],
            length,
            multiplier,
        )
        state["length"] = length
        state["multiplier"] = multiplier
        crossed_up = bool(state["latest_up"])
        crossed_down = bool(state["latest_down"])
    else:
        bars = get_history(2, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 2:
            return
        current_close = float(bars["close"].iloc[-1])
        current_high = float(bars["high"].iloc[-1])
        current_low = float(bars["low"].iloc[-1])
        previous_close = float(state["previous_close"])
        true_range = max(
            current_high - current_low,
            abs(current_high - previous_close),
            abs(current_low - previous_close),
        )
        atr_value = (float(state["atr"]) * (length - 1) + true_range) / length
        previous_ts = float(state["ts"])
        diff = current_close - previous_ts
        previous_diff = float(state["diff"])
        crossed_up = diff > 0 and previous_diff <= 0
        crossed_down = diff < 0 and previous_diff >= 0
        if crossed_up or crossed_down:
            state["last_value"] = atr_value / length
            state["bars_since_cross"] = 0
            current_ts = previous_ts - atr_value * multiplier if crossed_up else previous_ts + atr_value * multiplier
        else:
            state["bars_since_cross"] = int(state["bars_since_cross"]) + 1
            sign = 1.0 if diff > 0 else (-1.0 if diff < 0 else 0.0)
            current_ts = previous_ts + sign * float(state["last_value"]) * int(state["bars_since_cross"])
        state["ts"] = current_ts
        state["diff"] = diff
        state["atr"] = atr_value
        state["previous_close"] = current_close
    g.activator_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if crossed_up and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="activator_cross_long")
    elif crossed_down and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="activator_cross_short")
