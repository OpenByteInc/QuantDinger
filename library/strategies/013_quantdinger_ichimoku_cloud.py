"""
QuantDinger Ichimoku Cloud
One-way cloud regime strategy using displaced Donchian midpoints.
"""

# @param conversion_period int 9 range=1:100:1
# @param base_period int 26 range=1:200:1
# @param span_period int 52 range=1:300:1
# @param displacement int 26 range=0:100:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _donchian_midpoint(high, low, length):
    return (high.rolling(length, min_periods=length).max() + low.rolling(length, min_periods=length).min()) / 2.0


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.pending_target = 0.0
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(405)
    context.set_metadata(direction_mode="one_way", strategy_family="ichimoku")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    conversion_period = int(context.params.get("conversion_period", 9))
    base_period = int(context.params.get("base_period", 26))
    span_period = int(context.params.get("span_period", 52))
    displacement = int(context.params.get("displacement", 26))
    target_pct = float(context.params.get("target_pct", 0.95))
    if min(conversion_period, base_period, span_period) < 1 or displacement < 0:
        return
    required = max(conversion_period, base_period, span_period) + displacement + 2
    bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
    if len(bars) < required:
        return
    conversion = _donchian_midpoint(bars["high"], bars["low"], conversion_period)
    baseline = _donchian_midpoint(bars["high"], bars["low"], base_period)
    lead_one = (conversion + baseline) / 2.0
    lead_two = _donchian_midpoint(bars["high"], bars["low"], span_period)
    signal_index = -1 - displacement
    cloud_one = float(lead_one.iloc[signal_index])
    cloud_two = float(lead_two.iloc[signal_index])
    close_now = float(bars["close"].iloc[-1])
    if not cloud_one == cloud_one or not cloud_two == cloud_two:
        return
    target = target_pct if close_now > cloud_one and close_now > cloud_two else (-target_pct if close_now < cloud_one and close_now < cloud_two else 0.0)
    if target == 0.0:
        return
    amount = float(get_position(g.symbol).amount or 0.0)
    if abs(amount) <= 1e-12 and abs(float(g.pending_target)) > 0:
        pending = float(g.pending_target)
        g.pending_target = 0.0
        order_target_percent(g.symbol, pending, reason="ichimoku_pending_reversal")
    elif amount * target < 0:
        g.pending_target = target
        order_target_percent(g.symbol, 0.0, reason="ichimoku_close_for_reversal")
    elif amount == 0.0:
        order_target_percent(g.symbol, target, reason="ichimoku_cloud_entry")
