"""
QuantDinger Price Action Bollinger
One-way reversal strategy combining candle contraction with Bollinger extremes.
"""

# @param body_average_length int 100 range=2:500:1
# @param band_length int 21 range=2:300:1
# @param deviations float 2.1 range=0.1:10:0.1
# @param target_pct float 0.95 range=0.05:1:0.05


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["open", "close"])
    context.set_warmup(1000)
    context.set_metadata(direction_mode="one_way", strategy_family="price_action")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    body_average_length = int(context.params.get("body_average_length", 100))
    band_length = int(context.params.get("band_length", 21))
    deviations = float(context.params.get("deviations", 2.1))
    target_pct = float(context.params.get("target_pct", 0.95))
    if body_average_length < 2 or band_length < 2 or deviations <= 0:
        return
    required = max(8 * max(body_average_length, band_length), 300)
    bars = get_history(required, "1h", ["open", "close"], g.symbol)
    if len(bars) < required:
        return
    open_price = bars["open"]
    close = bars["close"]
    body = (close - open_price).abs()
    average_body = body.rolling(body_average_length, min_periods=body_average_length).mean()
    middle = close.rolling(band_length, min_periods=band_length).mean()
    deviation = close.rolling(band_length, min_periods=band_length).std(ddof=0)
    upper = middle + deviations * deviation
    lower = middle - deviations * deviation
    values = (body.iloc[-1], body.iloc[-2], average_body.iloc[-1], upper.iloc[-1], lower.iloc[-1])
    if not all(float(value) == float(value) for value in values):
        return
    current_bearish = float(close.iloc[-1]) < float(open_price.iloc[-1])
    current_bullish = float(close.iloc[-1]) > float(open_price.iloc[-1])
    previous_bullish = float(close.iloc[-2]) > float(open_price.iloc[-2])
    previous_bearish = float(close.iloc[-2]) < float(open_price.iloc[-2])
    contraction = float(body.iloc[-1]) < float(body.iloc[-2]) / 2.0
    oversized_previous = float(body.iloc[-2]) > float(average_body.iloc[-1]) * 2.0
    short_signal = (
        contraction
        and oversized_previous
        and previous_bullish
        and current_bearish
        and float(close.iloc[-2]) > float(upper.iloc[-1])
    )
    long_signal = (
        contraction
        and oversized_previous
        and previous_bearish
        and current_bullish
        and float(close.iloc[-2]) < float(lower.iloc[-1])
    )
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_signal and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="price_action_bollinger_long")
    elif short_signal and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="price_action_bollinger_short")
