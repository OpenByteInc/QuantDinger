"""Standard-market-bar reversal strategy derived from a non-standard-chart caution example."""

# @param use_ma_cross bool false
# @param fast_length int 5 range=2:200:1
# @param slow_length int 25 range=3:400:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "opens": [], "closes": [], "previous_fast": None, "previous_slow": None, "pending": 0}


def _step(state, open_price, close, use_ma_cross, fast_length, slow_length):
    state["opens"].append(open_price)
    state["closes"].append(close)
    if len(state["opens"]) > slow_length + 5:
        state["opens"].pop(0)
        state["closes"].pop(0)
    if len(state["closes"]) < slow_length:
        return False, False
    fast = sum(state["closes"][-fast_length:]) / float(fast_length)
    slow = sum(state["closes"][-slow_length:]) / float(slow_length)
    previous_fast = state["previous_fast"]
    previous_slow = state["previous_slow"]
    state["previous_fast"] = fast
    state["previous_slow"] = slow
    if use_ma_cross:
        raw_long = previous_fast is not None and previous_slow is not None and float(previous_fast) <= float(previous_slow) and fast > slow
        raw_short = previous_fast is not None and previous_slow is not None and float(previous_fast) >= float(previous_slow) and fast < slow
    else:
        up = [state["closes"][-index] > state["opens"][-index] for index in (1, 2, 3)]
        raw_long = up[0] and up[1] and not up[2]
        raw_short = not up[0] and not up[1] and up[2]
    delayed = int(state["pending"])
    state["pending"] = 1 if raw_long else -1 if raw_short else 0
    return delayed == 1, delayed == -1


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.standard_reversal_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["open", "close"])
    context.set_warmup(300)
    context.set_metadata(direction_mode="one_way", strategy_family="reversal")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    use_ma_cross = bool(context.params.get("use_ma_cross", False))
    fast_length = int(context.params.get("fast_length", 5))
    slow_length = int(context.params.get("slow_length", 25))
    target_pct = float(context.params.get("target_pct", 0.95))
    if fast_length < 2 or slow_length <= fast_length:
        return
    signature = [use_ma_cross, fast_length, slow_length]
    state = dict(g.standard_reversal_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(max(240, slow_length * 8), "1h", ["open", "close"], g.symbol)
        if len(bars) < max(240, slow_length * 8):
            return
        state = _new_state(signature)
        go_long = False
        go_short = False
        for index in range(len(bars)):
            go_long, go_short = _step(state, float(bars["open"].iloc[index]), float(bars["close"].iloc[index]), use_ma_cross, fast_length, slow_length)
    else:
        bars = get_history(1, "1h", ["open", "close"], g.symbol)
        if len(bars) < 1:
            return
        go_long, go_short = _step(state, float(bars["open"].iloc[-1]), float(bars["close"].iloc[-1]), use_ma_cross, fast_length, slow_length)
    g.standard_reversal_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if go_long and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="standard_bar_long")
    elif go_short and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="standard_bar_short")
