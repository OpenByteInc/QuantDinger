"""Open Close Cross Strategy Norepaint Version By Justunclel rewritten for QuantDinger Strategy API V2."""

# @param fast_length int 10 range=2:100:1
# @param slow_length int 30 range=3:250:1
# @param trend_length int 80 range=5:500:1
# @param rsi_length int 14 range=2:100:1
# @param breakout_length int 20 range=3:250:1
# @param stop_loss_pct float 0.030000 range=0.001:0.3:0.001
# @param take_profit_pct float 0.060000 range=0:1:0.001
# @param trailing_stop_pct float 0.000000 range=0:0.3:0.001
# @param trailing_activation_pct float 0.000000 range=0:1:0.001
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _average(values, length):
    if len(values) < length:
        return None
    return sum(values[-length:]) / float(length)


def _deviation(values, length, average):
    if len(values) < length or average is None:
        return None
    return (sum((value - average) * (value - average) for value in values[-length:]) / float(length)) ** 0.5


def _cross_up(previous_value, previous_level, value, level):
    return previous_value is not None and previous_level is not None and float(previous_value) <= float(previous_level) and float(value) > float(level)


def _cross_down(previous_value, previous_level, value, level):
    return previous_value is not None and previous_level is not None and float(previous_value) >= float(previous_level) and float(value) < float(level)


def _new_state(signature):
    return {
        "signature": signature,
        "closes": [], "opens": [], "highs": [], "lows": [], "volumes": [], "middles": [],
        "fast": None, "slow": None, "trend": None, "signal": None, "open_ema": None,
        "avg_gain": None, "avg_loss": None, "atr": None,
        "previous_close": None, "previous_fast": None, "previous_slow": None,
        "previous_signal": None, "previous_rsi": None, "previous_basis": None,
        "previous_upper": None, "previous_lower": None, "previous_vwap": None,
        "previous_trend": None, "previous_ao": None, "previous_stoch": None,
    }


def _regression_slope(values, length):
    if len(values) < length:
        return 0.0
    sample = values[-length:]
    center = (length - 1) / 2.0
    denominator = sum((index - center) * (index - center) for index in range(length))
    if denominator <= 1e-15:
        return 0.0
    mean = sum(sample) / float(length)
    return sum((index - center) * (sample[index] - mean) for index in range(length)) / denominator


def _step(state, open_price, high, low, close, volume, mode, fast_length, slow_length, trend_length, rsi_length, breakout_length):
    previous_close = state["previous_close"]
    previous_fast = state["fast"]
    previous_slow = state["slow"]
    previous_signal = state["signal"]
    previous_rsi = state["previous_rsi"]
    previous_trend = state["trend"]
    previous_open_ema = state["open_ema"]
    prior_high = max(state["highs"][-breakout_length:]) if len(state["highs"]) >= breakout_length else None
    prior_low = min(state["lows"][-breakout_length:]) if len(state["lows"]) >= breakout_length else None

    state["fast"] = _ema(state["fast"], close, fast_length)
    state["slow"] = _ema(state["slow"], close, slow_length)
    state["trend"] = _ema(state["trend"], close, trend_length)
    state["open_ema"] = _ema(state["open_ema"], open_price, fast_length)
    macd = float(state["fast"]) - float(state["slow"])
    state["signal"] = _ema(state["signal"], macd, 9)

    change = 0.0 if previous_close is None else close - float(previous_close)
    gain = max(change, 0.0)
    loss = max(-change, 0.0)
    state["avg_gain"] = gain if state["avg_gain"] is None else (float(state["avg_gain"]) * (rsi_length - 1) + gain) / float(rsi_length)
    state["avg_loss"] = loss if state["avg_loss"] is None else (float(state["avg_loss"]) * (rsi_length - 1) + loss) / float(rsi_length)
    rsi = 100.0 if float(state["avg_loss"]) <= 1e-15 else 100.0 - 100.0 / (1.0 + float(state["avg_gain"]) / float(state["avg_loss"]))
    true_range = high - low if previous_close is None else max(high - low, abs(high - float(previous_close)), abs(low - float(previous_close)))
    state["atr"] = true_range if state["atr"] is None else (float(state["atr"]) * (rsi_length - 1) + true_range) / float(rsi_length)

    state["closes"].append(close)
    state["opens"].append(open_price)
    state["highs"].append(high)
    state["lows"].append(low)
    state["volumes"].append(max(volume, 0.0))
    state["middles"].append((high + low) / 2.0)
    keep = max(trend_length, breakout_length, 60) + 5
    for key in ("closes", "opens", "highs", "lows", "volumes", "middles"):
        state[key] = state[key][-keep:]

    basis = _average(state["closes"], slow_length)
    deviation = _deviation(state["closes"], slow_length, basis)
    upper = None if basis is None or deviation is None else basis + 2.0 * deviation
    lower = None if basis is None or deviation is None else basis - 2.0 * deviation
    volume_sum = sum(state["volumes"][-breakout_length:])
    vwap = None if volume_sum <= 1e-15 or len(state["volumes"]) < breakout_length else sum(
        state["closes"][-breakout_length + index] * state["volumes"][-breakout_length + index]
        for index in range(breakout_length)
    ) / volume_sum
    range_high = max(state["highs"][-breakout_length:]) if len(state["highs"]) >= breakout_length else None
    range_low = min(state["lows"][-breakout_length:]) if len(state["lows"]) >= breakout_length else None
    stochastic = 50.0 if range_high is None or range_low is None or range_high <= range_low else (close - range_low) / (range_high - range_low) * 100.0
    fast_middle = _average(state["middles"], 5)
    slow_middle = _average(state["middles"], 34)
    ao = 0.0 if fast_middle is None or slow_middle is None else float(fast_middle) - float(slow_middle)
    average_volume = _average(state["volumes"], breakout_length)
    volume_ok = average_volume is None or volume >= float(average_volume) * 0.8
    trend_up = float(state["fast"]) > float(state["slow"]) and close > float(state["trend"])
    trend_down = float(state["fast"]) < float(state["slow"]) and close < float(state["trend"])
    fast_cross_up = _cross_up(previous_fast, previous_slow, state["fast"], state["slow"])
    fast_cross_down = _cross_down(previous_fast, previous_slow, state["fast"], state["slow"])
    macd_cross_up = _cross_up(previous_fast - previous_slow if previous_fast is not None and previous_slow is not None else None, previous_signal, macd, state["signal"])
    macd_cross_down = _cross_down(previous_fast - previous_slow if previous_fast is not None and previous_slow is not None else None, previous_signal, macd, state["signal"])
    rsi_cross_up = previous_rsi is not None and float(previous_rsi) <= 45.0 and rsi > 45.0
    rsi_cross_down = previous_rsi is not None and float(previous_rsi) >= 55.0 and rsi < 55.0

    long_signal = fast_cross_up
    short_signal = fast_cross_down
    if mode == "ema_rsi":
        long_signal = fast_cross_up and rsi >= 45.0
        short_signal = fast_cross_down and rsi <= 55.0
    elif mode == "pattern":
        double_low = len(state["lows"]) >= 5 and low <= min(state["lows"][-5:-1]) * 1.003 and close > open_price
        double_high = len(state["highs"]) >= 5 and high >= max(state["highs"][-5:-1]) * 0.997 and close < open_price
        long_signal = (double_low and rsi < 55.0) or fast_cross_up
        short_signal = (double_high and rsi > 45.0) or fast_cross_down
    elif mode == "rsi_mean":
        long_signal = rsi_cross_up or (lower is not None and close < lower and close > open_price)
        short_signal = rsi_cross_down or (upper is not None and close > upper and close < open_price)
    elif mode == "supertrend":
        band = float(state["atr"]) * 2.0
        long_signal = (previous_close is not None and previous_trend is not None and float(previous_close) <= float(previous_trend) + band and close > float(state["trend"]) + band * 0.25) or fast_cross_up
        short_signal = (previous_close is not None and previous_trend is not None and float(previous_close) >= float(previous_trend) - band and close < float(state["trend"]) - band * 0.25) or fast_cross_down
    elif mode in ("breakout", "session_breakout", "trendline"):
        long_signal = prior_high is not None and close > prior_high and volume_ok
        short_signal = prior_low is not None and close < prior_low and volume_ok
    elif mode == "retest":
        long_signal = trend_up and low <= float(state["fast"]) and close > float(state["fast"])
        short_signal = trend_down and high >= float(state["fast"]) and close < float(state["fast"])
    elif mode in ("sweep", "wyckoff"):
        long_signal = prior_low is not None and low < prior_low and close > prior_low
        short_signal = prior_high is not None and high > prior_high and close < prior_high
    elif mode == "ao_stoch":
        long_signal = state["previous_ao"] is not None and float(state["previous_ao"]) <= 0 < ao and stochastic < 70.0
        short_signal = state["previous_ao"] is not None and float(state["previous_ao"]) >= 0 > ao and stochastic > 30.0
    elif mode in ("dip", "grid_pullback"):
        long_signal = trend_up and (rsi_cross_up or (lower is not None and low <= lower))
        short_signal = trend_down and (rsi_cross_down or (upper is not None and high >= upper))
    elif mode == "stoch":
        long_signal = state["previous_stoch"] is not None and float(state["previous_stoch"]) <= 25.0 < stochastic
        short_signal = state["previous_stoch"] is not None and float(state["previous_stoch"]) >= 75.0 > stochastic
    elif mode in ("macd", "macd_bb"):
        long_signal = macd_cross_up and (mode != "macd_bb" or lower is None or close >= lower)
        short_signal = macd_cross_down and (mode != "macd_bb" or upper is None or close <= upper)
    elif mode == "rsi_trend":
        long_signal = trend_up and rsi_cross_up
        short_signal = trend_down and rsi_cross_down
    elif mode == "divergence":
        recent_low = min(state["lows"][-10:-1]) if len(state["lows"]) >= 10 else None
        recent_high = max(state["highs"][-10:-1]) if len(state["highs"]) >= 10 else None
        long_signal = (recent_low is not None and low < recent_low and previous_rsi is not None and rsi > float(previous_rsi)) or rsi_cross_up
        short_signal = (recent_high is not None and high > recent_high and previous_rsi is not None and rsi < float(previous_rsi)) or rsi_cross_down
    elif mode == "divergence_slow":
        recent_low = min(state["lows"][-50:-1]) if len(state["lows"]) >= 50 else None
        recent_high = max(state["highs"][-50:-1]) if len(state["highs"]) >= 50 else None
        long_signal = recent_low is not None and low < recent_low and previous_rsi is not None and rsi < 42.0 and rsi > float(previous_rsi) + 1.0
        short_signal = recent_high is not None and high > recent_high and previous_rsi is not None and rsi > 58.0 and rsi < float(previous_rsi) - 1.0
    elif mode == "elder":
        long_signal = previous_close is not None and previous_slow is not None and high - float(state["slow"]) > 0 >= float(previous_close) - float(previous_slow)
        short_signal = previous_close is not None and previous_slow is not None and low - float(state["slow"]) < 0 <= float(previous_close) - float(previous_slow)
    elif mode == "fibonacci":
        if range_high is not None and range_low is not None:
            retrace_up = range_low + (range_high - range_low) * 0.618
            retrace_down = range_high - (range_high - range_low) * 0.618
            long_signal = _cross_up(previous_close, state["previous_basis"], close, retrace_up)
            short_signal = _cross_down(previous_close, state["previous_basis"], close, retrace_down)
    elif mode == "channel":
        long_signal = prior_high is not None and close > prior_high
        short_signal = prior_low is not None and close < prior_low
    elif mode == "fractal":
        long_signal = len(state["lows"]) >= 5 and state["lows"][-3] == min(state["lows"][-5:]) and trend_up
        short_signal = len(state["highs"]) >= 5 and state["highs"][-3] == max(state["highs"][-5:]) and trend_down
    elif mode == "bollinger":
        long_signal = lower is not None and _cross_up(previous_close, state["previous_lower"], close, lower)
        short_signal = upper is not None and _cross_down(previous_close, state["previous_upper"], close, upper)
    elif mode == "gap":
        gap_long = previous_close is not None and open_price < float(previous_close) * 0.995 and close > open_price
        gap_short = previous_close is not None and open_price > float(previous_close) * 1.005 and close < open_price
        long_signal = gap_long or fast_cross_up
        short_signal = gap_short or fast_cross_down
    elif mode == "vwap":
        long_signal = vwap is not None and _cross_up(previous_close, state["previous_vwap"], close, vwap) and rsi >= 45.0
        short_signal = vwap is not None and _cross_down(previous_close, state["previous_vwap"], close, vwap) and rsi <= 55.0
    elif mode == "scalper":
        long_signal = fast_cross_up and stochastic < 80.0
        short_signal = fast_cross_down and stochastic > 20.0
    elif mode == "ichimoku":
        conversion = (max(state["highs"][-9:]) + min(state["lows"][-9:])) / 2.0 if len(state["highs"]) >= 26 else close
        base = (max(state["highs"][-26:]) + min(state["lows"][-26:])) / 2.0 if len(state["highs"]) >= 26 else close
        long_signal = conversion > base and previous_fast is not None and float(previous_fast) <= float(previous_slow) and close > max(conversion, base)
        short_signal = conversion < base and previous_fast is not None and float(previous_fast) >= float(previous_slow) and close < min(conversion, base)
    elif mode == "regression":
        slope = _regression_slope(state["closes"], breakout_length)
        long_signal = slope > 0 and prior_high is not None and close > prior_high
        short_signal = slope < 0 and prior_low is not None and close < prior_low
    elif mode == "macd_pullback":
        long_signal = trend_up and low <= float(state["slow"]) and close > float(state["fast"]) and macd > float(state["signal"])
        short_signal = trend_down and high >= float(state["slow"]) and close < float(state["fast"]) and macd < float(state["signal"])
    elif mode == "momentum":
        momentum = 0.0 if len(state["closes"]) < 10 else close - state["closes"][-10]
        previous_momentum = 0.0 if len(state["closes"]) < 11 else state["closes"][-2] - state["closes"][-11]
        long_signal = previous_momentum <= 0 < momentum
        short_signal = previous_momentum >= 0 > momentum
    elif mode == "ema_trend":
        long_signal = fast_cross_up and close > float(state["trend"])
        short_signal = fast_cross_down and close < float(state["trend"])
    elif mode == "open_close":
        long_signal = _cross_up(previous_fast, previous_open_ema, state["fast"], state["open_ema"])
        short_signal = _cross_down(previous_fast, previous_open_ema, state["fast"], state["open_ema"])
    elif mode == "adx":
        strength = abs(float(state["fast"]) - float(state["slow"])) / max(float(state["atr"]), 1e-15)
        long_signal = fast_cross_up and strength >= 0.25
        short_signal = fast_cross_down and strength >= 0.25
    elif mode == "alligator":
        long_signal = fast_cross_up and float(state["slow"]) > float(state["trend"])
        short_signal = fast_cross_down and float(state["slow"]) < float(state["trend"])
    elif mode == "zero_lag":
        lag = max(1, (slow_length - 1) // 2)
        zero_lag_value = close if len(state["closes"]) <= lag else close + (close - state["closes"][-lag - 1])
        zero_lag = _ema(previous_fast, zero_lag_value, fast_length)
        long_signal = _cross_up(previous_fast, previous_slow, zero_lag, state["slow"])
        short_signal = _cross_down(previous_fast, previous_slow, zero_lag, state["slow"])

    state["previous_close"] = close
    state["previous_fast"] = state["fast"]
    state["previous_slow"] = state["slow"]
    state["previous_signal"] = state["signal"]
    state["previous_rsi"] = rsi
    state["previous_basis"] = basis
    state["previous_upper"] = upper
    state["previous_lower"] = lower
    state["previous_vwap"] = vwap
    state["previous_trend"] = state["trend"]
    state["previous_ao"] = ao
    state["previous_stoch"] = stochastic
    return long_signal, short_signal


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.port_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["open", "high", "low", "close", "volume"])
    context.set_warmup(400)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    fast_length = int(context.params.get("fast_length", 10))
    slow_length = int(context.params.get("slow_length", 30))
    trend_length = int(context.params.get("trend_length", 80))
    rsi_length = int(context.params.get("rsi_length", 14))
    breakout_length = int(context.params.get("breakout_length", 20))
    stop_loss_pct = float(context.params.get("stop_loss_pct", 0.030000))
    take_profit_pct = float(context.params.get("take_profit_pct", 0.060000))
    trailing_stop_pct = float(context.params.get("trailing_stop_pct", 0.000000))
    trailing_activation_pct = float(context.params.get("trailing_activation_pct", 0.000000))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [fast_length, slow_length, trend_length, rsi_length, breakout_length]
    state = dict(g.port_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(240, trend_length * 4, breakout_length * 8)
    bars = get_history(required if cold else 1, "1h", ["open", "high", "low", "close", "volume"], g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    long_signal = short_signal = False
    for _, row in bars.iterrows():
        long_signal, short_signal = _step(
            state,
            float(row["open"]), float(row["high"]), float(row["low"]),
            float(row["close"]), float(row["volume"]), "open_close",
            fast_length, slow_length, trend_length, rsi_length, breakout_length,
        )
    g.port_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    close = float(state["previous_close"] or 0.0)
    atr_ratio = float(state["atr"] or 0.0) / max(close, 1e-15)
    effective_stop = max(stop_loss_pct, min(0.2, atr_ratio * 1.5)) if stop_loss_pct > 0 else 0.0
    effective_take = max(take_profit_pct, effective_stop * 1.5) if take_profit_pct > 0 else 0.0
    if long_signal and amount <= 0:
        order_target_percent(
            g.symbol, target_pct, reason="open_close_cross_strategy_norepaint__long",
            stop_loss_pct=effective_stop, take_profit_pct=effective_take,
            trailing_stop_pct=trailing_stop_pct,
            trailing_activation_pct=trailing_activation_pct,
        )
    elif short_signal and amount >= 0:
        order_target_percent(
            g.symbol, -target_pct, reason="open_close_cross_strategy_norepaint__short",
            stop_loss_pct=effective_stop, take_profit_pct=effective_take,
            trailing_stop_pct=trailing_stop_pct,
            trailing_activation_pct=trailing_activation_pct,
        )
