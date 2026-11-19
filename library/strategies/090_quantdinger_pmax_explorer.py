"""EMA and ATR-based Profit Maximizer crossover strategy."""

# @param ma_length int 10 range=1:200:1
# @param atr_length int 10 range=1:200:1
# @param atr_multiplier float 3 range=0.1:20:0.1
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _new_state(signature):
    return {"signature": signature, "previous_close": None, "ma": None, "atr": None, "long_stop": None, "short_stop": None, "direction": 1, "previous_ma": None, "previous_pmax": None}


def _step(state, high, low, close, ma_length, atr_length, multiplier):
    previous_close = state["previous_close"]
    midpoint = (high + low) / 2.0
    ma = _ema(state["ma"], midpoint, ma_length)
    true_range = high - low if previous_close is None else max(high - low, abs(high - float(previous_close)), abs(low - float(previous_close)))
    atr = true_range if state["atr"] is None else (float(state["atr"]) * (atr_length - 1) + true_range) / float(atr_length)
    raw_long = ma - multiplier * atr
    raw_short = ma + multiplier * atr
    old_long = raw_long if state["long_stop"] is None else float(state["long_stop"])
    old_short = raw_short if state["short_stop"] is None else float(state["short_stop"])
    long_stop = max(raw_long, old_long) if ma > old_long else raw_long
    short_stop = min(raw_short, old_short) if ma < old_short else raw_short
    direction = int(state["direction"])
    if direction == -1 and ma > old_short:
        direction = 1
    elif direction == 1 and ma < old_long:
        direction = -1
    pmax = long_stop if direction == 1 else short_stop
    previous_ma = state["previous_ma"]
    previous_pmax = state["previous_pmax"]
    buy = previous_ma is not None and previous_pmax is not None and float(previous_ma) <= float(previous_pmax) and ma > pmax
    sell = previous_ma is not None and previous_pmax is not None and float(previous_ma) >= float(previous_pmax) and ma < pmax
    state["previous_close"] = close
    state["ma"] = ma
    state["atr"] = atr
    state["long_stop"] = long_stop
    state["short_stop"] = short_stop
    state["direction"] = direction
    state["previous_ma"] = ma
    state["previous_pmax"] = pmax
    return buy, sell


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.pmax_explorer_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    ma_length = int(context.params.get("ma_length", 10))
    atr_length = int(context.params.get("atr_length", 10))
    multiplier = float(context.params.get("atr_multiplier", 3.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [ma_length, atr_length, multiplier]
    state = dict(g.pmax_explorer_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(320, ma_length * 12, atr_length * 20)
    bars = get_history(required if cold else 1, "1h", ["high", "low", "close"], g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    buy = sell = False
    for _, row in bars.iterrows():
        buy, sell = _step(state, float(row["high"]), float(row["low"]), float(row["close"]), ma_length, atr_length, multiplier)
    g.pmax_explorer_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="pmax_explorer_long")
    elif sell and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="pmax_explorer_short")
