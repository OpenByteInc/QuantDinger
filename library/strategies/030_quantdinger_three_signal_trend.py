"""Directional trend strategy requiring agreement from two of three signals."""

# @param average_length int 50 range=2:300:1
# @param roc_length int 1 range=1:50:1
# @param roc_average_length int 10 range=1:100:1
# @param stochastic_length int 14 range=2:200:1
# @param stochastic_smoothing int 3 range=1:50:1
# @param overbought float 80 range=51:99:1
# @param oversold float 20 range=1:49:1
# @param macd_fast int 12 range=1:200:1
# @param macd_slow int 26 range=2:300:1
# @param macd_signal int 9 range=1:100:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema_step(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


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
        "prices": [],
        "average_values": [],
        "roc_values": [],
        "highs": [],
        "lows": [],
        "stochastic_values": [],
        "fast_ema": None,
        "slow_ema": None,
        "macd_average": None,
    }


def _step(state, high, low, close, settings):
    average_length, roc_length, roc_average_length, stochastic_length, stochastic_smoothing, overbought, oversold, macd_fast, macd_slow, macd_signal = settings
    average = _sma_step(state["prices"], close, average_length)
    state["average_values"].append(float(average))
    if len(state["average_values"]) > roc_length + 1:
        state["average_values"].pop(0)
    roc = float("nan")
    if len(state["average_values"]) > roc_length:
        prior = float(state["average_values"][0])
        if average == average and prior == prior and abs(prior) > 1e-15:
            roc = 100.0 * (float(average) - prior) / abs(prior)
    average_roc = _sma_step(state["roc_values"], roc, roc_average_length)
    state["highs"].append(float(high))
    state["lows"].append(float(low))
    if len(state["highs"]) > stochastic_length:
        state["highs"].pop(0)
        state["lows"].pop(0)
    raw_stochastic = float("nan")
    if len(state["highs"]) == stochastic_length:
        highest = max(state["highs"])
        lowest = min(state["lows"])
        if highest > lowest:
            raw_stochastic = 100.0 * (float(close) - lowest) / (highest - lowest)
    stochastic = _sma_step(state["stochastic_values"], raw_stochastic, stochastic_smoothing)
    state["fast_ema"] = _ema_step(state["fast_ema"], close, macd_fast)
    state["slow_ema"] = _ema_step(state["slow_ema"], close, macd_slow)
    macd = float(state["fast_ema"]) - float(state["slow_ema"])
    previous_macd_average = state["macd_average"]
    state["macd_average"] = _ema_step(previous_macd_average, macd, macd_signal)
    speed_signal = 1 if average_roc == average_roc and average_roc > 0 else -1 if average_roc == average_roc and average_roc < 0 else 0
    stochastic_signal = 1 if stochastic == stochastic and stochastic <= oversold else -1 if stochastic == stochastic and stochastic >= overbought else 0
    macd_direction = 0
    if previous_macd_average is not None:
        macd_direction = 1 if float(state["macd_average"]) > float(previous_macd_average) else -1 if float(state["macd_average"]) < float(previous_macd_average) else 0
    long_count = int(speed_signal == 1) + int(stochastic_signal == 1) + int(macd_direction == 1)
    short_count = int(speed_signal == -1) + int(stochastic_signal == -1) + int(macd_direction == -1)
    return long_count, short_count


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.signal_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(600)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    settings = [
        int(context.params.get("average_length", 50)),
        int(context.params.get("roc_length", 1)),
        int(context.params.get("roc_average_length", 10)),
        int(context.params.get("stochastic_length", 14)),
        int(context.params.get("stochastic_smoothing", 3)),
        float(context.params.get("overbought", 80.0)),
        float(context.params.get("oversold", 20.0)),
        int(context.params.get("macd_fast", 12)),
        int(context.params.get("macd_slow", 26)),
        int(context.params.get("macd_signal", 9)),
    ]
    target_pct = float(context.params.get("target_pct", 0.95))
    if min(settings[0], settings[1], settings[2], settings[3], settings[4], settings[7], settings[8], settings[9]) < 1:
        return
    state = dict(g.signal_state or {})
    if not state or list(state.get("signature") or []) != settings:
        required = max(6 * max(settings[0], settings[3], settings[8]), 300)
        bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(settings)
        long_count = 0
        short_count = 0
        for index in range(len(bars)):
            long_count, short_count = _step(
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
        long_count, short_count = _step(
            state,
            float(bars["high"].iloc[-1]),
            float(bars["low"].iloc[-1]),
            float(bars["close"].iloc[-1]),
            settings,
        )
    g.signal_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_count >= 2 and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="three_signal_long")
    elif short_count >= 2 and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="three_signal_short")
