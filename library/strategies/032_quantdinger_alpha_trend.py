"""Alpha-style trend strategy driven by volatility and money flow."""

# @param multiplier float 1.0 range=0.1:10:0.1
# @param common_period int 14 range=2:300:1
# @param use_rsi bool false
# @param target_pct float 0.95 range=0.05:1:0.05


def _window_average(buffer, value, length):
    buffer.append(float(value))
    if len(buffer) > length:
        buffer.pop(0)
    if len(buffer) < length:
        return float("nan")
    return sum(buffer) / float(length)


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
        "previous_close": None,
        "tr": [],
        "previous_typical": None,
        "positive_flow": [],
        "negative_flow": [],
        "gain": {"value": None, "seed": []},
        "loss": {"value": None, "seed": []},
        "alpha": None,
        "alpha_history": [],
    }


def _flow_step(state, high, low, close, volume, period):
    typical = (high + low + close) / 3.0
    previous_typical = state["previous_typical"]
    raw_flow = typical * max(volume, 0.0)
    positive = raw_flow if previous_typical is not None and typical > float(previous_typical) else 0.0
    negative = raw_flow if previous_typical is not None and typical < float(previous_typical) else 0.0
    state["previous_typical"] = typical
    state["positive_flow"].append(positive)
    state["negative_flow"].append(negative)
    if len(state["positive_flow"]) > period:
        state["positive_flow"].pop(0)
        state["negative_flow"].pop(0)
    if len(state["positive_flow"]) < period:
        return float("nan")
    positive_sum = sum(state["positive_flow"])
    negative_sum = sum(state["negative_flow"])
    if negative_sum <= 1e-15:
        return 100.0
    return 100.0 - 100.0 / (1.0 + positive_sum / negative_sum)


def _step(state, high, low, close, volume, multiplier, period, use_rsi):
    previous_close = state["previous_close"]
    true_range = high - low if previous_close is None else max(
        high - low,
        abs(high - float(previous_close)),
        abs(low - float(previous_close)),
    )
    atr = _window_average(state["tr"], true_range, period)
    change = 0.0 if previous_close is None else close - float(previous_close)
    average_gain = _rma_step(state["gain"], max(change, 0.0), period)
    average_loss = _rma_step(state["loss"], max(-change, 0.0), period)
    rsi = float("nan")
    if average_gain == average_gain and average_loss == average_loss:
        rsi = 100.0 if average_loss <= 1e-15 else 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    money_flow = _flow_step(state, high, low, close, volume, period)
    state["previous_close"] = close
    if atr != atr:
        return False, False
    positive_regime = rsi >= 50.0 if use_rsi else money_flow >= 50.0
    upper_candidate = low - atr * multiplier
    lower_candidate = high + atr * multiplier
    previous_alpha = state["alpha"]
    if previous_alpha is None:
        alpha = upper_candidate if positive_regime else lower_candidate
    elif positive_regime:
        alpha = max(float(previous_alpha), upper_candidate)
    else:
        alpha = min(float(previous_alpha), lower_candidate)
    state["alpha"] = alpha
    history = state["alpha_history"]
    history.append(float(alpha))
    if len(history) > 4:
        history.pop(0)
    if len(history) < 4:
        return False, False
    previous_difference = float(history[-2]) - float(history[-4])
    current_difference = float(history[-1]) - float(history[-3])
    return previous_difference <= 0 and current_difference > 0, previous_difference >= 0 and current_difference < 0


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.alpha_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close", "volume"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    multiplier = float(context.params.get("multiplier", 1.0))
    period = int(context.params.get("common_period", 14))
    use_rsi = bool(context.params.get("use_rsi", False))
    target_pct = float(context.params.get("target_pct", 0.95))
    if multiplier <= 0 or period < 2:
        return
    signature = [multiplier, period, use_rsi]
    state = dict(g.alpha_state or {})
    fields = ["high", "low", "close", "volume"]
    if not state or list(state.get("signature") or []) != signature:
        required = max(12 * period, 240)
        bars = get_history(required, "1h", fields, g.symbol)
        if len(bars) < required:
            return
        state = _new_state(signature)
        buy_signal = False
        sell_signal = False
        for index in range(len(bars)):
            buy_signal, sell_signal = _step(
                state,
                float(bars["high"].iloc[index]),
                float(bars["low"].iloc[index]),
                float(bars["close"].iloc[index]),
                float(bars["volume"].iloc[index]),
                multiplier,
                period,
                use_rsi,
            )
    else:
        bars = get_history(1, "1h", fields, g.symbol)
        if len(bars) < 1:
            return
        buy_signal, sell_signal = _step(
            state,
            float(bars["high"].iloc[-1]),
            float(bars["low"].iloc[-1]),
            float(bars["close"].iloc[-1]),
            float(bars["volume"].iloc[-1]),
            multiplier,
            period,
            use_rsi,
        )
    g.alpha_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy_signal and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="alpha_trend_long")
    elif sell_signal and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="alpha_trend_short")
