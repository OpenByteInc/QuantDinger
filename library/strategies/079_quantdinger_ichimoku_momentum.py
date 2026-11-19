"""Ichimoku trend with MACD, money-flow and TSI confirmation."""

# @param tenkan_length int 10 range=1:100:1
# @param kijun_length int 30 range=2:200:1
# @param span_b_length int 52 range=2:300:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _new_state(signature):
    return {"signature": signature, "highs": [], "lows": [], "closes": [], "volumes": [], "ad": [], "macd_fast": None, "macd_slow": None, "macd_signal": None, "pc_long": None, "pc_short": None, "abs_long": None, "abs_short": None}


def _step(state, high, low, close, volume, tenkan_length, kijun_length, span_b_length):
    previous_close = state["closes"][-1] if state["closes"] else None
    for key, value in (("highs", high), ("lows", low), ("closes", close), ("volumes", max(volume, 0.0))):
        state[key].append(value)
        if len(state[key]) > max(span_b_length, 60):
            state[key].pop(0)
    ad = 0.0 if high <= low else ((2.0 * close - low - high) / (high - low)) * max(volume, 0.0)
    state["ad"].append(ad)
    if len(state["ad"]) > 10:
        state["ad"].pop(0)
    state["macd_fast"] = _ema(state["macd_fast"], (high + low) / 2.0, 12)
    state["macd_slow"] = _ema(state["macd_slow"], (high + low) / 2.0, 26)
    macd = float(state["macd_fast"]) - float(state["macd_slow"])
    state["macd_signal"] = _ema(state["macd_signal"], macd, 9)
    change = 0.0 if previous_close is None else close - float(previous_close)
    state["pc_long"] = _ema(state["pc_long"], change, 20)
    state["pc_short"] = _ema(state["pc_short"], state["pc_long"], 20)
    state["abs_long"] = _ema(state["abs_long"], abs(change), 20)
    state["abs_short"] = _ema(state["abs_short"], state["abs_long"], 20)
    if len(state["closes"]) < max(span_b_length, 26) or len(state["ad"]) < 10:
        return False, False
    tenkan = (max(state["highs"][-tenkan_length:]) + min(state["lows"][-tenkan_length:])) / 2.0
    kijun = (max(state["highs"][-kijun_length:]) + min(state["lows"][-kijun_length:])) / 2.0
    span_a = (tenkan + kijun) / 2.0
    span_b = (max(state["highs"][-span_b_length:]) + min(state["lows"][-span_b_length:])) / 2.0
    cmf = sum(state["ad"]) / max(sum(state["volumes"][-10:]), 1e-15)
    tsi = 0.0 if float(state["abs_short"]) <= 1e-15 else 100.0 * float(state["pc_short"]) / float(state["abs_short"])
    momentum = close - state["closes"][-26]
    bullish = tenkan > kijun and momentum > 0 and close > max(span_a, span_b) and macd > state["macd_signal"] and cmf > 0.1 and tsi > 0
    bearish = tenkan < kijun and momentum < 0 and close < min(span_a, span_b) and macd < state["macd_signal"] and cmf < -0.1 and tsi < 0
    return bullish, bearish


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.ichimoku_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close", "volume"])
    context.set_warmup(700)
    context.set_metadata(direction_mode="one_way", strategy_family="trend")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    tenkan_length = int(context.params.get("tenkan_length", 10))
    kijun_length = int(context.params.get("kijun_length", 30))
    span_b_length = int(context.params.get("span_b_length", 52))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [tenkan_length, kijun_length, span_b_length]
    state = dict(g.ichimoku_state or {})
    fields = ["high", "low", "close", "volume"]
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(500, "1h", fields, g.symbol)
        if len(bars) < 500:
            return
        state = _new_state(signature)
        bullish = False
        bearish = False
        for index in range(len(bars)):
            bullish, bearish = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]), float(bars["volume"].iloc[index]), tenkan_length, kijun_length, span_b_length)
    else:
        bars = get_history(1, "1h", fields, g.symbol)
        if len(bars) < 1:
            return
        bullish, bearish = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]), float(bars["volume"].iloc[-1]), tenkan_length, kijun_length, span_b_length)
    g.ichimoku_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if bullish and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="ichimoku_combo_long")
    elif bearish and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="ichimoku_combo_short")
