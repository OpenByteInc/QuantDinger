"""
QuantDinger EVWMA VWAP MACD
One-way reversal strategy using fast and slow smoothing of the VWAP-EVWMA spread.
"""

# @param evwma_length int 30 range=2:200:1
# @param fast_signal int 5 range=1:100:1
# @param slow_signal int 20 range=2:200:1
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
    context.set_metadata(direction_mode="one_way", strategy_family="volume_momentum")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    evwma_length = int(context.params.get("evwma_length", 30))
    fast_signal = int(context.params.get("fast_signal", 5))
    slow_signal = int(context.params.get("slow_signal", 20))
    target_pct = float(context.params.get("target_pct", 0.95))
    if evwma_length < 2 or fast_signal < 1 or slow_signal <= fast_signal:
        return
    required = max(6 * evwma_length, 6 * slow_signal, 100)
    bars = get_history(required, "1h", ["high", "low", "close", "volume"], g.symbol)
    if len(bars) < required:
        return
    close = bars["close"]
    volume = bars["volume"]
    evwma = _evwma(close, volume, evwma_length)
    typical = (bars["high"] + bars["low"] + close) / 3.0
    sessions = bars.index.normalize()
    vwap = (typical * volume).groupby(sessions).cumsum() / volume.groupby(sessions).cumsum()
    spread = vwap - evwma
    fast = spread.ewm(span=fast_signal, adjust=False, min_periods=fast_signal).mean()
    slow = spread.ewm(span=slow_signal, adjust=False, min_periods=slow_signal).mean()
    values = (fast.iloc[-2], fast.iloc[-1], slow.iloc[-2], slow.iloc[-1])
    if not all(float(value) == float(value) for value in values):
        return
    crossed_up = float(fast.iloc[-2]) <= float(slow.iloc[-2]) and float(fast.iloc[-1]) > float(slow.iloc[-1])
    crossed_down = float(fast.iloc[-2]) >= float(slow.iloc[-2]) and float(fast.iloc[-1]) < float(slow.iloc[-1])
    target = target_pct if crossed_up else (-target_pct if crossed_down else 0.0)
    amount = float(get_position(g.symbol).amount or 0.0)
    if abs(amount) <= 1e-12 and abs(float(g.pending_target)) > 0:
        pending = float(g.pending_target)
        g.pending_target = 0.0
        order_target_percent(g.symbol, pending, reason="evwma_macd_pending_reversal")
    elif target != 0.0 and amount * target < 0:
        g.pending_target = target
        order_target_percent(g.symbol, 0.0, reason="evwma_macd_close_for_reversal")
    elif target != 0.0 and amount == 0.0:
        order_target_percent(g.symbol, target, reason="evwma_macd_cross_entry")
