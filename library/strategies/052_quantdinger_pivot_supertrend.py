"""Pivot-center SuperTrend reversal strategy."""

# @param pivot_period int 2 range=1:50:1
# @param atr_factor float 3.0 range=0.1:20:0.1
# @param atr_period int 10 range=1:300:1
# @param only_long bool false
# @param target_pct float 0.95 range=0.05:1:0.05


def _rma_step(state, value, length):
    current = state.get("value")
    if current is not None:
        current = (float(current) * (length - 1) + float(value)) / float(length)
        state["value"] = current
        return current
    state["seed"].append(float(value))
    if len(state["seed"]) < length:
        return float("nan")
    current = sum(state["seed"][-length:]) / float(length)
    state["value"] = current
    state["seed"] = []
    return current


def _new_state(signature):
    return {
        "signature": signature,
        "highs": [],
        "lows": [],
        "previous_close": None,
        "atr": {"value": None, "seed": []},
        "center": None,
        "upper_trail": None,
        "lower_trail": None,
        "trend": 1,
    }


def _step(state, high, low, close, pivot_period, factor, atr_period):
    previous_close = state["previous_close"]
    true_range = high - low if previous_close is None else max(
        high - low,
        abs(high - float(previous_close)),
        abs(low - float(previous_close)),
    )
    atr = _rma_step(state["atr"], true_range, atr_period)
    state["highs"].append(high)
    state["lows"].append(low)
    pivot_window = 2 * pivot_period + 1
    if len(state["highs"]) > pivot_window:
        state["highs"].pop(0)
        state["lows"].pop(0)
    pivot_value = None
    if len(state["highs"]) == pivot_window:
        pivot_high = float(state["highs"][pivot_period])
        pivot_low = float(state["lows"][pivot_period])
        if pivot_high >= max(state["highs"]):
            pivot_value = pivot_high
        elif pivot_low <= min(state["lows"]):
            pivot_value = pivot_low
    if pivot_value is not None:
        state["center"] = pivot_value if state["center"] is None else (2.0 * float(state["center"]) + pivot_value) / 3.0
    previous_trend = int(state["trend"])
    if state["center"] is None or atr != atr:
        state["previous_close"] = close
        return False, False
    upper_candidate = float(state["center"]) - factor * atr
    lower_candidate = float(state["center"]) + factor * atr
    previous_upper = state["upper_trail"]
    previous_lower = state["lower_trail"]
    if previous_upper is None:
        upper_trail = upper_candidate
    else:
        upper_trail = max(upper_candidate, float(previous_upper)) if previous_close is not None and float(previous_close) > float(previous_upper) else upper_candidate
    if previous_lower is None:
        lower_trail = lower_candidate
    else:
        lower_trail = min(lower_candidate, float(previous_lower)) if previous_close is not None and float(previous_close) < float(previous_lower) else lower_candidate
    trend = previous_trend
    if previous_lower is not None and close > float(previous_lower):
        trend = 1
    elif previous_upper is not None and close < float(previous_upper):
        trend = -1
    state["upper_trail"] = upper_trail
    state["lower_trail"] = lower_trail
    state["trend"] = trend
    state["previous_close"] = close
    return previous_trend < 0 and trend > 0, previous_trend > 0 and trend < 0


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.pivot_trend_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(600)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    pivot_period = int(context.params.get("pivot_period", 2))
    factor = float(context.params.get("atr_factor", 3.0))
    atr_period = int(context.params.get("atr_period", 10))
    only_long = bool(context.params.get("only_long", False))
    target_pct = float(context.params.get("target_pct", 0.95))
    if pivot_period < 1 or factor <= 0 or atr_period < 1:
        return
    signature = [pivot_period, factor, atr_period]
    state = dict(g.pivot_trend_state or {})
    if not state or list(state.get("signature") or []) != signature:
        required = max(30 * atr_period, 400)
        bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(signature)
        buy_signal = False
        sell_signal = False
        for index in range(len(bars)):
            buy_signal, sell_signal = _step(
                state,
                float(bars["high"].iloc[index]),
                float(bars["low"].iloc[index]),
                float(bars["close"].iloc[index]),
                pivot_period,
                factor,
                atr_period,
            )
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        buy_signal, sell_signal = _step(
            state,
            float(bars["high"].iloc[-1]),
            float(bars["low"].iloc[-1]),
            float(bars["close"].iloc[-1]),
            pivot_period,
            factor,
            atr_period,
        )
    g.pivot_trend_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy_signal and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="pivot_supertrend_long")
    elif sell_signal and amount > 0 and only_long:
        order_target_percent(g.symbol, 0.0, reason="pivot_supertrend_long_exit")
    elif sell_signal and amount >= 0 and not only_long:
        order_target_percent(g.symbol, -target_pct, reason="pivot_supertrend_short")
