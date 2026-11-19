"""
QuantDinger Dual EMA Cross
One-way trend strategy driven by two exponential moving averages.
"""

# @param fast_length int 10 range=1:300:1
# @param slow_length int 20 range=2:500:1
# @param target_pct float 0.95 range=0.05:1:0.05


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(800)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    fast_length = int(context.params.get("fast_length", 10))
    slow_length = int(context.params.get("slow_length", 20))
    target_pct = float(context.params.get("target_pct", 0.95))
    if fast_length < 1 or slow_length < 2:
        return
    required = max(8 * max(fast_length, slow_length), 160)
    bars = get_history(required, "1h", ["close"], g.symbol)
    if len(bars) < required:
        return
    close = bars["close"]
    fast = close.ewm(span=fast_length, adjust=False, min_periods=fast_length).mean()
    slow = close.ewm(span=slow_length, adjust=False, min_periods=slow_length).mean()
    values = (fast.iloc[-2], fast.iloc[-1], slow.iloc[-2], slow.iloc[-1])
    if not all(float(value) == float(value) for value in values):
        return
    crossed_up = float(fast.iloc[-2]) <= float(slow.iloc[-2]) and float(fast.iloc[-1]) > float(slow.iloc[-1])
    crossed_down = float(fast.iloc[-2]) >= float(slow.iloc[-2]) and float(fast.iloc[-1]) < float(slow.iloc[-1])
    amount = float(get_position(g.symbol).amount or 0.0)
    if crossed_up and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="dual_ema_cross_long")
    elif crossed_down and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="dual_ema_cross_short")
