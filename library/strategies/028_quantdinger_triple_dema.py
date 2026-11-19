"""Three-line DEMA trend strategy using mixed price sources."""

# @param fast_length int 8 range=1:200:1
# @param medium_length int 20 range=1:300:1
# @param slow_length int 63 range=1:500:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema_step(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _dema_step(state, value, length):
    state["first"] = _ema_step(state.get("first"), value, length)
    state["second"] = _ema_step(state.get("second"), state["first"], length)
    return 2.0 * float(state["first"]) - float(state["second"])


def _new_state(signature):
    return {
        "signature": signature,
        "fast": {"first": None, "second": None},
        "medium": {"first": None, "second": None},
        "slow": {"first": None, "second": None},
    }


def _step(state, open_price, high, low, close, fast_length, medium_length, slow_length):
    average_price = (open_price + high + low + close) / 4.0
    fast = _dema_step(state["fast"], average_price, fast_length)
    medium = _dema_step(state["medium"], low, medium_length)
    slow = _dema_step(state["slow"], close, slow_length)
    return fast, medium, slow


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.dema_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["open", "high", "low", "close"])
    context.set_warmup(600)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    fast_length = int(context.params.get("fast_length", 8))
    medium_length = int(context.params.get("medium_length", 20))
    slow_length = int(context.params.get("slow_length", 63))
    target_pct = float(context.params.get("target_pct", 0.95))
    if min(fast_length, medium_length, slow_length) < 1:
        return
    signature = [fast_length, medium_length, slow_length]
    state = dict(g.dema_state or {})
    fields = ["open", "high", "low", "close"]
    if not state or list(state.get("signature") or []) != signature:
        required = max(6 * max(signature), 300)
        bars = get_history(required, "1h", fields, g.symbol)
        if len(bars) < required:
            return
        state = _new_state(signature)
        values = (float("nan"), float("nan"), float("nan"))
        for index in range(len(bars)):
            values = _step(
                state,
                float(bars["open"].iloc[index]),
                float(bars["high"].iloc[index]),
                float(bars["low"].iloc[index]),
                float(bars["close"].iloc[index]),
                fast_length,
                medium_length,
                slow_length,
            )
    else:
        bars = get_history(1, "1h", fields, g.symbol)
        if len(bars) < 1:
            return
        values = _step(
            state,
            float(bars["open"].iloc[-1]),
            float(bars["high"].iloc[-1]),
            float(bars["low"].iloc[-1]),
            float(bars["close"].iloc[-1]),
            fast_length,
            medium_length,
            slow_length,
        )
    g.dema_state = state
    fast, medium, slow = values
    if not all(float(value) == float(value) for value in values):
        return
    amount = float(get_position(g.symbol).amount or 0.0)
    if fast > medium and fast > slow and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="triple_dema_long")
    elif fast < medium and fast < slow and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="triple_dema_short")
