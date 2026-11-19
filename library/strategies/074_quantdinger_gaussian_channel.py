"""Four-pole Gaussian channel crossover with a late-entry window."""

# @param poles int 4 range=1:9:1
# @param sampling_period int 144 range=2:500:1
# @param range_multiplier float 1.414 range=0:10:0.01
# @param lookback int 3 range=0:50:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature, poles):
    return {"signature": signature, "price_filters": [None] * poles, "range_filters": [None] * poles, "previous_close": None, "previous_source": None, "previous_line": None, "long_age": 999, "short_age": 999}


def _step(state, high, low, close, alpha, poles, range_multiplier, lookback):
    source = (high + low + close) / 3.0
    previous_close = state["previous_close"]
    true_range = high - low if previous_close is None else max(high - low, abs(high - float(previous_close)), abs(low - float(previous_close)))
    value = source
    for index in range(poles):
        previous = state["price_filters"][index]
        value = value if previous is None else alpha * value + (1.0 - alpha) * float(previous)
        state["price_filters"][index] = value
    range_value = true_range
    for index in range(poles):
        previous = state["range_filters"][index]
        range_value = range_value if previous is None else alpha * range_value + (1.0 - alpha) * float(previous)
        state["range_filters"][index] = range_value
    line = value
    previous_source = state["previous_source"]
    previous_line = state["previous_line"]
    cross_up = previous_source is not None and previous_line is not None and float(previous_source) <= float(previous_line) and source > line
    cross_down = previous_source is not None and previous_line is not None and float(previous_source) >= float(previous_line) and source < line
    state["long_age"] = 0 if cross_up else int(state["long_age"]) + 1
    state["short_age"] = 0 if cross_down else int(state["short_age"]) + 1
    state["previous_close"] = close
    state["previous_source"] = source
    state["previous_line"] = line
    return source > line and int(state["long_age"]) <= lookback, source < line and int(state["short_age"]) <= lookback


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.gaussian_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(1200)
    context.set_metadata(direction_mode="one_way", strategy_family="channel")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    poles = int(context.params.get("poles", 4))
    sampling_period = int(context.params.get("sampling_period", 144))
    range_multiplier = float(context.params.get("range_multiplier", 1.414))
    lookback = int(context.params.get("lookback", 3))
    target_pct = float(context.params.get("target_pct", 0.95))
    if poles < 1 or poles > 9 or sampling_period < 2:
        return
    beta = (1.0 - np.cos(4.0 * np.arcsin(1.0) / float(sampling_period))) / (np.power(1.414, 2.0 / float(poles)) - 1.0)
    alpha = -beta + np.sqrt(beta * beta + 2.0 * beta)
    signature = [poles, sampling_period, range_multiplier, lookback]
    state = dict(g.gaussian_state or {})
    required = max(800, sampling_period * 6)
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(signature, poles)
        go_long = False
        go_short = False
        for index in range(len(bars)):
            go_long, go_short = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]), alpha, poles, range_multiplier, lookback)
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        go_long, go_short = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]), alpha, poles, range_multiplier, lookback)
    g.gaussian_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if go_long and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="gaussian_long")
    elif go_short and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="gaussian_short")
