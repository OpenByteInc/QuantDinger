"""Fast and slow ATR trailing-stop crossover strategy."""

# @param fast_period int 5 range=1:300:1
# @param fast_multiplier float 0.5 range=0.1:20:0.1
# @param slow_period int 10 range=1:300:1
# @param slow_multiplier float 3.0 range=0.1:20:0.1
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


def _trail(previous_stop, previous_source, source, loss):
    if source > previous_stop and previous_source > previous_stop:
        return max(previous_stop, source - loss)
    if source < previous_stop and previous_source < previous_stop:
        return min(previous_stop, source + loss)
    if source > previous_stop:
        return source - loss
    return source + loss


def _new_state(signature):
    return {
        "signature": signature,
        "previous_close": None,
        "fast_atr": {"value": None, "seed": []},
        "slow_atr": {"value": None, "seed": []},
        "fast_trail": 0.0,
        "slow_trail": 0.0,
        "ready": False,
    }


def _step(state, high, low, close, fast_period, fast_multiplier, slow_period, slow_multiplier):
    previous_close = state["previous_close"]
    true_range = high - low if previous_close is None else max(
        high - low,
        abs(high - float(previous_close)),
        abs(low - float(previous_close)),
    )
    fast_atr = _rma_step(state["fast_atr"], true_range, fast_period)
    slow_atr = _rma_step(state["slow_atr"], true_range, slow_period)
    if fast_atr != fast_atr or slow_atr != slow_atr:
        state["previous_close"] = close
        return False, False
    previous_fast = float(state["fast_trail"])
    previous_slow = float(state["slow_trail"])
    source_before = close if previous_close is None else float(previous_close)
    current_fast = _trail(previous_fast, source_before, close, fast_multiplier * fast_atr)
    current_slow = _trail(previous_slow, source_before, close, slow_multiplier * slow_atr)
    state["fast_trail"] = current_fast
    state["slow_trail"] = current_slow
    state["previous_close"] = close
    if not state["ready"]:
        state["ready"] = True
        return False, False
    return previous_fast <= previous_slow and current_fast > current_slow, previous_fast >= previous_slow and current_fast < current_slow


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.trail_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    fast_period = int(context.params.get("fast_period", 5))
    fast_multiplier = float(context.params.get("fast_multiplier", 0.5))
    slow_period = int(context.params.get("slow_period", 10))
    slow_multiplier = float(context.params.get("slow_multiplier", 3.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    if min(fast_period, slow_period) < 1 or min(fast_multiplier, slow_multiplier) <= 0:
        return
    signature = [fast_period, fast_multiplier, slow_period, slow_multiplier]
    state = dict(g.trail_state or {})
    if not state or list(state.get("signature") or []) != signature:
        required = max(20 * max(fast_period, slow_period), 300)
        bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
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
                fast_period,
                fast_multiplier,
                slow_period,
                slow_multiplier,
            )
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        buy_signal, sell_signal = _step(
            state,
            float(bars["high"].iloc[-1]),
            float(bars["low"].iloc[-1]),
            float(bars["close"].iloc[-1]),
            fast_period,
            fast_multiplier,
            slow_period,
            slow_multiplier,
        )
    g.trail_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy_signal and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="dual_atr_long")
    elif sell_signal and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="dual_atr_short")
