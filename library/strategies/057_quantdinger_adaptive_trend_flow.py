"""Long-only adaptive trend flow with SMA and MACD confirmation."""

# @param trend_length int 2 range=2:100:1
# @param smooth_length int 2 range=2:100:1
# @param sensitivity float 2.0 range=0.1:10:0.1
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _new_state(signature):
    return {"signature": signature, "typical": [], "closes": [], "fast": None, "slow": None, "smooth_vol": None, "macd_fast": None, "macd_slow": None, "macd_signal": None, "trend": 0}


def _step(state, high, low, close, length, smooth_length, sensitivity):
    typical = (high + low + close) / 3.0
    state["typical"].append(typical)
    state["closes"].append(close)
    if len(state["typical"]) > length:
        state["typical"].pop(0)
    if len(state["closes"]) > 4:
        state["closes"].pop(0)
    state["fast"] = _ema(state["fast"], typical, length)
    state["slow"] = _ema(state["slow"], typical, length * 2)
    basis = (float(state["fast"]) + float(state["slow"])) / 2.0
    mean = sum(state["typical"]) / float(len(state["typical"]))
    variance = sum((value - mean) * (value - mean) for value in state["typical"]) / float(len(state["typical"]))
    state["smooth_vol"] = _ema(state["smooth_vol"], variance ** 0.5, smooth_length)
    upper = basis + sensitivity * float(state["smooth_vol"])
    lower = basis - sensitivity * float(state["smooth_vol"])
    previous_trend = int(state["trend"])
    trend = previous_trend
    if trend == 0:
        trend = 1 if close > basis else -1
    elif trend > 0 and close < lower:
        trend = -1
    elif trend < 0 and close > upper:
        trend = 1
    state["trend"] = trend
    state["macd_fast"] = _ema(state["macd_fast"], close, 2)
    state["macd_slow"] = _ema(state["macd_slow"], close, 7)
    macd = float(state["macd_fast"]) - float(state["macd_slow"])
    state["macd_signal"] = _ema(state["macd_signal"], macd, 2)
    sma_ok = len(state["closes"]) >= 4 and close > sum(state["closes"][-4:]) / 4.0
    macd_ok = macd > float(state["macd_signal"])
    return trend == 1 and previous_trend == -1 and sma_ok and macd_ok, trend == -1 and previous_trend == 1


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.flow_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(300)
    context.set_metadata(direction_mode="long_only", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    length = int(context.params.get("trend_length", 2))
    smooth_length = int(context.params.get("smooth_length", 2))
    sensitivity = float(context.params.get("sensitivity", 2.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    if min(length, smooth_length) < 2 or sensitivity <= 0:
        return
    signature = [length, smooth_length, sensitivity]
    state = dict(g.flow_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(240, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 240:
            return
        state = _new_state(signature)
        enter = False
        exit_signal = False
        for index in range(len(bars)):
            enter, exit_signal = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]), length, smooth_length, sensitivity)
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        enter, exit_signal = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]), length, smooth_length, sensitivity)
    g.flow_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if enter and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="adaptive_flow_long")
    elif exit_signal and amount > 0:
        order_target_percent(g.symbol, 0.0, reason="adaptive_flow_exit")
