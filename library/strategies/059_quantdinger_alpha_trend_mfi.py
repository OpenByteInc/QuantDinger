"""AlphaTrend reversal strategy using ATR and money-flow regime."""

# @param multiplier float 1.0 range=0.1:10:0.1
# @param common_period int 14 range=2:300:1
# @param use_rsi bool false
# @param target_pct float 0.95 range=0.05:1:0.05


def _average(buffer, value, length):
    buffer.append(float(value))
    if len(buffer) > length:
        buffer.pop(0)
    if len(buffer) < length:
        return float("nan")
    return sum(buffer) / float(length)


def _new_state(signature):
    return {"signature": signature, "previous_close": None, "previous_typical": None, "tr": [], "positive": [], "negative": [], "gains": [], "losses": [], "alpha": None, "alpha_history": []}


def _step(state, high, low, close, volume, multiplier, period, use_rsi):
    previous_close = state["previous_close"]
    true_range = high - low if previous_close is None else max(high - low, abs(high - float(previous_close)), abs(low - float(previous_close)))
    atr = _average(state["tr"], true_range, period)
    change = 0.0 if previous_close is None else close - float(previous_close)
    gain = _average(state["gains"], max(change, 0.0), period)
    loss = _average(state["losses"], max(-change, 0.0), period)
    typical = (high + low + close) / 3.0
    previous_typical = state["previous_typical"]
    flow = typical * max(volume, 0.0)
    positive = _average(state["positive"], flow if previous_typical is not None and typical > float(previous_typical) else 0.0, period)
    negative = _average(state["negative"], flow if previous_typical is not None and typical < float(previous_typical) else 0.0, period)
    state["previous_close"] = close
    state["previous_typical"] = typical
    if atr != atr or gain != gain or positive != positive:
        return False, False
    rsi = 100.0 if loss <= 1e-15 else 100.0 - 100.0 / (1.0 + gain / loss)
    mfi = 100.0 if negative <= 1e-15 else 100.0 - 100.0 / (1.0 + positive / negative)
    positive_regime = rsi >= 50.0 if use_rsi else mfi >= 50.0
    candidate = low - multiplier * atr if positive_regime else high + multiplier * atr
    previous_alpha = state["alpha"]
    if previous_alpha is None:
        alpha = candidate
    elif positive_regime:
        alpha = max(float(previous_alpha), candidate)
    else:
        alpha = min(float(previous_alpha), candidate)
    state["alpha"] = alpha
    state["alpha_history"].append(alpha)
    if len(state["alpha_history"]) > 4:
        state["alpha_history"].pop(0)
    if len(state["alpha_history"]) < 4:
        return False, False
    previous_difference = state["alpha_history"][-2] - state["alpha_history"][-4]
    difference = state["alpha_history"][-1] - state["alpha_history"][-3]
    return previous_difference <= 0 < difference, previous_difference >= 0 > difference


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.alpha_mfi_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close", "volume"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    multiplier = float(context.params.get("multiplier", 1.0))
    period = int(context.params.get("common_period", 14))
    use_rsi = bool(context.params.get("use_rsi", False))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [multiplier, period, use_rsi]
    state = dict(g.alpha_mfi_state or {})
    fields = ["high", "low", "close", "volume"]
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(max(240, period * 12), "1h", fields, g.symbol)
        if len(bars) < max(240, period * 12):
            return
        state = _new_state(signature)
        buy = False
        sell = False
        for index in range(len(bars)):
            buy, sell = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]), float(bars["volume"].iloc[index]), multiplier, period, use_rsi)
    else:
        bars = get_history(1, "1h", fields, g.symbol)
        if len(bars) < 1:
            return
        buy, sell = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]), float(bars["volume"].iloc[-1]), multiplier, period, use_rsi)
    g.alpha_mfi_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="alpha_mfi_long")
    elif sell and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="alpha_mfi_short")
