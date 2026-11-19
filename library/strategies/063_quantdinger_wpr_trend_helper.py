"""Williams-percent-range reversal signals with SMA crossover exits."""

# @param reversal_lookback int 14 range=2:300:1
# @param fast_length int 7 range=2:300:1
# @param slow_length int 20 range=3:600:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "highs": [], "lows": [], "closes": [], "oscillator": [], "previous_fast": None, "previous_slow": None, "pending": 0}


def _step(state, high, low, close, reversal_lookback, fast_length, slow_length):
    state["highs"].append(high)
    state["lows"].append(low)
    state["closes"].append(close)
    maximum = max(reversal_lookback, slow_length, 55)
    for key in ("highs", "lows", "closes"):
        if len(state[key]) > maximum:
            state[key].pop(0)
    if len(state["closes"]) < maximum:
        return False, False, False, False
    highest = max(state["highs"][-reversal_lookback:])
    lowest = min(state["lows"][-reversal_lookback:])
    value = 50.0 if highest <= lowest else 100.0 * (close - lowest) / (highest - lowest)
    state["oscillator"].append(value)
    if len(state["oscillator"]) > 51:
        state["oscillator"].pop(0)
    prior_extreme = None
    for old_value in reversed(state["oscillator"][:-1]):
        if old_value < 10.0 or old_value > 70.0:
            prior_extreme = old_value
            break
    raw_up = value > 70.0 and prior_extreme is not None and float(prior_extreme) < 10.0
    raw_down = value < 10.0 and prior_extreme is not None and float(prior_extreme) > 70.0
    delayed = int(state["pending"])
    state["pending"] = 1 if raw_up else -1 if raw_down else 0
    fast = sum(state["closes"][-fast_length:]) / float(fast_length)
    slow = sum(state["closes"][-slow_length:]) / float(slow_length)
    previous_fast = state["previous_fast"]
    previous_slow = state["previous_slow"]
    state["previous_fast"] = fast
    state["previous_slow"] = slow
    long_exit = previous_fast is not None and previous_slow is not None and float(previous_fast) >= float(previous_slow) and fast < slow
    short_exit = previous_fast is not None and previous_slow is not None and float(previous_fast) <= float(previous_slow) and fast > slow
    return delayed == 1, delayed == -1, long_exit, short_exit


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.wpr_helper_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="reversal")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    reversal_lookback = int(context.params.get("reversal_lookback", 14))
    fast_length = int(context.params.get("fast_length", 7))
    slow_length = int(context.params.get("slow_length", 20))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [reversal_lookback, fast_length, slow_length]
    state = dict(g.wpr_helper_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(400, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 400:
            return
        state = _new_state(signature)
        signals = (False, False, False, False)
        for index in range(len(bars)):
            signals = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]), reversal_lookback, fast_length, slow_length)
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        signals = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]), reversal_lookback, fast_length, slow_length)
    g.wpr_helper_state = state
    go_long, go_short, long_exit, short_exit = signals
    amount = float(get_position(g.symbol).amount or 0.0)
    if go_long and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="wpr_helper_long")
    elif go_short and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="wpr_helper_short")
    elif amount > 0 and long_exit:
        order_target_percent(g.symbol, 0.0, reason="wpr_helper_long_exit")
    elif amount < 0 and short_exit:
        order_target_percent(g.symbol, 0.0, reason="wpr_helper_short_exit")
