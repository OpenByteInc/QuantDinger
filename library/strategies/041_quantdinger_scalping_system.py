"""Scalping system combining moving averages, Keltner range, stochastic, and MACD."""

# @param sma_length int 25 range=1:300:1
# @param ema_length int 200 range=2:800:1
# @param keltner_length int 10 range=1:200:1
# @param keltner_multiplier float 2.0 range=0.1:20:0.1
# @param atr_length int 14 range=1:300:1
# @param stochastic_length int 10 range=2:200:1
# @param macd_fast int 4 range=1:100:1
# @param macd_slow int 34 range=2:300:1
# @param macd_signal int 5 range=1:100:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema_step(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


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


def _sma_step(buffer, value, length):
    buffer.append(float(value))
    if len(buffer) > length:
        buffer.pop(0)
    if len(buffer) < length:
        return float("nan")
    return sum(buffer) / float(length)


def _new_state(signature):
    return {
        "signature": signature,
        "sma": [],
        "ema": None,
        "keltner": [],
        "previous_close": None,
        "atr": {"value": None, "seed": []},
        "highs": [],
        "lows": [],
        "macd_fast": None,
        "macd_slow": None,
        "macd_signal": None,
    }


def _step(state, high, low, close, settings):
    sma_length, ema_length, keltner_length, keltner_multiplier, atr_length, stochastic_length, macd_fast, macd_slow, macd_signal = settings
    average = _sma_step(state["sma"], low, sma_length)
    state["ema"] = _ema_step(state["ema"], low, ema_length)
    keltner_middle = _sma_step(state["keltner"], low, keltner_length)
    previous_close = state["previous_close"]
    true_range = high - low if previous_close is None else max(
        high - low,
        abs(high - float(previous_close)),
        abs(low - float(previous_close)),
    )
    atr = _rma_step(state["atr"], true_range, atr_length)
    state["previous_close"] = close
    state["highs"].append(high)
    state["lows"].append(low)
    if len(state["highs"]) > stochastic_length:
        state["highs"].pop(0)
        state["lows"].pop(0)
    stochastic = float("nan")
    if len(state["highs"]) == stochastic_length:
        highest = max(state["highs"])
        lowest = min(state["lows"])
        if highest > lowest:
            stochastic = 100.0 * (close - lowest) / (highest - lowest)
    state["macd_fast"] = _ema_step(state["macd_fast"], low, macd_fast)
    state["macd_slow"] = _ema_step(state["macd_slow"], low, macd_slow)
    macd = float(state["macd_fast"]) - float(state["macd_slow"])
    state["macd_signal"] = _ema_step(state["macd_signal"], macd, macd_signal)
    histogram = macd - float(state["macd_signal"])
    if any(value != value for value in (average, keltner_middle, atr, stochastic)):
        return False, False
    upper = keltner_middle + atr * keltner_multiplier
    lower = keltner_middle - atr * keltner_multiplier
    long_signal = close > average and lower < close < upper and histogram < 0 and stochastic < 50 and close > float(state["ema"])
    short_signal = close < average and lower < close < upper and histogram > 0 and stochastic > 50 and close < float(state["ema"])
    return long_signal, short_signal


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.scalping_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(1200)
    context.set_metadata(direction_mode="one_way", strategy_family="scalping")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    settings = [
        int(context.params.get("sma_length", 25)),
        int(context.params.get("ema_length", 200)),
        int(context.params.get("keltner_length", 10)),
        float(context.params.get("keltner_multiplier", 2.0)),
        int(context.params.get("atr_length", 14)),
        int(context.params.get("stochastic_length", 10)),
        int(context.params.get("macd_fast", 4)),
        int(context.params.get("macd_slow", 34)),
        int(context.params.get("macd_signal", 5)),
    ]
    target_pct = float(context.params.get("target_pct", 0.95))
    if min(settings[0], settings[1], settings[2], settings[4], settings[5], settings[6], settings[7], settings[8]) < 1:
        return
    state = dict(g.scalping_state or {})
    if not state or list(state.get("signature") or []) != settings:
        required = max(5 * settings[1], 1000)
        bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(settings)
        long_signal = False
        short_signal = False
        for index in range(len(bars)):
            long_signal, short_signal = _step(
                state,
                float(bars["high"].iloc[index]),
                float(bars["low"].iloc[index]),
                float(bars["close"].iloc[index]),
                settings,
            )
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        long_signal, short_signal = _step(
            state,
            float(bars["high"].iloc[-1]),
            float(bars["low"].iloc[-1]),
            float(bars["close"].iloc[-1]),
            settings,
        )
    g.scalping_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_signal and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="scalping_system_long")
    elif short_signal and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="scalping_system_short")
