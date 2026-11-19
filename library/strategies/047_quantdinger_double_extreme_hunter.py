"""Double-bottom and double-top reversal strategy with price tolerance."""

# @param period int 100 range=10:500:1
# @param tolerance_pct float 0.5 range=0.05:5:0.05
# @param minimum_gap int 5 range=1:100:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {
        "signature": signature,
        "highs": [],
        "lows": [],
        "last_bottom_index": None,
        "last_bottom_price": None,
        "last_top_index": None,
        "last_top_price": None,
        "bar_index": 0,
    }


def _step(state, high, low, period, tolerance_pct, minimum_gap):
    state["bar_index"] = int(state["bar_index"]) + 1
    state["highs"].append(high)
    state["lows"].append(low)
    if len(state["highs"]) > period:
        state["highs"].pop(0)
        state["lows"].pop(0)
    if len(state["highs"]) < period:
        return False, False
    tolerance = tolerance_pct / 100.0
    is_bottom = low <= min(state["lows"])
    is_top = high >= max(state["highs"])
    double_bottom = False
    double_top = False
    index = int(state["bar_index"])
    if is_bottom:
        previous_price = state["last_bottom_price"]
        previous_index = state["last_bottom_index"]
        if previous_price is not None and previous_index is not None:
            distance = abs(low - float(previous_price)) / max(abs(float(previous_price)), 1e-9)
            double_bottom = index - int(previous_index) >= minimum_gap and distance <= tolerance
        state["last_bottom_price"] = low
        state["last_bottom_index"] = index
    if is_top:
        previous_price = state["last_top_price"]
        previous_index = state["last_top_index"]
        if previous_price is not None and previous_index is not None:
            distance = abs(high - float(previous_price)) / max(abs(float(previous_price)), 1e-9)
            double_top = index - int(previous_index) >= minimum_gap and distance <= tolerance
        state["last_top_price"] = high
        state["last_top_index"] = index
    return double_bottom, double_top


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.extreme_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low"])
    context.set_warmup(900)
    context.set_metadata(direction_mode="one_way", strategy_family="pattern")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    period = int(context.params.get("period", 100))
    tolerance_pct = float(context.params.get("tolerance_pct", 0.5))
    minimum_gap = int(context.params.get("minimum_gap", 5))
    target_pct = float(context.params.get("target_pct", 0.95))
    if period < 10 or tolerance_pct <= 0 or minimum_gap < 1:
        return
    signature = [period, tolerance_pct, minimum_gap]
    state = dict(g.extreme_state or {})
    if not state or list(state.get("signature") or []) != signature:
        required = max(5 * period, 500)
        bars = get_history(required, "1h", ["high", "low"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(signature)
        double_bottom = False
        double_top = False
        for index in range(len(bars)):
            double_bottom, double_top = _step(
                state,
                float(bars["high"].iloc[index]),
                float(bars["low"].iloc[index]),
                period,
                tolerance_pct,
                minimum_gap,
            )
    else:
        bars = get_history(1, "1h", ["high", "low"], g.symbol)
        if len(bars) < 1:
            return
        double_bottom, double_top = _step(
            state,
            float(bars["high"].iloc[-1]),
            float(bars["low"].iloc[-1]),
            period,
            tolerance_pct,
            minimum_gap,
        )
    g.extreme_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if double_bottom and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="double_bottom_long")
    elif double_top and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="double_top_short")
