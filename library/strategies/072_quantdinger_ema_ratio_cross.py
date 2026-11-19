"""Fast and ratio-scaled slow EMA crossover strategy."""

# @param length int 20 range=1:300:1
# @param ratio int 3 range=3:10:1
# @param long_only bool false
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _new_state(signature):
    return {"signature": signature, "fast": None, "slow": None}


def _step(state, high, low, length, ratio):
    source = (high + low) / 2.0
    previous_fast = state["fast"]
    previous_slow = state["slow"]
    state["fast"] = _ema(state["fast"], source, length)
    state["slow"] = _ema(state["slow"], source, length * ratio)
    if previous_fast is None or previous_slow is None:
        return False, False
    return float(previous_fast) <= float(previous_slow) and float(state["fast"]) > float(state["slow"]), float(previous_fast) >= float(previous_slow) and float(state["fast"]) < float(state["slow"])


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.ema_ratio_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low"])
    context.set_warmup(700)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    length = int(context.params.get("length", 20))
    ratio = int(context.params.get("ratio", 3))
    long_only = bool(context.params.get("long_only", False))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [length, ratio]
    state = dict(g.ema_ratio_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(max(400, length * ratio * 6), "1h", ["high", "low"], g.symbol)
        if len(bars) < max(400, length * ratio * 6):
            return
        state = _new_state(signature)
        go_long = False
        go_short = False
        for index in range(len(bars)):
            go_long, go_short = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), length, ratio)
    else:
        bars = get_history(1, "1h", ["high", "low"], g.symbol)
        if len(bars) < 1:
            return
        go_long, go_short = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), length, ratio)
    g.ema_ratio_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if go_long and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="ema_ratio_long")
    elif go_short and long_only and amount > 0:
        order_target_percent(g.symbol, 0.0, reason="ema_ratio_long_exit")
    elif go_short and not long_only and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="ema_ratio_short")
