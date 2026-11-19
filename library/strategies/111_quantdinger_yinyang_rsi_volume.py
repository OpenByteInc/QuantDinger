"""Volume- and RSI-shaped purchase zones with symmetric long/short exits."""

# @param length int 80 range=10:300:1
# @param stop_loss_multiplier float 0.1 range=0.01:10:0.01
# @param use_take_profit bool true
# @param use_stop_loss bool true
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * value + (1.0 - alpha) * float(previous)


def _vwma(values, volumes, length):
    if len(values) < length:
        return None
    denominator = sum(volumes[-length:])
    if denominator <= 1e-15:
        return sum(values[-length:]) / float(length)
    return sum(values[-length + index] * volumes[-length + index] for index in range(length)) / denominator


def _cross_up(previous_value, previous_level, value, level):
    return previous_value is not None and previous_level is not None and float(previous_value) <= float(previous_level) and value > level


def _cross_down(previous_value, previous_level, value, level):
    return previous_value is not None and previous_level is not None and float(previous_value) >= float(previous_level) and value < level


def _new_state(signature):
    return {"signature": signature, "highs": [], "lows": [], "volumes": [], "rolling_highs": [], "rolling_lows": [], "mid_differences": [], "mid_smoothed": None, "mid_vol": [], "mid_volumes": [], "previous_mid_vol": None, "avg_gain": None, "avg_loss": None, "previous_close": None, "previous_zone_high": None, "previous_zone_low": None, "previous_basis": None, "previous_stop_high": None, "previous_stop_low": None, "long_available": False, "short_available": False, "long_tp_available": False, "short_tp_available": False}


def _step(state, high, low, close, volume, length, stop_multiplier, use_take_profit, use_stop_loss):
    state["highs"].append(high)
    state["lows"].append(low)
    state["volumes"].append(max(volume, 0.0))
    state["highs"] = state["highs"][-length:]
    state["lows"] = state["lows"][-length:]
    state["volumes"] = state["volumes"][-length:]
    if len(state["highs"]) < length:
        state["previous_close"] = close
        return False, False, False, False
    state["rolling_highs"].append(max(state["highs"]))
    state["rolling_lows"].append(min(state["lows"]))
    state["rolling_highs"] = state["rolling_highs"][-length:]
    state["rolling_lows"] = state["rolling_lows"][-length:]
    if len(state["rolling_highs"]) < length:
        state["previous_close"] = close
        return False, False, False, False
    mid_high = _vwma(state["rolling_highs"], state["volumes"], length)
    mid_low = _vwma(state["rolling_lows"], state["volumes"], length)
    middle = (float(mid_high) + float(mid_low)) / 2.0
    state["mid_smoothed"] = _ema(state["mid_smoothed"], middle, length)
    denominator = sum(state["volumes"])
    average_volume = sum(value * value for value in state["volumes"]) / max(denominator, 1e-15)
    volume_ratio = volume / max(average_volume, 1e-15)
    state["mid_vol"].append(float(state["mid_smoothed"]) * volume_ratio)
    state["mid_volumes"].append(max(volume, 0.0))
    state["mid_vol"] = state["mid_vol"][-3:]
    state["mid_volumes"] = state["mid_volumes"][-3:]
    filtered_middle = _vwma(state["mid_vol"], state["mid_volumes"], 3)
    if filtered_middle is None:
        state["previous_close"] = close
        return False, False, False, False
    previous_filtered = filtered_middle if state["previous_mid_vol"] is None else float(state["previous_mid_vol"])
    change = filtered_middle - previous_filtered
    gain = max(change, 0.0)
    loss = max(-change, 0.0)
    state["avg_gain"] = gain if state["avg_gain"] is None else (float(state["avg_gain"]) * (length - 1) + gain) / float(length)
    state["avg_loss"] = loss if state["avg_loss"] is None else (float(state["avg_loss"]) * (length - 1) + loss) / float(length)
    rsi = 100.0 if float(state["avg_loss"]) <= 1e-15 else 100.0 - 100.0 / (1.0 + float(state["avg_gain"]) / float(state["avg_loss"]))
    state["mid_differences"].append(float(mid_high) - float(mid_low))
    state["mid_differences"] = state["mid_differences"][-length:]
    difference = sum(state["mid_differences"]) / float(len(state["mid_differences"]))
    addition = rsi * 0.01 * difference
    zone_high = float(state["mid_smoothed"]) + addition
    zone_low = float(state["mid_smoothed"]) - addition
    basis = (zone_high + zone_low) / 2.0
    stop_high = zone_high * (1.0 + stop_multiplier * 0.01)
    stop_low = zone_low * (1.0 - stop_multiplier * 0.01)
    previous_close = state["previous_close"]
    long_entry_zone = _cross_down(previous_close, state["previous_zone_low"], close, zone_low)
    short_entry_zone = _cross_up(previous_close, state["previous_zone_high"], close, zone_high)
    long_start = _cross_up(previous_close, state["previous_zone_low"], close, zone_low) and bool(state["long_available"])
    short_start = _cross_down(previous_close, state["previous_zone_high"], close, zone_high) and bool(state["short_available"])
    long_end = _cross_up(previous_close, state["previous_zone_high"], close, zone_high)
    short_end = _cross_down(previous_close, state["previous_zone_low"], close, zone_low)
    long_stop = _cross_down(previous_close, state["previous_stop_low"], close, stop_low)
    short_stop = _cross_up(previous_close, state["previous_stop_high"], close, stop_high)
    basis_up = _cross_up(previous_close, state["previous_basis"], close, basis)
    basis_down = _cross_down(previous_close, state["previous_basis"], close, basis)
    long_take_profit = basis_down and bool(state["long_tp_available"])
    short_take_profit = basis_up and bool(state["short_tp_available"])
    state["long_available"] = True if long_entry_zone or _cross_down(previous_close, state["previous_zone_high"], close, zone_high) else False if long_start else bool(state["long_available"])
    state["short_available"] = True if short_entry_zone or _cross_up(previous_close, state["previous_zone_low"], close, zone_low) else False if short_start else bool(state["short_available"])
    state["long_tp_available"] = True if basis_up else False if long_end else bool(state["long_tp_available"])
    state["short_tp_available"] = True if basis_down else False if short_end else bool(state["short_tp_available"])
    state["previous_mid_vol"] = filtered_middle
    state["previous_close"] = close
    state["previous_zone_high"] = zone_high
    state["previous_zone_low"] = zone_low
    state["previous_basis"] = basis
    state["previous_stop_high"] = stop_high
    state["previous_stop_low"] = stop_low
    long_exit = long_end or (use_stop_loss and long_stop) or (use_take_profit and long_take_profit)
    short_exit = short_end or (use_stop_loss and short_stop) or (use_take_profit and short_take_profit)
    return long_start, short_start, long_exit, short_exit


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.yinyang_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close", "volume"])
    context.set_warmup(1800)
    context.set_metadata(direction_mode="one_way", strategy_family="mean_reversion")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    length = int(context.params.get("length", 80))
    stop_multiplier = float(context.params.get("stop_loss_multiplier", 0.1))
    use_take_profit = bool(context.params.get("use_take_profit", True))
    use_stop_loss = bool(context.params.get("use_stop_loss", True))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [length, stop_multiplier]
    state = dict(g.yinyang_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(1200, length * 15)
    bars = get_history(required if cold else 1, "1h", ["high", "low", "close", "volume"], g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    long_start = short_start = long_exit = short_exit = False
    for _, row in bars.iterrows():
        long_start, short_start, long_exit, short_exit = _step(state, float(row["high"]), float(row["low"]), float(row["close"]), float(row["volume"]), length, stop_multiplier, use_take_profit, use_stop_loss)
    g.yinyang_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if amount > 0 and long_exit:
        order_target_percent(g.symbol, 0.0, reason="yinyang_long_exit")
    elif amount < 0 and short_exit:
        order_target_percent(g.symbol, 0.0, reason="yinyang_short_exit")
    elif long_start and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="yinyang_long")
    elif short_start and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="yinyang_short")
