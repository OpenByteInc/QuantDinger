"""Smoothed stochastic oscillator crossing an optimized trend tracker."""

# @param stochastic_length int 250 range=10:500:1
# @param smoothing_length int 50 range=1:200:1
# @param ott_length int 3 range=1:100:1
# @param ott_percent float 0.618 range=0.05:10:0.01
# @param target_pct float 0.95 range=0.05:1:0.05


def _adaptive(previous, value, previous_value, ups, downs, length):
    change = 0.0 if previous_value is None else value - previous_value
    ups.append(max(change, 0.0))
    downs.append(max(-change, 0.0))
    del ups[:-9]
    del downs[:-9]
    total_up = sum(ups)
    total_down = sum(downs)
    cmo = 0.0 if total_up + total_down <= 1e-15 else (total_up - total_down) / (total_up + total_down)
    alpha = 2.0 / (float(length) + 1.0) * abs(cmo)
    return value if previous is None else alpha * value + (1.0 - alpha) * float(previous)


def _new_state(signature):
    return {"signature": signature, "highs": [], "lows": [], "raw_previous": None, "smooth": None, "smooth_ups": [], "smooth_downs": [], "src_previous": None, "ma": None, "ma_ups": [], "ma_downs": [], "long_stop": None, "short_stop": None, "direction": 1, "ott": [], "previous_src": None}


def _step(state, high, low, close, stochastic_length, smoothing_length, ott_length, ott_percent):
    state["highs"].append(high)
    state["lows"].append(low)
    state["highs"] = state["highs"][-stochastic_length:]
    state["lows"] = state["lows"][-stochastic_length:]
    if len(state["highs"]) < stochastic_length:
        return False, False
    highest = max(state["highs"])
    lowest = min(state["lows"])
    raw = 50.0 if highest <= lowest else 100.0 * (close - lowest) / (highest - lowest)
    smooth = _adaptive(state["smooth"], raw, state["raw_previous"], state["smooth_ups"], state["smooth_downs"], smoothing_length)
    src = smooth + 1000.0
    ma = _adaptive(state["ma"], src, state["src_previous"], state["ma_ups"], state["ma_downs"], ott_length)
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
    if state["previous_src"] is not None and len(state["ott"]) >= 4:
        buy = float(state["previous_src"]) <= state["ott"][-4] and src > state["ott"][-3]
        sell = float(state["previous_src"]) >= state["ott"][-4] and src < state["ott"][-3]
    state["raw_previous"] = raw
    state["smooth"] = smooth
    state["src_previous"] = src
    state["previous_src"] = src
    state["ma"] = ma
    state["long_stop"] = long_stop
    state["short_stop"] = short_stop
    state["direction"] = direction
    return buy, sell


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.stochastic_ott_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(1600)
    context.set_metadata(direction_mode="one_way", strategy_family="momentum")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    stochastic_length = int(context.params.get("stochastic_length", 250))
    smoothing_length = int(context.params.get("smoothing_length", 50))
    ott_length = int(context.params.get("ott_length", 3))
    ott_percent = float(context.params.get("ott_percent", 0.618))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [stochastic_length, smoothing_length, ott_length, ott_percent]
    state = dict(g.stochastic_ott_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(1000, stochastic_length + smoothing_length * 10)
    bars = get_history(required if cold else 1, "1h", ["high", "low", "close"], g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    buy = sell = False
    for _, row in bars.iterrows():
        buy, sell = _step(state, float(row["high"]), float(row["low"]), float(row["close"]), stochastic_length, smoothing_length, ott_length, ott_percent)
    g.stochastic_ott_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="stochastic_ott_long")
    elif sell and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="stochastic_ott_short")
