"""
QuantDinger KPL Swing
One-way channel regime strategy using a dynamic trailing swing line.
"""

# @param length int 20 range=2:300:1
# @param target_pct float 0.95 range=0.05:1:0.05


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.kpl_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(700)
    context.set_metadata(direction_mode="one_way", strategy_family="swing")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    length = int(context.params.get("length", 20))
    target_pct = float(context.params.get("target_pct", 0.95))
    if length < 2:
        return
    saved = dict(g.kpl_state or {})
    if not saved or int(saved.get("length") or 0) != length:
        required = max(10 * length, 180)
        bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < required:
            return
        close = bars["close"]
        resistance = bars["high"].rolling(length, min_periods=length).max()
        support = bars["low"].rolling(length, min_periods=length).min()
        regime = 0
        trailing = close.copy()
        trailing[:] = float("nan")
        for index in range(1, len(close)):
            previous_resistance = float(resistance.iloc[index - 1])
            previous_support = float(support.iloc[index - 1])
            if previous_resistance != previous_resistance or previous_support != previous_support:
                continue
            if float(close.iloc[index]) > previous_resistance:
                regime = 1
            elif float(close.iloc[index]) < previous_support:
                regime = -1
            trailing.iloc[index] = float(support.iloc[index]) if regime == 1 else float(resistance.iloc[index])
        previous_trailing = float(trailing.iloc[-2])
        current_trailing = float(trailing.iloc[-1])
        previous_close = float(close.iloc[-2])
        current_close = float(close.iloc[-1])
        saved = {"length": length, "regime": regime}
    else:
        bars = get_history(length + 1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < length + 1:
            return
        previous_resistance = float(bars["high"].iloc[:-1].max())
        previous_support = float(bars["low"].iloc[:-1].min())
        current_resistance = float(bars["high"].iloc[-length:].max())
        current_support = float(bars["low"].iloc[-length:].min())
        current_close = float(bars["close"].iloc[-1])
        previous_close = float(bars["close"].iloc[-2])
        regime = int(saved["regime"])
        if current_close > previous_resistance:
            regime = 1
        elif current_close < previous_support:
            regime = -1
        previous_trailing = float(saved["trailing"])
        current_trailing = current_support if regime == 1 else current_resistance
        saved["regime"] = regime
    saved["trailing"] = current_trailing
    g.kpl_state = saved
    values = (previous_trailing, current_trailing)
    if not all(float(value) == float(value) for value in values):
        return
    crossed_up = previous_close <= previous_trailing and current_close > current_trailing
    crossed_down = previous_close >= previous_trailing and current_close < current_trailing
    amount = float(get_position(g.symbol).amount or 0.0)
    if crossed_up and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="kpl_swing_long")
    elif crossed_down and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="kpl_swing_short")
