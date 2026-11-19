"""Non-repainting rolling-swing direction with an adaptive anchored VWAP."""

# @param swing_period int 50 range=2:500:1
# @param adaptive_period float 20 range=1:300:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "highs": [], "lows": [], "bar": 0, "high_index": -1, "low_index": -1, "weighted_price": None, "weighted_volume": None, "direction": 0}


def _step(state, high, low, close, volume, swing_period, adaptive_period):
    state["bar"] = int(state["bar"]) + 1
    state["highs"].append(high)
    state["lows"].append(low)
    if len(state["highs"]) > swing_period:
        state["highs"].pop(0)
        state["lows"].pop(0)
    if len(state["highs"]) < swing_period:
        return False, False
    if high >= max(state["highs"]):
        state["high_index"] = int(state["bar"])
        state["weighted_price"] = ((high + low + close) / 3.0) * max(volume, 0.0)
        state["weighted_volume"] = max(volume, 0.0)
    if low <= min(state["lows"]):
        state["low_index"] = int(state["bar"])
        state["weighted_price"] = ((high + low + close) / 3.0) * max(volume, 0.0)
        state["weighted_volume"] = max(volume, 0.0)
    alpha = 1.0 - np.exp(-np.log(2.0) / max(1.0, adaptive_period))
    pxv = ((high + low + close) / 3.0) * max(volume, 0.0)
    if state["weighted_price"] is None:
        state["weighted_price"] = pxv
        state["weighted_volume"] = max(volume, 0.0)
    else:
        state["weighted_price"] = (1.0 - alpha) * float(state["weighted_price"]) + alpha * pxv
        state["weighted_volume"] = (1.0 - alpha) * float(state["weighted_volume"]) + alpha * max(volume, 0.0)
    previous_direction = int(state["direction"])
    state["direction"] = 1 if int(state["low_index"]) > int(state["high_index"]) else -1
    return int(state["direction"]) == 1 and previous_direction != 1, int(state["direction"]) == -1 and previous_direction != -1


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.swing_vwap_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close", "volume"])
    context.set_warmup(800)
    context.set_metadata(direction_mode="long_only", strategy_family="swing")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    swing_period = int(context.params.get("swing_period", 50))
    adaptive_period = float(context.params.get("adaptive_period", 20.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [swing_period, adaptive_period]
    state = dict(g.swing_vwap_state or {})
    fields = ["high", "low", "close", "volume"]
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(max(500, swing_period * 8), "1h", fields, g.symbol)
        if len(bars) < max(500, swing_period * 8):
            return
        state = _new_state(signature)
        enter = False
        exit_signal = False
        for index in range(len(bars)):
            enter, exit_signal = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]), float(bars["volume"].iloc[index]), swing_period, adaptive_period)
    else:
        bars = get_history(1, "1h", fields, g.symbol)
        if len(bars) < 1:
            return
        enter, exit_signal = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]), float(bars["volume"].iloc[-1]), swing_period, adaptive_period)
    g.swing_vwap_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if enter and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="swing_vwap_long")
    elif exit_signal and amount > 0:
        order_target_percent(g.symbol, 0.0, reason="swing_vwap_exit")
