"""
QuantDinger Trend Reversal
Long-only candle resistance strategy using three-bar directional confirmation.
"""

# @param delta float 1.0 range=0.01:1:0.01
# @param buy_resistance float 0.0 range=0:10:0.01
# @param sell_resistance float 0.0 range=-10:10:0.01
# @param target_pct float 0.95 range=0.05:1:0.05


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["open", "high", "low", "close"])
    context.set_warmup(10)
    context.set_metadata(direction_mode="long_only", strategy_family="price_action")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    delta = float(context.params.get("delta", 1.0))
    buy_resistance = float(context.params.get("buy_resistance", 0.0))
    sell_resistance = float(context.params.get("sell_resistance", 0.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    if delta <= 0 or delta > 1:
        return
    bars = get_history(3, "1h", ["open", "high", "low", "close"], g.symbol)
    if len(bars) < 3:
        return
    open_now = float(bars["open"].iloc[-1])
    close = bars["close"]
    close_now = float(close.iloc[-1])
    body_span = abs(close_now - open_now)
    if body_span <= 0:
        return
    resistance = float(bars["high"].iloc[-1] - bars["low"].iloc[-1]) / body_span
    if open_now > close_now:
        resistance = -resistance
    buy_signal = float(close.iloc[-2]) * delta > float(close.iloc[-3]) and close_now * delta > float(close.iloc[-2]) and resistance > buy_resistance
    sell_scale = 2.0 - delta
    sell_signal = float(close.iloc[-2]) * sell_scale < float(close.iloc[-3]) and close_now * sell_scale < float(close.iloc[-2]) and resistance < sell_resistance
    amount = float(get_position(g.symbol).amount or 0.0)
    if amount <= 0 and buy_signal:
        order_target_percent(g.symbol, target_pct, reason="trend_reversal_entry")
    elif amount > 0 and sell_signal:
        order_target_percent(g.symbol, 0.0, reason="trend_reversal_exit")
