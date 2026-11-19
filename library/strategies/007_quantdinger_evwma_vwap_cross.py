"""
QuantDinger EVWMA VWAP Cross
One-way reversal strategy comparing a volume-weighted average with EVWMA.
"""

# @param evwma_length int 30 range=2:200:1
# @param vwap_smoothing int 1 range=1:100:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _evwma(close, volume, length):
    volume_sum = volume.rolling(length, min_periods=length).sum()
    output = close.copy()
    previous = float(close.iloc[0])
    for index in range(len(close)):
        total = float(volume_sum.iloc[index])
        price = float(close.iloc[index])
        current_volume = float(volume.iloc[index])
        if total == total and total > 0:
            previous = ((total - current_volume) * previous + current_volume * price) / total
        else:
            previous = price
        output.iloc[index] = previous
    return output


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.pending_target = 0.0
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close", "volume"])
    context.set_warmup(405)
    context.set_metadata(direction_mode="one_way", strategy_family="volume_trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    length = int(context.params.get("evwma_length", 30))
    smoothing = int(context.params.get("vwap_smoothing", 1))
    target_pct = float(context.params.get("target_pct", 0.95))
    if length < 2 or smoothing < 1:
        return
    required = max(6 * length, 6 * smoothing, 80)
    bars = get_history(required, "1h", ["high", "low", "close", "volume"], g.symbol)
    if len(bars) < required:
        return
    close = bars["close"]
    volume = bars["volume"]
    evwma = _evwma(close, volume, length)
    typical = (bars["high"] + bars["low"] + close) / 3.0
    sessions = bars.index.normalize()
    vwap = (typical * volume).groupby(sessions).cumsum() / volume.groupby(sessions).cumsum()
    vwap_smooth = vwap.ewm(span=smoothing, adjust=False, min_periods=smoothing).mean()
    values = (vwap_smooth.iloc[-2], vwap_smooth.iloc[-1], evwma.iloc[-2], evwma.iloc[-1])
    if not all(float(value) == float(value) for value in values):
        return
    crossed_up = float(vwap_smooth.iloc[-2]) <= float(evwma.iloc[-2]) and float(vwap_smooth.iloc[-1]) > float(evwma.iloc[-1])
    crossed_down = float(vwap_smooth.iloc[-2]) >= float(evwma.iloc[-2]) and float(vwap_smooth.iloc[-1]) < float(evwma.iloc[-1])
    target = target_pct if crossed_up else (-target_pct if crossed_down else 0.0)
    amount = float(get_position(g.symbol).amount or 0.0)
    if abs(amount) <= 1e-12 and abs(float(g.pending_target)) > 0:
        pending = float(g.pending_target)
        g.pending_target = 0.0
        order_target_percent(g.symbol, pending, reason="evwma_vwap_pending_reversal")
    elif target != 0.0 and amount * target < 0:
        g.pending_target = target
        order_target_percent(g.symbol, 0.0, reason="evwma_vwap_close_for_reversal")
    elif target != 0.0 and amount == 0.0:
        order_target_percent(g.symbol, target, reason="evwma_vwap_cross_entry")
