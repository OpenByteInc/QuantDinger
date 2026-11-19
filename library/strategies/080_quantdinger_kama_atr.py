"""Long-only Kaufman adaptive moving-average slope with ATR trailing exit."""

# @param kama_length int 10 range=2:300:1
# @param fast_length int 2 range=1:100:1
# @param slow_length int 30 range=2:300:1
# @param atr_length int 14 range=1:100:1
# @param atr_multiplier float 2.0 range=0.1:20:0.1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "closes": [], "tr": [], "previous_close": None, "kama": None, "previous_kama": None, "trail": None}


def _step(state, high, low, close, kama_length, fast_length, slow_length, atr_length, atr_multiplier):
    previous_close = state["previous_close"]
    state["closes"].append(close)
    if len(state["closes"]) > kama_length + 1:
        state["closes"].pop(0)
    true_range = high - low if previous_close is None else max(high - low, abs(high - float(previous_close)), abs(low - float(previous_close)))
    state["tr"].append(true_range)
    if len(state["tr"]) > atr_length:
        state["tr"].pop(0)
    state["previous_close"] = close
    if len(state["closes"]) < kama_length + 1 or len(state["tr"]) < atr_length:
        return False, False
    change = abs(state["closes"][-1] - state["closes"][0])
    volatility = sum(abs(state["closes"][index] - state["closes"][index - 1]) for index in range(1, len(state["closes"])))
    efficiency = 0.0 if volatility <= 1e-15 else change / volatility
    fast_sc = 2.0 / (fast_length + 1.0)
    slow_sc = 2.0 / (slow_length + 1.0)
    smoothing = (efficiency * (fast_sc - slow_sc) + slow_sc) ** 2
    previous_kama = state["kama"]
    state["kama"] = close if previous_kama is None else float(previous_kama) + smoothing * (close - float(previous_kama))
    delta = 0.0 if previous_kama is None else float(state["kama"]) - float(previous_kama)
    atr = sum(state["tr"]) / float(atr_length)
    candidate = low - atr_multiplier * atr
    state["trail"] = candidate if state["trail"] is None or close <= float(state["trail"]) else max(candidate, float(state["trail"]))
    state["previous_kama"] = previous_kama
    trail_hit = low <= float(state["trail"])
    if trail_hit:
        state["trail"] = candidate
    return delta > 0, delta < 0 or trail_hit


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.kama_atr_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="long_only", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    kama_length = int(context.params.get("kama_length", 10))
    fast_length = int(context.params.get("fast_length", 2))
    slow_length = int(context.params.get("slow_length", 30))
    atr_length = int(context.params.get("atr_length", 14))
    atr_multiplier = float(context.params.get("atr_multiplier", 2.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [kama_length, fast_length, slow_length, atr_length, atr_multiplier]
    state = dict(g.kama_atr_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(360, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 360:
            return
        state = _new_state(signature)
        enter = False
        exit_signal = False
        for index in range(len(bars)):
            enter, exit_signal = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]), kama_length, fast_length, slow_length, atr_length, atr_multiplier)
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        enter, exit_signal = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]), kama_length, fast_length, slow_length, atr_length, atr_multiplier)
    g.kama_atr_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if enter and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="kama_long")
    elif exit_signal and amount > 0:
        order_target_percent(g.symbol, 0.0, reason="kama_exit")
