"""
QuantDinger Bollinger Breakout
Long-only volatility breakout with a configurable band-based exit.
"""

# @param length int 20 range=2:200:1
# @param std_multiplier float 1.5 range=0.1:10:0.1
# @param exit_option int 1 range=1:2:1
# @param target_pct float 0.95 range=0.05:1:0.05


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(205)
    context.set_metadata(direction_mode="long_only", strategy_family="breakout")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    length = int(context.params.get("length", 20))
    multiplier = float(context.params.get("std_multiplier", 1.5))
    exit_option = int(context.params.get("exit_option", 1))
    target_pct = float(context.params.get("target_pct", 0.95))
    if length < 2 or multiplier <= 0 or exit_option not in (1, 2):
        return

    bars = get_history(length + 2, "1h", "close", g.symbol)
    if len(bars) < length + 2:
        return
    close = bars["close"]
    current_window = close.tail(length)
    previous_window = close.iloc[:-1].tail(length)
    basis_now = float(current_window.mean())
    basis_previous = float(previous_window.mean())
    deviation_now = float(current_window.std(ddof=0)) * multiplier
    deviation_previous = float(previous_window.std(ddof=0)) * multiplier
    close_now = float(close.iloc[-1])
    close_previous = float(close.iloc[-2])
    upper_now = basis_now + deviation_now
    upper_previous = basis_previous + deviation_previous
    lower_now = basis_now - deviation_now
    lower_previous = basis_previous - deviation_previous
    crossed_above = close_previous <= upper_previous and close_now > upper_now
    exit_level_now = lower_now if exit_option == 1 else basis_now
    exit_level_previous = lower_previous if exit_option == 1 else basis_previous
    crossed_below_exit = close_previous >= exit_level_previous and close_now < exit_level_now

    amount = float(get_position(g.symbol).amount or 0.0)
    if amount <= 0 and crossed_above:
        order_target_percent(g.symbol, target_pct, reason="bollinger_breakout_entry")
    elif amount > 0 and crossed_below_exit:
        order_target_percent(g.symbol, 0.0, reason="bollinger_breakout_exit")
