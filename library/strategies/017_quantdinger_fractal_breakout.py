"""
QuantDinger Fractal Breakout
One-way breakout strategy using confirmed plateau-aware fractal levels.
"""

# @param target_pct float 0.95 range=0.05:1:0.05


def _fractal_channels(high, low):
    upper = high.copy()
    lower = low.copy()
    upper[:] = float("nan")
    lower[:] = float("nan")
    upper_events = []
    lower_events = []
    for index in range(len(high)):
        up = False
        down = False
        if index >= 4:
            pivot_high = float(high.iloc[index - 2])
            pivot_low = float(low.iloc[index - 2])
            up = (
                float(high.iloc[index - 4]) < pivot_high
                and float(high.iloc[index - 3]) < pivot_high
                and float(high.iloc[index - 1]) < pivot_high
                and float(high.iloc[index]) < pivot_high
            )
            down = (
                float(low.iloc[index - 4]) > pivot_low
                and float(low.iloc[index - 3]) > pivot_low
                and float(low.iloc[index - 1]) > pivot_low
                and float(low.iloc[index]) > pivot_low
            )
        if not up and index >= 5:
            pivot_high = float(high.iloc[index - 2])
            up = (
                float(high.iloc[index - 5]) < pivot_high
                and float(high.iloc[index - 4]) < pivot_high
                and float(high.iloc[index - 3]) <= pivot_high
                and float(high.iloc[index - 1]) < pivot_high
                and float(high.iloc[index]) < pivot_high
            )
        if not down and index >= 5:
            pivot_low = float(low.iloc[index - 2])
            down = (
                float(low.iloc[index - 5]) > pivot_low
                and float(low.iloc[index - 4]) > pivot_low
                and float(low.iloc[index - 3]) >= pivot_low
                and float(low.iloc[index - 1]) > pivot_low
                and float(low.iloc[index]) > pivot_low
            )
        if not up and index >= 6:
            pivot_high = float(high.iloc[index - 2])
            up = (
                float(high.iloc[index - 6]) < pivot_high
                and float(high.iloc[index - 5]) < pivot_high
                and float(high.iloc[index - 4]) <= pivot_high
                and float(high.iloc[index - 3]) <= pivot_high
                and float(high.iloc[index - 1]) < pivot_high
                and float(high.iloc[index]) < pivot_high
            )
        if not down and index >= 6:
            pivot_low = float(low.iloc[index - 2])
            down = (
                float(low.iloc[index - 6]) > pivot_low
                and float(low.iloc[index - 5]) > pivot_low
                and float(low.iloc[index - 4]) >= pivot_low
                and float(low.iloc[index - 3]) >= pivot_low
                and float(low.iloc[index - 1]) > pivot_low
                and float(low.iloc[index]) > pivot_low
            )
        if not up and index >= 7:
            pivot_high = float(high.iloc[index - 2])
            up = (
                float(high.iloc[index - 7]) < pivot_high
                and float(high.iloc[index - 6]) < pivot_high
                and float(high.iloc[index - 5]) <= pivot_high
                and float(high.iloc[index - 4]) <= pivot_high
                and float(high.iloc[index - 3]) <= pivot_high
                and float(high.iloc[index - 1]) < pivot_high
                and float(high.iloc[index]) < pivot_high
            )
        if not down and index >= 7:
            pivot_low = float(low.iloc[index - 2])
            down = (
                float(low.iloc[index - 7]) > pivot_low
                and float(low.iloc[index - 6]) > pivot_low
                and float(low.iloc[index - 5]) >= pivot_low
                and float(low.iloc[index - 4]) >= pivot_low
                and float(low.iloc[index - 3]) >= pivot_low
                and float(low.iloc[index - 1]) > pivot_low
                and float(low.iloc[index]) > pivot_low
            )
        if not up and index >= 8:
            pivot_high = float(high.iloc[index - 2])
            up = (
                float(high.iloc[index - 8]) < pivot_high
                and float(high.iloc[index - 7]) < pivot_high
                and float(high.iloc[index - 6]) <= pivot_high
                and float(high.iloc[index - 5]) <= pivot_high
                and float(high.iloc[index - 4]) <= pivot_high
                and float(high.iloc[index - 3]) <= pivot_high
                and float(high.iloc[index - 1]) < pivot_high
                and float(high.iloc[index]) < pivot_high
            )
        if not down and index >= 8:
            pivot_low = float(low.iloc[index - 2])
            down = (
                float(low.iloc[index - 8]) > pivot_low
                and float(low.iloc[index - 7]) > pivot_low
                and float(low.iloc[index - 6]) >= pivot_low
                and float(low.iloc[index - 5]) >= pivot_low
                and float(low.iloc[index - 4]) >= pivot_low
                and float(low.iloc[index - 3]) >= pivot_low
                and float(low.iloc[index - 1]) > pivot_low
                and float(low.iloc[index]) > pivot_low
            )
        if up:
            upper_events.append(float(high.iloc[index - 2]))
        if down:
            lower_events.append(float(low.iloc[index - 2]))
        if len(upper_events) >= 2:
            upper.iloc[index] = upper_events[-2]
        if len(lower_events) >= 2:
            lower.iloc[index] = lower_events[-2]
    return upper, lower, upper_events, lower_events


def _current_fractal_flags(high, low):
    pivot_high = float(high.iloc[-3])
    pivot_low = float(low.iloc[-3])
    up = (
        (
            float(high.iloc[-5]) < pivot_high
            and float(high.iloc[-4]) < pivot_high
            and float(high.iloc[-2]) < pivot_high
            and float(high.iloc[-1]) < pivot_high
        )
        or (
            float(high.iloc[-6]) < pivot_high
            and float(high.iloc[-5]) < pivot_high
            and float(high.iloc[-4]) <= pivot_high
            and float(high.iloc[-2]) < pivot_high
            and float(high.iloc[-1]) < pivot_high
        )
        or (
            float(high.iloc[-7]) < pivot_high
            and float(high.iloc[-6]) < pivot_high
            and float(high.iloc[-5]) <= pivot_high
            and float(high.iloc[-4]) <= pivot_high
            and float(high.iloc[-2]) < pivot_high
            and float(high.iloc[-1]) < pivot_high
        )
        or (
            float(high.iloc[-8]) < pivot_high
            and float(high.iloc[-7]) < pivot_high
            and float(high.iloc[-6]) <= pivot_high
            and float(high.iloc[-5]) <= pivot_high
            and float(high.iloc[-4]) <= pivot_high
            and float(high.iloc[-2]) < pivot_high
            and float(high.iloc[-1]) < pivot_high
        )
        or (
            float(high.iloc[-9]) < pivot_high
            and float(high.iloc[-8]) < pivot_high
            and float(high.iloc[-7]) <= pivot_high
            and float(high.iloc[-6]) <= pivot_high
            and float(high.iloc[-5]) <= pivot_high
            and float(high.iloc[-4]) <= pivot_high
            and float(high.iloc[-2]) < pivot_high
            and float(high.iloc[-1]) < pivot_high
        )
    )
    down = (
        (
            float(low.iloc[-5]) > pivot_low
            and float(low.iloc[-4]) > pivot_low
            and float(low.iloc[-2]) > pivot_low
            and float(low.iloc[-1]) > pivot_low
        )
        or (
            float(low.iloc[-6]) > pivot_low
            and float(low.iloc[-5]) > pivot_low
            and float(low.iloc[-4]) >= pivot_low
            and float(low.iloc[-2]) > pivot_low
            and float(low.iloc[-1]) > pivot_low
        )
        or (
            float(low.iloc[-7]) > pivot_low
            and float(low.iloc[-6]) > pivot_low
            and float(low.iloc[-5]) >= pivot_low
            and float(low.iloc[-4]) >= pivot_low
            and float(low.iloc[-2]) > pivot_low
            and float(low.iloc[-1]) > pivot_low
        )
        or (
            float(low.iloc[-8]) > pivot_low
            and float(low.iloc[-7]) > pivot_low
            and float(low.iloc[-6]) >= pivot_low
            and float(low.iloc[-5]) >= pivot_low
            and float(low.iloc[-4]) >= pivot_low
            and float(low.iloc[-2]) > pivot_low
            and float(low.iloc[-1]) > pivot_low
        )
        or (
            float(low.iloc[-9]) > pivot_low
            and float(low.iloc[-8]) > pivot_low
            and float(low.iloc[-7]) >= pivot_low
            and float(low.iloc[-6]) >= pivot_low
            and float(low.iloc[-5]) >= pivot_low
            and float(low.iloc[-4]) >= pivot_low
            and float(low.iloc[-2]) > pivot_low
            and float(low.iloc[-1]) > pivot_low
        )
    )
    return up, down


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.fractal_seeded = False
    g.upper_events = []
    g.lower_events = []
    g.previous_upper = float("nan")
    g.previous_lower = float("nan")
    g.previous_close = float("nan")
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(600)
    context.set_metadata(direction_mode="one_way", strategy_family="breakout")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    target_pct = float(context.params.get("target_pct", 0.95))
    if not bool(g.fractal_seeded):
        bars = get_history(500, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 120:
            return
        upper, lower, upper_events, lower_events = _fractal_channels(bars["high"], bars["low"])
        close = bars["close"]
        previous_upper = float(upper.iloc[-2])
        current_upper = float(upper.iloc[-1])
        previous_lower = float(lower.iloc[-2])
        current_lower = float(lower.iloc[-1])
        previous_close = float(close.iloc[-2])
        current_close = float(close.iloc[-1])
        g.upper_events = upper_events[-3:]
        g.lower_events = lower_events[-3:]
        g.fractal_seeded = True
    else:
        bars = get_history(9, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 9:
            return
        up, down = _current_fractal_flags(bars["high"], bars["low"])
        if up:
            g.upper_events.append(float(bars["high"].iloc[-3]))
            g.upper_events = g.upper_events[-3:]
        if down:
            g.lower_events.append(float(bars["low"].iloc[-3]))
            g.lower_events = g.lower_events[-3:]
        previous_upper = float(g.previous_upper)
        previous_lower = float(g.previous_lower)
        previous_close = float(g.previous_close)
        current_upper = float(g.upper_events[-2]) if len(g.upper_events) >= 2 else float("nan")
        current_lower = float(g.lower_events[-2]) if len(g.lower_events) >= 2 else float("nan")
        current_close = float(bars["close"].iloc[-1])
    g.previous_upper = current_upper
    g.previous_lower = current_lower
    g.previous_close = current_close
    values = (previous_upper, current_upper, previous_lower, current_lower, previous_close, current_close)
    if not all(float(value) == float(value) for value in values):
        return
    crossed_up = previous_close <= previous_upper and current_close > current_upper
    crossed_down = previous_close >= previous_lower and current_close < current_lower
    amount = float(get_position(g.symbol).amount or 0.0)
    if crossed_up and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="fractal_breakout_long")
    elif crossed_down and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="fractal_breakout_short")
