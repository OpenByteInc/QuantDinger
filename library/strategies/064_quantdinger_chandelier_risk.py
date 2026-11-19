"""Chandelier direction strategy with stop, take-profit and trailing exits."""

# @param atr_period int 22 range=1:300:1
# @param atr_multiplier float 3.0 range=0.1:20:0.1
# @param stop_loss_pct float 0.05 range=0:0.5:0.01
# @param take_profit_pct float 0.20 range=0:2:0.01
# @param trail_start_pct float 0.10 range=0:2:0.01
# @param trail_pct float 0.05 range=0.001:0.5:0.01
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "highs": [], "lows": [], "closes": [], "tr": [], "previous_close": None, "long_stop": None, "short_stop": None, "direction": 1, "entry_reference": None, "peak": None, "trough": None}


def _step(state, high, low, close, period, multiplier):
    previous_close = state["previous_close"]
    true_range = high - low if previous_close is None else max(high - low, abs(high - float(previous_close)), abs(low - float(previous_close)))
    for key, value in (("highs", high), ("lows", low), ("closes", close), ("tr", true_range)):
        state[key].append(value)
        if len(state[key]) > period:
            state[key].pop(0)
    if len(state["closes"]) < period:
        state["previous_close"] = close
        return False, False
    atr = sum(state["tr"]) / float(period)
    long_candidate = max(state["closes"]) - multiplier * atr
    short_candidate = min(state["closes"]) + multiplier * atr
    previous_long = state["long_stop"]
    previous_short = state["short_stop"]
    long_stop = long_candidate if previous_long is None or previous_close is None or float(previous_close) <= float(previous_long) else max(long_candidate, float(previous_long))
    short_stop = short_candidate if previous_short is None or previous_close is None or float(previous_close) >= float(previous_short) else min(short_candidate, float(previous_short))
    previous_direction = int(state["direction"])
    direction = previous_direction
    if previous_short is not None and close > float(previous_short):
        direction = 1
    elif previous_long is not None and close < float(previous_long):
        direction = -1
    state["long_stop"] = long_stop
    state["short_stop"] = short_stop
    state["direction"] = direction
    state["previous_close"] = close
    return direction == 1 and previous_direction == -1, direction == -1 and previous_direction == 1


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.chandelier_risk_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(600)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    period = int(context.params.get("atr_period", 22))
    multiplier = float(context.params.get("atr_multiplier", 3.0))
    stop_loss_pct = float(context.params.get("stop_loss_pct", 0.05))
    take_profit_pct = float(context.params.get("take_profit_pct", 0.20))
    trail_start_pct = float(context.params.get("trail_start_pct", 0.10))
    trail_pct = float(context.params.get("trail_pct", 0.05))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [period, multiplier]
    state = dict(g.chandelier_risk_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(max(360, period * 12), "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < max(360, period * 12):
            return
        state = _new_state(signature)
        buy = False
        sell = False
        for index in range(len(bars)):
            buy, sell = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]), period, multiplier)
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        buy, sell = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]), period, multiplier)
    high = float(bars["high"].iloc[-1])
    low = float(bars["low"].iloc[-1])
    close = float(bars["close"].iloc[-1])
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="chandelier_long")
        state["entry_reference"] = close
        state["peak"] = high
        state["trough"] = None
    elif sell and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="chandelier_short")
        state["entry_reference"] = close
        state["trough"] = low
        state["peak"] = None
    elif amount > 0:
        if state.get("entry_reference") is None:
            state["entry_reference"] = close
        state["peak"] = high if state.get("peak") is None else max(high, float(state["peak"]))
        entry = float(state["entry_reference"])
        exit_now = close <= entry * (1.0 - stop_loss_pct) or high >= entry * (1.0 + take_profit_pct) or (float(state["peak"]) >= entry * (1.0 + trail_start_pct) and close <= float(state["peak"]) * (1.0 - trail_pct))
        if exit_now:
            order_target_percent(g.symbol, 0.0, reason="chandelier_long_risk_exit")
            state["entry_reference"] = None
    elif amount < 0:
        if state.get("entry_reference") is None:
            state["entry_reference"] = close
        state["trough"] = low if state.get("trough") is None else min(low, float(state["trough"]))
        entry = float(state["entry_reference"])
        exit_now = close >= entry * (1.0 + stop_loss_pct) or low <= entry * (1.0 - take_profit_pct) or (float(state["trough"]) <= entry * (1.0 - trail_start_pct) and close >= float(state["trough"]) * (1.0 + trail_pct))
        if exit_now:
            order_target_percent(g.symbol, 0.0, reason="chandelier_short_risk_exit")
            state["entry_reference"] = None
    g.chandelier_risk_state = state
