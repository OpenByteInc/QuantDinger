"""Completed-bar Renko emulation with direction-pattern reversals."""

# @param brick_size float 3 range=0.1:10000:0.1
# @param history_step int 3 range=1:20:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "renko": None, "directions": [], "levels": []}


def _step(state, close, brick_size, history_step):
    if state["renko"] is None:
        state["renko"] = close
        state["directions"].append(0)
        state["levels"].append(close)
        return False, False
    difference = close - float(state["renko"])
    direction = state["directions"][-1] if state["directions"] else 0
    if abs(difference) >= brick_size:
        bricks = int(abs(difference) // brick_size)
        direction = 1 if difference > 0 else -1
        state["renko"] = float(state["renko"]) + bricks * brick_size * direction
    state["directions"].append(direction)
    state["levels"].append(float(state["renko"]))
    keep = history_step * 4 + 5
    state["directions"] = state["directions"][-keep:]
    state["levels"] = state["levels"][-keep:]
    if abs(difference) < brick_size or len(state["levels"]) <= history_step * 3:
        return False, False
    dirs = state["directions"]
    levels = state["levels"]
    long_signal = dirs[-1 - history_step] < dirs[-1] and levels[-1 - history_step] < levels[-1 - history_step * 2] and levels[-1] < levels[-1 - history_step] and levels[-1 - history_step * 2] < levels[-1 - history_step * 3]
    short_signal = dirs[-1 - history_step] > dirs[-1] and levels[-1 - history_step] > levels[-1 - history_step * 2] and levels[-1] > levels[-1 - history_step] and levels[-1 - history_step * 2] > levels[-1 - history_step * 3]
    return long_signal, short_signal


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.renko_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="price_action")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    brick_size = float(context.params.get("brick_size", 3.0))
    history_step = int(context.params.get("history_step", 3))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [brick_size, history_step]
    state = dict(g.renko_state or {})
    required = max(300, history_step * 20)
    cold = not state or list(state.get("signature") or []) != signature
    bars = get_history(required if cold else 1, "1h", "close", g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    long_signal = short_signal = False
    for value in bars["close"]:
        long_signal, short_signal = _step(state, float(value), brick_size, history_step)
    g.renko_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_signal and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="renko_long")
    elif short_signal and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="renko_short")
