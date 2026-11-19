"""
QuantDinger Support Resistance
One-way reversal strategy using confirmed moving-average crossing levels.
"""

# @param length int 10 range=2:200:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _previous_occurrence(condition, values):
    matches = values[condition.fillna(False)]
    if len(matches) < 2:
        return None
    return float(matches.iloc[-2])


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.pending_target = 0.0
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(605)
    context.set_metadata(direction_mode="one_way", strategy_family="support_resistance")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    length = int(context.params.get("length", 10))
    target_pct = float(context.params.get("target_pct", 0.95))
    if length < 2:
        return
    required = max(20 * length, 200)
    bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
    if len(bars) < required:
        return
    close = bars["close"]
    average = close.rolling(length, min_periods=length).mean()
    crossing = ((average.shift(1) <= close.shift(1)) & (average > close)) | ((average.shift(1) >= close.shift(1)) & (average < close))
    highest = bars["high"].rolling(length, min_periods=length).max()
    lowest = bars["low"].rolling(length, min_periods=length).min()
    resistance_now = _previous_occurrence(crossing, highest)
    support_now = _previous_occurrence(crossing, lowest)
    resistance_previous = _previous_occurrence(crossing.iloc[:-1], highest.iloc[:-1])
    support_previous = _previous_occurrence(crossing.iloc[:-1], lowest.iloc[:-1])
    if None in (resistance_now, support_now, resistance_previous, support_previous):
        return
    close_now = float(close.iloc[-1])
    close_previous = float(close.iloc[-2])
    crossed_up = close_previous <= resistance_previous and close_now > resistance_now
    crossed_down = close_previous >= support_previous and close_now < support_now
    target = target_pct if crossed_up else (-target_pct if crossed_down else 0.0)
    amount = float(get_position(g.symbol).amount or 0.0)
    if abs(amount) <= 1e-12 and abs(float(g.pending_target)) > 0:
        pending = float(g.pending_target)
        g.pending_target = 0.0
        order_target_percent(g.symbol, pending, reason="support_resistance_pending_reversal")
    elif target != 0.0 and amount * target < 0:
        g.pending_target = target
        order_target_percent(g.symbol, 0.0, reason="support_resistance_close_for_reversal")
    elif target != 0.0 and amount == 0.0:
        order_target_percent(g.symbol, target, reason="support_resistance_breakout")
