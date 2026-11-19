"""Long-only fast/slow SMA crossover with configurable active windows."""

# @param fast_length int 14 range=1:300:1
# @param slow_length int 28 range=2:600:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "closes": [], "previous_fast": None, "previous_slow": None}


def _step(state, close, fast_length, slow_length):
    state["closes"].append(close)
    if len(state["closes"]) > slow_length:
        state["closes"].pop(0)
    if len(state["closes"]) < slow_length:
        return False, False
    fast = sum(state["closes"][-fast_length:]) / float(fast_length)
    slow = sum(state["closes"][-slow_length:]) / float(slow_length)
    previous_fast = state["previous_fast"]
    previous_slow = state["previous_slow"]
    state["previous_fast"] = fast
    state["previous_slow"] = slow
    if previous_fast is None or previous_slow is None:
        return False, False
    return float(previous_fast) <= float(previous_slow) and fast > slow, float(previous_fast) >= float(previous_slow) and fast < slow


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.timed_sma_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(400)
    context.set_metadata(direction_mode="long_only", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    fast_length = int(context.params.get("fast_length", 14))
    slow_length = int(context.params.get("slow_length", 28))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [fast_length, slow_length]
    state = dict(g.timed_sma_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(max(240, slow_length * 8), "1h", "close", g.symbol)
        if len(bars) < max(240, slow_length * 8):
            return
        state = _new_state(signature)
        enter = False
        exit_signal = False
        for value in bars["close"]:
            enter, exit_signal = _step(state, float(value), fast_length, slow_length)
    else:
        bars = get_history(1, "1h", "close", g.symbol)
        if len(bars) < 1:
            return
        enter, exit_signal = _step(state, float(bars["close"].iloc[-1]), fast_length, slow_length)
    g.timed_sma_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if enter and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="timed_sma_long")
    elif exit_signal and amount > 0:
        order_target_percent(g.symbol, 0.0, reason="timed_sma_exit")
