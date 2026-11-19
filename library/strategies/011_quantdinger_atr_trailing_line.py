"""
QuantDinger ATR Trailing Line
One-way reversal strategy using the highest ATR-adjusted trailing level.
"""

# @param atr_period int 5 range=1:500:1
# @param high_period int 10 range=1:500:1
# @param multiplier float 2.5 range=0.1:20:0.1
# @param target_pct float 0.95 range=0.05:1:0.05


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.pending_target = 0.0
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(605)
    context.set_metadata(direction_mode="one_way", strategy_family="trailing_trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    atr_period = int(context.params.get("atr_period", 5))
    high_period = int(context.params.get("high_period", 10))
    multiplier = float(context.params.get("multiplier", 2.5))
    target_pct = float(context.params.get("target_pct", 0.95))
    if atr_period < 1 or high_period < 1 or multiplier <= 0:
        return
    required = max(6 * atr_period + high_period, 100)
    bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
    if len(bars) < required:
        return
    high = bars["high"]
    low = bars["low"]
    close = bars["close"]
    previous_close = close.shift(1)
    true_range = pd.concat([high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1).max(axis=1)
    atr = true_range.ewm(alpha=1.0 / atr_period, adjust=False, min_periods=atr_period).mean()
    trailing = (high - multiplier * atr).rolling(high_period, min_periods=high_period).max()
    values = (close.iloc[-2], close.iloc[-1], trailing.iloc[-2], trailing.iloc[-1])
    if not all(float(value) == float(value) for value in values):
        return
    crossed_up = float(close.iloc[-2]) <= float(trailing.iloc[-2]) and float(close.iloc[-1]) > float(trailing.iloc[-1])
    crossed_down = float(close.iloc[-2]) >= float(trailing.iloc[-2]) and float(close.iloc[-1]) < float(trailing.iloc[-1])
    target = target_pct if crossed_up else (-target_pct if crossed_down else 0.0)
    amount = float(get_position(g.symbol).amount or 0.0)
    if abs(amount) <= 1e-12 and abs(float(g.pending_target)) > 0:
        pending = float(g.pending_target)
        g.pending_target = 0.0
        order_target_percent(g.symbol, pending, reason="atr_line_pending_reversal")
    elif target != 0.0 and amount * target < 0:
        g.pending_target = target
        order_target_percent(g.symbol, 0.0, reason="atr_line_close_for_reversal")
    elif target != 0.0 and amount == 0.0:
        order_target_percent(g.symbol, target, reason="atr_line_cross_entry")
