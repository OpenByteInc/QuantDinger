"""ATR trailing-stop crossover strategy with completed-bar signals."""

# @param multiplier float 1.0 range=0.1:20:0.1
# @param atr_period int 10 range=1:500:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _rma_step(state, value, length):
    current = state.get("value")
    if current is not None:
        current = (float(current) * (length - 1) + float(value)) / float(length)
        state["value"] = current
        return current
    state["seed"].append(float(value))
    if len(state["seed"]) < length:
        return float("nan")
    current = sum(state["seed"][-length:]) / float(length)
    state["value"] = current
    state["seed"] = []
    return current


def _new_state(signature):
    return {
        "signature": signature,
        "previous_close": None,
        "stop": 0.0,
        "atr": {"value": None, "seed": []},
        "ready": False,
    }


def _step(state, high, low, close, atr_period, multiplier):
    previous_close = state["previous_close"]
    true_range = high - low if previous_close is None else max(
        high - low,
        abs(high - float(previous_close)),
        abs(low - float(previous_close)),
    )
    atr = _rma_step(state["atr"], true_range, atr_period)
    previous_stop = float(state["stop"])
    state["previous_close"] = close
    if atr != atr or previous_close is None:
        return False, False
    loss = multiplier * atr
    if close > previous_stop and float(previous_close) > previous_stop:
        stop = max(previous_stop, close - loss)
    elif close < previous_stop and float(previous_close) < previous_stop:
        stop = min(previous_stop, close + loss)
    elif close > previous_stop:
        stop = close - loss
    else:
        stop = close + loss
    state["stop"] = stop
    if not state["ready"]:
        state["ready"] = True
        return False, False
    return float(previous_close) <= previous_stop and close > stop, float(previous_close) >= previous_stop and close < stop


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.ut_v2_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(600)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    multiplier = float(context.params.get("multiplier", 1.0))
    atr_period = int(context.params.get("atr_period", 10))
    target_pct = float(context.params.get("target_pct", 0.95))
    if multiplier <= 0 or atr_period < 1:
        return
    signature = [multiplier, atr_period]
    state = dict(g.ut_v2_state or {})
    if not state or list(state.get("signature") or []) != signature:
        required = max(20 * atr_period, 300)
        bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(signature)
        buy_signal = False
        sell_signal = False
        for index in range(len(bars)):
            buy_signal, sell_signal = _step(
                state,
                float(bars["high"].iloc[index]),
                float(bars["low"].iloc[index]),
                float(bars["close"].iloc[index]),
                atr_period,
                multiplier,
            )
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        buy_signal, sell_signal = _step(
            state,
            float(bars["high"].iloc[-1]),
            float(bars["low"].iloc[-1]),
            float(bars["close"].iloc[-1]),
            atr_period,
            multiplier,
        )
    g.ut_v2_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy_signal and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="ut_v2_long")
    elif sell_signal and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="ut_v2_short")
