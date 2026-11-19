"""Long-only moving-average crossover scalper."""

# @param signal_length int 9 range=1:100:1
# @param fast_length int 50 range=2:300:1
# @param middle_length int 100 range=2:500:1
# @param slow_length int 200 range=2:800:1
# @param target_pct float 0.30 range=0.05:1:0.05


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
        "signal": [],
        "fast": [],
        "middle": [],
        "slow": [],
        "previous_signal": float("nan"),
        "previous_fast": float("nan"),
        "previous_slow": float("nan"),
    }


def _step(state, close, signal_length, fast_length, middle_length, slow_length):
    signal = _sma_step(state["signal"], close, signal_length)
    fast = _sma_step(state["fast"], close, fast_length)
    middle = _sma_step(state["middle"], close, middle_length)
    slow = _sma_step(state["slow"], close, slow_length)
    previous_signal = float(state["previous_signal"])
    previous_fast = float(state["previous_fast"])
    previous_slow = float(state["previous_slow"])
    state["previous_signal"] = signal
    state["previous_fast"] = fast
    state["previous_slow"] = slow
    if not all(value == value for value in (previous_signal, previous_fast, previous_slow, signal, fast, middle, slow)):
        return False, False
    enter = previous_signal <= previous_fast and signal > fast and fast < middle < slow
    exit_signal = previous_signal <= previous_slow and signal > slow
    return enter, exit_signal


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.scalper_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(1200)
    context.set_metadata(direction_mode="long_only", strategy_family="trend")


def handle_data(context, data):
    signal_length = int(context.params.get("signal_length", 9))
    fast_length = int(context.params.get("fast_length", 50))
    middle_length = int(context.params.get("middle_length", 100))
    slow_length = int(context.params.get("slow_length", 200))
    target_pct = float(context.params.get("target_pct", 0.30))
    signature = [signal_length, fast_length, middle_length, slow_length]
    if min(signature) < 1:
        return
    state = dict(g.scalper_state or {})
    if not state or list(state.get("signature") or []) != signature:
        required = max(5 * slow_length, 1000)
        bars = get_history(required, "1h", ["close"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(signature)
        enter = False
        exit_signal = False
        for close in bars["close"]:
            enter, exit_signal = _step(
                state, float(close), signal_length, fast_length, middle_length, slow_length
            )
    else:
        bars = get_history(1, "1h", ["close"], g.symbol)
        if len(bars) < 1:
            return
        enter, exit_signal = _step(
            state,
            float(bars["close"].iloc[-1]),
            signal_length,
            fast_length,
            middle_length,
            slow_length,
        )
    g.scalper_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if enter and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="ma_scalper_long")
    elif exit_signal and amount > 0:
        order_target_percent(g.symbol, 0.0, reason="ma_scalper_exit")
