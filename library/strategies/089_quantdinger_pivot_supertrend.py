"""Pivot-center SuperTrend combined with a moving-average trend filter."""

# @param pivot_period int 2 range=1:20:1
# @param atr_length int 10 range=1:100:1
# @param atr_multiplier float 3 range=0.1:20:0.1
# @param ma_length int 20 range=2:300:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "highs": [], "lows": [], "closes": [], "atr": None, "center": None, "pivot_up": None, "pivot_down": None, "pivot_trend": 1, "trend_up": None, "trend_down": None, "trend": 1}


def _step(state, high, low, close, pivot_period, atr_length, multiplier, ma_length):
    previous_close = state["closes"][-1] if state["closes"] else close
    true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
    state["atr"] = true_range if state["atr"] is None else (float(state["atr"]) * (atr_length - 1) + true_range) / float(atr_length)
    state["highs"].append(high)
    state["lows"].append(low)
    state["closes"].append(close)
    keep = max(ma_length + 5, pivot_period * 2 + 5)
    state["highs"] = state["highs"][-keep:]
    state["lows"] = state["lows"][-keep:]
    state["closes"] = state["closes"][-keep:]
    window = pivot_period * 2 + 1
    if len(state["closes"]) >= window:
        highs = state["highs"][-window:]
        lows = state["lows"][-window:]
        candidate_high = highs[pivot_period]
        candidate_low = lows[pivot_period]
        pivot = candidate_high if candidate_high >= max(highs) else candidate_low if candidate_low <= min(lows) else None
        if pivot is not None:
            state["center"] = pivot if state["center"] is None else (float(state["center"]) * 2.0 + pivot) / 3.0
    if state["center"] is None or len(state["closes"]) < ma_length:
        return False, False
    atr = float(state["atr"])
    raw_pivot_up = float(state["center"]) - multiplier * atr
    raw_pivot_down = float(state["center"]) + multiplier * atr
    old_pivot_up = raw_pivot_up if state["pivot_up"] is None else float(state["pivot_up"])
    old_pivot_down = raw_pivot_down if state["pivot_down"] is None else float(state["pivot_down"])
    pivot_up = max(raw_pivot_up, old_pivot_up) if previous_close > old_pivot_up else raw_pivot_up
    pivot_down = min(raw_pivot_down, old_pivot_down) if previous_close < old_pivot_down else raw_pivot_down
    previous_pivot_trend = int(state["pivot_trend"])
    pivot_trend = 1 if close > old_pivot_down else -1 if close < old_pivot_up else previous_pivot_trend
    midpoint = (high + low) / 2.0
    raw_up = midpoint - multiplier * atr
    raw_down = midpoint + multiplier * atr
    old_up = raw_up if state["trend_up"] is None else float(state["trend_up"])
    old_down = raw_down if state["trend_down"] is None else float(state["trend_down"])
    trend_up = max(raw_up, old_up) if previous_close > old_up else raw_up
    trend_down = min(raw_down, old_down) if previous_close < old_down else raw_down
    previous_trend = int(state["trend"])
    trend = 1 if previous_trend == -1 and close > old_down else -1 if previous_trend == 1 and close < old_up else previous_trend
    average = sum(state["closes"][-ma_length:]) / float(ma_length)
    long_signal = (trend == 1 and previous_trend == -1 and close > average) or (pivot_trend == 1 and previous_pivot_trend == -1)
    short_signal = (trend == -1 and previous_trend == 1 and close < average) or (pivot_trend == -1 and previous_pivot_trend == 1)
    state["pivot_up"] = pivot_up
    state["pivot_down"] = pivot_down
    state["pivot_trend"] = pivot_trend
    state["trend_up"] = trend_up
    state["trend_down"] = trend_down
    state["trend"] = trend
    return long_signal, short_signal


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.pivot_supertrend_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(700)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    pivot_period = int(context.params.get("pivot_period", 2))
    atr_length = int(context.params.get("atr_length", 10))
    multiplier = float(context.params.get("atr_multiplier", 3.0))
    ma_length = int(context.params.get("ma_length", 20))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [pivot_period, atr_length, multiplier, ma_length]
    state = dict(g.pivot_supertrend_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(400, ma_length * 10, atr_length * 20)
    bars = get_history(required if cold else 1, "1h", ["high", "low", "close"], g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    long_signal = short_signal = False
    for _, row in bars.iterrows():
        long_signal, short_signal = _step(state, float(row["high"]), float(row["low"]), float(row["close"]), pivot_period, atr_length, multiplier, ma_length)
    g.pivot_supertrend_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_signal and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="pivot_supertrend_long")
    elif short_signal and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="pivot_supertrend_short")
