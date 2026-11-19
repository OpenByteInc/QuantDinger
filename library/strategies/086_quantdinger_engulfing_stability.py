"""Stable-candle engulfing strategy with RSI and entry stop protection."""

# @param stability float 0.5 range=0:1:0.05
# @param rsi_level float 50 range=1:99:1
# @param delta_length int 5 range=3:100:1
# @param stop_loss_pct float 2 range=0.1:20:0.1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "opens": [], "highs": [], "lows": [], "closes": [], "gains": [], "losses": [], "stop": None}


def _step(state, open_price, high, low, close, stability, rsi_level, delta_length, stop_loss_pct):
    previous_close = state["closes"][-1] if state["closes"] else close
    change = close - previous_close
    state["gains"].append(max(change, 0.0))
    state["losses"].append(max(-change, 0.0))
    state["gains"] = state["gains"][-14:]
    state["losses"] = state["losses"][-14:]
    state["opens"].append(open_price)
    state["highs"].append(high)
    state["lows"].append(low)
    state["closes"].append(close)
    keep = max(20, delta_length + 2)
    state["opens"] = state["opens"][-keep:]
    state["highs"] = state["highs"][-keep:]
    state["lows"] = state["lows"][-keep:]
    state["closes"] = state["closes"][-keep:]
    if len(state["gains"]) < 14 or len(state["closes"]) <= delta_length:
        return False, False, False
    avg_gain = sum(state["gains"]) / 14.0
    avg_loss = sum(state["losses"]) / 14.0
    rsi = 100.0 if avg_loss <= 1e-15 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
    stable = abs(close - open_price) / max(true_range, 1e-15) > stability
    previous_open = state["opens"][-2]
    previous_bar_close = state["closes"][-2]
    bullish = previous_bar_close < previous_open and close > open_price and close > previous_open
    bearish = previous_bar_close > previous_open and close < open_price and close < previous_open
    entry = bullish and stable and rsi < rsi_level and close < state["closes"][-delta_length - 1]
    exit_signal = bearish and stable and rsi > 100.0 - rsi_level and close > state["closes"][-delta_length - 1]
    if entry:
        state["stop"] = close * (1.0 - stop_loss_pct / 100.0)
    stop_hit = state["stop"] is not None and close <= float(state["stop"])
    return entry, exit_signal, stop_hit


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.engulfing_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["open", "high", "low", "close"])
    context.set_warmup(500)
    context.set_metadata(direction_mode="long_only", strategy_family="price_action")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    stability = float(context.params.get("stability", 0.5))
    rsi_level = float(context.params.get("rsi_level", 50.0))
    delta_length = int(context.params.get("delta_length", 5))
    stop_loss_pct = float(context.params.get("stop_loss_pct", 2.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [stability, rsi_level, delta_length, stop_loss_pct]
    state = dict(g.engulfing_state or {})
    count = max(300, delta_length * 8)
    bars = get_history(count if not state or list(state.get("signature") or []) != signature else 1, "1h", ["open", "high", "low", "close"], g.symbol)
    if len(bars) < (count if not state or list(state.get("signature") or []) != signature else 1):
        return
    if not state or list(state.get("signature") or []) != signature:
        state = _new_state(signature)
    entry = exit_signal = stop_hit = False
    for _, row in bars.iterrows():
        entry, exit_signal, stop_hit = _step(state, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]), stability, rsi_level, delta_length, stop_loss_pct)
    g.engulfing_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if amount > 0 and (exit_signal or stop_hit):
        order_target_percent(g.symbol, 0.0, reason="engulfing_exit")
    elif entry and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="engulfing_long")
