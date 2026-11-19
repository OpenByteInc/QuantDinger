"""Parabolic SAR reversal strategy evaluated on completed closes."""

# @param start float 0.02 range=0.001:0.2:0.001
# @param increment float 0.02 range=0.001:0.2:0.001
# @param maximum float 0.2 range=0.01:1:0.01
# @param entry_bars int 1 range=1:20:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {
        "signature": signature,
        "ready": False,
        "direction": 0,
        "trend_bars": 0,
        "af": 0.0,
        "extreme": 0.0,
        "psar": 0.0,
    }


def _step(state, open_price, high, low, close, start, increment, maximum):
    if not state["ready"]:
        direction = 1 if close > open_price else -1
        state["ready"] = True
        state["direction"] = direction
        state["trend_bars"] = direction
        state["af"] = start
        state["extreme"] = high if direction > 0 else low
        state["psar"] = low if direction > 0 else high
        return direction
    previous_direction = int(state["direction"])
    previous_psar = float(state["psar"])
    previous_extreme = float(state["extreme"])
    switched = (previous_direction > 0 and close <= previous_psar) or (
        previous_direction < 0 and close >= previous_psar
    )
    if switched:
        direction = -previous_direction
        state["direction"] = direction
        state["trend_bars"] = direction
        state["af"] = start
        state["extreme"] = high if direction > 0 else low
        state["psar"] = previous_extreme
    else:
        direction = previous_direction
        state["trend_bars"] = int(state["trend_bars"]) + direction
        new_extreme = high if direction > 0 else low
        improved = new_extreme > previous_extreme if direction > 0 else new_extreme < previous_extreme
        if improved:
            state["af"] = min(maximum, float(state["af"]) + increment)
            state["extreme"] = new_extreme
        current_extreme = float(state["extreme"])
        acceleration = float(state["af"])
        state["psar"] = previous_psar + acceleration * (current_extreme - previous_psar)
    return int(state["trend_bars"])


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.psar_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["open", "high", "low", "close"])
    context.set_warmup(400)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    start = float(context.params.get("start", 0.02))
    increment = float(context.params.get("increment", 0.02))
    maximum = float(context.params.get("maximum", 0.2))
    entry_bars = int(context.params.get("entry_bars", 1))
    target_pct = float(context.params.get("target_pct", 0.95))
    if min(start, increment, maximum) <= 0 or entry_bars < 1:
        return
    signature = [start, increment, maximum]
    state = dict(g.psar_state or {})
    fields = ["open", "high", "low", "close"]
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(300, "1h", fields, g.symbol)
        if len(bars) < 300:
            return
        state = _new_state(signature)
        trend_bars = 0
        for index in range(len(bars)):
            trend_bars = _step(
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
        trend_bars = _step(
            state,
            float(bars["open"].iloc[-1]),
            float(bars["high"].iloc[-1]),
            float(bars["low"].iloc[-1]),
            float(bars["close"].iloc[-1]),
            start,
            increment,
            maximum,
        )
    g.psar_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if trend_bars == entry_bars and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="psar_close_long")
    elif trend_bars == -entry_bars and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="psar_close_short")
