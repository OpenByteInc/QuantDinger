"""Countertrend-release strategy combining stochastic and RSI states."""

# @param rsi_length int 5 range=2:100:1
# @param stochastic_length int 60 range=2:500:1
# @param stochastic_smoothing int 13 range=1:100:1
# @param pullback_window int 15 range=2:200:1
# @param pullback_threshold float 35 range=1:49:1
# @param trend_threshold float 50 range=1:99:1
# @param exit_when_reason_ends bool false
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
        "previous_source": None,
        "gain": {"value": None, "seed": []},
        "loss": {"value": None, "seed": []},
        "rsi_values": [],
        "highs": [],
        "lows": [],
        "stochastic_values": [],
    }


def _step(state, high, low, source, settings):
    rsi_length, stochastic_length, stochastic_smoothing, pullback_window, pullback_threshold, trend_threshold = settings
    previous_source = state["previous_source"]
    state["previous_source"] = source
    rsi = float("nan")
    if previous_source is not None:
        change = source - float(previous_source)
        gain = _rma_step(state["gain"], max(change, 0.0), rsi_length)
        loss = _rma_step(state["loss"], max(-change, 0.0), rsi_length)
        if gain == gain and loss == loss:
            rsi = 100.0 if loss <= 1e-15 else 100.0 - 100.0 / (1.0 + gain / loss)
    state["highs"].append(high)
    state["lows"].append(low)
    if len(state["highs"]) > stochastic_length:
        state["highs"].pop(0)
        state["lows"].pop(0)
    raw_stochastic = float("nan")
    if len(state["highs"]) == stochastic_length:
        highest = max(state["highs"])
        lowest = min(state["lows"])
        if highest > lowest:
            raw_stochastic = 100.0 * (source - lowest) / (highest - lowest)
    stochastic = _sma_step(state["stochastic_values"], raw_stochastic, stochastic_smoothing)
    if rsi == rsi:
        state["rsi_values"].append(rsi)
        if len(state["rsi_values"]) > pullback_window:
            state["rsi_values"].pop(0)
    if stochastic != stochastic or rsi != rsi or len(state["rsi_values"]) < pullback_window:
        return False, False
    long_score = min(
        stochastic - 50.0,
        rsi - trend_threshold,
        pullback_threshold - min(state["rsi_values"]),
    )
    short_score = max(
        stochastic - 50.0,
        rsi - trend_threshold,
        100.0 - pullback_threshold - max(state["rsi_values"]),
    )
    return long_score > 0.0, short_score < 0.0


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.countertrend_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low"])
    context.set_warmup(700)
    context.set_metadata(direction_mode="one_way", strategy_family="reversal")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    settings = [
        int(context.params.get("rsi_length", 5)),
        int(context.params.get("stochastic_length", 60)),
        int(context.params.get("stochastic_smoothing", 13)),
        int(context.params.get("pullback_window", 15)),
        float(context.params.get("pullback_threshold", 35.0)),
        float(context.params.get("trend_threshold", 50.0)),
    ]
    exit_when_reason_ends = bool(context.params.get("exit_when_reason_ends", False))
    target_pct = float(context.params.get("target_pct", 0.95))
    if min(settings[0], settings[1], settings[2], settings[3]) < 1:
        return
    state = dict(g.countertrend_state or {})
    if not state or list(state.get("signature") or []) != settings:
        required = max(6 * settings[1], 360)
        bars = get_history(required, "1h", ["high", "low"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(settings)
        long_signal = False
        short_signal = False
        for index in range(len(bars)):
            source = (float(bars["high"].iloc[index]) + float(bars["low"].iloc[index])) / 2.0
            long_signal, short_signal = _step(
                state,
                float(bars["high"].iloc[index]),
                float(bars["low"].iloc[index]),
                source,
                settings,
            )
    else:
        bars = get_history(1, "1h", ["high", "low"], g.symbol)
        if len(bars) < 1:
            return
        high = float(bars["high"].iloc[-1])
        low = float(bars["low"].iloc[-1])
        long_signal, short_signal = _step(state, high, low, (high + low) / 2.0, settings)
    g.countertrend_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_signal and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="countertrend_long")
    elif short_signal and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="countertrend_short")
    elif exit_when_reason_ends and amount > 0 and not long_signal:
        order_target_percent(g.symbol, 0.0, reason="countertrend_long_exit")
    elif exit_when_reason_ends and amount < 0 and not short_signal:
        order_target_percent(g.symbol, 0.0, reason="countertrend_short_exit")
