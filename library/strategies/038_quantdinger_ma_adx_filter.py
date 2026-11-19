"""Moving-average bias strategy gated by Wilder ADX."""

# @param fast_length int 20 range=1:300:1
# @param slow_length int 120 range=2:600:1
# @param adx_length int 14 range=2:200:1
# @param adx_threshold float 20 range=0:100:1
# @param use_adx bool true
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


def _sma_step(buffer, value, length):
    buffer.append(float(value))
    if len(buffer) > length:
        buffer.pop(0)
    if len(buffer) < length:
        return float("nan")
    return sum(buffer) / float(length)


def _new_state(signature):
    return {
        "signature": signature,
        "fast": [],
        "slow": [],
        "previous_high": None,
        "previous_low": None,
        "previous_close": None,
        "tr": {"value": None, "seed": []},
        "plus_dm": {"value": None, "seed": []},
        "minus_dm": {"value": None, "seed": []},
        "adx": {"value": None, "seed": []},
    }


def _step(state, high, low, close, fast_length, slow_length, adx_length):
    fast = _sma_step(state["fast"], close, fast_length)
    slow = _sma_step(state["slow"], close, slow_length)
    previous_high = state["previous_high"]
    previous_low = state["previous_low"]
    previous_close = state["previous_close"]
    state["previous_high"] = high
    state["previous_low"] = low
    state["previous_close"] = close
    if previous_high is None or previous_low is None or previous_close is None:
        return fast, slow, float("nan")
    upward = high - float(previous_high)
    downward = float(previous_low) - low
    plus_dm = upward if upward > downward and upward > 0 else 0.0
    minus_dm = downward if downward > upward and downward > 0 else 0.0
    true_range = max(high - low, abs(high - float(previous_close)), abs(low - float(previous_close)))
    smoothed_tr = _rma_step(state["tr"], true_range, adx_length)
    smoothed_plus = _rma_step(state["plus_dm"], plus_dm, adx_length)
    smoothed_minus = _rma_step(state["minus_dm"], minus_dm, adx_length)
    if smoothed_tr != smoothed_tr or smoothed_tr <= 1e-15:
        return fast, slow, float("nan")
    plus_di = 100.0 * smoothed_plus / smoothed_tr
    minus_di = 100.0 * smoothed_minus / smoothed_tr
    denominator = plus_di + minus_di
    dx = 0.0 if denominator <= 1e-15 else 100.0 * abs(plus_di - minus_di) / denominator
    adx = _rma_step(state["adx"], dx, adx_length)
    return fast, slow, adx


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.adx_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(900)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    fast_length = int(context.params.get("fast_length", 20))
    slow_length = int(context.params.get("slow_length", 120))
    adx_length = int(context.params.get("adx_length", 14))
    adx_threshold = float(context.params.get("adx_threshold", 20.0))
    use_adx = bool(context.params.get("use_adx", True))
    target_pct = float(context.params.get("target_pct", 0.95))
    if min(fast_length, slow_length, adx_length) < 1:
        return
    signature = [fast_length, slow_length, adx_length]
    state = dict(g.adx_state or {})
    if not state or list(state.get("signature") or []) != signature:
        required = max(5 * slow_length, 600)
        bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(signature)
        values = (float("nan"), float("nan"), float("nan"))
        for index in range(len(bars)):
            values = _step(
                state,
                float(bars["high"].iloc[index]),
                float(bars["low"].iloc[index]),
                float(bars["close"].iloc[index]),
                fast_length,
                slow_length,
                adx_length,
            )
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        values = _step(
            state,
            float(bars["high"].iloc[-1]),
            float(bars["low"].iloc[-1]),
            float(bars["close"].iloc[-1]),
            fast_length,
            slow_length,
            adx_length,
        )
    g.adx_state = state
    fast, slow, adx = values
    if fast != fast or slow != slow or (use_adx and adx != adx):
        return
    if use_adx and adx < adx_threshold:
        return
    amount = float(get_position(g.symbol).amount or 0.0)
    if fast > slow and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="ma_adx_long")
    elif fast < slow and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="ma_adx_short")
