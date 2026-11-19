"""Hull moving-average direction confirmed by SuperTrend."""

# @param hull_length int 50 range=2:300:1
# @param atr_length int 10 range=1:200:1
# @param atr_multiplier float 3 range=0.1:20:0.1
# @param exit_choppy bool true
# @param target_pct float 0.95 range=0.05:1:0.05


def _wma(values, length):
    if len(values) < length:
        return None
    denominator = float(length * (length + 1) // 2)
    return sum(values[-length + index] * (index + 1) for index in range(length)) / denominator


def _new_state(signature):
    return {"signature": signature, "closes": [], "hull_raw": [], "previous_hull": None, "previous_close": None, "atr": None, "upper": None, "lower": None, "trend": 1}


def _step(state, high, low, close, hull_length, atr_length, multiplier):
    state["closes"].append(close)
    state["closes"] = state["closes"][-hull_length * 2 - 5:]
    half = max(1, hull_length // 2)
    root = max(1, int(hull_length ** 0.5))
    half_value = _wma(state["closes"], half)
    full_value = _wma(state["closes"], hull_length)
    if half_value is None or full_value is None:
        state["previous_close"] = close
        return False, False, False
    state["hull_raw"].append(2.0 * half_value - full_value)
    state["hull_raw"] = state["hull_raw"][-root:]
    hull = _wma(state["hull_raw"], root)
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
    trend = int(state["trend"])
    if trend == -1 and close > old_lower:
        trend = 1
    elif trend == 1 and close < old_upper:
        trend = -1
    previous_hull = state["previous_hull"]
    state["previous_hull"] = hull
    state["previous_close"] = close
    state["upper"] = upper
    state["lower"] = lower
    state["trend"] = trend
    if hull is None or previous_hull is None:
        return False, False, False
    rising = hull > float(previous_hull)
    falling = hull < float(previous_hull)
    bullish = rising and trend == 1
    bearish = falling and trend == -1
    choppy = (rising and trend == -1) or (falling and trend == 1)
    return bullish, bearish, choppy


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.hma_supertrend_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(900)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    hull_length = int(context.params.get("hull_length", 50))
    atr_length = int(context.params.get("atr_length", 10))
    multiplier = float(context.params.get("atr_multiplier", 3.0))
    exit_choppy = bool(context.params.get("exit_choppy", True))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [hull_length, atr_length, multiplier]
    state = dict(g.hma_supertrend_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(500, hull_length * 12, atr_length * 20)
    bars = get_history(required if cold else 1, "1h", ["high", "low", "close"], g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    bullish = bearish = choppy = False
    for _, row in bars.iterrows():
        bullish, bearish, choppy = _step(state, float(row["high"]), float(row["low"]), float(row["close"]), hull_length, atr_length, multiplier)
    g.hma_supertrend_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if exit_choppy and choppy and amount != 0:
        order_target_percent(g.symbol, 0.0, reason="hma_supertrend_choppy")
    elif bullish and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="hma_supertrend_long")
    elif bearish and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="hma_supertrend_short")
