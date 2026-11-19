"""EMA crossover strategy gated by RSI and ADX with wick-based stops."""

# @param fast_length int 9 range=1:200:1
# @param slow_length int 21 range=2:400:1
# @param rsi_length int 14 range=2:100:1
# @param rsi_long float 55 range=1:100:1
# @param rsi_short float 40 range=1:100:1
# @param adx_length int 14 range=2:100:1
# @param adx_threshold float 0 range=0:100:1
# @param stop_loss_pct float 0.02 range=0.001:0.2:0.001
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _new_state(signature):
    return {"signature": signature, "fast": None, "slow": None, "previous_fast": None, "previous_slow": None, "previous_close": None, "previous_high": None, "previous_low": None, "gains": [], "losses": [], "tr": None, "plus_dm": None, "minus_dm": None, "adx": None, "stop": None}


def _step(state, high, low, close, fast_length, slow_length, rsi_length, adx_length, rsi_long, rsi_short, adx_threshold):
    previous_close = state["previous_close"]
    previous_high = state["previous_high"]
    previous_low = state["previous_low"]
    change = 0.0 if previous_close is None else close - float(previous_close)
    state["gains"].append(max(change, 0.0))
    state["losses"].append(max(-change, 0.0))
    if len(state["gains"]) > rsi_length:
        state["gains"].pop(0)
        state["losses"].pop(0)
    previous_fast = state["fast"]
    previous_slow = state["slow"]
    state["fast"] = _ema(state["fast"], close, fast_length)
    state["slow"] = _ema(state["slow"], close, slow_length)
    state["previous_fast"] = previous_fast
    state["previous_slow"] = previous_slow
    state["previous_close"] = close
    state["previous_high"] = high
    state["previous_low"] = low
    if previous_close is not None and previous_high is not None and previous_low is not None:
        up = high - float(previous_high)
        down = float(previous_low) - low
        true_range = max(high - low, abs(high - float(previous_close)), abs(low - float(previous_close)))
        state["tr"] = _ema(state["tr"], true_range, adx_length)
        state["plus_dm"] = _ema(state["plus_dm"], up if up > down and up > 0 else 0.0, adx_length)
        state["minus_dm"] = _ema(state["minus_dm"], down if down > up and down > 0 else 0.0, adx_length)
        denominator = max(float(state["tr"]), 1e-15)
        plus_di = 100.0 * float(state["plus_dm"]) / denominator
        minus_di = 100.0 * float(state["minus_dm"]) / denominator
        total = plus_di + minus_di
        dx = 0.0 if total <= 1e-15 else 100.0 * abs(plus_di - minus_di) / total
        state["adx"] = _ema(state["adx"], dx, adx_length)
    if len(state["gains"]) < rsi_length or previous_fast is None or previous_slow is None or state["adx"] is None:
        return False, False, False, False
    gain = sum(state["gains"]) / float(rsi_length)
    loss = sum(state["losses"]) / float(rsi_length)
    rsi = 100.0 if loss <= 1e-15 else 100.0 - 100.0 / (1.0 + gain / loss)
    bull_cross = float(previous_fast) <= float(previous_slow) and float(state["fast"]) > float(state["slow"])
    bear_cross = float(previous_fast) >= float(previous_slow) and float(state["fast"]) < float(state["slow"])
    adx_ok = float(state["adx"]) > adx_threshold
    return bull_cross and rsi > rsi_long and adx_ok, bear_cross and rsi < rsi_short and adx_ok, bear_cross, bull_cross


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.ema_rsi_adx_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(600)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    fast_length = int(context.params.get("fast_length", 9))
    slow_length = int(context.params.get("slow_length", 21))
    rsi_length = int(context.params.get("rsi_length", 14))
    rsi_long = float(context.params.get("rsi_long", 55.0))
    rsi_short = float(context.params.get("rsi_short", 40.0))
    adx_length = int(context.params.get("adx_length", 14))
    adx_threshold = float(context.params.get("adx_threshold", 0.0))
    stop_loss_pct = float(context.params.get("stop_loss_pct", 0.02))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [fast_length, slow_length, rsi_length, adx_length, rsi_long, rsi_short, adx_threshold]
    state = dict(g.ema_rsi_adx_state or {})
    fields = ["high", "low", "close"]
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(400, "1h", fields, g.symbol)
        if len(bars) < 400:
            return
        state = _new_state(signature)
        signals = (False, False, False, False)
        for index in range(len(bars)):
            signals = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]), fast_length, slow_length, rsi_length, adx_length, rsi_long, rsi_short, adx_threshold)
    else:
        bars = get_history(1, "1h", fields, g.symbol)
        if len(bars) < 1:
            return
        signals = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]), fast_length, slow_length, rsi_length, adx_length, rsi_long, rsi_short, adx_threshold)
    high = float(bars["high"].iloc[-1])
    low = float(bars["low"].iloc[-1])
    close = float(bars["close"].iloc[-1])
    long_entry, short_entry, close_long, close_short = signals
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_entry and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="ema_rsi_adx_long")
        state["stop"] = close * (1.0 - stop_loss_pct)
    elif short_entry and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="ema_rsi_adx_short")
        state["stop"] = close * (1.0 + stop_loss_pct)
    elif amount > 0 and (close_long or (state.get("stop") is not None and low <= float(state["stop"]))):
        order_target_percent(g.symbol, 0.0, reason="ema_rsi_adx_long_exit")
        state["stop"] = None
    elif amount < 0 and (close_short or (state.get("stop") is not None and high >= float(state["stop"]))):
        order_target_percent(g.symbol, 0.0, reason="ema_rsi_adx_short_exit")
        state["stop"] = None
    g.ema_rsi_adx_state = state
