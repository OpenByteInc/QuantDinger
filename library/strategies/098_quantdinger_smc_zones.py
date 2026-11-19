"""Swing-zone and moving-average Smart Money Concepts strategy."""

# @param swing_length int 8 range=2:100:1
# @param sma_length int 50 range=2:300:1
# @param order_block_length int 20 range=2:300:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "highs": [], "lows": [], "closes": [], "swing_high": None, "swing_low": None}


def _step(state, high, low, close, swing_length, sma_length, order_block_length):
    state["highs"].append(high)
    state["lows"].append(low)
    state["closes"].append(close)
    keep = max(swing_length, sma_length, order_block_length) + 3
    state["highs"] = state["highs"][-keep:]
    state["lows"] = state["lows"][-keep:]
    state["closes"] = state["closes"][-keep:]
    if len(state["highs"]) >= swing_length and high >= max(state["highs"][-swing_length:]):
        state["swing_high"] = high
    if len(state["lows"]) >= swing_length and low <= min(state["lows"][-swing_length:]):
        state["swing_low"] = low
    if state["swing_high"] is None or state["swing_low"] is None or len(state["closes"]) < sma_length or len(state["highs"]) < order_block_length:
        return False, False
    equilibrium = (float(state["swing_high"]) + float(state["swing_low"])) / 2.0
    average = sum(state["closes"][-sma_length:]) / float(sma_length)
    order_low = min(state["lows"][-order_block_length:])
    order_high = max(state["highs"][-order_block_length:])
    buy = close < equilibrium and close > float(state["swing_low"]) and close > average and close >= order_low
    sell = close > equilibrium and close < float(state["swing_high"]) and close < average and close <= order_high
    return buy, sell


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.smc_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(700)
    context.set_metadata(direction_mode="one_way", strategy_family="price_action")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    swing_length = int(context.params.get("swing_length", 8))
    sma_length = int(context.params.get("sma_length", 50))
    order_block_length = int(context.params.get("order_block_length", 20))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [swing_length, sma_length, order_block_length]
    state = dict(g.smc_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(400, sma_length * 10, order_block_length * 15)
    bars = get_history(required if cold else 1, "1h", ["high", "low", "close"], g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    buy = sell = False
    for _, row in bars.iterrows():
        buy, sell = _step(state, float(row["high"]), float(row["low"]), float(row["close"]), swing_length, sma_length, order_block_length)
    g.smc_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="smc_discount_long")
    elif sell and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="smc_premium_short")
