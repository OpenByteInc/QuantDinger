"""UT-style ATR trailing-stop reversal strategy for the requested backtest range."""

# @param key_value float 1.0 range=0.1:20:0.1
# @param atr_period int 10 range=1:500:1
# @param use_heikin_ashi bool false
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


def _trail(previous_stop, previous_source, source, loss):
    if source > previous_stop and previous_source > previous_stop:
        return max(previous_stop, source - loss)
    if source < previous_stop and previous_source < previous_stop:
        return min(previous_stop, source + loss)
    if source > previous_stop:
        return source - loss
    return source + loss


def _new_state(signature):
    return {
        "signature": signature,
        "previous_close": None,
        "source": None,
        "stop": 0.0,
        "atr": {"value": None, "seed": []},
        "ready": False,
    }


def _step(state, open_price, high, low, close, atr_period, key_value, use_heikin_ashi):
    previous_close = state["previous_close"]
    true_range = high - low if previous_close is None else max(
        high - low,
        abs(high - float(previous_close)),
        abs(low - float(previous_close)),
    )
    atr = _rma_step(state["atr"], true_range, atr_period)
    source = (open_price + high + low + close) / 4.0 if use_heikin_ashi else close
    previous_source = state["source"]
    previous_stop = float(state["stop"])
    state["previous_close"] = close
    state["source"] = source
    if atr != atr or previous_source is None:
        return False, False
    current_stop = _trail(previous_stop, float(previous_source), source, key_value * atr)
    state["stop"] = current_stop
    if not state["ready"]:
        state["ready"] = True
        return False, False
    return float(previous_source) <= previous_stop and source > current_stop, float(previous_source) >= previous_stop and source < current_stop


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.ut_range_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["open", "high", "low", "close"])
    context.set_warmup(600)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    key_value = float(context.params.get("key_value", 1.0))
    atr_period = int(context.params.get("atr_period", 10))
    use_heikin_ashi = bool(context.params.get("use_heikin_ashi", False))
    target_pct = float(context.params.get("target_pct", 0.95))
    if key_value <= 0 or atr_period < 1:
        return
    signature = [key_value, atr_period, use_heikin_ashi]
    state = dict(g.ut_range_state or {})
    fields = ["open", "high", "low", "close"]
    if not state or list(state.get("signature") or []) != signature:
        required = max(20 * atr_period, 300)
        bars = get_history(required, "1h", fields, g.symbol)
        if len(bars) < required:
            return
        state = _new_state(signature)
        buy_signal = False
        sell_signal = False
        for index in range(len(bars)):
            buy_signal, sell_signal = _step(
                state,
                float(bars["open"].iloc[index]),
                float(bars["high"].iloc[index]),
                float(bars["low"].iloc[index]),
                float(bars["close"].iloc[index]),
                atr_period,
                key_value,
                use_heikin_ashi,
            )
    else:
        bars = get_history(1, "1h", fields, g.symbol)
        if len(bars) < 1:
            return
        buy_signal, sell_signal = _step(
            state,
            float(bars["open"].iloc[-1]),
            float(bars["high"].iloc[-1]),
            float(bars["low"].iloc[-1]),
            float(bars["close"].iloc[-1]),
            atr_period,
            key_value,
            use_heikin_ashi,
        )
    g.ut_range_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy_signal and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="ut_range_long")
    elif sell_signal and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="ut_range_short")
