"""VWMA crossover filtered by an online MFI/ADX nearest-neighbor vote."""

# @param fast_length int 13 range=2:100:1
# @param slow_length int 19 range=3:200:1
# @param filter_length int 13 range=2:100:1
# @param adx_smoothing int 6 range=1:100:1
# @param neighbors int 45 range=1:200:1
# @param minimum_vote int 2 range=0:100:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "opens": [], "volumes": [], "flows_up": [], "flows_down": [], "previous_typical": None, "previous_high": None, "previous_low": None, "previous_close": None, "tr_rma": None, "plus_rma": None, "minus_rma": None, "adx_rma": None, "previous_fast": None, "previous_slow": None, "features_adx": [], "features_mfi": [], "results": [], "buffer_adx": None, "buffer_mfi": None, "buffer_source": None}


def _rma(previous, value, length):
    return value if previous is None else (float(previous) * (length - 1) + value) / float(length)


def _vwma(values, volumes, length):
    if len(values) < length:
        return None
    denominator = sum(volumes[-length:])
    if denominator <= 1e-15:
        return sum(values[-length:]) / float(length)
    return sum(values[-length + index] * volumes[-length + index] for index in range(length)) / denominator


def _step(state, open_price, high, low, close, volume, fast_length, slow_length, filter_length, adx_smoothing, neighbors, minimum_vote):
    previous_close = close if state["previous_close"] is None else float(state["previous_close"])
    previous_high = high if state["previous_high"] is None else float(state["previous_high"])
    previous_low = low if state["previous_low"] is None else float(state["previous_low"])
    state["opens"].append(open_price)
    state["volumes"].append(max(volume, 0.0))
    keep = max(slow_length, filter_length) + 3
    state["opens"] = state["opens"][-keep:]
    state["volumes"] = state["volumes"][-keep:]
    fast = _vwma(state["opens"], state["volumes"], fast_length)
    slow = _vwma(state["opens"], state["volumes"], slow_length)
    up_move = high - previous_high
    down_move = previous_low - low
    plus_dm = up_move if up_move > down_move and up_move > 0 else 0.0
    minus_dm = down_move if down_move > up_move and down_move > 0 else 0.0
    true_range = max(high - low, abs(high - previous_close), abs(low - previous_close))
    state["tr_rma"] = _rma(state["tr_rma"], true_range, filter_length)
    state["plus_rma"] = _rma(state["plus_rma"], plus_dm, filter_length)
    state["minus_rma"] = _rma(state["minus_rma"], minus_dm, filter_length)
    plus = 100.0 * float(state["plus_rma"]) / max(float(state["tr_rma"]), 1e-15)
    minus = 100.0 * float(state["minus_rma"]) / max(float(state["tr_rma"]), 1e-15)
    dx = abs(plus - minus) / max(plus + minus, 1e-15) * 100.0
    state["adx_rma"] = _rma(state["adx_rma"], dx, adx_smoothing)
    typical = (open_price + high + low + close) / 4.0
    previous_typical = typical if state["previous_typical"] is None else float(state["previous_typical"])
    state["flows_up"].append(volume * typical if typical > previous_typical else 0.0)
    state["flows_down"].append(volume * typical if typical < previous_typical else 0.0)
    state["flows_up"] = state["flows_up"][-filter_length:]
    state["flows_down"] = state["flows_down"][-filter_length:]
    positive = sum(state["flows_up"])
    negative = sum(state["flows_down"])
    mfi = 100.0 if negative <= 1e-15 else 100.0 - 100.0 / (1.0 + positive / negative)
    long_cross = fast is not None and slow is not None and state["previous_fast"] is not None and state["previous_slow"] is not None and float(state["previous_fast"]) <= float(state["previous_slow"]) and fast > slow
    short_cross = fast is not None and slow is not None and state["previous_fast"] is not None and state["previous_slow"] is not None and float(state["previous_fast"]) >= float(state["previous_slow"]) and fast < slow
    vote = 0
    if long_cross or short_cross:
        if state["buffer_adx"] is not None:
            state["features_adx"].append(float(state["buffer_adx"]))
            state["features_mfi"].append(float(state["buffer_mfi"]))
            state["results"].append(1 if open_price >= float(state["buffer_source"]) else -1)
            state["features_adx"] = state["features_adx"][-500:]
            state["features_mfi"] = state["features_mfi"][-500:]
            state["results"] = state["results"][-500:]
        state["buffer_adx"] = float(state["adx_rma"])
        state["buffer_mfi"] = mfi
        state["buffer_source"] = open_price
        if state["results"]:
            indices = sorted(range(len(state["results"])), key=lambda index: (float(state["features_adx"][index]) - float(state["adx_rma"])) ** 2 + (float(state["features_mfi"][index]) - mfi) ** 2)
            vote = sum(int(state["results"][index]) for index in indices[:max(1, min(neighbors, len(indices)))])
    state["previous_typical"] = typical
    state["previous_high"] = high
    state["previous_low"] = low
    state["previous_close"] = close
    state["previous_fast"] = fast
    state["previous_slow"] = slow
    return long_cross and vote >= minimum_vote, short_cross and vote <= -minimum_vote


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.vwma_knn_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["open", "high", "low", "close", "volume"])
    context.set_warmup(900)
    context.set_metadata(direction_mode="one_way", strategy_family="machine_learning")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    fast_length = int(context.params.get("fast_length", 13))
    slow_length = int(context.params.get("slow_length", 19))
    filter_length = int(context.params.get("filter_length", 13))
    adx_smoothing = int(context.params.get("adx_smoothing", 6))
    neighbors = int(context.params.get("neighbors", 45))
    minimum_vote = int(context.params.get("minimum_vote", 2))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [fast_length, slow_length, filter_length, adx_smoothing, neighbors, minimum_vote]
    state = dict(g.vwma_knn_state or {})
    cold = not state or list(state.get("signature") or []) != signature
    required = max(600, slow_length * 25, filter_length * 30)
    bars = get_history(required if cold else 1, "1h", ["open", "high", "low", "close", "volume"], g.symbol)
    if len(bars) < (required if cold else 1):
        return
    if cold:
        state = _new_state(signature)
    buy = sell = False
    for _, row in bars.iterrows():
        buy, sell = _step(state, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]), float(row["volume"]), fast_length, slow_length, filter_length, adx_smoothing, neighbors, minimum_vote)
    g.vwma_knn_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="vwma_knn_long")
    elif sell and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="vwma_knn_short")
