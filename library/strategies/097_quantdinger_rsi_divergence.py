"""Confirmed regular RSI divergence entries with RSI threshold exits."""

# @param rsi_length int 14 range=2:100:1
# @param long_entry_level float 35 range=0:100:0.5
# @param short_entry_level float 76 range=0:100:0.5
# @param long_exit_level float 80 range=0:100:0.5
# @param short_exit_level float 54.1 range=0:100:0.1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "previous_close": None, "avg_gain": None, "avg_loss": None, "rsi": [], "highs": [], "lows": [], "index": -1, "last_low_pivot": None, "last_high_pivot": None}


def _step(state, high, low, close, rsi_length, long_entry_level, short_entry_level, long_exit_level, short_exit_level):
    state["index"] = int(state["index"]) + 1
    previous_close = state["previous_close"]
    change = 0.0 if previous_close is None else close - float(previous_close)
    gain = max(change, 0.0)
    loss = max(-change, 0.0)
    state["avg_gain"] = gain if state["avg_gain"] is None else (float(state["avg_gain"]) * (rsi_length - 1) + gain) / float(rsi_length)
    state["avg_loss"] = loss if state["avg_loss"] is None else (float(state["avg_loss"]) * (rsi_length - 1) + loss) / float(rsi_length)
    rsi = 100.0 if float(state["avg_loss"]) <= 1e-15 else 100.0 - 100.0 / (1.0 + float(state["avg_gain"]) / float(state["avg_loss"]))
    state["rsi"].append(rsi)
    state["highs"].append(high)
    state["lows"].append(low)
    state["rsi"] = state["rsi"][-61:]
    state["highs"] = state["highs"][-61:]
    state["lows"] = state["lows"][-61:]
    bull = bear = False
    if len(state["rsi"]) >= 11:
        rsi_window = state["rsi"][-11:]
        candidate_rsi = rsi_window[5]
        candidate_index = int(state["index"]) - 5
        candidate_low = state["lows"][-6]
        candidate_high = state["highs"][-6]
        if candidate_rsi <= min(rsi_window):
            previous = state["last_low_pivot"]
            if previous is not None:
                distance = candidate_index - int(previous["index"])
                bull = 5 <= distance <= 60 and candidate_low < float(previous["price"]) and candidate_rsi > float(previous["rsi"]) and rsi < long_entry_level
            state["last_low_pivot"] = {"index": candidate_index, "price": candidate_low, "rsi": candidate_rsi}
        if candidate_rsi >= max(rsi_window):
            previous = state["last_high_pivot"]
            if previous is not None:
                distance = candidate_index - int(previous["index"])
                bear = 5 <= distance <= 60 and candidate_high > float(previous["price"]) and candidate_rsi < float(previous["rsi"]) and rsi > short_entry_level
            state["last_high_pivot"] = {"index": candidate_index, "price": candidate_high, "rsi": candidate_rsi}
    state["previous_close"] = close
    return bull, bear, rsi >= long_exit_level, rsi <= short_exit_level


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.rsi_divergence_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(900)
    context.set_metadata(direction_mode="one_way", strategy_family="momentum")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    rsi_length = int(context.params.get("rsi_length", 14))
    long_entry_level = float(context.params.get("long_entry_level", 35.0))
    short_entry_level = float(context.params.get("short_entry_level", 76.0))
    long_exit_level = float(context.params.get("long_exit_level", 80.0))
    short_exit_level = float(context.params.get("short_exit_level", 54.1))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [rsi_length, long_entry_level, short_entry_level, long_exit_level, short_exit_level]
    state = dict(g.rsi_divergence_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(600, rsi_length * 30)
    bars = get_history(required if cold else 1, "1h", ["high", "low", "close"], g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    long_entry = short_entry = long_exit = short_exit = False
    for _, row in bars.iterrows():
        long_entry, short_entry, long_exit, short_exit = _step(state, float(row["high"]), float(row["low"]), float(row["close"]), rsi_length, long_entry_level, short_entry_level, long_exit_level, short_exit_level)
    g.rsi_divergence_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if amount > 0 and long_exit:
        order_target_percent(g.symbol, 0.0, reason="rsi_divergence_long_exit")
    elif amount < 0 and short_exit:
        order_target_percent(g.symbol, 0.0, reason="rsi_divergence_short_exit")
    elif long_entry and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="rsi_divergence_long")
    elif short_entry and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="rsi_divergence_short")
