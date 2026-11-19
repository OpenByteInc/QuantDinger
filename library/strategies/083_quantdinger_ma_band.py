"""High/low moving-average band breakout with fixed band risk targets."""

# @param ma_length int 40 range=1:400:1
# @param risk_reward float 3.0 range=0.5:20:0.5
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "highs": [], "lows": [], "previous_close": None, "previous_upper": None, "previous_lower": None, "stop": None, "take_profit": None}


def _step(state, high, low, close, ma_length):
    state["highs"].append(high)
    state["lows"].append(low)
    if len(state["highs"]) > ma_length:
        state["highs"].pop(0)
        state["lows"].pop(0)
    if len(state["highs"]) < ma_length:
        state["previous_close"] = close
        return False, False, float("nan"), float("nan")
    upper = sum(state["highs"]) / float(ma_length)
    lower = sum(state["lows"]) / float(ma_length)
    previous_close = state["previous_close"]
    previous_upper = state["previous_upper"]
    previous_lower = state["previous_lower"]
    state["previous_close"] = close
    state["previous_upper"] = upper
    state["previous_lower"] = lower
    if previous_close is None or previous_upper is None or previous_lower is None:
        return False, False, upper, lower
    return float(previous_close) <= float(previous_upper) and close > upper, float(previous_close) >= float(previous_lower) and close < lower, upper, lower


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.ma_band_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="breakout")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    ma_length = int(context.params.get("ma_length", 40))
    risk_reward = float(context.params.get("risk_reward", 3.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [ma_length, risk_reward]
    state = dict(g.ma_band_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(max(300, ma_length * 8), "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < max(300, ma_length * 8):
            return
        state = _new_state(signature)
        values = (False, False, float("nan"), float("nan"))
        for index in range(len(bars)):
            values = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]), ma_length)
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        values = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]), ma_length)
    long_entry, short_entry, upper, lower = values
    close = float(bars["close"].iloc[-1])
    high = float(bars["high"].iloc[-1])
    low = float(bars["low"].iloc[-1])
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_entry and amount <= 0:
        state["stop"] = lower
        state["take_profit"] = close + max(close - lower, 0.0) * risk_reward
        order_target_percent(g.symbol, target_pct, reason="ma_band_long")
    elif short_entry and amount >= 0:
        state["stop"] = upper
        state["take_profit"] = close - max(upper - close, 0.0) * risk_reward
        order_target_percent(g.symbol, -target_pct, reason="ma_band_short")
    elif amount > 0 and state.get("stop") is not None and (low <= float(state["stop"]) or high >= float(state["take_profit"])):
        order_target_percent(g.symbol, 0.0, reason="ma_band_long_exit")
    elif amount < 0 and state.get("stop") is not None and (high >= float(state["stop"]) or low <= float(state["take_profit"])):
        order_target_percent(g.symbol, 0.0, reason="ma_band_short_exit")
    g.ma_band_state = state
