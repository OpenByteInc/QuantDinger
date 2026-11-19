"""Reloaded MACD histogram zero-cross reversal strategy."""

# @param fast_length int 12 range=1:200:1
# @param slow_length int 26 range=2:400:1
# @param signal_length int 9 range=1:100:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _new_state(signature):
    return {"signature": signature, "fast": None, "slow": None, "signal": None, "previous_histogram": None}


def _step(state, close, fast_length, slow_length, signal_length):
    state["fast"] = _ema(state["fast"], close, fast_length)
    state["slow"] = _ema(state["slow"], close, slow_length)
    macd = float(state["fast"]) - float(state["slow"])
    state["signal"] = _ema(state["signal"], macd, signal_length)
    histogram = macd - float(state["signal"])
    previous = state["previous_histogram"]
    state["previous_histogram"] = histogram
    if previous is None:
        return False, False
    return float(previous) <= 0 < histogram, float(previous) >= 0 > histogram


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.macd_reload_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="momentum")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    fast_length = int(context.params.get("fast_length", 12))
    slow_length = int(context.params.get("slow_length", 26))
    signal_length = int(context.params.get("signal_length", 9))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [fast_length, slow_length, signal_length]
    state = dict(g.macd_reload_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(360, "1h", "close", g.symbol)
        if len(bars) < 360:
            return
        state = _new_state(signature)
        buy = False
        sell = False
        for value in bars["close"]:
            buy, sell = _step(state, float(value), fast_length, slow_length, signal_length)
    else:
        bars = get_history(1, "1h", "close", g.symbol)
        if len(bars) < 1:
            return
        buy, sell = _step(state, float(bars["close"].iloc[-1]), fast_length, slow_length, signal_length)
    g.macd_reload_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="macd_reload_long")
    elif sell and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="macd_reload_short")
