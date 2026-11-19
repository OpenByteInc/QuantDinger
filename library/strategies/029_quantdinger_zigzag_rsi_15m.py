"""Fifteen-minute RSI event strategy with a percentage zigzag state."""

# @param rsi_length int 5 range=2:100:1
# @param oversold float 25 range=1:49:1
# @param overbought float 75 range=51:99:1
# @param reversal_pct float 1.0 range=0.1:20:0.1
# @param enable_short bool false
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
        "previous_close": None,
        "gain": {"value": None, "seed": []},
        "loss": {"value": None, "seed": []},
        "rsi": float("nan"),
        "last_long_price": None,
        "last_short_price": None,
        "trend": 1,
        "high_anchor": None,
        "low_anchor": None,
    }


def _step(state, close, rsi_length, oversold, overbought, reversal_pct):
    previous_close = state["previous_close"]
    if previous_close is None:
        state["previous_close"] = float(close)
        return False, False
    change = float(close) - float(previous_close)
    average_gain = _rma_step(state["gain"], max(change, 0.0), rsi_length)
    average_loss = _rma_step(state["loss"], max(-change, 0.0), rsi_length)
    previous_rsi = float(state["rsi"])
    if average_gain == average_gain and average_loss == average_loss:
        current_rsi = 100.0 if average_loss <= 1e-15 else 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    else:
        current_rsi = float("nan")
    state["previous_close"] = float(close)
    state["rsi"] = current_rsi
    if previous_rsi != previous_rsi or current_rsi != current_rsi:
        return False, False
    long_event = previous_rsi <= oversold and current_rsi > oversold
    short_event = previous_rsi >= overbought and current_rsi < overbought
    if long_event:
        state["last_long_price"] = float(close)
    if short_event:
        state["last_short_price"] = float(close)
    long_price = state["last_long_price"]
    short_price = state["last_short_price"]
    if long_price is None or short_price is None:
        return False, False
    if state["high_anchor"] is None:
        state["high_anchor"] = float(long_price)
    if state["low_anchor"] is None:
        state["low_anchor"] = float(short_price)
    previous_trend = int(state["trend"])
    ratio = reversal_pct * 0.01
    if previous_trend > 0:
        if float(long_price) >= float(state["high_anchor"]):
            state["high_anchor"] = float(long_price)
        elif float(short_price) < float(state["high_anchor"]) * (1.0 - ratio):
            state["trend"] = -1
            state["low_anchor"] = float(short_price)
    else:
        if float(short_price) <= float(state["low_anchor"]):
            state["low_anchor"] = float(short_price)
        elif float(long_price) > float(state["low_anchor"]) * (1.0 + ratio):
            state["trend"] = 1
            state["high_anchor"] = float(short_price)
    current_trend = int(state["trend"])
    return previous_trend < 0 and current_trend > 0, previous_trend > 0 and current_trend < 0


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.zigzag_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="15m", fields=["close"])
    context.set_warmup(900)
    context.set_metadata(direction_mode="one_way", strategy_family="reversal")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    rsi_length = int(context.params.get("rsi_length", 5))
    oversold = float(context.params.get("oversold", 25.0))
    overbought = float(context.params.get("overbought", 75.0))
    reversal_pct = float(context.params.get("reversal_pct", 1.0))
    enable_short = bool(context.params.get("enable_short", False))
    target_pct = float(context.params.get("target_pct", 0.95))
    if rsi_length < 2 or not 0 < oversold < overbought < 100 or reversal_pct <= 0:
        return
    signature = [rsi_length, oversold, overbought, reversal_pct]
    state = dict(g.zigzag_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(500, "15m", ["close"], g.symbol)
        if len(bars) < 500:
            return
        state = _new_state(signature)
        long_signal = False
        short_signal = False
        for close in bars["close"]:
            long_signal, short_signal = _step(
                state, float(close), rsi_length, oversold, overbought, reversal_pct
            )
    else:
        bars = get_history(1, "15m", ["close"], g.symbol)
        if len(bars) < 1:
            return
        long_signal, short_signal = _step(
            state,
            float(bars["close"].iloc[-1]),
            rsi_length,
            oversold,
            overbought,
            reversal_pct,
        )
    g.zigzag_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_signal and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="zigzag_rsi_long")
    elif short_signal and amount > 0:
        order_target_percent(g.symbol, 0.0, reason="zigzag_rsi_long_exit")
    elif enable_short and short_signal and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="zigzag_rsi_short")
    elif enable_short and long_signal and amount < 0:
        order_target_percent(g.symbol, 0.0, reason="zigzag_rsi_short_exit")
