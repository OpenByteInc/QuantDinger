"""Cyclic-RSI wave reversals confirmed by a volume-weighted moving average."""

# @param rsi_length int 14 range=2:100:1
# @param cycle_memory int 40 range=10:300:1
# @param vwma_length int 20 range=2:300:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {"signature": signature, "previous_close": None, "gains": [], "losses": [], "rsi_values": [], "prices": [], "volumes": [], "previous_rsi": None, "previous_price": None, "previous_vwma": None, "regime": 0}


def _step(state, close, volume, rsi_length, cycle_memory, vwma_length):
    previous_close = state["previous_close"]
    change = 0.0 if previous_close is None else close - float(previous_close)
    state["gains"].append(max(change, 0.0))
    state["losses"].append(max(-change, 0.0))
    state["prices"].append(close)
    state["volumes"].append(max(volume, 0.0))
    if len(state["gains"]) > rsi_length:
        state["gains"].pop(0)
        state["losses"].pop(0)
    if len(state["prices"]) > vwma_length:
        state["prices"].pop(0)
        state["volumes"].pop(0)
    state["previous_close"] = close
    if len(state["gains"]) < rsi_length or len(state["prices"]) < vwma_length:
        return False, False
    gain = sum(state["gains"]) / float(rsi_length)
    loss = sum(state["losses"]) / float(rsi_length)
    rsi = 100.0 if loss <= 1e-15 else 100.0 - 100.0 / (1.0 + gain / loss)
    state["rsi_values"].append(rsi)
    if len(state["rsi_values"]) > cycle_memory:
        state["rsi_values"].pop(0)
    volume_sum = sum(state["volumes"])
    vwma = close if volume_sum <= 1e-15 else sum(state["prices"][index] * state["volumes"][index] for index in range(len(state["prices"]))) / volume_sum
    previous_rsi = state["previous_rsi"]
    previous_price = state["previous_price"]
    previous_vwma = state["previous_vwma"]
    state["previous_rsi"] = rsi
    state["previous_price"] = close
    state["previous_vwma"] = vwma
    if len(state["rsi_values"]) < cycle_memory or previous_rsi is None or previous_price is None or previous_vwma is None:
        return False, False
    ordered = sorted(state["rsi_values"])
    low_band = ordered[max(0, int(0.2 * (len(ordered) - 1)))]
    high_band = ordered[min(len(ordered) - 1, int(0.8 * (len(ordered) - 1)))]
    bull_start = float(previous_price) <= float(previous_vwma) and close > vwma and rsi > float(previous_rsi) and float(previous_rsi) <= low_band
    wave_sell = rsi < float(previous_rsi) and (float(previous_rsi) >= high_band or float(previous_rsi) >= 70.0)
    previous_regime = int(state["regime"])
    if bull_start:
        state["regime"] = 1
    elif wave_sell:
        state["regime"] = -1
    return int(state["regime"]) == 1 and previous_regime != 1, int(state["regime"]) == -1 and previous_regime != -1


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.crsi_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["close", "volume"])
    context.set_warmup(600)
    context.set_metadata(direction_mode="one_way", strategy_family="oscillator")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    rsi_length = int(context.params.get("rsi_length", 14))
    cycle_memory = int(context.params.get("cycle_memory", 40))
    vwma_length = int(context.params.get("vwma_length", 20))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [rsi_length, cycle_memory, vwma_length]
    state = dict(g.crsi_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(max(400, cycle_memory * 8), "1h", ["close", "volume"], g.symbol)
        if len(bars) < max(400, cycle_memory * 8):
            return
        state = _new_state(signature)
        buy = False
        sell = False
        for index in range(len(bars)):
            buy, sell = _step(state, float(bars["close"].iloc[index]), float(bars["volume"].iloc[index]), rsi_length, cycle_memory, vwma_length)
    else:
        bars = get_history(1, "1h", ["close", "volume"], g.symbol)
        if len(bars) < 1:
            return
        buy, sell = _step(state, float(bars["close"].iloc[-1]), float(bars["volume"].iloc[-1]), rsi_length, cycle_memory, vwma_length)
    g.crsi_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if buy and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="crsi_wave_buy")
    elif sell and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="crsi_wave_sell")
