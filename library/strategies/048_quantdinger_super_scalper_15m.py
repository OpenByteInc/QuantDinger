"""Fifteen-minute ATR-band scalper confirmed by dual RSI."""

# @param atr_length int 14 range=1:300:1
# @param atr_multiplier float 1.0 range=0.1:20:0.1
# @param fast_rsi_length int 25 range=2:300:1
# @param slow_rsi_length int 100 range=2:500:1
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


def _wma_step(buffer, value, length):
    buffer.append(float(value))
    if len(buffer) > length:
        buffer.pop(0)
    if len(buffer) < length:
        return float("nan")
    total = 0.0
    for index in range(length):
        total += float(buffer[index]) * (index + 1)
    return total / float(length * (length + 1) / 2)


def _new_rsi_state():
    return {"gain": {"value": None, "seed": []}, "loss": {"value": None, "seed": []}}


def _rsi_step(state, change, length):
    gain = _rma_step(state["gain"], max(change, 0.0), length)
    loss = _rma_step(state["loss"], max(-change, 0.0), length)
    if gain != gain or loss != loss:
        return float("nan")
    return 100.0 if loss <= 1e-15 else 100.0 - 100.0 / (1.0 + gain / loss)


def _new_state(signature):
    return {
        "signature": signature,
        "previous_close": None,
        "tr": [],
        "fast_rsi": _new_rsi_state(),
        "slow_rsi": _new_rsi_state(),
    }


def _step(state, open_price, high, low, close, atr_length, atr_multiplier, fast_length, slow_length):
    previous_close = state["previous_close"]
    true_range = high - low if previous_close is None else max(
        high - low,
        abs(high - float(previous_close)),
        abs(low - float(previous_close)),
    )
    atr = _wma_step(state["tr"], true_range, atr_length)
    change = 0.0 if previous_close is None else close - float(previous_close)
    fast_rsi = _rsi_step(state["fast_rsi"], change, fast_length)
    slow_rsi = _rsi_step(state["slow_rsi"], change, slow_length)
    state["previous_close"] = close
    if atr != atr or fast_rsi != fast_rsi or slow_rsi != slow_rsi:
        return False, False
    lower_band = close - atr * atr_multiplier
    upper_band = close + atr * atr_multiplier
    return open_price < lower_band and fast_rsi > slow_rsi, open_price > upper_band and fast_rsi < slow_rsi


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.super_scalper_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="15m", fields=["open", "high", "low", "close"])
    context.set_warmup(1200)
    context.set_metadata(direction_mode="one_way", strategy_family="scalping")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    atr_length = int(context.params.get("atr_length", 14))
    atr_multiplier = float(context.params.get("atr_multiplier", 1.0))
    fast_length = int(context.params.get("fast_rsi_length", 25))
    slow_length = int(context.params.get("slow_rsi_length", 100))
    target_pct = float(context.params.get("target_pct", 0.95))
    if min(atr_length, fast_length, slow_length) < 1 or atr_multiplier <= 0:
        return
    signature = [atr_length, atr_multiplier, fast_length, slow_length]
    state = dict(g.super_scalper_state or {})
    fields = ["open", "high", "low", "close"]
    if not state or list(state.get("signature") or []) != signature:
        required = max(8 * slow_length, 800)
        bars = get_history(required, "15m", fields, g.symbol)
        if len(bars) < required:
            return
        state = _new_state(signature)
        long_signal = False
        short_signal = False
        for index in range(len(bars)):
            long_signal, short_signal = _step(
                state,
                float(bars["open"].iloc[index]),
                float(bars["high"].iloc[index]),
                float(bars["low"].iloc[index]),
                float(bars["close"].iloc[index]),
                atr_length,
                atr_multiplier,
                fast_length,
                slow_length,
            )
    else:
        bars = get_history(1, "15m", fields, g.symbol)
        if len(bars) < 1:
            return
        long_signal, short_signal = _step(
            state,
            float(bars["open"].iloc[-1]),
            float(bars["high"].iloc[-1]),
            float(bars["low"].iloc[-1]),
            float(bars["close"].iloc[-1]),
            atr_length,
            atr_multiplier,
            fast_length,
            slow_length,
        )
    g.super_scalper_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_signal and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="super_scalper_long")
    elif short_signal and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="super_scalper_short")
