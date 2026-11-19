"""CCI-regime SuperTrend strategy with two-sided reversal orders."""

# @param cci_period int 28 range=2:300:1
# @param atr_period int 3 range=1:100:1
# @param factor float 3.0 range=0.1:20:0.1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "typical": [], "tr": [], "previous_close": None, "previous_cci": None, "trend_up": None, "trend_down": None, "trend": 1}


def _step(state, high, low, close, cci_period, atr_period, factor):
    previous_close = state["previous_close"]
    true_range = high - low if previous_close is None else max(high - low, abs(high - float(previous_close)), abs(low - float(previous_close)))
    typical = (high + low + close) / 3.0
    state["tr"].append(true_range)
    state["typical"].append(typical)
    if len(state["tr"]) > atr_period:
        state["tr"].pop(0)
    if len(state["typical"]) > cci_period:
        state["typical"].pop(0)
    state["previous_close"] = close
    if len(state["tr"]) < atr_period or len(state["typical"]) < cci_period:
        return False, False
    atr = sum(state["tr"]) / float(atr_period)
    mean = sum(state["typical"]) / float(cci_period)
    deviation = sum(abs(value - mean) for value in state["typical"]) / float(cci_period)
    cci = 0.0 if deviation <= 1e-15 else (typical - mean) / (0.015 * deviation)
    upper_candidate = (high + low) / 2.0 - factor * atr
    lower_candidate = (high + low) / 2.0 + factor * atr
    previous_cci = state["previous_cci"]
    if previous_cci is not None and float(previous_cci) > 0:
        state["trend_up"] = upper_candidate if state["trend_up"] is None else max(upper_candidate, float(state["trend_up"]))
    else:
        state["trend_up"] = upper_candidate
    if previous_cci is not None and float(previous_cci) < 0:
        state["trend_down"] = lower_candidate if state["trend_down"] is None else min(lower_candidate, float(state["trend_down"]))
    else:
        state["trend_down"] = lower_candidate
    previous_trend = int(state["trend"])
    if cci > 0:
        state["trend"] = 1
    elif cci < 0:
        state["trend"] = -1
    state["previous_cci"] = cci
    return int(state["trend"]) == 1 and previous_trend == -1, int(state["trend"]) == -1 and previous_trend == 1


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.cci_supertrend_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    cci_period = int(context.params.get("cci_period", 28))
    atr_period = int(context.params.get("atr_period", 3))
    factor = float(context.params.get("factor", 3.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    if min(cci_period, atr_period) < 1 or factor <= 0:
        return
    signature = [cci_period, atr_period, factor]
    state = dict(g.cci_supertrend_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(max(300, cci_period * 10), "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < max(300, cci_period * 10):
            return
        state = _new_state(signature)
        go_long = False
        go_short = False
        for index in range(len(bars)):
            go_long, go_short = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]), cci_period, atr_period, factor)
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        go_long, go_short = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]), cci_period, atr_period, factor)
    g.cci_supertrend_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if go_long and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="cci_supertrend_long")
    elif go_short and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="cci_supertrend_short")
