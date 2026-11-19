"""Variable moving average crossing twin optimized trend bands."""

# @param length int 40 range=1:300:1
# @param ott_percent float 1 range=0.1:20:0.1
# @param twin_coefficient float 0.001 range=0:0.1:0.001
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "previous_close": None, "ups": [], "downs": [], "ma": None, "previous_ma": None, "long_stop": None, "short_stop": None, "direction": 1, "upper": [], "lower": []}


def _step(state, close, length, ott_percent, coefficient):
    previous_close = state["previous_close"]
    change = 0.0 if previous_close is None else close - float(previous_close)
    state["ups"].append(max(change, 0.0))
    state["downs"].append(max(-change, 0.0))
    state["ups"] = state["ups"][-9:]
    state["downs"] = state["downs"][-9:]
    total_up = sum(state["ups"])
    total_down = sum(state["downs"])
    cmo = 0.0 if total_up + total_down <= 1e-15 else (total_up - total_down) / (total_up + total_down)
    alpha = 2.0 / (float(length) + 1.0) * abs(cmo)
    previous_ma = state["ma"]
    ma = close if previous_ma is None else alpha * close + (1.0 - alpha) * float(previous_ma)
    width = ma * ott_percent * 0.01
    raw_long = ma - width
    raw_short = ma + width
    old_long = raw_long if state["long_stop"] is None else float(state["long_stop"])
    old_short = raw_short if state["short_stop"] is None else float(state["short_stop"])
    long_stop = max(raw_long, old_long) if ma > old_long else raw_long
    short_stop = min(raw_short, old_short) if ma < old_short else raw_short
    direction = int(state["direction"])
    if direction == -1 and ma > old_short:
        direction = 1
    elif direction == 1 and ma < old_long:
        direction = -1
    tracker = long_stop if direction == 1 else short_stop
    ott = tracker * (200.0 + ott_percent) / 200.0 if ma > tracker else tracker * (200.0 - ott_percent) / 200.0
    state["upper"].append(ott * (1.0 + coefficient))
    state["lower"].append(ott * (1.0 - coefficient))
    state["upper"] = state["upper"][-6:]
    state["lower"] = state["lower"][-6:]
    buy = sell = False
    if previous_ma is not None and len(state["upper"]) >= 4:
        buy = float(previous_ma) <= state["upper"][-4] and ma > state["upper"][-3]
        sell = float(previous_ma) >= state["lower"][-4] and ma < state["lower"][-3]
    state["previous_close"] = close
    state["previous_ma"] = ma
    state["ma"] = ma
    state["long_stop"] = long_stop
    state["short_stop"] = short_stop
    state["direction"] = direction
    return buy, sell


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.twin_ott_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(700)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    length = int(context.params.get("length", 40))
    ott_percent = float(context.params.get("ott_percent", 1.0))
    coefficient = float(context.params.get("twin_coefficient", 0.001))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [length, ott_percent, coefficient]
    state = dict(g.twin_ott_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(400, length * 12)
    bars = get_history(required if cold else 1, "1h", "close", g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    buy = sell = False
    for value in bars["close"]:
        buy, sell = _step(state, float(value), length, ott_percent, coefficient)
    g.twin_ott_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="twin_ott_long")
    elif sell and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="twin_ott_short")
