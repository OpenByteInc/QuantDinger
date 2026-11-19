"""
QuantDinger RSI Buy Dips
Long-only mean-reversion strategy filtered by a simple moving average.
"""

# @param rsi_length int 14 range=2:200:1
# @param entry_rsi float 35.0 range=1:99:0.5
# @param exit_rsi float 65.0 range=1:99:0.5
# @param average_length int 100 range=2:500:1
# @param target_pct float 0.30 range=0.05:1:0.05


def _rsi(close, length):
    change = close.diff()
    gain = change.where(change > 0, 0.0)
    loss = (-change).where(change < 0, 0.0)
    average_gain = gain.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    average_loss = loss.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    value = 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return value.where(average_loss > 0, 100.0)


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(1000)
    context.set_metadata(direction_mode="long_only", strategy_family="mean_reversion")


def handle_data(context, data):
    rsi_length = int(context.params.get("rsi_length", 14))
    entry_rsi = float(context.params.get("entry_rsi", 35.0))
    exit_rsi = float(context.params.get("exit_rsi", 65.0))
    average_length = int(context.params.get("average_length", 100))
    target_pct = float(context.params.get("target_pct", 0.30))
    if rsi_length < 2 or average_length < 2:
        return
    required = max(8 * max(rsi_length, average_length), 300)
    bars = get_history(required, "1h", ["close"], g.symbol)
    if len(bars) < required:
        return
    close = bars["close"]
    rsi_value = float(_rsi(close, rsi_length).iloc[-1])
    average = float(close.rolling(average_length, min_periods=average_length).mean().iloc[-1])
    current = float(close.iloc[-1])
    if rsi_value != rsi_value or average != average:
        return
    amount = float(get_position(g.symbol).amount or 0.0)
    if amount <= 0 and rsi_value < entry_rsi and current < average:
        order_target_percent(g.symbol, target_pct, reason="rsi_dip_entry")
    elif amount > 0 and rsi_value > exit_rsi:
        order_target_percent(g.symbol, 0.0, reason="rsi_dip_exit")
