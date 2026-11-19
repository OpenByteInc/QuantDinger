"""State-selected moving-average crossover strategy."""

# @param target_pct float 0.95 range=0.05:1:0.05


def _ema_step(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _window_step(buffer, value, length):
    buffer.append(float(value))
    if len(buffer) > length:
        buffer.pop(0)
    if len(buffer) < length:
        return float("nan")
    return sum(buffer) / float(length)


def _rma_step(state, value, length):
    current = state.get("value")
    if current is not None:
        current = (float(current) * (length - 1) + float(value)) / float(length)
        state["value"] = current
        return current
    seed = state["seed"]
    seed.append(float(value))
    if len(seed) < length:
        return float("nan")
    current = sum(seed[-length:]) / float(length)
    state["value"] = current
    state["seed"] = []
    return current


def _wma_step(buffer, value, length):
    buffer.append(float(value))
    if len(buffer) > length:
        buffer.pop(0)
    if len(buffer) < length:
        return float("nan")
    total = 0.0
    for index in range(length):
        item = float(buffer[index])
        if item != item:
            return float("nan")
        total += item * (index + 1)
    return total / float(length * (length + 1) / 2)


def _new_hma_state():
    return {"half": [], "full": [], "root": []}


def _hma_step(state, value, length):
    half = max(1, int(length / 2))
    root = max(1, int(length ** 0.5))
    half_value = _wma_step(state["half"], value, half)
    full_value = _wma_step(state["full"], value, length)
    return _wma_step(state["root"], 2.0 * half_value - full_value, root)


def _new_state():
    return {
        "base": None,
        "ema15": None,
        "sma19": [],
        "rma45": {"value": None, "seed": []},
        "rma16": {"value": None, "seed": []},
        "rma12": {"value": None, "seed": []},
        "rma36": {"value": None, "seed": []},
        "hma24": _new_hma_state(),
        "hma59": _new_hma_state(),
        "selected_short": None,
        "selected_long": None,
    }


def _ma_step(state, price):
    previous_base = state["base"]
    state["base"] = _ema_step(previous_base, price, 20)
    state["ema15"] = _ema_step(state["ema15"], price, 15)
    sma19 = _window_step(state["sma19"], price, 19)
    rma45 = _rma_step(state["rma45"], price, 45)
    rma16 = _rma_step(state["rma16"], price, 16)
    rma12 = _rma_step(state["rma12"], price, 12)
    rma36 = _rma_step(state["rma36"], price, 36)
    hma24 = _hma_step(state["hma24"], price, 24)
    hma59 = _hma_step(state["hma59"], price, 59)
    slope_up = previous_base is not None and float(state["base"]) > float(previous_base)
    above_base = float(price) > float(state["base"])
    if slope_up and above_base:
        current_short, current_long = rma12, rma36
    elif slope_up:
        current_short, current_long = rma16, hma59
    elif above_base:
        current_short, current_long = sma19, rma45
    else:
        current_short, current_long = float(state["ema15"]), hma24
    previous_short = state["selected_short"]
    previous_long = state["selected_long"]
    state["selected_short"] = current_short
    state["selected_long"] = current_long
    return previous_short, previous_long, current_short, current_long


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.ma_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="long_only", strategy_family="trend")


def handle_data(context, data):
    target_pct = float(context.params.get("target_pct", 0.95))
    state = dict(g.ma_state or {})
    if not state:
        bars = get_history(320, "1h", ["close"], g.symbol)
        if len(bars) < 320:
            return
        state = _new_state()
        values = (None, None, float("nan"), float("nan"))
        for price in bars["close"]:
            values = _ma_step(state, float(price))
    else:
        bars = get_history(1, "1h", ["close"], g.symbol)
        if len(bars) < 1:
            return
        values = _ma_step(state, float(bars["close"].iloc[-1]))
    g.ma_state = state
    if not all(value is not None and float(value) == float(value) for value in values):
        return
    previous_short, previous_long, current_short, current_long = values
    crossed_up = float(previous_short) <= float(previous_long) and float(current_short) > float(current_long)
    crossed_down = float(previous_short) >= float(previous_long) and float(current_short) < float(current_long)
    amount = float(get_position(g.symbol).amount or 0.0)
    if crossed_up and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="state_ma_long")
    elif crossed_down and amount > 0:
        order_target_percent(g.symbol, 0.0, reason="state_ma_exit")
