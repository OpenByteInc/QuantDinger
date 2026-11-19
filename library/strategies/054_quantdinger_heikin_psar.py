"""Heikin-Ashi parabolic SAR reversal strategy."""

# @param start float 0.02 range=0.001:0.2:0.001
# @param increment float 0.02 range=0.001:0.2:0.001
# @param maximum float 0.2 range=0.01:1:0.01
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {
        "signature": signature,
        "ready": False,
        "ha_open": 0.0,
        "ha_close": 0.0,
        "direction": 0,
        "af": 0.0,
        "extreme": 0.0,
        "psar": 0.0,
    }


def _step(state, open_price, high, low, close, start, increment, maximum):
    ha_close = (open_price + high + low + close) / 4.0
    ha_open = (open_price + close) / 2.0 if not state["ready"] else (float(state["ha_open"]) + float(state["ha_close"])) / 2.0
    ha_high = max(high, ha_open, ha_close)
    ha_low = min(low, ha_open, ha_close)
    state["ha_open"] = ha_open
    state["ha_close"] = ha_close
    if not state["ready"]:
        direction = 1 if ha_close > ha_open else -1
        state["ready"] = True
        state["direction"] = direction
        state["af"] = start
        state["extreme"] = ha_high if direction > 0 else ha_low
        state["psar"] = ha_low if direction > 0 else ha_high
        return False, False
    previous_direction = int(state["direction"])
    previous_psar = float(state["psar"])
    previous_extreme = float(state["extreme"])
    switch_to_short = previous_direction > 0 and ha_close <= previous_psar
    switch_to_long = previous_direction < 0 and ha_close >= previous_psar
    if switch_to_short or switch_to_long:
        direction = -previous_direction
        state["direction"] = direction
        state["af"] = start
        state["extreme"] = ha_high if direction > 0 else ha_low
        state["psar"] = previous_extreme
    else:
        direction = previous_direction
        candidate = ha_high if direction > 0 else ha_low
        improved = candidate > previous_extreme if direction > 0 else candidate < previous_extreme
        if improved:
            state["af"] = min(maximum, float(state["af"]) + increment)
            state["extreme"] = candidate
        state["psar"] = previous_psar + float(state["af"]) * (float(state["extreme"]) - previous_psar)
    return switch_to_long, switch_to_short


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.heikin_psar_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["open", "high", "low", "close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    start = float(context.params.get("start", 0.02))
    increment = float(context.params.get("increment", 0.02))
    maximum = float(context.params.get("maximum", 0.2))
    target_pct = float(context.params.get("target_pct", 0.95))
    if min(start, increment, maximum) <= 0:
        return
    signature = [start, increment, maximum]
    state = dict(g.heikin_psar_state or {})
    fields = ["open", "high", "low", "close"]
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(300, "1h", fields, g.symbol)
        if len(bars) < 300:
            return
        state = _new_state(signature)
        buy_signal = False
        sell_signal = False
        for index in range(len(bars)):
            buy_signal, sell_signal = _step(
                state,
                float(bars["open"].iloc[index]),
                float(bars["high"].iloc[index]),
                float(bars["low"].iloc[index]),
                float(bars["close"].iloc[index]),
                start,
                increment,
                maximum,
            )
    else:
        bars = get_history(1, "1h", fields, g.symbol)
        if len(bars) < 1:
            return
        buy_signal, sell_signal = _step(
            state,
            float(bars["open"].iloc[-1]),
            float(bars["high"].iloc[-1]),
            float(bars["low"].iloc[-1]),
            float(bars["close"].iloc[-1]),
            start,
            increment,
            maximum,
        )
    g.heikin_psar_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy_signal and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="heikin_psar_long")
    elif sell_signal and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="heikin_psar_short")
