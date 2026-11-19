"""Thirty-minute friction and FIR-direction strategy."""

# @param bars_back int 500 range=50:1500:50
# @param friction_level float 50 range=0:100:1
# @param trigger_level float -10 range=-100:0:1
# @param threshold_length int 100 range=20:500:10
# @param enable_short bool true
# @param target_pct float 0.95 range=0.05:1:0.05


def _new_state(signature):
    return {
        "signature": signature,
        "highs": [],
        "lows": [],
        "closes": [],
        "fir_closes": [],
        "previous_fir": None,
        "frictions": [],
        "trigger_history": [],
        "mid_history": [],
    }


def _append_market(state, high, low, close, bars_back):
    state["highs"].append(float(high))
    state["lows"].append(float(low))
    state["closes"].append(float(close))
    if len(state["highs"]) > bars_back + 1:
        state["highs"].pop(0)
        state["lows"].pop(0)
        state["closes"].pop(0)


def _current_friction(state, bars_back):
    if len(state["highs"]) < bars_back + 1:
        return float("nan")
    close = float(state["closes"][-1])
    friction = 0.0
    for distance in range(1, bars_back + 1):
        if float(state["highs"][-1 - distance]) >= close and float(state["lows"][-1 - distance]) <= close:
            friction += float(1 + bars_back) / float(distance + bars_back)
    return friction


def _step(state, high, low, close, bars_back, friction_level, trigger_level, threshold_length):
    _append_market(state, high, low, close, bars_back)
    friction = _current_friction(state, bars_back)
    fir_prices = state["fir_closes"]
    fir_prices.append(float(close))
    if len(fir_prices) > 4:
        fir_prices.pop(0)
    current_fir = float("nan")
    if len(fir_prices) == 4:
        current_fir = (
            4.0 * float(fir_prices[-1])
            + 3.0 * float(fir_prices[-2])
            + 2.0 * float(fir_prices[-3])
            + float(fir_prices[-4])
        ) / 10.0
    previous_fir = state["previous_fir"]
    state["previous_fir"] = current_fir if current_fir == current_fir else previous_fir
    if friction != friction:
        return False, False, False
    friction_values = state["frictions"]
    friction_values.append(friction)
    if len(friction_values) > threshold_length:
        friction_values.pop(0)
    if len(friction_values) < threshold_length:
        return False, False, False
    lowest = min(friction_values)
    highest = max(friction_values)
    friction_ratio = friction_level / 100.0
    trigger_ratio = trigger_level / 100.0
    midpoint = lowest * (1.0 - friction_ratio) + highest * friction_ratio
    trigger = lowest * (1.0 - trigger_ratio) + highest * trigger_ratio
    state["trigger_history"].append(trigger)
    state["mid_history"].append(midpoint)
    if len(state["trigger_history"]) > 6:
        state["trigger_history"].pop(0)
        state["mid_history"].pop(0)
    if len(state["trigger_history"]) < 6 or previous_fir is None or current_fir != current_fir:
        return False, False, False
    trend = 1 if current_fir > float(previous_fir) else -1
    lagged_trigger = float(state["trigger_history"][0])
    lagged_midpoint = float(state["mid_history"][0])
    return friction < lagged_trigger and trend > 0, friction < lagged_trigger and trend < 0, friction > lagged_midpoint


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.friction_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="30m", fields=["high", "low", "close"])
    context.set_warmup(1800)
    context.set_metadata(direction_mode="one_way", strategy_family="reversal")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    bars_back = int(context.params.get("bars_back", 500))
    friction_level = float(context.params.get("friction_level", 50.0))
    trigger_level = float(context.params.get("trigger_level", -10.0))
    threshold_length = int(context.params.get("threshold_length", 100))
    enable_short = bool(context.params.get("enable_short", True))
    target_pct = float(context.params.get("target_pct", 0.95))
    if bars_back < 10 or threshold_length < 5:
        return
    signature = [bars_back, friction_level, trigger_level, threshold_length]
    state = dict(g.friction_state or {})
    if not state or list(state.get("signature") or []) != signature:
        required = bars_back + threshold_length + 30
        bars = get_history(required, "30m", ["high", "low", "close"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(signature)
        long_signal = False
        short_signal = False
        exit_signal = False
        for index in range(len(bars)):
            long_signal, short_signal, exit_signal = _step(
                state,
                float(bars["high"].iloc[index]),
                float(bars["low"].iloc[index]),
                float(bars["close"].iloc[index]),
                bars_back,
                friction_level,
                trigger_level,
                threshold_length,
            )
    else:
        bars = get_history(1, "30m", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        long_signal, short_signal, exit_signal = _step(
            state,
            float(bars["high"].iloc[-1]),
            float(bars["low"].iloc[-1]),
            float(bars["close"].iloc[-1]),
            bars_back,
            friction_level,
            trigger_level,
            threshold_length,
        )
    g.friction_state = state
    amount = float(get_position(g.symbol).amount or 0.0)
    if long_signal and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="friction_long")
    elif enable_short and short_signal and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="friction_short")
    elif exit_signal and amount != 0:
        order_target_percent(g.symbol, 0.0, reason="friction_exit")
