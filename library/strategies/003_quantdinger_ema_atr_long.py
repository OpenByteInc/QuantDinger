"""
QuantDinger EMA ATR Long
Long-only trend strategy using a smoothed price and ATR envelope.
"""

# @param length int 21 range=2:200:1
# @param atr_multiplier float 1.0 range=0:10:0.1
# @param target_pct float 0.95 range=0.05:1:0.05


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["open", "high", "low", "close"])
    context.set_warmup(405)
    context.set_metadata(direction_mode="long_only", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    length = int(context.params.get("length", 21))
    multiplier = float(context.params.get("atr_multiplier", 1.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    if length < 2 or multiplier < 0:
        return

    required = max(4 * length, length + 3)
    bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
    if len(bars) < required:
        return
    high = bars["high"]
    low = bars["low"]
    close = bars["close"]
    price = close.rolling(2, min_periods=2).mean()
    average = close.ewm(span=length, adjust=False, min_periods=length).mean()
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = true_range.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    bull_level = average + atr * multiplier
    bear_level = average - atr * multiplier
    values = (price.iloc[-2], price.iloc[-1], bull_level.iloc[-2], bull_level.iloc[-1], bear_level.iloc[-2], bear_level.iloc[-1])
    if not all(float(value) == float(value) for value in values):
        return
    bull_cross = float(price.iloc[-2]) <= float(bull_level.iloc[-2]) and float(price.iloc[-1]) > float(bull_level.iloc[-1])
    bear_cross = float(bear_level.iloc[-2]) <= float(price.iloc[-2]) and float(bear_level.iloc[-1]) > float(price.iloc[-1])

    amount = float(get_position(g.symbol).amount or 0.0)
    if amount <= 0 and bull_cross:
        order_target_percent(g.symbol, target_pct, reason="ema_atr_long_entry")
    elif amount > 0 and bear_cross:
        order_target_percent(g.symbol, 0.0, reason="ema_atr_long_exit")
