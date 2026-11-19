"""RSI threshold reversal strategy."""

# @param rsi_length int 14 range=2:300:1
# @param oversold float 30 range=1:49:1
# @param overbought float 70 range=51:99:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _rma_step(state, value, length):
    current = state.get("value")
    if current is not None:
        current = (float(current) * (length - 1) + float(value)) / float(length)
        state["value"] = current
        return current
    seed = state["seed"]
    seed.append(float(value))
    if len(seed) < length:
        return float("nan")
    current = sum(seed[-length:]) / float(length)
    state["value"] = current
    state["seed"] = []
    return current


def _new_state(signature):
    return {
        "signature": signature,
        "previous_close": None,
        "gain": {"value": None, "seed": []},
        "loss": {"value": None, "seed": []},
        "rsi": float("nan"),
    }


def _step(state, close, length, oversold, overbought):
    previous_close = state["previous_close"]
    if previous_close is None:
        state["previous_close"] = close
        return False, False
    change = close - float(previous_close)
    gain = _rma_step(state["gain"], max(change, 0.0), length)
    loss = _rma_step(state["loss"], max(-change, 0.0), length)
    previous_rsi = float(state["rsi"])
    current_rsi = float("nan")
    if gain == gain and loss == loss:
        current_rsi = 100.0 if loss <= 1e-15 else 100.0 - 100.0 / (1.0 + gain / loss)
    state["previous_close"] = close
    state["rsi"] = current_rsi
    if previous_rsi != previous_rsi or current_rsi != current_rsi:
        return False, False
    return previous_rsi <= oversold and current_rsi > oversold, previous_rsi >= overbought and current_rsi < overbought


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.rsi_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(400)
    context.set_metadata(direction_mode="one_way", strategy_family="reversal")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    length = int(context.params.get("rsi_length", 14))
    oversold = float(context.params.get("oversold", 30.0))
    overbought = float(context.params.get("overbought", 70.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    if length < 2 or not 0 < oversold < overbought < 100:
        return
    signature = [length, oversold, overbought]
    state = dict(g.rsi_state or {})
    if not state or list(state.get("signature") or []) != signature:
        required = max(16 * length, 240)
        bars = get_history(required, "1h", ["close"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(signature)
        long_signal = False
        short_signal = False
        for close in bars["close"]:
            long_signal, short_signal = _step(state, float(close), length, oversold, overbought)
    else:
        bars = get_history(1, "1h", ["close"], g.symbol)
        if len(bars) < 1:
            return
        long_signal, short_signal = _step(
            state, float(bars["close"].iloc[-1]), length, oversold, overbought
        )
    g.rsi_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_signal and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="rsi_reversal_long")
    elif short_signal and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="rsi_reversal_short")
