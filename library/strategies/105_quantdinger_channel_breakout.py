"""Long/short price-channel breakout strategy with separate exit channels."""

# @param entry_length int 100 range=2:500:1
# @param exit_length int 50 range=2:300:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "highs": [], "lows": [], "previous_close": None, "previous_entry_high": None, "previous_entry_low": None, "previous_exit_high": None, "previous_exit_low": None}


def _step(state, high, low, close, entry_length, exit_length):
    if len(state["highs"]) >= max(entry_length, exit_length):
        entry_high = max(state["highs"][-entry_length:])
        entry_low = min(state["lows"][-entry_length:])
        exit_low = min(state["highs"][-exit_length:])
        exit_high = max(state["lows"][-exit_length:])
        previous_close = state["previous_close"]
        long_entry = previous_close is not None and state["previous_entry_high"] is not None and float(previous_close) <= float(state["previous_entry_high"]) and close > entry_high
        short_entry = previous_close is not None and state["previous_entry_low"] is not None and float(previous_close) >= float(state["previous_entry_low"]) and close < entry_low
        long_exit = previous_close is not None and state["previous_exit_low"] is not None and float(previous_close) <= float(state["previous_exit_low"]) and close > exit_low
        short_exit = previous_close is not None and state["previous_exit_high"] is not None and float(previous_close) <= float(state["previous_exit_high"]) and close > exit_high
        state["previous_entry_high"] = entry_high
        state["previous_entry_low"] = entry_low
        state["previous_exit_high"] = exit_high
        state["previous_exit_low"] = exit_low
    else:
        long_entry = short_entry = long_exit = short_exit = False
    state["highs"].append(high)
    state["lows"].append(low)
    keep = max(entry_length, exit_length) + 2
    state["highs"] = state["highs"][-keep:]
    state["lows"] = state["lows"][-keep:]
    state["previous_close"] = close
    return long_entry, short_entry, long_exit, short_exit


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.channel_breakout_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(1200)
    context.set_metadata(direction_mode="one_way", strategy_family="breakout")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    entry_length = int(context.params.get("entry_length", 100))
    exit_length = int(context.params.get("exit_length", 50))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [entry_length, exit_length]
    state = dict(g.channel_breakout_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(700, entry_length * 8, exit_length * 10)
    bars = get_history(required if cold else 1, "1h", ["high", "low", "close"], g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    long_entry = short_entry = long_exit = short_exit = False
    for _, row in bars.iterrows():
        long_entry, short_entry, long_exit, short_exit = _step(state, float(row["high"]), float(row["low"]), float(row["close"]), entry_length, exit_length)
    g.channel_breakout_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_entry and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="channel_breakout_long")
    elif short_entry and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="channel_breakout_short")
    elif amount > 0 and long_exit:
        order_target_percent(g.symbol, 0.0, reason="channel_breakout_long_exit")
    elif amount < 0 and short_exit:
        order_target_percent(g.symbol, 0.0, reason="channel_breakout_short_exit")
