"""Long-only 10/200 moving-average pullback with drawdown entry and risk exit."""

# @param long_length int 200 range=20:600:1
# @param pullback_length int 10 range=2:100:1
# @param drawdown_lookback int 52 range=10:300:1
# @param drawdown_trigger float 0.14 range=0.01:0.8:0.01
# @param stop_loss_pct float 0.15 range=0.01:0.8:0.01
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "closes": [], "highs": [], "previous_low": None, "entry_reference": None}


def _step(state, high, low, close, long_length, pullback_length, drawdown_lookback, drawdown_trigger):
    state["closes"].append(close)
    state["highs"].append(high)
    maximum = max(long_length, drawdown_lookback)
    if len(state["closes"]) > maximum:
        state["closes"].pop(0)
        state["highs"].pop(0)
    previous_low = state["previous_low"]
    state["previous_low"] = low
    if len(state["closes"]) < maximum:
        return False, False
    long_ma = sum(state["closes"][-long_length:]) / float(long_length)
    pullback_ma = sum(state["closes"][-pullback_length:]) / float(pullback_length)
    highest = max(state["highs"][-drawdown_lookback:])
    drawdown = 0.0 if highest <= 1e-15 else (highest - close) / highest
    buy = (close > long_ma and close < pullback_ma) or drawdown > drawdown_trigger
    exit_signal = close > pullback_ma and previous_low is not None and close < float(previous_low)
    return buy, exit_signal


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.pullback_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(1200)
    context.set_metadata(direction_mode="long_only", strategy_family="pullback")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    long_length = int(context.params.get("long_length", 200))
    pullback_length = int(context.params.get("pullback_length", 10))
    drawdown_lookback = int(context.params.get("drawdown_lookback", 52))
    drawdown_trigger = float(context.params.get("drawdown_trigger", 0.14))
    stop_loss_pct = float(context.params.get("stop_loss_pct", 0.15))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [long_length, pullback_length, drawdown_lookback, drawdown_trigger]
    state = dict(g.pullback_state or {})
    required = max(800, long_length * 4)
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(signature)
        buy = False
        exit_signal = False
        for index in range(len(bars)):
            buy, exit_signal = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]), long_length, pullback_length, drawdown_lookback, drawdown_trigger)
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        buy, exit_signal = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]), long_length, pullback_length, drawdown_lookback, drawdown_trigger)
    close = float(bars["close"].iloc[-1])
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="ma_pullback_long")
        state["entry_reference"] = close
    elif amount > 0 and (exit_signal or (state.get("entry_reference") is not None and close <= float(state["entry_reference"]) * (1.0 - stop_loss_pct))):
        order_target_percent(g.symbol, 0.0, reason="ma_pullback_exit")
        state["entry_reference"] = None
    g.pullback_state = state
