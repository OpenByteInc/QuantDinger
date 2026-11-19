"""Moving regression band breakout with slope filter and trailing-band exits."""

# @param window int 80 range=5:500:1
# @param band_multiplier float 2.0 range=0:10:0.1
# @param allow_short bool false
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "closes": [], "tr": [], "previous_close": None, "previous_center": None, "previous_upper": None, "previous_lower": None}


def _regression(values):
    count = len(values)
    x_mean = (count - 1) / 2.0
    y_mean = sum(values) / float(count)
    denominator = sum((index - x_mean) * (index - x_mean) for index in range(count))
    slope = 0.0 if denominator <= 1e-15 else sum((index - x_mean) * (values[index] - y_mean) for index in range(count)) / denominator
    intercept = y_mean - slope * x_mean
    center = intercept + slope * (count - 1)
    error = (sum((values[index] - (intercept + slope * index)) ** 2 for index in range(count)) / float(count)) ** 0.5
    return center, slope, error


def _step(state, high, low, close, window, multiplier):
    previous_close = state["previous_close"]
    state["closes"].append(close)
    if len(state["closes"]) > window:
        state["closes"].pop(0)
    true_range = high - low if previous_close is None else max(high - low, abs(high - float(previous_close)), abs(low - float(previous_close)))
    state["tr"].append(true_range)
    if len(state["tr"]) > 14:
        state["tr"].pop(0)
    state["previous_close"] = close
    if len(state["closes"]) < window or len(state["tr"]) < 14:
        return False, False, False, False
    center, slope, error = _regression(state["closes"])
    upper = center + multiplier * error
    lower = center - multiplier * error
    previous_upper = state["previous_upper"]
    previous_lower = state["previous_lower"]
    previous_price = state.get("previous_price")
    state["previous_center"] = center
    state["previous_upper"] = upper
    state["previous_lower"] = lower
    state["previous_price"] = close
    if previous_upper is None or previous_lower is None or previous_price is None:
        return False, False, False, False
    long_entry = float(previous_price) <= float(previous_upper) and close > upper and slope > 0
    short_entry = float(previous_price) >= float(previous_lower) and close < lower and slope < 0
    atr = sum(state["tr"]) / 14.0
    return long_entry, short_entry, close < lower - atr, close > center


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.regression_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(1200)
    context.set_metadata(direction_mode="one_way", strategy_family="breakout")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    window = int(context.params.get("window", 80))
    multiplier = float(context.params.get("band_multiplier", 2.0))
    allow_short = bool(context.params.get("allow_short", False))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [window, multiplier, allow_short]
    state = dict(g.regression_state or {})
    required = max(500, window * 6)
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(signature)
        signals = (False, False, False, False)
        for index in range(len(bars)):
            signals = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]), window, multiplier)
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        signals = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]), window, multiplier)
    g.regression_state = state
    long_entry, short_entry, long_exit, short_exit = signals
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_entry and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="regression_breakout_long")
    elif short_entry and allow_short and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="regression_breakout_short")
    elif amount > 0 and long_exit:
        order_target_percent(g.symbol, 0.0, reason="regression_long_exit")
    elif amount < 0 and short_exit:
        order_target_percent(g.symbol, 0.0, reason="regression_short_exit")
