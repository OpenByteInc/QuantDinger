"""Heikin-Ashi breakout strategy for a single crypto perpetual market."""

# @param use_heikin_ashi bool true
# @param target_pct float 0.95 range=0.05:1:0.05


def _update_bar(state, open_price, high, low, close, use_heikin_ashi):
    if use_heikin_ashi:
        synthetic_close = (open_price + high + low + close) / 4.0
        if state.get("ready"):
            synthetic_open = (float(state["ha_open"]) + float(state["ha_close"])) / 2.0
        else:
            synthetic_open = (open_price + close) / 2.0
        synthetic_high = max(high, synthetic_open, synthetic_close)
        synthetic_low = min(low, synthetic_open, synthetic_close)
    else:
        synthetic_open = open_price
        synthetic_high = high
        synthetic_low = low
        synthetic_close = close
    state["ha_open"] = float(synthetic_open)
    state["ha_close"] = float(synthetic_close)
    state["ready"] = True
    highs = state["highs"]
    lows = state["lows"]
    highs.append(float(synthetic_high))
    lows.append(float(synthetic_low))
    if len(highs) > 3:
        highs.pop(0)
    if len(lows) > 3:
        lows.pop(0)
    return float(synthetic_open), float(synthetic_close)


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.ha_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["open", "high", "low", "close"])
    context.set_warmup(300)
    context.set_metadata(direction_mode="long_only", strategy_family="price_action")


def handle_data(context, data):
    use_heikin_ashi = bool(context.params.get("use_heikin_ashi", True))
    target_pct = float(context.params.get("target_pct", 0.95))
    state = dict(g.ha_state or {})
    if not state or bool(state.get("use_heikin_ashi")) != use_heikin_ashi:
        bars = get_history(240, "1h", ["open", "high", "low", "close"], g.symbol)
        if len(bars) < 240:
            return
        state = {
            "use_heikin_ashi": use_heikin_ashi,
            "ready": False,
            "ha_open": 0.0,
            "ha_close": 0.0,
            "highs": [],
            "lows": [],
        }
        synthetic_open = float("nan")
        synthetic_close = float("nan")
        for index in range(len(bars)):
            synthetic_open, synthetic_close = _update_bar(
                state,
                float(bars["open"].iloc[index]),
                float(bars["high"].iloc[index]),
                float(bars["low"].iloc[index]),
                float(bars["close"].iloc[index]),
                use_heikin_ashi,
            )
    else:
        bars = get_history(1, "1h", ["open", "high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        synthetic_open, synthetic_close = _update_bar(
            state,
            float(bars["open"].iloc[-1]),
            float(bars["high"].iloc[-1]),
            float(bars["low"].iloc[-1]),
            float(bars["close"].iloc[-1]),
            use_heikin_ashi,
        )
    g.ha_state = state
    highs = state["highs"]
    lows = state["lows"]
    if len(highs) < 3 or len(lows) < 2:
        return
    enter_long = (
        synthetic_close > float(highs[-2])
        and synthetic_close > synthetic_open
        and float(highs[-2]) > float(highs[-3])
    )
    exit_long = synthetic_close < synthetic_open and synthetic_close < float(lows[-2])
    amount = float(get_position(g.symbol).amount or 0.0)
    if enter_long and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="ha_breakout_long")
    elif exit_long and amount > 0:
        order_target_percent(g.symbol, 0.0, reason="ha_breakout_exit")
