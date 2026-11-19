"""Fast/slow EMA scalper filtered by Hull trend, RSI, and volatility."""

# @param fast_length int 3 range=1:100:1
# @param slow_length int 33 range=2:300:1
# @param hull_length int 66 range=2:300:1
# @param rsi_length int 12 range=2:100:1
# @param atr_length int 14 range=1:100:1
# @param range_multiplier float 0.5 range=0.1:5:0.1
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _wma(values, length):
    if length < 1 or len(values) < length:
        return None
    window = values[-length:]
    denominator = float(length * (length + 1) // 2)
    return sum(window[index] * (index + 1) for index in range(length)) / denominator


def _new_state(signature):
    return {"signature": signature, "highs": [], "lows": [], "closes": [], "fast": None, "slow": None, "previous_fast": None, "previous_slow": None, "avg_gain": None, "avg_loss": None, "atr": None, "previous_hull": None}


def _step(state, high, low, close, fast_length, slow_length, hull_length, rsi_length, atr_length, range_multiplier):
    previous_close = state["closes"][-1] if state["closes"] else close
    previous_fast = state["fast"]
    previous_slow = state["slow"]
    state["fast"] = _ema(state["fast"], close, fast_length)
    state["slow"] = _ema(state["slow"], close, slow_length)
    change = close - previous_close
    gain = max(change, 0.0)
    loss = max(-change, 0.0)
    state["avg_gain"] = gain if state["avg_gain"] is None else (float(state["avg_gain"]) * (rsi_length - 1) + gain) / float(rsi_length)
    state["avg_loss"] = loss if state["avg_loss"] is None else (float(state["avg_loss"]) * (rsi_length - 1) + loss) / float(rsi_length)
    rsi = 100.0 if float(state["avg_loss"]) <= 1e-15 else 100.0 - 100.0 / (1.0 + float(state["avg_gain"]) / float(state["avg_loss"]))
    true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
    state["atr"] = true_range if state["atr"] is None else (float(state["atr"]) * (atr_length - 1) + true_range) / float(atr_length)
    state["highs"].append(high)
    state["lows"].append(low)
    state["closes"].append(close)
    keep = max(hull_length * 2 + 10, atr_length + 5)
    state["highs"] = state["highs"][-keep:]
    state["lows"] = state["lows"][-keep:]
    state["closes"] = state["closes"][-keep:]
    half = max(1, hull_length // 2)
    root = max(1, int(hull_length ** 0.5))
    half_wma = _wma(state["closes"], half)
    full_wma = _wma(state["closes"], hull_length)
    hull = None if half_wma is None or full_wma is None else 2.0 * half_wma - full_wma
    hull_series = state.get("hull_series") or []
    if hull is not None:
        hull_series.append(hull)
        hull_series = hull_series[-root:]
    state["hull_series"] = hull_series
    smoothed_hull = _wma(hull_series, root)
    previous_hull = state["previous_hull"]
    if smoothed_hull is not None:
        state["previous_hull"] = smoothed_hull
    if previous_fast is None or previous_slow is None or previous_hull is None or smoothed_hull is None or len(state["highs"]) < atr_length:
        return False, False
    crossed_up = float(previous_fast) <= float(previous_slow) and float(state["fast"]) > float(state["slow"])
    crossed_down = float(previous_fast) >= float(previous_slow) and float(state["fast"]) < float(state["slow"])
    has_range = max(state["highs"][-atr_length:]) - min(state["lows"][-atr_length:]) > float(state["atr"]) * range_multiplier
    return crossed_up and rsi > 56.0 and smoothed_hull > float(previous_hull) and has_range, crossed_down and rsi < 45.0 and smoothed_hull < float(previous_hull) and has_range


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.golden_edge_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1m", fields=["high", "low", "close"])
    context.set_warmup(1200)
    context.set_metadata(direction_mode="one_way", strategy_family="momentum")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    fast_length = int(context.params.get("fast_length", 3))
    slow_length = int(context.params.get("slow_length", 33))
    hull_length = int(context.params.get("hull_length", 66))
    rsi_length = int(context.params.get("rsi_length", 12))
    atr_length = int(context.params.get("atr_length", 14))
    range_multiplier = float(context.params.get("range_multiplier", 0.5))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [fast_length, slow_length, hull_length, rsi_length, atr_length, range_multiplier]
    state = dict(g.golden_edge_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(600, hull_length * 10, slow_length * 15)
    bars = get_history(required if cold else 1, "1m", ["high", "low", "close"], g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    buy = sell = False
    for _, row in bars.iterrows():
        buy, sell = _step(state, float(row["high"]), float(row["low"]), float(row["close"]), fast_length, slow_length, hull_length, rsi_length, atr_length, range_multiplier)
    g.golden_edge_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="golden_edge_long")
    elif sell and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="golden_edge_short")
