"""123 reversal signal confirmed by an extreme-value volatility regime."""

# @param stochastic_length int 14 range=2:300:1
# @param stochastic_smoothing int 1 range=1:100:1
# @param stochastic_signal int 3 range=1:100:1
# @param stochastic_level float 50 range=1:99:1
# @param volatility_length int 30 range=2:300:1
# @param upper_volatility float 0.005 range=0.0001:2.99:0.0001
# @param lower_volatility float 0.0016 range=0.0001:2.99:0.0001
# @param reverse bool false
# @param target_pct float 0.95 range=0.05:1:0.05


def _sma_step(buffer, value, length):
    buffer.append(float(value))
    if len(buffer) > length:
        buffer.pop(0)
    if len(buffer) < length:
        return float("nan")
    return sum(buffer) / float(length)


def _new_state(signature):
    return {
        "signature": signature,
        "closes": [],
        "highs": [],
        "lows": [],
        "fast_stochastic": [],
        "slow_stochastic": [],
        "vol_closes": [],
        "vol_highs": [],
        "vol_lows": [],
        "reversal_position": 0,
        "volatility_position": 0,
    }


def _step(state, high, low, close, settings):
    stochastic_length, stochastic_smoothing, stochastic_signal, stochastic_level, volatility_length, upper_volatility, lower_volatility = settings
    state["closes"].append(close)
    state["highs"].append(high)
    state["lows"].append(low)
    if len(state["closes"]) > max(stochastic_length, 3):
        state["closes"].pop(0)
    if len(state["highs"]) > stochastic_length:
        state["highs"].pop(0)
        state["lows"].pop(0)
    raw_stochastic = float("nan")
    if len(state["highs"]) == stochastic_length:
        highest = max(state["highs"])
        lowest = min(state["lows"])
        if highest > lowest:
            raw_stochastic = 100.0 * (close - lowest) / (highest - lowest)
    fast = _sma_step(state["fast_stochastic"], raw_stochastic, stochastic_smoothing)
    slow = _sma_step(state["slow_stochastic"], fast, stochastic_signal)
    if len(state["closes"]) >= 3 and fast == fast and slow == slow:
        two_ago = float(state["closes"][-3])
        previous = float(state["closes"][-2])
        if two_ago < previous and close > previous and fast < slow and fast > stochastic_level:
            state["reversal_position"] = 1
        elif two_ago > previous and close < previous and fast > slow and fast < stochastic_level:
            state["reversal_position"] = -1
    state["vol_closes"].append(close)
    state["vol_highs"].append(high)
    state["vol_lows"].append(low)
    if len(state["vol_closes"]) > volatility_length:
        state["vol_closes"].pop(0)
        state["vol_highs"].pop(0)
        state["vol_lows"].pop(0)
    if len(state["vol_closes"]) == volatility_length:
        highest_close = max(state["vol_closes"])
        lowest_close = min(state["vol_closes"])
        highest_high = max(state["vol_highs"])
        lowest_low = min(state["vol_lows"])
        annualizer = (253.0 / volatility_length) ** 0.5
        volatility = 0.3 * float(np.log(highest_close / lowest_close)) * annualizer + 0.3 * float(np.log(highest_high / lowest_low)) * annualizer
        volatility = min(2.99, max(0.0, volatility))
        if volatility > upper_volatility:
            state["volatility_position"] = 1
        elif volatility < lower_volatility:
            state["volatility_position"] = -1
    if int(state["reversal_position"]) == int(state["volatility_position"]):
        return int(state["reversal_position"])
    return 0


def initialize(context):
    g.symbol = "Crypto:BTC/USDT@swap"
    g.combo_vol_state = {}
    context.set_universe([g.symbol])
    context.set_benchmark(g.symbol)
    context.subscribe(frequency="1h", fields=["high", "low", "close"])
    context.set_warmup(700)
    context.set_metadata(direction_mode="one_way", strategy_family="reversal")
    context.allow_leverage(max_leverage=1)


def handle_data(context, data):
    settings = [
        int(context.params.get("stochastic_length", 14)),
        int(context.params.get("stochastic_smoothing", 1)),
        int(context.params.get("stochastic_signal", 3)),
        float(context.params.get("stochastic_level", 50.0)),
        int(context.params.get("volatility_length", 30)),
        float(context.params.get("upper_volatility", 0.005)),
        float(context.params.get("lower_volatility", 0.0016)),
    ]
    reverse = bool(context.params.get("reverse", False))
    target_pct = float(context.params.get("target_pct", 0.95))
    if min(settings[0], settings[1], settings[2], settings[4]) < 1:
        return
    state = dict(g.combo_vol_state or {})
    if not state or list(state.get("signature") or []) != settings:
        required = max(20 * max(settings[0], settings[4]), 600)
        bars = get_history(required, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < required:
            return
        state = _new_state(settings)
        position_signal = 0
        for index in range(len(bars)):
            position_signal = _step(
                state,
                float(bars["high"].iloc[index]),
                float(bars["low"].iloc[index]),
                float(bars["close"].iloc[index]),
                settings,
            )
    else:
        bars = get_history(1, "1h", ["high", "low", "close"], g.symbol)
        if len(bars) < 1:
            return
        position_signal = _step(
            state,
            float(bars["high"].iloc[-1]),
            float(bars["low"].iloc[-1]),
            float(bars["close"].iloc[-1]),
            settings,
        )
    g.combo_vol_state = state
    position_signal = -position_signal if reverse else position_signal
    amount = float(get_position(g.symbol).amount or 0.0)
    if position_signal > 0 and amount <= 0:
        order_target_percent(g.symbol, target_pct, reason="reversal_volatility_long")
    elif position_signal < 0 and amount >= 0:
        order_target_percent(g.symbol, -target_pct, reason="reversal_volatility_short")
    elif position_signal == 0 and amount != 0:
        order_target_percent(g.symbol, 0.0, reason="reversal_volatility_flat")
