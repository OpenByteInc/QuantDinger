"""
QuantDinger SSL Channel
One-way reversal strategy driven by SSL channel crosses.
"""

# @param length int 10 range=2:200:1
# @param target_pct float 0.95 range=0.05:1:0.05


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.pending_target = 0.0
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(405)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    length = int(context.params.get("length", 10))
    target_pct = float(context.params.get("target_pct", 0.95))
    if length < 2:
        return
    required = max(8 * length, 80)
    bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
    if len(bars) < required:
        return
    close = bars["close"]
    average_high = bars["high"].rolling(length, min_periods=length).mean()
    average_low = bars["low"].rolling(length, min_periods=length).mean()
    state = close.copy()
    state[:] = float("nan")
    state[close > average_high] = 1.0
    state[close < average_low] = -1.0
    state = state.ffill().fillna(0.0)
    ssl_down = average_high.where(state < 0, average_low)
    ssl_up = average_low.where(state < 0, average_high)
    values = (ssl_up.iloc[-2], ssl_up.iloc[-1], ssl_down.iloc[-2], ssl_down.iloc[-1])
    if not all(float(value) == float(value) for value in values):
        return
    crossed_up = float(ssl_up.iloc[-2]) <= float(ssl_down.iloc[-2]) and float(ssl_up.iloc[-1]) > float(ssl_down.iloc[-1])
    crossed_down = float(ssl_up.iloc[-2]) >= float(ssl_down.iloc[-2]) and float(ssl_up.iloc[-1]) < float(ssl_down.iloc[-1])
    target = target_pct if crossed_up else (-target_pct if crossed_down else 0.0)
    amount = float(get_position(g.symbol).amount or 0.0)
    if abs(amount) <= 1e-12 and abs(float(g.pending_target)) > 0:
        pending = float(g.pending_target)
        g.pending_target = 0.0
        order_target_percent(g.symbol, pending, reason="ssl_pending_reversal")
    elif target != 0.0 and amount * target < 0:
        g.pending_target = target
        order_target_percent(g.symbol, 0.0, reason="ssl_close_for_reversal")
    elif target != 0.0 and amount == 0.0:
        order_target_percent(g.symbol, target, reason="ssl_cross_entry")
