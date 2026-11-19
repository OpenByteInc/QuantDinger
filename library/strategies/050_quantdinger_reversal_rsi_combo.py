"""123 reversal signal confirmed by a persistent RSI regime."""

# @param stochastic_length int 14 range=2:300:1
# @param stochastic_smoothing int 1 range=1:100:1
# @param stochastic_signal int 3 range=1:100:1
# @param stochastic_level float 50 range=1:99:1
# @param rsi_length int 12 range=2:300:1
# @param oversold float 30 range=1:49:1
# @param overbought float 70 range=51:99:1
# @param reverse bool false
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
        "closes": [],
        "highs": [],
        "lows": [],
        "fast_stochastic": [],
        "previous_source": None,
        "rsi_gain": {"value": None, "seed": []},
        "rsi_loss": {"value": None, "seed": []},
        "reversal_position": 0,
        "rsi_position": 0,
    }


def _step(state, high, low, close, settings):
    stochastic_length, stochastic_smoothing, stochastic_signal, stochastic_level, rsi_length, oversold, overbought = settings
    state["closes"].append(close)
    state["highs"].append(high)
    state["lows"].append(low)
    if len(state["closes"]) > max(stochastic_length, 3):
        state["closes"].pop(0)
    if len(state["highs"]) > stochastic_length:
        state["highs"].pop(0)
        state["lows"].pop(0)
    raw_stochastic = float("nan")
    if len(state["highs"]) == stochastic_length:
        highest = max(state["highs"])
        lowest = min(state["lows"])
        if highest > lowest:
            raw_stochastic = 100.0 * (close - lowest) / (highest - lowest)
    fast = _sma_step(state["fast_stochastic"], raw_stochastic, stochastic_smoothing)
    slow_buffer = state.setdefault("slow_stochastic", [])
    slow = _sma_step(slow_buffer, fast, stochastic_signal)
    if len(state["closes"]) >= 3 and fast == fast and slow == slow:
        two_ago = float(state["closes"][-3])
        previous = float(state["closes"][-2])
        if two_ago < previous and close > previous and fast < slow and fast > stochastic_level:
            state["reversal_position"] = 1
        elif two_ago > previous and close < previous and fast > slow and fast < stochastic_level:
            state["reversal_position"] = -1
    previous_source = state["previous_source"]
    state["previous_source"] = close
    if previous_source is not None:
        change = close - float(previous_source)
        gain = _rma_step(state["rsi_gain"], max(change, 0.0), rsi_length)
        loss = _rma_step(state["rsi_loss"], max(-change, 0.0), rsi_length)
        if gain == gain and loss == loss:
            rsi = 100.0 if loss <= 1e-15 else 100.0 - 100.0 / (1.0 + gain / loss)
            if rsi > overbought:
                state["rsi_position"] = 1
            elif rsi < oversold:
                state["rsi_position"] = -1
    if int(state["reversal_position"]) == int(state["rsi_position"]):
        return int(state["reversal_position"])
    return 0


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.combo_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(700)
    context.set_metadata(direction_mode="one_way", strategy_family="reversal")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    settings = [
        int(context.params.get("stochastic_length", 14)),
        int(context.params.get("stochastic_smoothing", 1)),
        int(context.params.get("stochastic_signal", 3)),
        float(context.params.get("stochastic_level", 50.0)),
        int(context.params.get("rsi_length", 12)),
        float(context.params.get("oversold", 30.0)),
        float(context.params.get("overbought", 70.0)),
    ]
    reverse = bool(context.params.get("reverse", False))
    target_pct = float(context.params.get("target_pct", 0.95))
    if min(settings[0], settings[1], settings[2], settings[4]) < 1:
        return
    state = dict(g.combo_state or {})
    if not state or list(state.get("signature") or []) != settings:
        required = max(20 * max(settings[0], settings[4]), 400)
        bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(settings)
        position_signal = 0
        for index in range(len(bars)):
            position_signal = _step(
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
        position_signal = _step(
            state,
            float(bars["high"].iloc[-1]),
            float(bars["low"].iloc[-1]),
            float(bars["close"].iloc[-1]),
            settings,
        )
    g.combo_state = state
    position_signal = -position_signal if reverse else position_signal
    amount = float(get_position(g.symbol).amount or 0.0)
    if position_signal > 0 and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="reversal_rsi_long")
    elif position_signal < 0 and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="reversal_rsi_short")
    elif position_signal == 0 and amount != 0:
        order_target_percent(g.symbol, 0.0, reason="reversal_rsi_flat")
