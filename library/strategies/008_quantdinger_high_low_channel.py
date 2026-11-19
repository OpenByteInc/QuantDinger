"""
QuantDinger High Low Channel
One-way channel breakout strategy with optional signal reversal.
"""

# @param length int 20 range=2:200:1
# @param reverse bool false
# @param target_pct float 0.95 range=0.05:1:0.05


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.signal_state = 0
    g.pending_target = 0.0
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(205)
    context.set_metadata(direction_mode="one_way", strategy_family="breakout")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    length = int(context.params.get("length", 20))
    reverse = bool(context.params.get("reverse", False))
    target_pct = float(context.params.get("target_pct", 0.95))
    if length < 2:
        return
    bars = get_history(length + 1, "1h", ["high", "low", "close"], g.symbol)
    if len(bars) < length + 1:
        return
    upper = float(bars["high"].iloc[-length - 1:-1].max())
    lower = float(bars["low"].iloc[-length - 1:-1].min())
    close_now = float(bars["close"].iloc[-1])
    if close_now > upper:
        g.signal_state = 1
    elif close_now < lower:
        g.signal_state = -1
    state = -int(g.signal_state) if reverse else int(g.signal_state)
    target = target_pct * state
    if target == 0.0:
        return
    amount = float(get_position(g.symbol).amount or 0.0)
    if abs(amount) <= 1e-12 and abs(float(g.pending_target)) > 0:
        pending = float(g.pending_target)
        g.pending_target = 0.0
        order_target_percent(g.symbol, pending, reason="high_low_pending_reversal")
    elif amount * target < 0:
        g.pending_target = target
        order_target_percent(g.symbol, 0.0, reason="high_low_close_for_reversal")
    elif amount == 0.0:
        order_target_percent(g.symbol, target, reason="high_low_channel_entry")
