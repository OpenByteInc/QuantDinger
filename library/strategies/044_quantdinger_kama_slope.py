"""Long-only Kaufman adaptive moving-average slope strategy."""

# @param efficiency_length int 10 range=2:300:1
# @param fast_length int 5 range=1:100:1
# @param slow_length int 50 range=2:500:1
# @param rising_period int 10 range=1:100:1
# @param falling_period int 10 range=1:100:1
# @param target_pct float 0.80 range=0.05:1:0.05


def _new_state(signature):
    return {
        "signature": signature,
        "prices": [],
        "changes": [],
        "kama": None,
        "kama_history": [],
    }


def _step(state, close, efficiency_length, fast_length, slow_length, history_length):
    prices = state["prices"]
    if prices:
        state["changes"].append(abs(close - float(prices[-1])))
        if len(state["changes"]) > efficiency_length:
            state["changes"].pop(0)
    prices.append(close)
    if len(prices) > efficiency_length + 1:
        prices.pop(0)
    if len(prices) < efficiency_length + 1 or len(state["changes"]) < efficiency_length:
        return False, False
    movement = abs(close - float(prices[0]))
    volatility = sum(state["changes"])
    efficiency = 0.0 if volatility <= 1e-15 else movement / volatility
    fastest = 2.0 / (fast_length + 1.0)
    slowest = 2.0 / (slow_length + 1.0)
    smoothing = (efficiency * (fastest - slowest) + slowest) ** 2
    previous_kama = close if state["kama"] is None else float(state["kama"])
    kama = previous_kama + smoothing * (close - previous_kama)
    state["kama"] = kama
    history = state["kama_history"]
    history.append(kama)
    if len(history) > history_length + 1:
        history.pop(0)
    return True, True


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.kama_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(800)
    context.set_metadata(direction_mode="long_only", strategy_family="trend")


def handle_data(context, data):
    efficiency_length = int(context.params.get("efficiency_length", 10))
    fast_length = int(context.params.get("fast_length", 5))
    slow_length = int(context.params.get("slow_length", 50))
    rising_period = int(context.params.get("rising_period", 10))
    falling_period = int(context.params.get("falling_period", 10))
    target_pct = float(context.params.get("target_pct", 0.80))
    if min(efficiency_length, fast_length, slow_length, rising_period, falling_period) < 1:
        return
    signature = [efficiency_length, fast_length, slow_length, rising_period, falling_period]
    history_length = max(rising_period, falling_period)
    state = dict(g.kama_state or {})
    if not state or list(state.get("signature") or []) != signature:
        required = max(12 * slow_length, 600)
        bars = get_history(required, "1h", ["close"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(signature)
        for close in bars["close"]:
            _step(state, float(close), efficiency_length, fast_length, slow_length, history_length)
    else:
        bars = get_history(1, "1h", ["close"], g.symbol)
        if len(bars) < 1:
            return
        _step(
            state,
            float(bars["close"].iloc[-1]),
            efficiency_length,
            fast_length,
            slow_length,
            history_length,
        )
    g.kama_state = state
    history = state["kama_history"]
    if len(history) < history_length + 1:
        return
    current = float(history[-1])
    rising = current > max(history[-rising_period - 1:-1])
    falling = current < min(history[-falling_period - 1:-1])
    amount = float(get_position(g.symbol).amount or 0.0)
    if rising and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="kama_rising_long")
    elif falling and amount > 0:
        order_target_percent(g.symbol, 0.0, reason="kama_falling_exit")
