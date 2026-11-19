"""Five-minute composite trend and momentum score with alternating signals."""

# @param score_threshold float 10 range=1:30:1
# @param stop_loss_pct float 0.025 range=0.001:0.2:0.001
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _new_state(signature):
    return {
        "signature": signature,
        "closes": [],
        "highs": [],
        "lows": [],
        "gains": [],
        "losses": [],
        "ema9": None,
        "ema21": None,
        "ema50": None,
        "ema200": None,
        "macd_signal": None,
        "previous_macd_hist": None,
        "previous_close": None,
        "previous_ema9": None,
        "long_stop": None,
        "short_stop": None,
        "atr_direction": 1,
        "regime": 0,
        "entry_reference": None,
    }


def _step(state, high, low, close, threshold):
    previous_close = state["previous_close"]
    change = 0.0 if previous_close is None else close - float(previous_close)
    state["gains"].append(max(change, 0.0))
    state["losses"].append(max(-change, 0.0))
    state["closes"].append(close)
    state["highs"].append(high)
    state["lows"].append(low)
    for key in ("gains", "losses"):
        if len(state[key]) > 14:
            state[key].pop(0)
    for key in ("closes", "highs", "lows"):
        if len(state[key]) > 220:
            state[key].pop(0)
    previous_ema9 = state["ema9"]
    state["ema9"] = _ema(state["ema9"], close, 9)
    state["ema21"] = _ema(state["ema21"], close, 21)
    state["ema50"] = _ema(state["ema50"], close, 50)
    state["ema200"] = _ema(state["ema200"], close, 200)
    fast_macd = _ema(state.get("macd_fast"), close, 12)
    slow_macd = _ema(state.get("macd_slow"), close, 26)
    state["macd_fast"] = fast_macd
    state["macd_slow"] = slow_macd
    macd = fast_macd - slow_macd
    state["macd_signal"] = _ema(state["macd_signal"], macd, 9)
    histogram = macd - float(state["macd_signal"])
    previous_histogram = state["previous_macd_hist"]
    true_range = high - low if previous_close is None else max(high - low, abs(high - float(previous_close)), abs(low - float(previous_close)))
    long_candidate = high - 1.85 * true_range
    short_candidate = low + 1.85 * true_range
    previous_long_stop = state["long_stop"]
    previous_short_stop = state["short_stop"]
    long_stop = long_candidate if previous_long_stop is None or previous_close is None or float(previous_close) <= float(previous_long_stop) else max(long_candidate, float(previous_long_stop))
    short_stop = short_candidate if previous_short_stop is None or previous_close is None or float(previous_close) >= float(previous_short_stop) else min(short_candidate, float(previous_short_stop))
    direction = int(state["atr_direction"])
    if previous_short_stop is not None and close > float(previous_short_stop):
        direction = 1
    elif previous_long_stop is not None and close < float(previous_long_stop):
        direction = -1
    state["long_stop"] = long_stop
    state["short_stop"] = short_stop
    state["atr_direction"] = direction
    state["previous_close"] = close
    state["previous_ema9"] = state["ema9"]
    state["previous_macd_hist"] = histogram
    if len(state["closes"]) < 200 or len(state["gains"]) < 14:
        return False, False
    gain = sum(state["gains"]) / 14.0
    loss = sum(state["losses"]) / 14.0
    rsi = 100.0 if loss <= 1e-15 else 100.0 - 100.0 / (1.0 + gain / loss)
    highest = max(state["highs"][-14:])
    lowest = min(state["lows"][-14:])
    stochastic = 50.0 if highest <= lowest else 100.0 * (close - lowest) / (highest - lowest)
    ema_rising = previous_ema9 is not None and float(state["ema9"]) > float(previous_ema9)
    hist_rising = previous_histogram is not None and histogram > float(previous_histogram)
    long_score = 0.0
    short_score = 0.0
    long_score += 2.0 if state["ema9"] > state["ema21"] else 0.0
    short_score += 2.0 if state["ema9"] < state["ema21"] else 0.0
    long_score += 2.0 if ema_rising else 0.0
    short_score += 2.0 if not ema_rising else 0.0
    long_score += 4.0 if rsi > 50.0 and macd > state["macd_signal"] else 0.0
    short_score += 4.0 if rsi < 50.0 and macd < state["macd_signal"] else 0.0
    long_score += 1.0 if close > state["ema50"] else 0.0
    short_score += 1.0 if close < state["ema50"] else 0.0
    long_score += 1.0 if hist_rising else 0.0
    short_score += 1.0 if not hist_rising else 0.0
    long_score += 1.0 if stochastic > 50.0 else 0.0
    short_score += 1.0 if stochastic < 50.0 else 0.0
    long_score += 1.0 if direction > 0 else 0.0
    short_score += 1.0 if direction < 0 else 0.0
    previous_regime = int(state["regime"])
    if long_score >= threshold and previous_regime <= 0:
        state["regime"] = 1
    elif short_score >= threshold and previous_regime >= 0:
        state["regime"] = -1
    return int(state["regime"]) == 1 and previous_regime != 1, int(state["regime"]) == -1 and previous_regime != -1


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.composite_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="5m", fields=["high", "low", "close"])
    context.set_warmup(1200)
    context.set_metadata(direction_mode="one_way", strategy_family="composite")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    threshold = float(context.params.get("score_threshold", 10.0))
    stop_loss_pct = float(context.params.get("stop_loss_pct", 0.025))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [threshold]
    state = dict(g.composite_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(600, "5m", ["high", "low", "close"], g.symbol)
        if len(bars) < 600:
            return
        state = _new_state(signature)
        go_long = False
        go_short = False
        for index in range(len(bars)):
            go_long, go_short = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]), threshold)
    else:
        bars = get_history(1, "5m", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        go_long, go_short = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]), threshold)
    close = float(bars["close"].iloc[-1])
    amount = float(get_position(g.symbol).amount or 0.0)
    entry_reference = state.get("entry_reference")
    stopped = entry_reference is not None and ((amount > 0 and close <= float(entry_reference) * (1.0 - stop_loss_pct)) or (amount < 0 and close >= float(entry_reference) * (1.0 + stop_loss_pct)))
    if stopped:
        order_target_percent(g.symbol, 0.0, reason="composite_stop")
        state["entry_reference"] = None
    elif go_long and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="composite_long")
        state["entry_reference"] = close
    elif go_short and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="composite_short")
        state["entry_reference"] = close
    g.composite_state = state
