"""ATR-sized Renko state strategy with long-only trend transitions."""

# @param atr_length int 10 range=1:200:1
# @param allow_short bool false
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "previous_close": None, "atr": None, "close_brick": None, "open_brick": None, "renko_open": None, "previous_color": 0}


def _step(state, open_price, high, low, close, atr_length):
    previous_close = state["previous_close"]
    true_range = high - low if previous_close is None else max(high - low, abs(high - float(previous_close)), abs(low - float(previous_close)))
    state["atr"] = true_range if state["atr"] is None else (float(state["atr"]) * (atr_length - 1) + true_range) / float(atr_length)
    atr = max(float(state["atr"]), 1e-12)
    if state["close_brick"] is None:
        state["close_brick"] = close
        state["open_brick"] = open_price
        state["renko_open"] = open_price
        state["previous_close"] = close
        return False, False
    old_close_brick = float(state["close_brick"])
    old_open_brick = float(state["open_brick"])
    close_brick = old_close_brick + atr if close > old_close_brick + atr else old_close_brick - atr if close < old_close_brick - atr else old_close_brick
    open_brick = old_open_brick + atr if open_price > old_open_brick + atr else old_open_brick - atr if open_price < old_open_brick - atr else old_open_brick
    if open_brick != old_open_brick:
        state["renko_open"] = old_open_brick
    state["close_brick"] = close_brick
    state["open_brick"] = open_brick
    color = 1 if float(state["renko_open"]) < close_brick else -1 if float(state["renko_open"]) > close_brick else int(state["previous_color"])
    previous_color = int(state["previous_color"])
    state["previous_color"] = color
    state["previous_close"] = close
    return color == 1 and previous_color != 1, color == -1 and previous_color != -1


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.atr_renko_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["open", "high", "low", "close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="price_action")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    atr_length = int(context.params.get("atr_length", 10))
    allow_short = bool(context.params.get("allow_short", False))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [atr_length, allow_short]
    state = dict(g.atr_renko_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(320, atr_length * 20)
    bars = get_history(required if cold else 1, "1h", ["open", "high", "low", "close"], g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    long_signal = short_signal = False
    for _, row in bars.iterrows():
        long_signal, short_signal = _step(state, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]), atr_length)
    g.atr_renko_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_signal and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="atr_renko_long")
    elif short_signal and allow_short and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="atr_renko_short")
    elif short_signal and not allow_short and amount > 0:
        order_target_percent(g.symbol, 0.0, reason="atr_renko_long_exit")
