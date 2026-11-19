"""Displaced EMA envelope signals filtered by an ATR trailing state."""

# @param ema_period int 9 range=1:300:1
# @param displacement int 13 range=1:200:1
# @param envelope_pct float 0.005 range=0.0001:0.2:0.0001
# @param atr_period int 15 range=1:100:1
# @param atr_multiplier float 2.0 range=0.1:20:0.1
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _new_state(signature):
    return {"signature": signature, "ema": None, "ema_history": [], "tr": [], "previous_close": None, "trail": None, "position_signal": 0}


def _step(state, high, low, close, ema_period, displacement, envelope_pct, atr_period, atr_multiplier):
    previous_close = state["previous_close"]
    state["ema"] = _ema(state["ema"], close, ema_period)
    state["ema_history"].append(float(state["ema"]))
    if len(state["ema_history"]) > displacement + 1:
        state["ema_history"].pop(0)
    true_range = high - low if previous_close is None else max(high - low, abs(high - float(previous_close)), abs(low - float(previous_close)))
    state["tr"].append(true_range)
    if len(state["tr"]) > atr_period:
        state["tr"].pop(0)
    state["previous_close"] = close
    if len(state["ema_history"]) < displacement + 1 or len(state["tr"]) < atr_period:
        return 0, False, False
    core = state["ema_history"][0]
    top = core * (1.0 + envelope_pct)
    bottom = core * (1.0 - envelope_pct)
    signal = int(state["position_signal"])
    if close > top:
        signal = 1
    elif close < bottom:
        signal = -1
    atr = sum(state["tr"]) / float(atr_period)
    loss = atr_multiplier * atr
    previous_trail = state["trail"]
    if previous_trail is None:
        trail = close - loss
    elif close > float(previous_trail) and previous_close is not None and float(previous_close) > float(previous_trail):
        trail = max(float(previous_trail), close - loss)
    elif close < float(previous_trail) and previous_close is not None and float(previous_close) < float(previous_trail):
        trail = min(float(previous_trail), close + loss)
    else:
        trail = close - loss if close > float(previous_trail) else close + loss
    state["trail"] = trail
    state["position_signal"] = signal
    return signal, close > trail, close < trail


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.envelope_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="channel")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    ema_period = int(context.params.get("ema_period", 9))
    displacement = int(context.params.get("displacement", 13))
    envelope_pct = float(context.params.get("envelope_pct", 0.005))
    atr_period = int(context.params.get("atr_period", 15))
    atr_multiplier = float(context.params.get("atr_multiplier", 2.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [ema_period, displacement, envelope_pct, atr_period, atr_multiplier]
    state = dict(g.envelope_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(360, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 360:
            return
        state = _new_state(signature)
        values = (0, False, False)
        for index in range(len(bars)):
            values = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]), ema_period, displacement, envelope_pct, atr_period, atr_multiplier)
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        values = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]), ema_period, displacement, envelope_pct, atr_period, atr_multiplier)
    g.envelope_state = state
    signal, atr_long, atr_short = values
    amount = float(get_position(g.symbol).amount or 0.0)
    if signal > 0 and atr_long and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="envelope_long")
    elif signal < 0 and atr_short and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="envelope_short")
