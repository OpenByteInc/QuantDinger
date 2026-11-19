"""Three-stage noise filter that trades confirmed gradient reversals."""

# @param length int 25 range=2:300:1
# @param long_only bool true
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _new_state(signature):
    return {"signature": signature, "stage1": None, "stage2": None, "stage3": None, "history": []}


def _step(state, close, length):
    state["stage1"] = _ema(state["stage1"], close, length)
    state["stage2"] = _ema(state["stage2"], state["stage1"], length)
    state["stage3"] = _ema(state["stage3"], state["stage2"], length)
    state["history"].append(float(state["stage3"]))
    if len(state["history"]) > 4:
        state["history"].pop(0)
    if len(state["history"]) < 4:
        return False, False
    previous_diff = state["history"][-2] - state["history"][-4]
    diff = state["history"][-1] - state["history"][-3]
    return previous_diff <= 0 < diff, previous_diff >= 0 > diff


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.gradient_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(700)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    length = int(context.params.get("length", 25))
    long_only = bool(context.params.get("long_only", True))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [length]
    state = dict(g.gradient_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(max(300, length * 12), "1h", "close", g.symbol)
        if len(bars) < max(300, length * 12):
            return
        state = _new_state(signature)
        up = False
        down = False
        for value in bars["close"]:
            up, down = _step(state, float(value), length)
    else:
        bars = get_history(1, "1h", "close", g.symbol)
        if len(bars) < 1:
            return
        up, down = _step(state, float(bars["close"].iloc[-1]), length)
    g.gradient_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if up and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="gradient_long")
    elif down and long_only and amount > 0:
        order_target_percent(g.symbol, 0.0, reason="gradient_long_exit")
    elif down and not long_only and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="gradient_short")
