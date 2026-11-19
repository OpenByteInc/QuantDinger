"""ATR-stepped parabolic reversal strategy."""

# @param atr_length int 14 range=1:300:1
# @param start float 0.02 range=0.001:0.2:0.001
# @param increment float 0.02 range=0.001:0.2:0.001
# @param maximum float 0.2 range=0.01:1:0.01
# @param entry_bars int 1 range=1:20:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _rma_step(state, value, length):
    current = state.get("value")
    if current is not None:
        current = (float(current) * (length - 1) + float(value)) / float(length)
        state["value"] = current
        return current
    seed = state["seed"]
    seed.append(float(value))
    if len(seed) < length:
        return float("nan")
    current = sum(seed[-length:]) / float(length)
    state["value"] = current
    state["seed"] = []
    return current


def _new_state(signature):
    return {
        "signature": signature,
        "ready": False,
        "previous_close": None,
        "atr": {"value": None, "seed": []},
        "direction": 0,
        "trend_bars": 0,
        "af": 0.0,
        "extreme": 0.0,
        "psar": 0.0,
    }


def _step(state, open_price, high, low, close, atr_length, start, increment, maximum):
    previous_close = state["previous_close"]
    true_range = high - low if previous_close is None else max(
        high - low,
        abs(high - float(previous_close)),
        abs(low - float(previous_close)),
    )
    atr = _rma_step(state["atr"], true_range, atr_length)
    atr = true_range if atr != atr else atr
    state["previous_close"] = close
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
        candidate = high if direction > 0 else low
        improved = candidate > previous_extreme if direction > 0 else candidate < previous_extreme
        if improved:
            state["af"] = min(maximum, float(state["af"]) + increment)
            state["extreme"] = candidate
        step = float(state["af"]) * float(atr)
        state["psar"] = previous_psar + step if direction > 0 else previous_psar - step
    return int(state["trend_bars"])


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.atr_psar_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["open", "high", "low", "close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    atr_length = int(context.params.get("atr_length", 14))
    start = float(context.params.get("start", 0.02))
    increment = float(context.params.get("increment", 0.02))
    maximum = float(context.params.get("maximum", 0.2))
    entry_bars = int(context.params.get("entry_bars", 1))
    target_pct = float(context.params.get("target_pct", 0.95))
    if atr_length < 1 or min(start, increment, maximum) <= 0 or entry_bars < 1:
        return
    signature = [atr_length, start, increment, maximum]
    state = dict(g.atr_psar_state or {})
    fields = ["open", "high", "low", "close"]
    if not state or list(state.get("signature") or []) != signature:
        required = max(20 * atr_length, 300)
        bars = get_history(required, "1h", fields, g.symbol)
        if len(bars) < required:
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
                atr_length,
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
            atr_length,
            start,
            increment,
            maximum,
        )
    g.atr_psar_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if trend_bars == entry_bars and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="atr_psar_long")
    elif trend_bars == -entry_bars and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="atr_psar_short")
