"""
QuantDinger OBV Oscillator Reversal
One-way long and short reversal strategy driven by OBV oscillator EMA crosses.
"""

# @param obv_length int 20 range=2:200:1
# @param fast_ema int 12 range=2:100:1
# @param slow_ema int 26 range=3:200:1
# @param target_pct float 0.95 range=0.05:1:0.05


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.pending_target = 0.0
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close", "volume"])
    context.set_warmup(605)
    context.set_metadata(direction_mode="one_way", strategy_family="momentum")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    obv_length = int(context.params.get("obv_length", 20))
    fast_ema = int(context.params.get("fast_ema", 12))
    slow_ema = int(context.params.get("slow_ema", 26))
    target_pct = float(context.params.get("target_pct", 0.95))
    if obv_length < 2 or fast_ema < 2 or slow_ema <= fast_ema:
        return

    required = obv_length + 6 * slow_ema
    bars = get_history(required, "1h", ["close", "volume"], g.symbol)
    if len(bars) < required:
        return
    close = bars["close"]
    volume = bars["volume"]
    change = close.diff()
    direction = (change > 0).astype(float) - (change < 0).astype(float)
    signed_volume = volume * direction
    numerator = signed_volume.rolling(obv_length, min_periods=obv_length).sum()
    denominator = volume.rolling(obv_length, min_periods=obv_length).sum()
    oscillator = numerator / denominator
    fast = oscillator.ewm(span=fast_ema, adjust=False, min_periods=fast_ema).mean()
    slow = oscillator.ewm(span=slow_ema, adjust=False, min_periods=slow_ema).mean()
    values = (fast.iloc[-2], fast.iloc[-1], slow.iloc[-2], slow.iloc[-1])
    if not all(float(value) == float(value) for value in values):
        return

    amount = float(get_position(g.symbol).amount or 0.0)
    if abs(amount) <= 1e-12 and abs(float(g.pending_target)) > 0:
        target = float(g.pending_target)
        g.pending_target = 0.0
        order_target_percent(g.symbol, target, reason="obv_pending_reversal")
        return

    crossed_up = float(fast.iloc[-2]) <= float(slow.iloc[-2]) and float(fast.iloc[-1]) > float(slow.iloc[-1])
    crossed_down = float(fast.iloc[-2]) >= float(slow.iloc[-2]) and float(fast.iloc[-1]) < float(slow.iloc[-1])
    target = target_pct if crossed_up else (-target_pct if crossed_down else 0.0)
    if target == 0.0:
        return
    if amount * target < 0:
        g.pending_target = target
        order_target_percent(g.symbol, 0.0, reason="obv_close_for_reversal")
    elif amount == 0.0:
        order_target_percent(g.symbol, target, reason="obv_cross_entry")
