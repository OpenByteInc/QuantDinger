"""Dual-SuperTrend cloud breakout with between-band exits."""

# @param multiplier_fast float 2.4 range=0.1:20:0.1
# @param multiplier_slow float 4.8 range=0.1:30:0.1
# @param atr_period int 4 range=1:100:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "previous_close": None, "atr_fast": None, "atr_slow": None, "up_fast": None, "down_fast": None, "trend_fast": 1, "line_fast": None, "up_slow": None, "down_slow": None, "trend_slow": 1, "line_slow": None}


def _supertrend(state, prefix, high, low, close, previous_close, period, multiplier):
    true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
    atr_key = "atr_" + prefix
    state[atr_key] = true_range if state[atr_key] is None else (float(state[atr_key]) * (period - 1) + true_range) / float(period)
    midpoint = (high + low) / 2.0
    raw_up = midpoint - multiplier * float(state[atr_key])
    raw_down = midpoint + multiplier * float(state[atr_key])
    up_key = "up_" + prefix
    down_key = "down_" + prefix
    trend_key = "trend_" + prefix
    old_up = raw_up if state[up_key] is None else float(state[up_key])
    old_down = raw_down if state[down_key] is None else float(state[down_key])
    up = max(raw_up, old_up) if previous_close > old_up else raw_up
    down = min(raw_down, old_down) if previous_close < old_down else raw_down
    trend = int(state[trend_key])
    if close > old_down:
        trend = 1
    elif close < old_up:
        trend = -1
    line = up if trend == 1 else down
    state[up_key] = up
    state[down_key] = down
    state[trend_key] = trend
    return line


def _step(state, high, low, close, period, multiplier_fast, multiplier_slow):
    previous_close = close if state["previous_close"] is None else float(state["previous_close"])
    previous_fast = state["line_fast"]
    previous_slow = state["line_slow"]
    fast = _supertrend(state, "fast", high, low, close, previous_close, period, multiplier_fast)
    slow = _supertrend(state, "slow", high, low, close, previous_close, period, multiplier_slow)
    cross_fast_up = previous_fast is not None and previous_close <= float(previous_fast) and close > fast
    cross_fast_down = previous_fast is not None and previous_close >= float(previous_fast) and close < fast
    cross_slow_up = previous_slow is not None and previous_close <= float(previous_slow) and close > slow
    cross_slow_down = previous_slow is not None and previous_close >= float(previous_slow) and close < slow
    long_signal = (cross_fast_up and close > slow) or (cross_slow_up and close > fast)
    short_signal = (cross_fast_down and close < slow) or (cross_slow_down and close < fast)
    cloud_exit = (cross_fast_down and close > slow) or (cross_fast_up and close < slow) or (cross_slow_down and close > fast) or (cross_slow_up and close < fast)
    state["line_fast"] = fast
    state["line_slow"] = slow
    state["previous_close"] = close
    return long_signal, short_signal, cloud_exit


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.supertrend_cloud_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    multiplier_fast = float(context.params.get("multiplier_fast", 2.4))
    multiplier_slow = float(context.params.get("multiplier_slow", 4.8))
    period = int(context.params.get("atr_period", 4))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [multiplier_fast, multiplier_slow, period]
    state = dict(g.supertrend_cloud_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(320, period * 30)
    bars = get_history(required if cold else 1, "1h", ["high", "low", "close"], g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    long_signal = short_signal = cloud_exit = False
    for _, row in bars.iterrows():
        long_signal, short_signal, cloud_exit = _step(state, float(row["high"]), float(row["low"]), float(row["close"]), period, multiplier_fast, multiplier_slow)
    g.supertrend_cloud_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if cloud_exit and amount != 0:
        order_target_percent(g.symbol, 0.0, reason="supertrend_cloud_exit")
    elif long_signal and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="supertrend_cloud_long")
    elif short_signal and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="supertrend_cloud_short")
