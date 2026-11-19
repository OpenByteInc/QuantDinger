"""EMA slope trend follower with a long-horizon trend filter."""

# @param source_length int 130 range=2:600:1
# @param fast_slope_length int 9 range=1:200:1
# @param slow_slope_length int 21 range=2:300:1
# @param trend_length int 200 range=2:800:1
# @param use_trend_filter bool true
# @param target_pct float 0.30 range=0.05:1:0.05


def _ema_step(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _new_state(signature):
    return {
        "signature": signature,
        "source_ma": None,
        "trend_ma": None,
        "fast_slope": None,
        "slow_slope": None,
    }


def _step(state, close, source_length, fast_length, slow_length, trend_length):
    previous_source_ma = state["source_ma"]
    state["source_ma"] = _ema_step(previous_source_ma, close, source_length)
    state["trend_ma"] = _ema_step(state["trend_ma"], close, trend_length)
    slope = 0.0 if previous_source_ma is None or abs(float(previous_source_ma)) <= 1e-15 else (float(state["source_ma"]) - float(previous_source_ma)) / abs(float(previous_source_ma))
    previous_fast = state["fast_slope"]
    previous_slow = state["slow_slope"]
    state["fast_slope"] = _ema_step(previous_fast, slope, fast_length)
    state["slow_slope"] = _ema_step(previous_slow, slope, slow_length)
    return previous_fast, previous_slow, float(state["fast_slope"]), float(state["slow_slope"]), float(state["trend_ma"])


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.slope_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(1400)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    source_length = int(context.params.get("source_length", 130))
    fast_length = int(context.params.get("fast_slope_length", 9))
    slow_length = int(context.params.get("slow_slope_length", 21))
    trend_length = int(context.params.get("trend_length", 200))
    use_trend_filter = bool(context.params.get("use_trend_filter", True))
    target_pct = float(context.params.get("target_pct", 0.30))
    signature = [source_length, fast_length, slow_length, trend_length]
    if min(signature) < 1:
        return
    state = dict(g.slope_state or {})
    if not state or list(state.get("signature") or []) != signature:
        required = max(5 * max(signature), 1000)
        bars = get_history(required, "1h", ["close"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(signature)
        values = (None, None, float("nan"), float("nan"), float("nan"))
        for close in bars["close"]:
            values = _step(state, float(close), source_length, fast_length, slow_length, trend_length)
        close = float(bars["close"].iloc[-1])
    else:
        bars = get_history(1, "1h", ["close"], g.symbol)
        if len(bars) < 1:
            return
        close = float(bars["close"].iloc[-1])
        values = _step(state, close, source_length, fast_length, slow_length, trend_length)
    g.slope_state = state
    previous_fast, previous_slow, fast_slope, slow_slope, trend_ma = values
    if previous_fast is None or previous_slow is None:
        return
    amount = float(get_position(g.symbol).amount or 0.0)
    long_condition = fast_slope > slow_slope and (not use_trend_filter or close > trend_ma)
    short_condition = fast_slope < slow_slope and (not use_trend_filter or close < trend_ma)
    if long_condition and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="ema_slope_long")
    elif short_condition and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="ema_slope_short")
