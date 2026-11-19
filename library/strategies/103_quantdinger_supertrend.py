"""Classic ATR SuperTrend regime-reversal strategy."""

# @param atr_length int 10 range=1:200:1
# @param atr_multiplier float 3 range=0.1:20:0.1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "previous_close": None, "atr": None, "upper": None, "lower": None, "trend": 1}


def _step(state, high, low, close, atr_length, multiplier):
    previous_close = close if state["previous_close"] is None else float(state["previous_close"])
    true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
    state["atr"] = true_range if state["atr"] is None else (float(state["atr"]) * (atr_length - 1) + true_range) / float(atr_length)
    midpoint = (high + low) / 2.0
    raw_upper = midpoint - multiplier * float(state["atr"])
    raw_lower = midpoint + multiplier * float(state["atr"])
    old_upper = raw_upper if state["upper"] is None else float(state["upper"])
    old_lower = raw_lower if state["lower"] is None else float(state["lower"])
    upper = max(raw_upper, old_upper) if previous_close > old_upper else raw_upper
    lower = min(raw_lower, old_lower) if previous_close < old_lower else raw_lower
    previous_trend = int(state["trend"])
    trend = 1 if previous_trend == -1 and close > old_lower else -1 if previous_trend == 1 and close < old_upper else previous_trend
    state["previous_close"] = close
    state["upper"] = upper
    state["lower"] = lower
    state["trend"] = trend
    return trend == 1 and previous_trend == -1, trend == -1 and previous_trend == 1


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.supertrend_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    atr_length = int(context.params.get("atr_length", 10))
    multiplier = float(context.params.get("atr_multiplier", 3.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [atr_length, multiplier]
    state = dict(g.supertrend_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(320, atr_length * 25)
    bars = get_history(required if cold else 1, "1h", ["high", "low", "close"], g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    buy = sell = False
    for _, row in bars.iterrows():
        buy, sell = _step(state, float(row["high"]), float(row["low"]), float(row["close"]), atr_length, multiplier)
    g.supertrend_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="supertrend_long")
    elif sell and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="supertrend_short")
