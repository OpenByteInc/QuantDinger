"""Polyfactor SuperTrend ensemble with a median oscillator trend trigger."""

# @param atr_length int 10 range=2:100:1
# @param starting_factor float 0.618 range=0:10:0.01
# @param factor_increment float 0.382 range=0:5:0.01
# @param outer_factor float 15 range=1:20:0.5
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _new_state(signature):
    holders = []
    for _index in range(20):
        holders.append({"upper": None, "lower": None, "trend": 0})
    return {"signature": signature, "holders": holders, "atr": None, "previous_source": None, "outer_atr": None, "outer_upper": None, "outer_lower": None, "outer_trend": 0}


def _step(state, high, low, close, atr_length, starting_factor, factor_increment, outer_factor):
    source = (high + low + close) / 3.0
    previous_source = state["previous_source"]
    true_range = high - low if previous_source is None else max(high - low, abs(high - float(previous_source)), abs(low - float(previous_source)))
    state["atr"] = _ema(state["atr"], true_range, atr_length)
    differences = []
    factor = starting_factor
    for holder in state["holders"]:
        upper_candidate = (high + low) / 2.0 + factor * float(state["atr"])
        lower_candidate = (high + low) / 2.0 - factor * float(state["atr"])
        previous_upper = holder["upper"]
        previous_lower = holder["lower"]
        trend = int(holder["trend"])
        if previous_upper is None or previous_lower is None:
            upper = upper_candidate
            lower = lower_candidate
            trend = 1 if close >= source else -1
        else:
            if source > float(previous_upper):
                trend = 1
            elif source < float(previous_lower):
                trend = -1
            upper = upper_candidate if previous_source is None or float(previous_source) >= float(previous_upper) else min(upper_candidate, float(previous_upper))
            lower = lower_candidate if previous_source is None or float(previous_source) <= float(previous_lower) else max(lower_candidate, float(previous_lower))
        holder["upper"] = upper
        holder["lower"] = lower
        holder["trend"] = trend
        output = lower if trend > 0 else upper
        differences.append(source - output)
        factor += factor_increment
    ordered = sorted(differences)
    median = (ordered[9] + ordered[10]) / 2.0
    previous_median = state.get("previous_median")
    outer_range = 0.0 if previous_median is None else abs(median - float(previous_median))
    state["outer_atr"] = _ema(state["outer_atr"], outer_range, atr_length)
    upper_candidate = median + outer_factor * float(state["outer_atr"])
    lower_candidate = median - outer_factor * float(state["outer_atr"])
    previous_upper = state["outer_upper"]
    previous_lower = state["outer_lower"]
    previous_trend = int(state["outer_trend"])
    trend = previous_trend
    if previous_upper is None or previous_lower is None:
        trend = 1 if median >= 0 else -1
        upper = upper_candidate
        lower = lower_candidate
    else:
        if median > float(previous_upper):
            trend = 1
        elif median < float(previous_lower):
            trend = -1
        upper = upper_candidate if previous_median is None or float(previous_median) >= float(previous_upper) else min(upper_candidate, float(previous_upper))
        lower = lower_candidate if previous_median is None or float(previous_median) <= float(previous_lower) else max(lower_candidate, float(previous_lower))
    state["outer_upper"] = upper
    state["outer_lower"] = lower
    state["outer_trend"] = trend
    state["previous_median"] = median
    state["previous_source"] = source
    return trend > 0 and median > 0 and not (previous_trend > 0), trend < 0 and median < 0 and not (previous_trend < 0), trend < 0, trend > 0


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.flexi_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(600)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    atr_length = int(context.params.get("atr_length", 10))
    starting_factor = float(context.params.get("starting_factor", 0.618))
    factor_increment = float(context.params.get("factor_increment", 0.382))
    outer_factor = float(context.params.get("outer_factor", 15.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [atr_length, starting_factor, factor_increment, outer_factor]
    state = dict(g.flexi_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(400, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 400:
            return
        state = _new_state(signature)
        signals = (False, False, False, False)
        for index in range(len(bars)):
            signals = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]), atr_length, starting_factor, factor_increment, outer_factor)
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        signals = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]), atr_length, starting_factor, factor_increment, outer_factor)
    g.flexi_state = state
    long_entry, short_entry, long_exit, short_exit = signals
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_entry and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="flexi_long")
    elif short_entry and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="flexi_short")
    elif amount > 0 and long_exit:
        order_target_percent(g.symbol, 0.0, reason="flexi_long_exit")
    elif amount < 0 and short_exit:
        order_target_percent(g.symbol, 0.0, reason="flexi_short_exit")
