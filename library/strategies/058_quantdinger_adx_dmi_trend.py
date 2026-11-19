"""ADX and DMI trend entries with volume, EMA and ATR-trail filters."""

# @param adx_length int 14 range=2:100:1
# @param adx_threshold float 23 range=1:100:1
# @param ema_length int 200 range=2:600:1
# @param volume_multiplier float 2.0 range=0:10:0.1
# @param atr_multiplier float 2.0 range=0.5:10:0.1
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _new_state(signature):
    return {"signature": signature, "previous_high": None, "previous_low": None, "previous_close": None, "tr": None, "plus_dm": None, "minus_dm": None, "adx": None, "adx_history": [], "ema": None, "volumes": [], "trail_low": None, "trail_high": None}


def _step(state, high, low, close, volume, adx_length, ema_length, volume_multiplier, atr_multiplier):
    previous_high = state["previous_high"]
    previous_low = state["previous_low"]
    previous_close = state["previous_close"]
    state["ema"] = _ema(state["ema"], close, ema_length)
    state["volumes"].append(max(volume, 0.0))
    if len(state["volumes"]) > 20:
        state["volumes"].pop(0)
    state["previous_high"] = high
    state["previous_low"] = low
    state["previous_close"] = close
    if previous_high is None or previous_low is None or previous_close is None:
        return False, False, False, False
    up = high - float(previous_high)
    down = float(previous_low) - low
    plus_dm = up if up > down and up > 0 else 0.0
    minus_dm = down if down > up and down > 0 else 0.0
    true_range = max(high - low, abs(high - float(previous_close)), abs(low - float(previous_close)))
    state["tr"] = _ema(state["tr"], true_range, adx_length)
    state["plus_dm"] = _ema(state["plus_dm"], plus_dm, adx_length)
    state["minus_dm"] = _ema(state["minus_dm"], minus_dm, adx_length)
    denominator = float(state["tr"])
    plus_di = 0.0 if denominator <= 1e-15 else 100.0 * float(state["plus_dm"]) / denominator
    minus_di = 0.0 if denominator <= 1e-15 else 100.0 * float(state["minus_dm"]) / denominator
    total = plus_di + minus_di
    dx = 0.0 if total <= 1e-15 else 100.0 * abs(plus_di - minus_di) / total
    state["adx"] = _ema(state["adx"], dx, adx_length)
    state["adx_history"].append(float(state["adx"]))
    if len(state["adx_history"]) > 4:
        state["adx_history"].pop(0)
    atr_low = low - atr_multiplier * float(state["tr"])
    atr_high = high + atr_multiplier * float(state["tr"])
    state["trail_low"] = atr_low if state["trail_low"] is None or low <= float(state["trail_low"]) else max(atr_low, float(state["trail_low"]))
    state["trail_high"] = atr_high if state["trail_high"] is None or high >= float(state["trail_high"]) else min(atr_high, float(state["trail_high"]))
    if len(state["adx_history"]) < 4 or len(state["volumes"]) < 20:
        return False, False, False, False
    rising = state["adx_history"][-1] > state["adx_history"][0]
    falling = state["adx_history"][-1] < state["adx_history"][-2]
    volume_ok = volume > volume_multiplier * sum(state["volumes"]) / 20.0
    long_entry = rising and plus_di > minus_di and close >= float(state["ema"]) and volume_ok
    short_entry = rising and minus_di > plus_di and close <= float(state["ema"]) and volume_ok
    long_exit = falling or low <= float(state["trail_low"])
    short_exit = falling or high >= float(state["trail_high"])
    return long_entry, short_entry, long_exit, short_exit


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.dmi_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close", "volume"])
    context.set_warmup(1200)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    adx_length = int(context.params.get("adx_length", 14))
    adx_threshold = float(context.params.get("adx_threshold", 23.0))
    ema_length = int(context.params.get("ema_length", 200))
    volume_multiplier = float(context.params.get("volume_multiplier", 2.0))
    atr_multiplier = float(context.params.get("atr_multiplier", 2.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [adx_length, ema_length, volume_multiplier, atr_multiplier]
    state = dict(g.dmi_state or {})
    fields = ["high", "low", "close", "volume"]
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(max(800, ema_length * 4), "1h", fields, g.symbol)
        if len(bars) < max(800, ema_length * 4):
            return
        state = _new_state(signature)
        signals = (False, False, False, False)
        for index in range(len(bars)):
            signals = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]), float(bars["volume"].iloc[index]), adx_length, ema_length, volume_multiplier, atr_multiplier)
    else:
        bars = get_history(1, "1h", fields, g.symbol)
        if len(bars) < 1:
            return
        signals = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]), float(bars["volume"].iloc[-1]), adx_length, ema_length, volume_multiplier, atr_multiplier)
    g.dmi_state = state
    long_entry, short_entry, long_exit, short_exit = signals
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_entry and float(state["adx"]) > adx_threshold and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="adx_dmi_long")
    elif short_entry and float(state["adx"]) > adx_threshold and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="adx_dmi_short")
    elif amount > 0 and long_exit:
        order_target_percent(g.symbol, 0.0, reason="adx_dmi_long_exit")
    elif amount < 0 and short_exit:
        order_target_percent(g.symbol, 0.0, reason="adx_dmi_short_exit")
