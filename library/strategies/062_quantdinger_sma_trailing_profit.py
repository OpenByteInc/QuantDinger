"""SMA crossover strategy with delayed trailing-profit exits."""

# @param fast_length int 20 range=2:300:1
# @param slow_length int 50 range=3:600:1
# @param trigger_pct float 0.03 range=0:0.5:0.005
# @param trail_pct float 0.01 range=0.001:0.2:0.001
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "closes": [], "previous_fast": None, "previous_slow": None, "entry_reference": None, "peak": None, "trough": None, "triggered": False}


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
    return previous_fast is not None and previous_slow is not None and float(previous_fast) <= float(previous_slow) and fast > slow, previous_fast is not None and previous_slow is not None and float(previous_fast) >= float(previous_slow) and fast < slow


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.trailing_profit_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    fast_length = int(context.params.get("fast_length", 20))
    slow_length = int(context.params.get("slow_length", 50))
    trigger_pct = float(context.params.get("trigger_pct", 0.03))
    trail_pct = float(context.params.get("trail_pct", 0.01))
    target_pct = float(context.params.get("target_pct", 0.95))
    if fast_length < 2 or slow_length <= fast_length or min(trigger_pct, trail_pct) < 0:
        return
    signature = [fast_length, slow_length, trigger_pct, trail_pct]
    state = dict(g.trailing_profit_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(max(300, slow_length * 6), "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < max(300, slow_length * 6):
            return
        state = _new_state(signature)
        go_long = False
        go_short = False
        for index in range(len(bars)):
            go_long, go_short = _step(state, float(bars["close"].iloc[index]), fast_length, slow_length)
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        go_long, go_short = _step(state, float(bars["close"].iloc[-1]), fast_length, slow_length)
    close = float(bars["close"].iloc[-1])
    high = float(bars["high"].iloc[-1])
    low = float(bars["low"].iloc[-1])
    amount = float(get_position(g.symbol).amount or 0.0)
    if go_long and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="sma_trailing_long")
        state["entry_reference"] = close
        state["peak"] = high
        state["trough"] = None
        state["triggered"] = False
    elif go_short and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="sma_trailing_short")
        state["entry_reference"] = close
        state["trough"] = low
        state["peak"] = None
        state["triggered"] = False
    elif amount > 0:
        if state.get("entry_reference") is None:
            state["entry_reference"] = close
        state["peak"] = high if state.get("peak") is None else max(high, float(state["peak"]))
        state["triggered"] = bool(state.get("triggered")) or float(state["peak"]) >= float(state["entry_reference"]) * (1.0 + trigger_pct)
        if state["triggered"] and close <= float(state["peak"]) * (1.0 - trail_pct):
            order_target_percent(g.symbol, 0.0, reason="long_trailing_profit")
            state["entry_reference"] = None
    elif amount < 0:
        if state.get("entry_reference") is None:
            state["entry_reference"] = close
        state["trough"] = low if state.get("trough") is None else min(low, float(state["trough"]))
        state["triggered"] = bool(state.get("triggered")) or float(state["trough"]) <= float(state["entry_reference"]) * (1.0 - trigger_pct)
        if state["triggered"] and close >= float(state["trough"]) * (1.0 + trail_pct):
            order_target_percent(g.symbol, 0.0, reason="short_trailing_profit")
            state["entry_reference"] = None
    g.trailing_profit_state = state
