"""Open-price UT Bot with Hull smoothing and ATR trailing stop."""

# @param ma_period int 2 range=1:100:1
# @param atr_period int 11 range=1:200:1
# @param sensitivity float 1 range=0.1:20:0.1
# @param allow_sell bool false
# @param target_pct float 0.95 range=0.05:1:0.05


def _wma(values, length):
    if len(values) < length:
        return None
    denominator = float(length * (length + 1) // 2)
    return sum(values[-length + index] * (index + 1) for index in range(length)) / denominator


def _new_state(signature):
    return {"signature": signature, "previous_close": None, "opens": [], "hull_raw": [], "atr": None, "stop": None, "previous_src": None, "previous_hull": None, "previous_stop": None}


def _step(state, open_price, high, low, close, ma_period, atr_period, sensitivity):
    previous_close = close if state["previous_close"] is None else float(state["previous_close"])
    true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
    state["atr"] = true_range if state["atr"] is None else (float(state["atr"]) * (atr_period - 1) + true_range) / float(atr_period)
    loss = sensitivity * float(state["atr"])
    source = open_price
    previous_stop = 0.0 if state["stop"] is None else float(state["stop"])
    previous_src = source if state["previous_src"] is None else float(state["previous_src"])
    if source > previous_stop and previous_src > previous_stop:
        stop = max(previous_stop, source - loss)
    elif source < previous_stop and previous_src < previous_stop:
        stop = min(previous_stop, source + loss)
    else:
        stop = source - loss if source > previous_stop else source + loss
    state["opens"].append(source)
    state["opens"] = state["opens"][-ma_period - 3:]
    half = max(1, ma_period // 2)
    root = max(1, int(ma_period ** 0.5))
    half_value = _wma(state["opens"], half)
    full_value = _wma(state["opens"], ma_period)
    hull = None
    if half_value is not None and full_value is not None:
        state["hull_raw"].append(2.0 * half_value - full_value)
        state["hull_raw"] = state["hull_raw"][-root:]
        hull = _wma(state["hull_raw"], root)
    previous_hull = state["previous_hull"]
    buy = previous_hull is not None and state["previous_stop"] is not None and float(previous_hull) <= float(state["previous_stop"]) and hull is not None and hull > stop and source > stop
    sell = previous_hull is not None and state["previous_stop"] is not None and float(previous_hull) >= float(state["previous_stop"]) and hull is not None and hull < stop and source < stop
    state["previous_close"] = close
    state["previous_src"] = source
    state["previous_hull"] = hull
    state["previous_stop"] = stop
    state["stop"] = stop
    return buy, sell


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.ut_bot_v5_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["open", "high", "low", "close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    ma_period = int(context.params.get("ma_period", 2))
    atr_period = int(context.params.get("atr_period", 11))
    sensitivity = float(context.params.get("sensitivity", 1.0))
    allow_sell = bool(context.params.get("allow_sell", False))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [ma_period, atr_period, sensitivity, allow_sell]
    state = dict(g.ut_bot_v5_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(320, atr_period * 25, ma_period * 20)
    bars = get_history(required if cold else 1, "1h", ["open", "high", "low", "close"], g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    buy = sell = False
    for _, row in bars.iterrows():
        buy, sell = _step(state, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]), ma_period, atr_period, sensitivity)
    g.ut_bot_v5_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="ut_bot_v5_long")
    elif sell and allow_sell and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="ut_bot_v5_short")
    elif sell and not allow_sell and amount > 0:
        order_target_percent(g.symbol, 0.0, reason="ut_bot_v5_long_exit")
