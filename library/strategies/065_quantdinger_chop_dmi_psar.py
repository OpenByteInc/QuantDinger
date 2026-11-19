"""Choppiness-zone entries filtered by DMI and exited by DMI or PSAR."""

# @param lookback int 14 range=2:300:1
# @param smooth_length int 4 range=1:100:1
# @param adx_length int 14 range=2:100:1
# @param adx_threshold float 25 range=1:100:1
# @param target_pct float 0.95 range=0.05:1:0.05


def _ema(previous, value, length):
    if previous is None:
        return float(value)
    alpha = 2.0 / (float(length) + 1.0)
    return alpha * float(value) + (1.0 - alpha) * float(previous)


def _new_state(signature):
    return {"signature": signature, "highs": [], "lows": [], "tr": [], "chop": [], "previous_high": None, "previous_low": None, "previous_close": None, "plus_dm": None, "minus_dm": None, "smoothed_tr": None, "adx": None, "psar": None, "psar_bull": True, "psar_extreme": None, "psar_af": 0.015, "previous_high_zone": False, "previous_low_zone": False}


def _step_psar(state, high, low):
    previous_high = state["previous_high"]
    previous_low = state["previous_low"]
    if state["psar"] is None or previous_high is None or previous_low is None:
        state["psar"] = low
        state["psar_extreme"] = high
        return bool(state["psar_bull"]), False
    previous_bull = bool(state["psar_bull"])
    psar = float(state["psar"]) + float(state["psar_af"]) * (float(state["psar_extreme"]) - float(state["psar"]))
    if previous_bull:
        psar = min(psar, float(previous_low))
        if low < psar:
            state["psar_bull"] = False
            psar = float(state["psar_extreme"])
            state["psar_extreme"] = low
            state["psar_af"] = 0.015
        elif high > float(state["psar_extreme"]):
            state["psar_extreme"] = high
            state["psar_af"] = min(0.2, float(state["psar_af"]) + 0.001)
    else:
        psar = max(psar, float(previous_high))
        if high > psar:
            state["psar_bull"] = True
            psar = float(state["psar_extreme"])
            state["psar_extreme"] = high
            state["psar_af"] = 0.015
        elif low < float(state["psar_extreme"]):
            state["psar_extreme"] = low
            state["psar_af"] = min(0.2, float(state["psar_af"]) + 0.001)
    state["psar"] = psar
    return bool(state["psar_bull"]), bool(state["psar_bull"]) != previous_bull


def _step(state, high, low, close, lookback, smooth_length, adx_length, adx_threshold):
    previous_high = state["previous_high"]
    previous_low = state["previous_low"]
    previous_close = state["previous_close"]
    psar_bull, psar_flip = _step_psar(state, high, low)
    true_range = high - low if previous_close is None else max(high - low, abs(high - float(previous_close)), abs(low - float(previous_close)))
    for key, value in (("highs", high), ("lows", low), ("tr", true_range)):
        state[key].append(value)
        if len(state[key]) > lookback:
            state[key].pop(0)
    plus_di = 0.0
    minus_di = 0.0
    if previous_high is not None and previous_low is not None:
        up = high - float(previous_high)
        down = float(previous_low) - low
        plus_dm = up if up > down and up > 0 else 0.0
        minus_dm = down if down > up and down > 0 else 0.0
        state["smoothed_tr"] = _ema(state["smoothed_tr"], true_range, adx_length)
        state["plus_dm"] = _ema(state["plus_dm"], plus_dm, adx_length)
        state["minus_dm"] = _ema(state["minus_dm"], minus_dm, adx_length)
        denominator = float(state["smoothed_tr"])
        plus_di = 0.0 if denominator <= 1e-15 else 100.0 * float(state["plus_dm"]) / denominator
        minus_di = 0.0 if denominator <= 1e-15 else 100.0 * float(state["minus_dm"]) / denominator
        total = plus_di + minus_di
        dx = 0.0 if total <= 1e-15 else 100.0 * abs(plus_di - minus_di) / total
        state["adx"] = _ema(state["adx"], dx, adx_length)
    state["previous_high"] = high
    state["previous_low"] = low
    state["previous_close"] = close
    if len(state["tr"]) < lookback or state["adx"] is None:
        return False, False, False, False
    price_range = max(state["highs"]) - min(state["lows"])
    raw_chop = 100.0 if price_range <= 1e-15 else 100.0 * np.log(sum(state["tr"]) / price_range) / np.log(float(lookback))
    state["chop"].append(raw_chop)
    if len(state["chop"]) > smooth_length:
        state["chop"].pop(0)
    if len(state["chop"]) < smooth_length:
        return False, False, False, False
    chop = sum(state["chop"]) / float(smooth_length)
    high_zone = chop >= 61.8 and previous_close is not None and float(previous_close) < close
    low_zone = chop <= 38.2 and previous_close is not None and float(previous_close) > close
    adx_ok = float(state["adx"]) > adx_threshold
    go_long = high_zone and not bool(state["previous_high_zone"]) and adx_ok and psar_bull
    go_short = low_zone and not bool(state["previous_low_zone"]) and adx_ok and not psar_bull
    state["previous_high_zone"] = high_zone
    state["previous_low_zone"] = low_zone
    close_long = (psar_flip and not psar_bull) or (float(state["adx"]) < adx_threshold and minus_di > plus_di)
    close_short = (psar_flip and psar_bull) or (float(state["adx"]) < adx_threshold and plus_di > minus_di)
    return go_long, go_short, close_long, close_short


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.chop_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(600)
    context.set_metadata(direction_mode="one_way", strategy_family="regime")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    lookback = int(context.params.get("lookback", 14))
    smooth_length = int(context.params.get("smooth_length", 4))
    adx_length = int(context.params.get("adx_length", 14))
    adx_threshold = float(context.params.get("adx_threshold", 25.0))
    target_pct = float(context.params.get("target_pct", 0.95))
    signature = [lookback, smooth_length, adx_length, adx_threshold]
    state = dict(g.chop_state or {})
    if not state or list(state.get("signature") or []) != signature:
        bars = get_history(400, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 400:
            return
        state = _new_state(signature)
        signals = (False, False, False, False)
        for index in range(len(bars)):
            signals = _step(state, float(bars["high"].iloc[index]), float(bars["low"].iloc[index]), float(bars["close"].iloc[index]), lookback, smooth_length, adx_length, adx_threshold)
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        signals = _step(state, float(bars["high"].iloc[-1]), float(bars["low"].iloc[-1]), float(bars["close"].iloc[-1]), lookback, smooth_length, adx_length, adx_threshold)
    g.chop_state = state
    go_long, go_short, close_long, close_short = signals
    amount = float(get_position(g.symbol).amount or 0.0)
    if go_long and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="chop_zone_long")
    elif go_short and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="chop_zone_short")
    elif amount > 0 and close_long:
        order_target_percent(g.symbol, 0.0, reason="chop_zone_long_exit")
    elif amount < 0 and close_short:
        order_target_percent(g.symbol, 0.0, reason="chop_zone_short_exit")
