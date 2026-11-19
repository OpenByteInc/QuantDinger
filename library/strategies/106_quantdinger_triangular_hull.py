"""Triangular Hull moving-average slope crossover strategy."""

# @param length int 40 range=3:300:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _wma(values, length):
    if length < 1 or len(values) < length:
        return None
    denominator = float(length * (length + 1) // 2)
    return sum(values[-length + index] * (index + 1) for index in range(length)) / denominator


def _new_state(signature):
    return {"signature": signature, "closes": [], "transformed": [], "thma": []}


def _step(state, close, length):
    state["closes"].append(close)
    state["closes"] = state["closes"][-length - 2:]
    first = _wma(state["closes"], max(1, length // 3))
    second = _wma(state["closes"], max(1, length // 2))
    third = _wma(state["closes"], length)
    if first is None or second is None or third is None:
        return False, False
    state["transformed"].append(3.0 * first - second - third)
    state["transformed"] = state["transformed"][-length:]
    value = _wma(state["transformed"], length)
    if value is None:
        return False, False
    state["thma"].append(value)
    state["thma"] = state["thma"][-4:]
    if len(state["thma"]) < 4:
        return False, False
    previous_diff = state["thma"][-2] - state["thma"][-4]
    current_diff = state["thma"][-1] - state["thma"][-3]
    return previous_diff <= 0 < current_diff, previous_diff >= 0 > current_diff


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.triangular_hull_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(1000)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    length = int(context.params.get("length", 40))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [length]
    state = dict(g.triangular_hull_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(500, length * 15)
    bars = get_history(required if cold else 1, "1h", "close", g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    buy = sell = False
    for value in bars["close"]:
        buy, sell = _step(state, float(value), length)
    g.triangular_hull_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="triangular_hull_long")
    elif sell and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="triangular_hull_short")
