"""
QuantDinger HHLL Bollinger
One-way volatility breakout strategy with optional signal reversal.
"""

# @param length int 29 range=2:300:1
# @param reverse bool true
# @param target_pct float 0.95 range=0.05:1:0.05


def _latest_signal(high, low, close, length, reverse):
    typical = (high + low + close) / 3.0
    middle = typical.rolling(length, min_periods=length).mean()
    deviation = typical.rolling(length, min_periods=length).std(ddof=0)
    upper = middle + 2.0 * deviation
    lower = middle - 2.0 * deviation
    move = (upper - lower) / 2.0
    upper_extreme = upper + move
    lower_extreme = lower - move
    position = 0
    previous_signal = 0
    current_signal = 0
    for index in range(1, len(close)):
        upper_value = float(upper_extreme.iloc[index - 1])
        lower_value = float(lower_extreme.iloc[index - 1])
        if upper_value != upper_value or lower_value != lower_value:
            continue
        if float(high.iloc[index]) > upper_value:
            position = -1
        if float(low.iloc[index]) < lower_value:
            position = 1
        previous_signal = current_signal
        current_signal = -position if reverse and position != 0 else position
    return position, previous_signal, current_signal


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.hhll_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(800)
    context.set_metadata(direction_mode="one_way", strategy_family="volatility")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    length = int(context.params.get("length", 29))
    reverse = bool(context.params.get("reverse", True))
    target_pct = float(context.params.get("target_pct", 0.95))
    if length < 2:
        return
    state = dict(g.hhll_state or {})
    reset = (
        not state
        or int(state.get("length") or 0) != length
        or bool(state.get("reverse")) != reverse
    )
    if reset:
        required = max(10 * length, 180)
        bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < required:
            return
        position, previous_signal, current_signal = _latest_signal(
            bars["high"],
            bars["low"],
            bars["close"],
            length,
            reverse,
        )
        state = {
            "length": length,
            "reverse": reverse,
            "position": position,
            "signal": current_signal,
        }
    else:
        bars = get_history(length + 1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < length + 1:
            return
        typical = (bars["high"] + bars["low"] + bars["close"]) / 3.0
        middle = typical.rolling(length, min_periods=length).mean()
        deviation = typical.rolling(length, min_periods=length).std(ddof=0)
        upper_extreme = middle + 4.0 * deviation
        lower_extreme = middle - 4.0 * deviation
        upper_value = float(upper_extreme.iloc[-2])
        lower_value = float(lower_extreme.iloc[-2])
        if upper_value != upper_value or lower_value != lower_value:
            return
        position = int(state["position"])
        if float(bars["high"].iloc[-1]) > upper_value:
            position = -1
        if float(bars["low"].iloc[-1]) < lower_value:
            position = 1
        previous_signal = int(state["signal"])
        current_signal = -position if reverse and position != 0 else position
        state["position"] = position
        state["signal"] = current_signal
    g.hhll_state = state
    if current_signal == 0 or current_signal == previous_signal:
        return
    amount = float(get_position(g.symbol).amount or 0.0)
    if current_signal > 0 and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="hhll_regime_long")
    elif current_signal < 0 and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="hhll_regime_short")
