"""Vegas-channel volatility-adjusted SuperTrend strategy."""

# @param atr_period int 10 range=1:200:1
# @param vegas_length int 100 range=10:500:1
# @param base_multiplier float 5 range=0.1:30:0.1
# @param volatility_factor float 5 range=0:30:0.1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "closes": [], "previous_close": None, "atr": None, "upper": None, "lower": None, "trend": 1}


def _step(state, high, low, close, atr_period, vegas_length, base_multiplier, volatility_factor):
    previous_close = close if state["previous_close"] is None else float(state["previous_close"])
    state["closes"].append(close)
    state["closes"] = state["closes"][-vegas_length:]
    true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
    state["atr"] = true_range if state["atr"] is None else (float(state["atr"]) * (atr_period - 1) + true_range) / float(atr_period)
    if len(state["closes"]) < vegas_length:
        state["previous_close"] = close
        return False, 0
    average = sum(state["closes"]) / float(vegas_length)
    variance = sum((value - average) ** 2 for value in state["closes"]) / float(vegas_length)
    deviation = variance ** 0.5
    adjusted = base_multiplier + volatility_factor * (2.0 * deviation / max(abs(average), 1e-12))
    midpoint = (high + low + close) / 3.0
    raw_upper = midpoint - adjusted * float(state["atr"])
    raw_lower = midpoint + adjusted * float(state["atr"])
    old_upper = raw_upper if state["upper"] is None else float(state["upper"])
    old_lower = raw_lower if state["lower"] is None else float(state["lower"])
    previous_trend = int(state["trend"])
    trend = 1 if close > old_lower else -1 if close < old_upper else previous_trend
    upper = max(raw_upper, old_upper) if trend == 1 else raw_upper
    lower = min(raw_lower, old_lower) if trend == -1 else raw_lower
    state["previous_close"] = close
    state["upper"] = upper
    state["lower"] = lower
    state["trend"] = trend
    return trend != previous_trend, trend


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.vegas_supertrend_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(1200)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    atr_period = int(context.params.get("atr_period", 10))
    vegas_length = int(context.params.get("vegas_length", 100))
    base_multiplier = float(context.params.get("base_multiplier", 5.0))
    volatility_factor = float(context.params.get("volatility_factor", 5.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [atr_period, vegas_length, base_multiplier, volatility_factor]
    state = dict(g.vegas_supertrend_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(700, vegas_length * 8, atr_period * 30)
    bars = get_history(required if cold else 1, "1h", ["high", "low", "close"], g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    shifted = False
    trend = 0
    for _, row in bars.iterrows():
        shifted, trend = _step(state, float(row["high"]), float(row["low"]), float(row["close"]), atr_period, vegas_length, base_multiplier, volatility_factor)
    g.vegas_supertrend_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if shifted and amount != 0:
        order_target_percent(g.symbol, 0.0, reason="vegas_trend_shift")
    elif not shifted and trend == 1 and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="vegas_long")
    elif not shifted and trend == -1 and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="vegas_short")
