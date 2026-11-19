"""Intraday breakout strategy using the previous completed daily range."""

# @param target_pct float 0.95 range=0.05:1:0.05


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.previous_high = None
    g.previous_low = None
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low"])
    context.subscribe(frequency="1d", fields=["high", "low"])
    context.set_warmup(120)
    context.set_metadata(direction_mode="one_way", strategy_family="breakout")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    target_pct = float(context.params.get("target_pct", 0.95))
    intraday = get_history(1, "1h", ["high", "low"], g.symbol)
    daily = get_history(2, "1d", ["high", "low"], g.symbol)
    if len(intraday) < 1 or len(daily) < 1:
        return
    current_high = float(intraday["high"].iloc[-1])
    current_low = float(intraday["low"].iloc[-1])
    daily_high = float(daily["high"].iloc[-1])
    daily_low = float(daily["low"].iloc[-1])
    previous_high = g.previous_high
    previous_low = g.previous_low
    g.previous_high = current_high
    g.previous_low = current_low
    if previous_high is None or previous_low is None:
        return
    broke_high = float(previous_high) <= daily_high and current_high > daily_high
    broke_low = float(previous_low) >= daily_low and current_low < daily_low
    amount = float(get_position(g.symbol).amount or 0.0)
    if broke_high and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="previous_day_high_break")
    elif broke_low and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="previous_day_low_break")
