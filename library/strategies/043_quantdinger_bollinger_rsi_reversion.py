"""Bollinger and RSI mean-reversion strategy with staged exposure."""

# @param band_length int 20 range=2:300:1
# @param band_multiplier float 2.0 range=0.1:20:0.1
# @param rsi_oversold float 30 range=1:49:1
# @param rsi_overbought float 70 range=51:99:1
# @param exposure_step float 0.10 range=0.05:0.5:0.05
# @param maximum_entries int 5 range=1:10:1


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
        "prices": [],
        "previous_close": None,
        "gain": {"value": None, "seed": []},
        "loss": {"value": None, "seed": []},
        "scale_count": 0,
        "scale_side": 0,
    }


def _step(state, close, length, multiplier):
    prices = state["prices"]
    prices.append(close)
    if len(prices) > length:
        prices.pop(0)
    basis = float("nan")
    lower = float("nan")
    upper = float("nan")
    if len(prices) == length:
        basis = sum(prices) / float(length)
        variance = sum((float(item) - basis) ** 2 for item in prices) / float(length)
        deviation = multiplier * variance ** 0.5
        upper = basis + deviation
        lower = basis - deviation
    previous_close = state["previous_close"]
    state["previous_close"] = close
    rsi = float("nan")
    if previous_close is not None:
        change = close - float(previous_close)
        gain = _rma_step(state["gain"], max(change, 0.0), 14)
        loss = _rma_step(state["loss"], max(-change, 0.0), 14)
        if gain == gain and loss == loss:
            rsi = 100.0 if loss <= 1e-15 else 100.0 - 100.0 / (1.0 + gain / loss)
    return basis, upper, lower, rsi


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.reversion_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="mean_reversion")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    length = int(context.params.get("band_length", 20))
    multiplier = float(context.params.get("band_multiplier", 2.0))
    oversold = float(context.params.get("rsi_oversold", 30.0))
    overbought = float(context.params.get("rsi_overbought", 70.0))
    exposure_step = float(context.params.get("exposure_step", 0.10))
    maximum_entries = int(context.params.get("maximum_entries", 5))
    if length < 2 or multiplier <= 0 or exposure_step <= 0 or maximum_entries < 1:
        return
    signature = [length, multiplier, oversold, overbought, exposure_step, maximum_entries]
    state = dict(g.reversion_state or {})
    if not state or list(state.get("signature") or []) != signature:
        required = max(12 * length, 300)
        bars = get_history(required, "1h", ["close"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(signature)
        values = (float("nan"),) * 4
        for close in bars["close"]:
            values = _step(state, float(close), length, multiplier)
        close = float(bars["close"].iloc[-1])
    else:
        bars = get_history(1, "1h", ["close"], g.symbol)
        if len(bars) < 1:
            return
        close = float(bars["close"].iloc[-1])
        values = _step(state, close, length, multiplier)
    basis, upper, lower, rsi = values
    if any(value != value for value in values):
        g.reversion_state = state
        return
    amount = float(get_position(g.symbol).amount or 0.0)
    if close < lower and rsi < oversold:
        current_scale = int(state["scale_count"]) if int(state["scale_side"]) == 1 else 0
        if current_scale < maximum_entries:
            state["scale_count"] = current_scale + 1
            state["scale_side"] = 1
            order_target_percent(g.symbol, min(0.95, exposure_step * int(state["scale_count"])), reason="bb_rsi_long")
    elif amount > 0 and close > upper and rsi > 50.0:
        state["scale_count"] = 0
        state["scale_side"] = 0
        order_target_percent(g.symbol, 0.0, reason="bb_rsi_long_exit")
    elif close > upper and rsi > overbought:
        current_scale = int(state["scale_count"]) if int(state["scale_side"]) == -1 else 0
        if current_scale < maximum_entries:
            state["scale_count"] = current_scale + 1
            state["scale_side"] = -1
            order_target_percent(g.symbol, -min(0.95, exposure_step * int(state["scale_count"])), reason="bb_rsi_short")
    elif amount < 0 and close <= basis:
        state["scale_count"] = 0
        state["scale_side"] = 0
        order_target_percent(g.symbol, 0.0, reason="bb_rsi_short_exit")
    g.reversion_state = state
