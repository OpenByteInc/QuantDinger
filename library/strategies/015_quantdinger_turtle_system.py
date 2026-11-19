"""
QuantDinger Turtle System
One-way Donchian breakout strategy with independent exit channels.
"""

# @param entry_length int 55 range=2:300:1
# @param exit_length int 20 range=2:200:1
# @param target_pct float 0.95 range=0.05:1:0.05


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.pending_target = 0.0
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(405)
    context.set_metadata(direction_mode="one_way", strategy_family="breakout")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    entry_length = int(context.params.get("entry_length", 55))
    exit_length = int(context.params.get("exit_length", 20))
    target_pct = float(context.params.get("target_pct", 0.95))
    if entry_length < 2 or exit_length < 2:
        return
    required = max(entry_length, exit_length) + 1
    bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
    if len(bars) < required:
        return
    high = bars["high"]
    low = bars["low"]
    close_now = float(bars["close"].iloc[-1])
    entry_high = float(high.iloc[-entry_length - 1:-1].max())
    entry_low = float(low.iloc[-entry_length - 1:-1].min())
    exit_high = float(high.iloc[-exit_length - 1:-1].max())
    exit_low = float(low.iloc[-exit_length - 1:-1].min())
    target = target_pct if close_now > entry_high else (-target_pct if close_now < entry_low else 0.0)
    amount = float(get_position(g.symbol).amount or 0.0)
    if abs(amount) <= 1e-12 and abs(float(g.pending_target)) > 0:
        pending = float(g.pending_target)
        g.pending_target = 0.0
        order_target_percent(g.symbol, pending, reason="turtle_pending_reversal")
    elif amount > 0 and float(low.iloc[-1]) < exit_low:
        order_target_percent(g.symbol, 0.0, reason="turtle_long_exit")
    elif amount < 0 and float(high.iloc[-1]) > exit_high:
        order_target_percent(g.symbol, 0.0, reason="turtle_short_exit")
    elif target != 0.0 and amount * target < 0:
        g.pending_target = target
        order_target_percent(g.symbol, 0.0, reason="turtle_close_for_reversal")
    elif target != 0.0 and amount == 0.0:
        order_target_percent(g.symbol, target, reason="turtle_breakout_entry")
