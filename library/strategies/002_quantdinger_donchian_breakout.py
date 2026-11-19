"""
QuantDinger Donchian Breakout
Long-only channel breakout with lower-channel or midpoint exit selection.
"""

# @param length int 20 range=2:200:1
# @param exit_option int 1 range=1:2:1
# @param target_pct float 0.95 range=0.05:1:0.05


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(205)
    context.set_metadata(direction_mode="long_only", strategy_family="breakout")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    length = int(context.params.get("length", 20))
    exit_option = int(context.params.get("exit_option", 1))
    target_pct = float(context.params.get("target_pct", 0.95))
    if length < 2 or exit_option not in (1, 2):
        return

    bars = get_history(length + 2, "1h", ["high", "low", "close"], g.symbol)
    if len(bars) < length + 2:
        return
    high = bars["high"]
    low = bars["low"]
    close = bars["close"]
    upper_previous = float(high.iloc[-length - 1:-1].max())
    upper_two_bars_ago = float(high.iloc[-length - 2:-2].max())
    lower_previous = float(low.iloc[-length - 1:-1].min())
    lower_two_bars_ago = float(low.iloc[-length - 2:-2].min())
    basis_previous = (upper_previous + lower_previous) / 2.0
    basis_two_bars_ago = (upper_two_bars_ago + lower_two_bars_ago) / 2.0
    close_now = float(close.iloc[-1])
    close_previous = float(close.iloc[-2])
    crossed_above = close_previous <= upper_two_bars_ago and close_now > upper_previous
    exit_now = lower_previous if exit_option == 1 else basis_previous
    exit_previous = lower_two_bars_ago if exit_option == 1 else basis_two_bars_ago
    crossed_below_exit = close_previous >= exit_previous and close_now < exit_now

    amount = float(get_position(g.symbol).amount or 0.0)
    if amount <= 0 and crossed_above:
        order_target_percent(g.symbol, target_pct, reason="donchian_breakout_entry")
    elif amount > 0 and crossed_below_exit:
        order_target_percent(g.symbol, 0.0, reason="donchian_breakout_exit")
