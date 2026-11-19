"""Slow EMA ribbon regime transitions with early long/short signals."""

# @param use_short bool true
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _new_state(signature):
    return {"signature": signature, "emas": [None] * 15, "previous_regime": 0, "logical_long": False, "logical_short": False}


def _step(state, close):
    lengths = list(range(24, 67, 3))
    for index, length in enumerate(lengths):
        state["emas"][index] = _ema(state["emas"][index], close, length)
    values = [float(value) for value in state["emas"]]
    regime = 1 if all(values[index] > values[index + 1] for index in range(len(values) - 1)) else -1 if all(values[index] < values[index + 1] for index in range(len(values) - 1)) else 0
    previous = int(state["previous_regime"])
    long_signal = not bool(state["logical_long"]) and ((regime == 1 and previous == 0) or (regime == 0 and previous == -1))
    short_signal = not bool(state["logical_short"]) and ((regime == 0 and previous == 1) or (regime == -1 and previous == 0))
    if long_signal:
        state["logical_long"] = True
        state["logical_short"] = False
    if short_signal:
        state["logical_long"] = False
        state["logical_short"] = True
    state["previous_regime"] = regime
    return long_signal, short_signal


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.super_guppy_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(700)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    use_short = bool(context.params.get("use_short", True))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [use_short]
    state = dict(g.super_guppy_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    bars = get_history(500 if cold else 1, "1h", "close", g.symbol)
    if len(bars) < (500 if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    long_signal = short_signal = False
    for value in bars["close"]:
        long_signal, short_signal = _step(state, float(value))
    g.super_guppy_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_signal and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="super_guppy_long")
    elif short_signal and use_short and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="super_guppy_short")
    elif short_signal and not use_short and amount > 0:
        order_target_percent(g.symbol, 0.0, reason="super_guppy_long_exit")
