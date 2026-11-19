"""Heikin-Ashi adaptive RSI and ATR threshold reversal strategy."""

# @param rsi_length int 8 range=2:200:1
# @param rsi_multiplier float 1.5 range=0.05:10:0.05
# @param signal_delay int 1 range=0:20:1
# @param maximum_distance_pct float 10 range=1:50:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _rma_step(state, value, length):
    current = state.get("value")
    if current is not None:
        current = (float(current) * (length - 1) + float(value)) / float(length)
        state["value"] = current
        return current
    state["seed"].append(float(value))
    if len(state["seed"]) < length:
        return float("nan")
    current = sum(state["seed"][-length:]) / float(length)
    state["value"] = current
    state["seed"] = []
    return current


def _new_state(signature):
    return {
        "signature": signature,
        "previous_close": None,
        "previous_source": None,
        "gain": {"value": None, "seed": []},
        "loss": {"value": None, "seed": []},
        "atr": {"value": None, "seed": []},
        "threshold": None,
        "direction": 1,
        "buy_queue": [],
        "sell_queue": [],
    }


def _step(state, open_price, high, low, close, length, multiplier, maximum_distance_pct, signal_delay):
    source = (open_price + high + low + close) / 4.0
    previous_close = state["previous_close"]
    previous_source = state["previous_source"]
    true_range = high - low if previous_close is None else max(
        high - low,
        abs(high - float(previous_close)),
        abs(low - float(previous_close)),
    )
    atr = _rma_step(state["atr"], true_range, length)
    change = 0.0 if previous_source is None else source - float(previous_source)
    gain = _rma_step(state["gain"], max(change, 0.0), length)
    loss = _rma_step(state["loss"], max(-change, 0.0), length)
    state["previous_close"] = close
    state["previous_source"] = source
    if atr != atr or gain != gain or loss != loss:
        return False, False
    rsi = 100.0 if loss <= 1e-15 else 100.0 - 100.0 / (1.0 + gain / loss)
    inverse_rsi = max(100.0 - rsi, 1e-6)
    rsi = max(rsi, 1e-6)
    atr_ratio = atr / max(abs(source), 1e-9)
    limit = maximum_distance_pct / 100.0
    lower = max(source * (1.0 - (atr_ratio + multiplier / rsi)), source * (1.0 - limit))
    upper = min(source * (1.0 + (atr_ratio + multiplier / inverse_rsi)), source * (1.0 + limit))
    previous_threshold = state["threshold"]
    if previous_threshold is None:
        threshold = lower
    elif int(state["direction"]) > 0:
        threshold = max(float(previous_threshold), lower)
    else:
        threshold = min(float(previous_threshold), upper)
    raw_buy = previous_source is not None and previous_threshold is not None and float(previous_source) <= float(previous_threshold) and source > threshold
    raw_sell = previous_source is not None and previous_threshold is not None and float(previous_source) >= float(previous_threshold) and source < threshold
    if raw_buy:
        state["direction"] = 1
        threshold = lower
    elif raw_sell:
        state["direction"] = -1
        threshold = upper
    state["threshold"] = threshold
    state["buy_queue"].append(bool(raw_buy))
    state["sell_queue"].append(bool(raw_sell))
    if len(state["buy_queue"]) > signal_delay + 1:
        state["buy_queue"].pop(0)
        state["sell_queue"].pop(0)
    if len(state["buy_queue"]) < signal_delay + 1:
        return False, False
    return bool(state["buy_queue"][0]), bool(state["sell_queue"][0])


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.threshold_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["open", "high", "low", "close"])
    context.set_warmup(700)
    context.set_metadata(direction_mode="one_way", strategy_family="reversal")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    length = int(context.params.get("rsi_length", 8))
    multiplier = float(context.params.get("rsi_multiplier", 1.5))
    signal_delay = int(context.params.get("signal_delay", 1))
    maximum_distance_pct = float(context.params.get("maximum_distance_pct", 10.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    if length < 2 or multiplier <= 0 or signal_delay < 0 or maximum_distance_pct <= 0:
        return
    signature = [length, multiplier, signal_delay, maximum_distance_pct]
    state = dict(g.threshold_state or {})
    fields = ["open", "high", "low", "close"]
    if not state or list(state.get("signature") or []) != signature:
        required = max(20 * length, 300)
        bars = get_history(required, "1h", fields, g.symbol)
        if len(bars) < required:
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
                length,
                multiplier,
                maximum_distance_pct,
                signal_delay,
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
            length,
            multiplier,
            maximum_distance_pct,
            signal_delay,
        )
    g.threshold_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy_signal and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="heikin_rsi_long")
    elif sell_signal and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="heikin_rsi_short")
