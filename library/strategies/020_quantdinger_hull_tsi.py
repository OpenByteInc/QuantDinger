"""
QuantDinger Hull TSI
One-way momentum strategy using double Hull smoothing and price direction.
"""

# @param long_length int 50 range=2:300:1
# @param short_length int 50 range=2:300:1
# @param signal_length int 7 range=1:100:1
# @param target_pct float 0.95 range=0.05:1:0.05


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
    length = max(1, int(length))
    half = max(1, int(length / 2))
    root = max(1, int(length ** 0.5))
    half_value = _wma_step(state["half"], value, half)
    full_value = _wma_step(state["full"], value, length)
    raw = 2.0 * half_value - full_value
    return _wma_step(state["root"], raw, root)


def _new_hull_state(long_length, short_length, signal_length):
    return {
        "signature": [long_length, short_length, signal_length],
        "price": None,
        "signal": float("nan"),
        "pc_first": _new_hma_state(),
        "pc_second": _new_hma_state(),
        "abs_first": _new_hma_state(),
        "abs_second": _new_hma_state(),
        "signal_hma": _new_hma_state(),
    }


def _hull_step(state, price, long_length, short_length, signal_length):
    previous_price = state["price"]
    change = float("nan") if previous_price is None else float(price) - float(previous_price)
    first_pc = _hma_step(state["pc_first"], change, long_length)
    smooth_pc = _hma_step(state["pc_second"], first_pc, short_length)
    first_abs = _hma_step(state["abs_first"], abs(change), long_length)
    smooth_abs = _hma_step(state["abs_second"], first_abs, short_length)
    tsi = float("nan") if smooth_abs != smooth_abs or abs(smooth_abs) <= 1e-12 else 500.0 * smooth_pc / smooth_abs
    previous_signal = float(state["signal"])
    current_signal = 5.0 * _hma_step(state["signal_hma"], tsi, signal_length)
    state["price"] = float(price)
    state["signal"] = current_signal
    return previous_price, previous_signal, current_signal


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.hull_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["open"])
    context.set_warmup(1400)
    context.set_metadata(direction_mode="one_way", strategy_family="momentum")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    long_length = int(context.params.get("long_length", 50))
    short_length = int(context.params.get("short_length", 50))
    signal_length = int(context.params.get("signal_length", 7))
    target_pct = float(context.params.get("target_pct", 0.95))
    if min(long_length, short_length, signal_length) < 1:
        return
    signature = [long_length, short_length, signal_length]
    state = dict(g.hull_state or {})
    if not state or list(state.get("signature") or []) != signature:
        required = max(5 * (long_length + short_length + signal_length), 400)
        bars = get_history(required, "1h", ["open"], g.symbol)
        if len(bars) < required:
            return
        state = _new_hull_state(long_length, short_length, signal_length)
        previous_price = None
        previous_signal = float("nan")
        current_signal = float("nan")
        for price in bars["open"]:
            previous_price, previous_signal, current_signal = _hull_step(
                state,
                float(price),
                long_length,
                short_length,
                signal_length,
            )
        current_price = float(bars["open"].iloc[-1])
    else:
        bars = get_history(1, "1h", ["open"], g.symbol)
        if len(bars) < 1:
            return
        current_price = float(bars["open"].iloc[-1])
        previous_price, previous_signal, current_signal = _hull_step(
            state,
            current_price,
            long_length,
            short_length,
            signal_length,
        )
    g.hull_state = state
    values = (previous_signal, current_signal, previous_price, current_price)
    if not all(value is not None and float(value) == float(value) and abs(float(value)) < 1e300 for value in values):
        return
    long_signal = float(current_signal) > float(previous_signal) and current_price > float(previous_price)
    short_signal = float(current_signal) < float(previous_signal) and current_price < float(previous_price)
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_signal and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="hull_tsi_long")
    elif short_signal and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="hull_tsi_short")
