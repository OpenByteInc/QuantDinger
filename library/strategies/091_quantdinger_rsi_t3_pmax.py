"""RSI Tillson-T3 crossover against an ATR-style oscillator stop."""

# @param rsi_length int 14 range=2:100:1
# @param t3_length int 8 range=1:100:1
# @param t3_factor float 0.7 range=0:1:0.05
# @param atr_length int 10 range=1:100:1
# @param atr_multiplier float 3 range=0.1:20:0.1
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _new_state(signature):
    return {"signature": signature, "previous_close": None, "avg_gain": None, "avg_loss": None, "ema": [None, None, None, None, None, None], "previous_rsi": None, "ranges": [], "long_stop": None, "short_stop": None, "direction": 1, "previous_t3": None, "previous_pmax": None}


def _step(state, close, rsi_length, t3_length, factor, atr_length, multiplier):
    previous_close = state["previous_close"]
    change = 0.0 if previous_close is None else close - float(previous_close)
    gain = max(change, 0.0)
    loss = max(-change, 0.0)
    state["avg_gain"] = gain if state["avg_gain"] is None else (float(state["avg_gain"]) * (rsi_length - 1) + gain) / float(rsi_length)
    state["avg_loss"] = loss if state["avg_loss"] is None else (float(state["avg_loss"]) * (rsi_length - 1) + loss) / float(rsi_length)
    rsi = 100.0 if float(state["avg_loss"]) <= 1e-15 else 100.0 - 100.0 / (1.0 + float(state["avg_gain"]) / float(state["avg_loss"]))
    previous_rsi = rsi if state["previous_rsi"] is None else float(state["previous_rsi"])
    state["ranges"].append(abs(rsi - previous_rsi))
    state["ranges"] = state["ranges"][-atr_length:]
    oscillator_atr = sum(state["ranges"]) / float(len(state["ranges"]))
    value = rsi
    for index in range(6):
        state["ema"][index] = _ema(state["ema"][index], value, t3_length)
        value = float(state["ema"][index])
    c1 = -(factor ** 3)
    c2 = 3.0 * factor * factor + 3.0 * factor ** 3
    c3 = -6.0 * factor * factor - 3.0 * factor - 3.0 * factor ** 3
    c4 = 1.0 + 3.0 * factor + factor ** 3 + 3.0 * factor * factor
    t3 = c1 * float(state["ema"][5]) + c2 * float(state["ema"][4]) + c3 * float(state["ema"][3]) + c4 * float(state["ema"][2])
    raw_long = t3 - multiplier * oscillator_atr
    raw_short = t3 + multiplier * oscillator_atr
    old_long = raw_long if state["long_stop"] is None else float(state["long_stop"])
    old_short = raw_short if state["short_stop"] is None else float(state["short_stop"])
    long_stop = max(raw_long, old_long) if t3 > old_long else raw_long
    short_stop = min(raw_short, old_short) if t3 < old_short else raw_short
    direction = int(state["direction"])
    if direction == -1 and t3 > old_short:
        direction = 1
    elif direction == 1 and t3 < old_long:
        direction = -1
    pmax = long_stop if direction == 1 else short_stop
    previous_t3 = state["previous_t3"]
    previous_pmax = state["previous_pmax"]
    buy = previous_t3 is not None and previous_pmax is not None and float(previous_t3) <= float(previous_pmax) and t3 > pmax
    sell = previous_t3 is not None and previous_pmax is not None and float(previous_t3) >= float(previous_pmax) and t3 < pmax
    state["previous_close"] = close
    state["previous_rsi"] = rsi
    state["long_stop"] = long_stop
    state["short_stop"] = short_stop
    state["direction"] = direction
    state["previous_t3"] = t3
    state["previous_pmax"] = pmax
    return buy, sell


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.rsi_t3_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(600)
    context.set_metadata(direction_mode="one_way", strategy_family="momentum")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    rsi_length = int(context.params.get("rsi_length", 14))
    t3_length = int(context.params.get("t3_length", 8))
    factor = float(context.params.get("t3_factor", 0.7))
    atr_length = int(context.params.get("atr_length", 10))
    multiplier = float(context.params.get("atr_multiplier", 3.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [rsi_length, t3_length, factor, atr_length, multiplier]
    state = dict(g.rsi_t3_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(400, rsi_length * 20, t3_length * 30)
    bars = get_history(required if cold else 1, "1h", "close", g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    buy = sell = False
    for value in bars["close"]:
        buy, sell = _step(state, float(value), rsi_length, t3_length, factor, atr_length, multiplier)
    g.rsi_t3_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="rsi_t3_long")
    elif sell and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="rsi_t3_short")
