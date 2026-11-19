"""
QuantDinger UT Bot
One-way ATR trailing-stop strategy with an optional Heikin-Ashi close source.
"""

# @param key_value float 1.0 range=0.1:20:0.1
# @param atr_period int 10 range=1:500:1
# @param use_heikin_ashi bool false
# @param target_pct float 0.95 range=0.05:1:0.05


def _atr(high, low, close, length):
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def _trailing_stop(source, loss):
    stop = source.copy()
    stop[:] = float("nan")
    previous_stop = 0.0
    stop.iloc[0] = previous_stop
    for index in range(1, len(source)):
        current = float(source.iloc[index])
        previous_source = float(source.iloc[index - 1])
        current_loss = float(loss.iloc[index])
        if current_loss != current_loss:
            stop.iloc[index] = previous_stop
            continue
        if current > previous_stop and previous_source > previous_stop:
            current_stop = max(previous_stop, current - current_loss)
        elif current < previous_stop and previous_source < previous_stop:
            current_stop = min(previous_stop, current + current_loss)
        elif current > previous_stop:
            current_stop = current - current_loss
        else:
            current_stop = current + current_loss
        stop.iloc[index] = current_stop
        previous_stop = current_stop
    return stop


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.ut_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["open", "high", "low", "close"])
    context.set_warmup(900)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    key_value = float(context.params.get("key_value", 1.0))
    atr_period = int(context.params.get("atr_period", 10))
    use_heikin_ashi = bool(context.params.get("use_heikin_ashi", False))
    target_pct = float(context.params.get("target_pct", 0.95))
    if key_value <= 0 or atr_period < 1:
        return
    state = dict(g.ut_state or {})
    reset = (
        not state
        or int(state.get("atr_period") or 0) != atr_period
        or float(state.get("key_value") or 0.0) != key_value
        or bool(state.get("use_heikin_ashi")) != use_heikin_ashi
    )
    if reset:
        required = max(12 * atr_period, 240)
        bars = get_history(required, "1h", ["open", "high", "low", "close"], g.symbol)
        if len(bars) < required:
            return
        source = (
            (bars["open"] + bars["high"] + bars["low"] + bars["close"]) / 4.0
            if use_heikin_ashi
            else bars["close"]
        )
        atr = _atr(bars["high"], bars["low"], bars["close"], atr_period)
        stop = _trailing_stop(source, key_value * atr)
        previous_source = float(source.iloc[-2])
        current_source = float(source.iloc[-1])
        previous_stop = float(stop.iloc[-2])
        current_stop = float(stop.iloc[-1])
        state = {
            "atr_period": atr_period,
            "key_value": key_value,
            "use_heikin_ashi": use_heikin_ashi,
            "atr": float(atr.iloc[-1]),
            "previous_close": float(bars["close"].iloc[-1]),
            "source": current_source,
            "stop": current_stop,
        }
    else:
        bars = get_history(1, "1h", ["open", "high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        current_close = float(bars["close"].iloc[-1])
        current_high = float(bars["high"].iloc[-1])
        current_low = float(bars["low"].iloc[-1])
        current_source = (
            (
                float(bars["open"].iloc[-1])
                + current_high
                + current_low
                + current_close
            )
            / 4.0
            if use_heikin_ashi
            else current_close
        )
        previous_source = float(state["source"])
        previous_stop = float(state["stop"])
        previous_close = float(state["previous_close"])
        true_range = max(
            current_high - current_low,
            abs(current_high - previous_close),
            abs(current_low - previous_close),
        )
        atr_value = (float(state["atr"]) * (atr_period - 1) + true_range) / atr_period
        current_loss = key_value * atr_value
        if current_source > previous_stop and previous_source > previous_stop:
            current_stop = max(previous_stop, current_source - current_loss)
        elif current_source < previous_stop and previous_source < previous_stop:
            current_stop = min(previous_stop, current_source + current_loss)
        elif current_source > previous_stop:
            current_stop = current_source - current_loss
        else:
            current_stop = current_source + current_loss
        state["atr"] = atr_value
        state["previous_close"] = current_close
        state["source"] = current_source
        state["stop"] = current_stop
    g.ut_state = state
    values = (previous_source, current_source, previous_stop, current_stop)
    if not all(float(value) == float(value) for value in values):
        return
    crossed_up = previous_source <= previous_stop and current_source > current_stop
    crossed_down = previous_source >= previous_stop and current_source < current_stop
    amount = float(get_position(g.symbol).amount or 0.0)
    if crossed_up and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="ut_bot_long")
    elif crossed_down and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="ut_bot_short")
