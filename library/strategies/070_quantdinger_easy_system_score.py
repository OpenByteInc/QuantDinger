"""Multi-indicator Easy System score gated by a volatility trend state."""

# @param entry_points float 15 range=0:36:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _new_state(signature):
    return {"signature": signature, "closes": [], "highs": [], "lows": [], "previous_close": None, "fast": None, "slow": None, "macd_signal": None, "plus_dm": None, "minus_dm": None, "tr": None, "trend_stop": None, "trend": 1}


def _step(state, high, low, close):
    previous_close = state["previous_close"]
    true_range = high - low if previous_close is None else max(high - low, abs(high - float(previous_close)), abs(low - float(previous_close)))
    state["closes"].append(close)
    state["highs"].append(high)
    state["lows"].append(low)
    for key in ("closes", "highs", "lows"):
        if len(state[key]) > 60:
            state[key].pop(0)
    state["fast"] = _ema(state["fast"], close, 12)
    state["slow"] = _ema(state["slow"], close, 26)
    macd = float(state["fast"]) - float(state["slow"])
    state["macd_signal"] = _ema(state["macd_signal"], macd, 9)
    if previous_close is not None:
        up = high - float(state.get("previous_high") or high)
        down = float(state.get("previous_low") or low) - low
        state["plus_dm"] = _ema(state["plus_dm"], up if up > down and up > 0 else 0.0, 14)
        state["minus_dm"] = _ema(state["minus_dm"], down if down > up and down > 0 else 0.0, 14)
        state["tr"] = _ema(state["tr"], true_range, 14)
    state["previous_high"] = high
    state["previous_low"] = low
    state["previous_close"] = close
    if len(state["closes"]) < 50 or state["tr"] is None:
        return 0.0, int(state["trend"])
    atr = float(state["tr"])
    upper = (high + low) / 2.0 + 3.0 * atr
    lower = (high + low) / 2.0 - 3.0 * atr
    previous_stop = state["trend_stop"]
    trend = int(state["trend"])
    if previous_stop is not None:
        if trend > 0 and close < float(previous_stop):
            trend = -1
        elif trend < 0 and close > float(previous_stop):
            trend = 1
    state["trend"] = trend
    state["trend_stop"] = lower if trend > 0 else upper
    sma20 = sum(state["closes"][-20:]) / 20.0
    sma50 = sum(state["closes"][-50:]) / 50.0
    mean20 = sma20
    deviation = sum(abs(value - mean20) for value in state["closes"][-20:]) / 20.0
    cci = 0.0 if deviation <= 1e-15 else (close - mean20) / (0.015 * deviation)
    roc = close - state["closes"][-11]
    momentum = close - state["closes"][-15]
    awesome = sum(state["closes"][-5:]) / 5.0 - sum(state["closes"][-34:]) / 34.0
    plus_di = float(state["plus_dm"]) / max(float(state["tr"]), 1e-15)
    minus_di = float(state["minus_dm"]) / max(float(state["tr"]), 1e-15)
    midpoint = (max(state["highs"][-20:]) + min(state["lows"][-20:])) / 2.0
    values = [trend, 1 if roc > 0 else -1, 1 if plus_di > minus_di else -1, 1 if cci > 0 else -1, 1 if sma20 > sma50 else -1, 1 if awesome > 0 else -1, 1 if momentum > 0 else -1, 1 if macd > float(state["macd_signal"]) else -1, 1 if close > mean20 else -1, 1 if close > midpoint else -1, 1 if close > state["closes"][-15] else -1, 1 if close > float(state["fast"]) else -1]
    return 3.0 * sum(values), trend


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.easy_score_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(700)
    context.set_metadata(direction_mode="one_way", strategy_family="composite")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    entry_points = float(context.params.get("entry_points", 15.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [entry_points]
    state = dict(g.easy_score_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(500, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 500:
            return
        state = _new_state(signature)
        score = 0.0
        trend = 0
        for index in range(len(bars)):
            score, trend = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]))
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        score, trend = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]))
    g.easy_score_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if score > entry_points and trend > 0 and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="easy_system_long")
    elif score < -entry_points and trend < 0 and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="easy_system_short")
