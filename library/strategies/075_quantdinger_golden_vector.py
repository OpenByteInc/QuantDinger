"""SuperTrend trigger filtered by a macro EMA and DMI/ADX strength."""

# @param atr_length int 14 range=1:100:1
# @param atr_factor float 3.5 range=0.1:20:0.1
# @param ema_length int 200 range=1:600:1
# @param adx_length int 14 range=1:100:1
# @param adx_threshold float 25 range=1:100:1
# @param target_pct float 0.02 range=0.01:1:0.01


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _new_state(signature):
    return {"signature": signature, "ema": None, "atr": None, "upper": None, "lower": None, "trend": 1, "previous_high": None, "previous_low": None, "previous_close": None, "plus_dm": None, "minus_dm": None, "smoothed_tr": None, "adx": None, "previous_long_condition": False, "previous_short_condition": False}


def _step(state, high, low, close, atr_length, atr_factor, ema_length, adx_length, adx_threshold):
    previous_high = state["previous_high"]
    previous_low = state["previous_low"]
    previous_close = state["previous_close"]
    true_range = high - low if previous_close is None else max(high - low, abs(high - float(previous_close)), abs(low - float(previous_close)))
    state["ema"] = _ema(state["ema"], close, ema_length)
    state["atr"] = _ema(state["atr"], true_range, atr_length)
    upper_candidate = (high + low) / 2.0 + atr_factor * float(state["atr"])
    lower_candidate = (high + low) / 2.0 - atr_factor * float(state["atr"])
    previous_upper = state["upper"]
    previous_lower = state["lower"]
    trend = int(state["trend"])
    if previous_upper is None or previous_lower is None:
        upper = upper_candidate
        lower = lower_candidate
    else:
        upper = upper_candidate if previous_close is None or float(previous_close) >= float(previous_upper) else min(upper_candidate, float(previous_upper))
        lower = lower_candidate if previous_close is None or float(previous_close) <= float(previous_lower) else max(lower_candidate, float(previous_lower))
        if close > float(previous_upper):
            trend = 1
        elif close < float(previous_lower):
            trend = -1
    state["upper"] = upper
    state["lower"] = lower
    state["trend"] = trend
    plus_di = 0.0
    minus_di = 0.0
    if previous_high is not None and previous_low is not None:
        up = high - float(previous_high)
        down = float(previous_low) - low
        state["smoothed_tr"] = _ema(state["smoothed_tr"], true_range, adx_length)
        state["plus_dm"] = _ema(state["plus_dm"], up if up > down and up > 0 else 0.0, adx_length)
        state["minus_dm"] = _ema(state["minus_dm"], down if down > up and down > 0 else 0.0, adx_length)
        denominator = max(float(state["smoothed_tr"]), 1e-15)
        plus_di = 100.0 * float(state["plus_dm"]) / denominator
        minus_di = 100.0 * float(state["minus_dm"]) / denominator
        total = plus_di + minus_di
        dx = 0.0 if total <= 1e-15 else 100.0 * abs(plus_di - minus_di) / total
        state["adx"] = _ema(state["adx"], dx, adx_length)
    state["previous_high"] = high
    state["previous_low"] = low
    state["previous_close"] = close
    if state["adx"] is None:
        return False, False, trend < 0, trend > 0
    long_condition = trend > 0 and close > float(state["ema"]) and float(state["adx"]) >= adx_threshold and plus_di > minus_di
    short_condition = trend < 0 and close < float(state["ema"]) and float(state["adx"]) >= adx_threshold and minus_di > plus_di
    long_trigger = long_condition and not bool(state["previous_long_condition"])
    short_trigger = short_condition and not bool(state["previous_short_condition"])
    state["previous_long_condition"] = long_condition
    state["previous_short_condition"] = short_condition
    return long_trigger, short_trigger, trend < 0, trend > 0


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.golden_vector_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(1200)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    atr_length = int(context.params.get("atr_length", 14))
    atr_factor = float(context.params.get("atr_factor", 3.5))
    ema_length = int(context.params.get("ema_length", 200))
    adx_length = int(context.params.get("adx_length", 14))
    adx_threshold = float(context.params.get("adx_threshold", 25.0))
    target_pct = float(context.params.get("target_pct", 0.02))
    signature = [atr_length, atr_factor, ema_length, adx_length, adx_threshold]
    state = dict(g.golden_vector_state or {})
    required = max(800, ema_length * 4)
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(signature)
        signals = (False, False, False, False)
        for index in range(len(bars)):
            signals = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]), atr_length, atr_factor, ema_length, adx_length, adx_threshold)
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        signals = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]), atr_length, atr_factor, ema_length, adx_length, adx_threshold)
    g.golden_vector_state = state
    long_entry, short_entry, long_exit, short_exit = signals
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_entry and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="golden_vector_long")
    elif short_entry and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="golden_vector_short")
    elif amount > 0 and long_exit:
        order_target_percent(g.symbol, 0.0, reason="golden_vector_long_exit")
    elif amount < 0 and short_exit:
        order_target_percent(g.symbol, 0.0, reason="golden_vector_short_exit")
