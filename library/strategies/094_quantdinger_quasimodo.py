"""Confirmed-pivot Quasimodo reversal pattern strategy."""

# @param pivot_period int 5 range=2:20:1
# @param entry_validity int 2 range=2:15:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "highs": [], "lows": [], "bar_index": -1, "swings": [], "long_setup": None, "short_setup": None}


def _add_swing(state, side, value, index):
    swings = list(state["swings"])
    if swings and swings[-1]["side"] == side:
        more_extreme = value > swings[-1]["value"] if side == "H" else value < swings[-1]["value"]
        if not more_extreme:
            return False
        swings.pop()
    previous_same = None
    for item in reversed(swings):
        if item["side"] == side:
            previous_same = item["value"]
            break
    if previous_same is None:
        label = side
    elif side == "H":
        label = "HH" if value > previous_same else "LH"
    else:
        label = "HL" if value > previous_same else "LL"
    swings.append({"side": side, "label": label, "value": value, "index": index})
    state["swings"] = swings[-20:]
    return True


def _detect_setup(state, entry_validity):
    swings = state["swings"]
    if len(swings) < 5:
        return
    labels = [item["label"] for item in swings[-5:]]
    values = [float(item["value"]) for item in swings[-5:]]
    indexes = [int(item["index"]) for item in swings[-5:]]
    span = max(2, int(abs(indexes[-2] - indexes[-4]) / 2.0 * entry_validity))
    if labels[-1] == "LL" and labels[-2] == "HH" and labels[-3] == "HL" and labels[-4] == "HH" and values[-5] < values[-1]:
        state["short_setup"] = {"entry": values[-4], "stop": values[-2], "target": values[-3], "until": indexes[-1] + span, "triggered": False}
    if labels[-1] == "HH" and labels[-2] == "LL" and labels[-3] == "LH" and labels[-4] == "LL" and values[-5] > values[-1]:
        state["long_setup"] = {"entry": values[-4], "stop": values[-2], "target": values[-3], "until": indexes[-1] + span, "triggered": False}


def _step(state, high, low, pivot_period, entry_validity):
    state["bar_index"] = int(state["bar_index"]) + 1
    state["highs"].append(high)
    state["lows"].append(low)
    window = pivot_period * 2 + 1
    state["highs"] = state["highs"][-window:]
    state["lows"] = state["lows"][-window:]
    if len(state["highs"]) == window:
        candidate_index = int(state["bar_index"]) - pivot_period
        candidate_high = state["highs"][pivot_period]
        candidate_low = state["lows"][pivot_period]
        changed = False
        if candidate_high >= max(state["highs"]):
            changed = _add_swing(state, "H", candidate_high, candidate_index) or changed
        if candidate_low <= min(state["lows"]):
            changed = _add_swing(state, "L", candidate_low, candidate_index) or changed
        if changed:
            _detect_setup(state, entry_validity)
    current = int(state["bar_index"])
    long_entry = short_entry = long_exit = short_exit = False
    long_setup = state["long_setup"]
    if long_setup is not None:
        if current > int(long_setup["until"]):
            state["long_setup"] = None
        else:
            long_entry = not bool(long_setup["triggered"]) and low <= float(long_setup["entry"]) and low > float(long_setup["stop"])
            if long_entry:
                long_setup["triggered"] = True
            long_exit = bool(long_setup["triggered"]) and (high >= float(long_setup["target"]) or low <= float(long_setup["stop"]))
    short_setup = state["short_setup"]
    if short_setup is not None:
        if current > int(short_setup["until"]):
            state["short_setup"] = None
        else:
            short_entry = not bool(short_setup["triggered"]) and high >= float(short_setup["entry"]) and high < float(short_setup["stop"])
            if short_entry:
                short_setup["triggered"] = True
            short_exit = bool(short_setup["triggered"]) and (low <= float(short_setup["target"]) or high >= float(short_setup["stop"]))
    return long_entry, short_entry, long_exit, short_exit


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.quasimodo_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low"])
    context.set_warmup(1000)
    context.set_metadata(direction_mode="one_way", strategy_family="price_action")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    pivot_period = int(context.params.get("pivot_period", 5))
    entry_validity = int(context.params.get("entry_validity", 2))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [pivot_period, entry_validity]
    state = dict(g.quasimodo_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(700, pivot_period * 100)
    bars = get_history(required if cold else 1, "1h", ["high", "low"], g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    long_entry = short_entry = long_exit = short_exit = False
    for _, row in bars.iterrows():
        long_entry, short_entry, long_exit, short_exit = _step(state, float(row["high"]), float(row["low"]), pivot_period, entry_validity)
    g.quasimodo_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if amount > 0 and long_exit:
        order_target_percent(g.symbol, 0.0, reason="quasimodo_long_exit")
    elif amount < 0 and short_exit:
        order_target_percent(g.symbol, 0.0, reason="quasimodo_short_exit")
    elif long_entry and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="quasimodo_long")
    elif short_entry and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="quasimodo_short")
