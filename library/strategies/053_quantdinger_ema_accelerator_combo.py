"""EMA state confirmed by the Accelerator Oscillator."""

# @param ema_length int 14 range=1:300:1
# @param oscillator_slow int 33 range=2:300:1
# @param oscillator_fast int 6 range=1:100:1
# @param reverse bool false
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
        "ema": None,
        "previous_close": None,
        "previous_high": None,
        "previous_low": None,
        "fast_hl2": [],
        "slow_hl2": [],
        "oscillator": [],
        "ema_position": 0,
        "oscillator_position": 0,
    }


def _step(state, high, low, close, ema_length, slow_length, fast_length):
    state["ema"] = _ema_step(state["ema"], close, ema_length)
    previous_high = state["previous_high"]
    previous_low = state["previous_low"]
    previous_close = state["previous_close"]
    if previous_high is not None and previous_low is not None and previous_close is not None:
        two_bar_high = max(high, float(previous_high))
        two_bar_low = min(low, float(previous_low))
        selector = two_bar_low if two_bar_low > float(state["ema"]) or two_bar_high < float(state["ema"]) else two_bar_high
        if selector < float(previous_close):
            state["ema_position"] = 1
        elif selector > float(previous_close):
            state["ema_position"] = -1
    state["previous_high"] = high
    state["previous_low"] = low
    state["previous_close"] = close
    hl2 = (high + low) / 2.0
    fast_average = _sma_step(state["fast_hl2"], hl2, fast_length)
    slow_average = _sma_step(state["slow_hl2"], hl2, slow_length)
    oscillator = fast_average - slow_average
    oscillator_average = _sma_step(state["oscillator"], oscillator, fast_length)
    acceleration = oscillator - oscillator_average
    if acceleration == acceleration:
        if acceleration > 0:
            state["oscillator_position"] = 1
        elif acceleration < 0:
            state["oscillator_position"] = -1
    if int(state["ema_position"]) == int(state["oscillator_position"]):
        return int(state["ema_position"])
    return 0


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.accelerator_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(700)
    context.set_metadata(direction_mode="one_way", strategy_family="momentum")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    ema_length = int(context.params.get("ema_length", 14))
    slow_length = int(context.params.get("oscillator_slow", 33))
    fast_length = int(context.params.get("oscillator_fast", 6))
    reverse = bool(context.params.get("reverse", False))
    target_pct = float(context.params.get("target_pct", 0.95))
    if min(ema_length, slow_length, fast_length) < 1:
        return
    signature = [ema_length, slow_length, fast_length]
    state = dict(g.accelerator_state or {})
    if not state or list(state.get("signature") or []) != signature:
        required = max(20 * slow_length, 700)
        bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(signature)
        signal = 0
        for index in range(len(bars)):
            signal = _step(
                state,
                float(bars["high"].iloc[index]),
                float(bars["low"].iloc[index]),
                float(bars["close"].iloc[index]),
                ema_length,
                slow_length,
                fast_length,
            )
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        signal = _step(
            state,
            float(bars["high"].iloc[-1]),
            float(bars["low"].iloc[-1]),
            float(bars["close"].iloc[-1]),
            ema_length,
            slow_length,
            fast_length,
        )
    g.accelerator_state = state
    signal = -signal if reverse else signal
    amount = float(get_position(g.symbol).amount or 0.0)
    if signal > 0 and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="ema_accelerator_long")
    elif signal < 0 and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="ema_accelerator_short")
    elif signal == 0 and amount != 0:
        order_target_percent(g.symbol, 0.0, reason="ema_accelerator_flat")
