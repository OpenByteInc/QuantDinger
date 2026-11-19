"""
QuantDinger UHL MA System
One-way adaptive trend strategy comparing UHL price and mean filters.
"""

# @param length int 100 range=2:500:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _uhl_lines(close, length):
    average = close.rolling(length, min_periods=length).mean()
    variance = close.rolling(length, min_periods=length).var(ddof=0)
    cma = close.copy()
    cts = close.copy()
    previous_cma = float(close.iloc[0])
    previous_cts = float(close.iloc[0])
    for index in range(len(close)):
        price = float(close.iloc[index])
        mean_value = float(average.iloc[index])
        var_value = float(variance.iloc[index])
        if not mean_value == mean_value or not var_value == var_value:
            current_cma = previous_cma
            current_cts = previous_cts
        else:
            mean_distance = (mean_value - previous_cma) ** 2
            price_distance = (price - previous_cts) ** 2
            mean_weight = 1.0 - var_value / mean_distance if mean_distance > 0 and var_value < mean_distance else 0.0
            price_weight = 1.0 - var_value / price_distance if price_distance > 0 and var_value < price_distance else 0.0
            current_cma = mean_weight * mean_value + (1.0 - mean_weight) * previous_cma
            current_cts = price_weight * price + (1.0 - price_weight) * previous_cts
        cma.iloc[index] = current_cma
        cts.iloc[index] = current_cts
        previous_cma = current_cma
        previous_cts = current_cts
    return cma, cts


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.pending_target = 0.0
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(805)
    context.set_metadata(direction_mode="one_way", strategy_family="adaptive_trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    length = int(context.params.get("length", 100))
    target_pct = float(context.params.get("target_pct", 0.95))
    if length < 2:
        return
    required = max(4 * length, length + 2)
    bars = get_history(required, "1h", "close", g.symbol)
    if len(bars) < required:
        return
    cma, cts = _uhl_lines(bars["close"], length)
    crossed_up = float(cts.iloc[-2]) <= float(cma.iloc[-2]) and float(cts.iloc[-1]) > float(cma.iloc[-1])
    crossed_down = float(cts.iloc[-2]) >= float(cma.iloc[-2]) and float(cts.iloc[-1]) < float(cma.iloc[-1])
    target = target_pct if crossed_up else (-target_pct if crossed_down else 0.0)
    amount = float(get_position(g.symbol).amount or 0.0)
    if abs(amount) <= 1e-12 and abs(float(g.pending_target)) > 0:
        pending = float(g.pending_target)
        g.pending_target = 0.0
        order_target_percent(g.symbol, pending, reason="uhl_pending_reversal")
    elif target != 0.0 and amount * target < 0:
        g.pending_target = target
        order_target_percent(g.symbol, 0.0, reason="uhl_close_for_reversal")
    elif target != 0.0 and amount == 0.0:
        order_target_percent(g.symbol, target, reason="uhl_cross_entry")
