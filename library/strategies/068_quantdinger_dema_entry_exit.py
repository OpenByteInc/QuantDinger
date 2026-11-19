"""OHLC4 EMA crossover against a 34-period double EMA."""

# @param signal_length int 8 range=1:100:1
# @param dema_length int 34 range=2:300:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _new_state(signature):
    return {"signature": signature, "signal": None, "dema_first": None, "dema_second": None, "previous_signal": None, "previous_dema": None}


def _step(state, open_price, high, low, close, signal_length, dema_length):
    state["signal"] = _ema(state["signal"], (open_price + high + low + close) / 4.0, signal_length)
    state["dema_first"] = _ema(state["dema_first"], close, dema_length)
    state["dema_second"] = _ema(state["dema_second"], state["dema_first"], dema_length)
    dema = 2.0 * float(state["dema_first"]) - float(state["dema_second"])
    previous_signal = state["previous_signal"]
    previous_dema = state["previous_dema"]
    state["previous_signal"] = float(state["signal"])
    state["previous_dema"] = dema
    if previous_signal is None or previous_dema is None:
        return False, False
    return float(previous_signal) <= float(previous_dema) and float(state["signal"]) > dema, float(previous_signal) >= float(previous_dema) and float(state["signal"]) < dema


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.dema_entry_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["open", "high", "low", "close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    signal_length = int(context.params.get("signal_length", 8))
    dema_length = int(context.params.get("dema_length", 34))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [signal_length, dema_length]
    state = dict(g.dema_entry_state or {})
    fields = ["open", "high", "low", "close"]
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(max(300, dema_length * 8), "1h", fields, g.symbol)
        if len(bars) < max(300, dema_length * 8):
            return
        state = _new_state(signature)
        buy = False
        sell = False
        for index in range(len(bars)):
            buy, sell = _step(state, float(bars["open"].iloc[index]), float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]), signal_length, dema_length)
    else:
        bars = get_history(1, "1h", fields, g.symbol)
        if len(bars) < 1:
            return
        buy, sell = _step(state, float(bars["open"].iloc[-1]), float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]), signal_length, dema_length)
    g.dema_entry_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="dema_cross_long")
    elif sell and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="dema_cross_short")
