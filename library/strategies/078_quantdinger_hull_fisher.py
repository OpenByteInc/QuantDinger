"""Hull moving-average Fisher transform with CCI confirmation."""

# @param hull_length int 14 range=2:200:1
# @param signal_length int 9 range=2:100:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _wma(values, length):
    if len(values) < length:
        return float("nan")
    weights = list(range(1, length + 1))
    return sum(values[-length + index] * weights[index] for index in range(length)) / float(sum(weights))


def _new_state(signature):
    return {"signature": signature, "opens": [], "hull": [], "fisher": 0.0, "normalized": 0.0, "previous_hull": None, "previous_cci": None}


def _step(state, open_price, hull_length, signal_length):
    state["opens"].append(open_price)
    if len(state["opens"]) > hull_length * 3:
        state["opens"].pop(0)
    half = max(1, hull_length // 2)
    root = max(1, int(hull_length ** 0.5))
    short_wma = _wma(state["opens"], half)
    long_wma = _wma(state["opens"], hull_length)
    if short_wma != short_wma or long_wma != long_wma:
        return False, False, False, False
    state["hull"].append(2.0 * short_wma - long_wma)
    if len(state["hull"]) > max(signal_length, root, hull_length):
        state["hull"].pop(0)
    hull = _wma(state["hull"], root)
    if hull != hull or len(state["hull"]) < signal_length:
        return False, False, False, False
    highest = max(state["hull"][-signal_length:])
    lowest = min(state["hull"][-signal_length:])
    normalized = 0.0 if highest <= lowest else 0.66 * ((hull - lowest) / (highest - lowest) - 0.5) + 0.67 * float(state["normalized"])
    normalized = min(0.999, max(-0.999, normalized))
    fisher = 0.5 * np.log((1.0 + normalized) / max(1.0 - normalized, 0.001)) + 0.5 * float(state["fisher"])
    mean = sum(state["opens"][-hull_length:]) / float(hull_length)
    deviation = sum(abs(value - mean) for value in state["opens"][-hull_length:]) / float(hull_length)
    cci = 0.0 if deviation <= 1e-15 else (open_price - mean) / (0.015 * deviation)
    previous_fisher = state["fisher"]
    previous_hull = state["previous_hull"]
    previous_cci = state["previous_cci"]
    state["normalized"] = normalized
    state["fisher"] = fisher
    state["previous_hull"] = hull
    state["previous_cci"] = cci
    if previous_hull is None or previous_cci is None:
        return False, False, False, False
    long_entry = fisher > previous_fisher and cci > float(previous_cci) and hull > float(previous_hull) and (fisher < -5.0 or fisher > 0.0)
    short_entry = fisher < previous_fisher and cci < float(previous_cci) and hull < float(previous_hull) and (fisher > 5.0 or fisher < 0.0)
    return long_entry, short_entry, fisher < previous_fisher and hull < float(previous_hull), fisher > previous_fisher and hull > float(previous_hull)


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.hull_fisher_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["open"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="oscillator")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    hull_length = int(context.params.get("hull_length", 14))
    signal_length = int(context.params.get("signal_length", 9))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [hull_length, signal_length]
    state = dict(g.hull_fisher_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(360, "1h", "open", g.symbol)
        if len(bars) < 360:
            return
        state = _new_state(signature)
        signals = (False, False, False, False)
        for value in bars["open"]:
            signals = _step(state, float(value), hull_length, signal_length)
    else:
        bars = get_history(1, "1h", "open", g.symbol)
        if len(bars) < 1:
            return
        signals = _step(state, float(bars["open"].iloc[-1]), hull_length, signal_length)
    g.hull_fisher_state = state
    long_entry, short_entry, long_exit, short_exit = signals
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_entry and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="hull_fisher_long")
    elif short_entry and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="hull_fisher_short")
    elif amount > 0 and long_exit:
        order_target_percent(g.symbol, 0.0, reason="hull_fisher_long_exit")
    elif amount < 0 and short_exit:
        order_target_percent(g.symbol, 0.0, reason="hull_fisher_short_exit")
