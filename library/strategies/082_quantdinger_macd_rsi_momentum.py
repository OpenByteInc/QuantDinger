"""One-hour MACD slope reversal with RSI take-profit thresholds."""

# @param fast_length int 12 range=1:200:1
# @param slow_length int 26 range=2:400:1
# @param signal_length int 9 range=1:100:1
# @param rsi_length int 14 range=2:100:1
# @param overbought float 90 range=50:100:1
# @param oversold float 44 range=0:50:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _new_state(signature):
    return {"signature": signature, "fast": None, "slow": None, "smooth": None, "previous_macd": None, "previous_delta": None, "previous_close": None, "gains": [], "losses": []}


def _step(state, close, fast_length, slow_length, rsi_length, overbought, oversold):
    previous_close = state["previous_close"]
    change = 0.0 if previous_close is None else close - float(previous_close)
    state["gains"].append(max(change, 0.0))
    state["losses"].append(max(-change, 0.0))
    if len(state["gains"]) > rsi_length:
        state["gains"].pop(0)
        state["losses"].pop(0)
    state["fast"] = _ema(state["fast"], close, fast_length)
    state["slow"] = _ema(state["slow"], close, slow_length)
    raw_macd = float(state["fast"]) - float(state["slow"])
    state["smooth"] = _ema(state["smooth"], raw_macd, 10)
    macd = float(state["smooth"])
    previous_macd = state["previous_macd"]
    delta = 0.0 if previous_macd is None else macd - float(previous_macd)
    previous_delta = state["previous_delta"]
    state["previous_macd"] = macd
    state["previous_delta"] = delta
    state["previous_close"] = close
    if len(state["gains"]) < rsi_length or previous_delta is None:
        return False, False, False, False
    gain = sum(state["gains"]) / float(rsi_length)
    loss = sum(state["losses"]) / float(rsi_length)
    rsi = 100.0 if loss <= 1e-15 else 100.0 - 100.0 / (1.0 + gain / loss)
    long_entry = delta > 0 and float(previous_delta) <= 0 and rsi < overbought
    short_entry = delta < 0 and float(previous_delta) >= 0 and rsi > oversold
    return long_entry, short_entry, rsi > overbought or short_entry, rsi < oversold or long_entry


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.macd_rsi_state = {}
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
    rsi_length = int(context.params.get("rsi_length", 14))
    overbought = float(context.params.get("overbought", 90.0))
    oversold = float(context.params.get("oversold", 44.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [fast_length, slow_length, signal_length, rsi_length, overbought, oversold]
    state = dict(g.macd_rsi_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(400, "1h", "close", g.symbol)
        if len(bars) < 400:
            return
        state = _new_state(signature)
        signals = (False, False, False, False)
        for value in bars["close"]:
            signals = _step(state, float(value), fast_length, slow_length, rsi_length, overbought, oversold)
    else:
        bars = get_history(1, "1h", "close", g.symbol)
        if len(bars) < 1:
            return
        signals = _step(state, float(bars["close"].iloc[-1]), fast_length, slow_length, rsi_length, overbought, oversold)
    g.macd_rsi_state = state
    long_entry, short_entry, close_long, close_short = signals
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_entry and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="macd_rsi_long")
    elif short_entry and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="macd_rsi_short")
    elif amount > 0 and close_long:
        order_target_percent(g.symbol, 0.0, reason="macd_rsi_long_exit")
    elif amount < 0 and close_short:
        order_target_percent(g.symbol, 0.0, reason="macd_rsi_short_exit")
