"""Variable-adaptive moving average crossing an optimized trend tracker."""

# @param ma_length int 2 range=1:100:1
# @param ott_percent float 1.4 range=0.1:20:0.1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "previous_close": None, "ups": [], "downs": [], "ma": None, "previous_ma": None, "long_stop": None, "short_stop": None, "direction": 1, "ott": []}


def _step(state, close, ma_length, ott_percent):
    previous_close = state["previous_close"]
    change = 0.0 if previous_close is None else close - float(previous_close)
    state["ups"].append(max(change, 0.0))
    state["downs"].append(max(-change, 0.0))
    state["ups"] = state["ups"][-9:]
    state["downs"] = state["downs"][-9:]
    total_up = sum(state["ups"])
    total_down = sum(state["downs"])
    cmo = 0.0 if total_up + total_down <= 1e-15 else (total_up - total_down) / (total_up + total_down)
    alpha = 2.0 / (float(ma_length) + 1.0) * abs(cmo)
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
    state["ott"].append(ott)
    state["ott"] = state["ott"][-6:]
    buy = sell = False
    if previous_ma is not None and len(state["ott"]) >= 4:
        previous_reference = state["ott"][-4]
        current_reference = state["ott"][-3]
        buy = float(previous_ma) <= previous_reference and ma > current_reference
        sell = float(previous_ma) >= previous_reference and ma < current_reference
    state["previous_close"] = close
    state["previous_ma"] = previous_ma
    state["ma"] = ma
    state["long_stop"] = long_stop
    state["short_stop"] = short_stop
    state["direction"] = direction
    return buy, sell


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.ott_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    ma_length = int(context.params.get("ma_length", 2))
    ott_percent = float(context.params.get("ott_percent", 1.4))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [ma_length, ott_percent]
    state = dict(g.ott_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    bars = get_history(320 if cold else 1, "1h", "close", g.symbol)
    if len(bars) < (320 if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    buy = sell = False
    for value in bars["close"]:
        buy, sell = _step(state, float(value), ma_length, ott_percent)
    g.ott_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="ott_long")
    elif sell and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="ott_short")
