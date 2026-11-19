# ============================================================
# QuantDinger Trend Pressure
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Trend Pressure"
my_indicator_description = (
    "Combines a reactive range pulse, a persistent macro trend, a pressure core, "
    "exhaustion-state markers, gradient pressure fills, and price exhaustion boxes."
)

# @param pulse_range int 21 Pulse range range=3:500:1
# @param pulse_stochastic int 9 Pulse stochastic range=3:200:1
# @param pulse_smoothing int 7 Pulse smoothing range=1:50:1
# @param trend_range int 55 Trend range range=5:500:1
# @param macro_trend int 120 Macro trend range=10:1000:1
# @param trend_smoothing int 5 Trend smoothing range=1:30:1
# @param trend_persistence float 9.0 Trend persistence range=0:20:0.5
# @param exhaustion_zone int 20 Exhaustion zone range=5:40:1
# @param sensitivity int 7 Sensitivity range=1:10:1
# @param show_crosses bool false Show pulse and trend crosses
# @param reactive_smoothing int 16 Reactive smoothing range=1:30:1
# @param regime_weight float 0.65 Regime weight range=0:1:0.05
# @param cold_color str rgba(41,98,255,0.85) Cold pressure color
# @param hot_color str rgba(230,40,62,0.85) Hot pressure color
# @param upper_level_color str rgba(230,40,62,0.80) Upper level color
# @param lower_level_color str rgba(41,98,255,0.80) Lower level color
# @param pulse_color str #E5E7EB Pulse color
# @param trend_color str #8BAEFF Trend color
# @param core_bull_color str rgba(64,227,195,0.85) Bull core color
# @param core_bear_color str rgba(223,81,65,0.85) Bear core color
# @param core_neutral_color str rgba(192,192,192,0.65) Neutral core color
# @param core_width int 2 Core width range=1:4:1
# @param show_gradient bool true Show pressure gradient
# @param show_price_boxes bool true Show price exhaustion boxes
# @param max_boxes int 60 Maximum price boxes range=1:300:1

pulse_range = min(500, max(3, int(params.get("pulse_range", 21))))
pulse_stochastic = min(200, max(3, int(params.get("pulse_stochastic", 9))))
pulse_smoothing = min(50, max(1, int(params.get("pulse_smoothing", 7))))
trend_range = min(500, max(5, int(params.get("trend_range", 55))))
macro_trend = min(1000, max(10, int(params.get("macro_trend", 120))))
trend_smoothing = min(30, max(1, int(params.get("trend_smoothing", 5))))
trend_persistence = min(20.0, max(0.0, float(params.get("trend_persistence", 9.0))))
exhaustion_zone = min(40, max(5, int(params.get("exhaustion_zone", 20))))
sensitivity = min(10, max(1, int(params.get("sensitivity", 7))))
show_crosses = bool(params.get("show_crosses", False))
reactive_smoothing = min(30, max(1, int(params.get("reactive_smoothing", 16))))
regime_weight = min(1.0, max(0.0, float(params.get("regime_weight", 0.65))))
cold_color = str(params.get("cold_color", "rgba(41,98,255,0.85)"))
hot_color = str(params.get("hot_color", "rgba(230,40,62,0.85)"))
upper_level_color = str(params.get("upper_level_color", "rgba(230,40,62,0.80)"))
lower_level_color = str(params.get("lower_level_color", "rgba(41,98,255,0.80)"))
pulse_color = str(params.get("pulse_color", "#E5E7EB"))
trend_color = str(params.get("trend_color", "#8BAEFF"))
core_bull_color = str(params.get("core_bull_color", "rgba(64,227,195,0.85)"))
core_bear_color = str(params.get("core_bear_color", "rgba(223,81,65,0.85)"))
core_neutral_color = str(params.get("core_neutral_color", "rgba(192,192,192,0.65)"))
core_width = min(4, max(1, int(params.get("core_width", 2))))
show_gradient = bool(params.get("show_gradient", True))
show_price_boxes = bool(params.get("show_price_boxes", True))
max_boxes = min(300, max(1, int(params.get("max_boxes", 60))))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)
epsilon = 1e-10

def clamp(value, lower, upper):
    return max(lower, min(upper, value))

def rolling_extreme(values, length, maximum):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        finite = window[np.isfinite(window)]
        if finite.size:
            result[index] = float(np.max(finite) if maximum else np.min(finite))
    return result

def rolling_sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result

def ema(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    if length <= 1:
        result[np.isfinite(values)] = values[np.isfinite(values)]
        return result
    alpha = 2.0 / (length + 1.0)
    seed_values = []
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed_values.append(float(value))
            if len(seed_values) == length:
                previous = float(np.mean(seed_values))
                result[index] = previous
        else:
            previous = alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result

def range_position(length):
    highest = rolling_extreme(high, length, True)
    lowest = rolling_extreme(low, length, False)
    result = np.full(n, np.nan, dtype=float)
    valid = np.isfinite(highest) & np.isfinite(lowest)
    denominator = np.maximum(highest - lowest, epsilon)
    result[valid] = 100.0 * (close[valid] - highest[valid]) / denominator[valid]
    return result

def efficiency(length):
    movement = np.full(n, np.nan, dtype=float)
    if n > 1:
        movement[1:] = np.abs(close[1:] - close[:-1])
    path = rolling_sma(movement, length) * length
    result = np.full(n, np.nan, dtype=float)
    for index in range(length, n):
        if np.isfinite(path[index]):
            result[index] = clamp(abs(close[index] - close[index - length]) / max(path[index], epsilon), 0.0, 1.0)
    return result

def stochastic(values, length):
    highest = rolling_extreme(values, length, True)
    lowest = rolling_extreme(values, length, False)
    result = np.full(n, np.nan, dtype=float)
    valid = np.isfinite(values) & np.isfinite(highest) & np.isfinite(lowest)
    result[valid] = -100.0 + 100.0 * (values[valid] - lowest[valid]) / np.maximum(highest[valid] - lowest[valid], epsilon)
    return result

fast_range = range_position(pulse_range)
structural_range = range_position(trend_range)
macro_range = range_position(macro_trend)
pulse_stoch = stochastic(fast_range, pulse_stochastic)

raw_pulse = np.full(n, np.nan, dtype=float)
valid_pulse = np.isfinite(fast_range) & np.isfinite(pulse_stoch)
raw_pulse[valid_pulse] = np.clip(fast_range[valid_pulse] * 0.72 + pulse_stoch[valid_pulse] * 0.28, -100.0, 0.0)
pulse = ema(raw_pulse, pulse_smoothing)

fast_weight = 0.10
structural_weight = 0.18
macro_weight = 0.72
consensus = np.full(n, np.nan, dtype=float)
agreement = np.full(n, np.nan, dtype=float)
for index in range(n):
    values = (fast_range[index], structural_range[index], macro_range[index])
    if all(np.isfinite(value) for value in values):
        consensus[index] = clamp(
            values[0] * fast_weight + values[1] * structural_weight + values[2] * macro_weight,
            -100.0,
            0.0,
        )
        distance = abs(values[0] - values[1]) + abs(values[1] - values[2]) + abs(values[0] - values[2])
        agreement[index] = 1.0 - clamp(distance / 300.0, 0.0, 1.0)

efficiency_value = efficiency(trend_range)
upper_dwell = np.zeros(n, dtype=float)
lower_dwell = np.zeros(n, dtype=float)
trend_target_raw = np.full(n, np.nan, dtype=float)
for index in range(n):
    previous_upper = upper_dwell[index - 1] if index > 0 else 0.0
    previous_lower = lower_dwell[index - 1] if index > 0 else 0.0
    if np.isfinite(consensus[index]) and consensus[index] > -30.0:
        upper_dwell[index] = min(1.0, previous_upper * 0.93 + 0.07)
    else:
        upper_dwell[index] = previous_upper * 0.94
    if np.isfinite(consensus[index]) and consensus[index] < -70.0:
        lower_dwell[index] = min(1.0, previous_lower * 0.93 + 0.07)
    else:
        lower_dwell[index] = previous_lower * 0.94
    if np.isfinite(consensus[index]):
        trend_target_raw[index] = clamp(
            consensus[index] + trend_persistence * (upper_dwell[index] - lower_dwell[index]),
            -100.0,
            0.0,
        )

trend_target = ema(trend_target_raw, 2)
trend = np.full(n, np.nan, dtype=float)
base_alpha = 2.0 / (trend_smoothing + 1.0)
for index in range(n):
    if not np.isfinite(trend_target[index]):
        continue
    if index == 0 or not np.isfinite(trend[index - 1]):
        trend[index] = trend_target[index]
        continue
    macro_value = macro_range[index] if np.isfinite(macro_range[index]) else -50.0
    side = 1 if macro_value > -50.0 else -1 if macro_value < -50.0 else 0
    pulling_back = (side == 1 and trend_target[index] < trend[index - 1]) or (side == -1 and trend_target[index] > trend[index - 1])
    agree = agreement[index] if np.isfinite(agreement[index]) else 0.0
    efficient = efficiency_value[index] if np.isfinite(efficiency_value[index]) else 0.0
    lock = clamp(0.15 + agree * efficient * 0.92, 0.0, 0.97)
    if pulling_back:
        adaptive_alpha = max(0.015, base_alpha * (1.0 - lock))
    else:
        adaptive_alpha = max(base_alpha * 0.75, 0.03)
    trend[index] = clamp(trend[index - 1] + adaptive_alpha * (trend_target[index] - trend[index - 1]), -100.0, 0.0)

def pressure_reading(length):
    highest = rolling_extreme(high, length, True)
    lowest = rolling_extreme(low, length, False)
    result = np.full(n, np.nan, dtype=float)
    for index in range(n):
        if not np.isfinite(highest[index]) or not np.isfinite(lowest[index]):
            continue
        total_range = max(highest[index] - lowest[index], epsilon)
        candle_range = max(high[index] - low[index], epsilon)
        body_high = max(open_price[index], close[index])
        body_low = min(open_price[index], close[index])
        upper_wick = high[index] - body_high
        lower_wick = body_low - low[index]
        close_location = (close[index] - lowest[index]) / total_range
        mean_location = ((high[index] + low[index] + close[index]) / 3.0 - lowest[index]) / total_range
        body_pressure = ((close[index] - open_price[index]) / candle_range + 1.0) * 0.5
        wick_pressure = ((lower_wick - upper_wick) / candle_range + 1.0) * 0.5
        comparison = close[index - 5] if index >= 5 else close[index]
        impulse = ((close[index] - comparison) / total_range + 1.0) * 0.5
        result[index] = clamp(
            (close_location * 0.42 + mean_location * 0.23 + body_pressure * 0.13 + wick_pressure * 0.12 + impulse * 0.10) * 100.0,
            0.0,
            100.0,
        )
    return result

reactive_core = ema(pressure_reading(21), reactive_smoothing)
global_core = ema(pressure_reading(112), 3)
core_value = np.full(n, np.nan, dtype=float)
core_plot = np.full(n, np.nan, dtype=float)
for index in range(n):
    if np.isfinite(reactive_core[index]) and np.isfinite(global_core[index]):
        core_value[index] = global_core[index] * regime_weight + reactive_core[index] * (1.0 - regime_weight)
        core_plot[index] = core_value[index] - 100.0

sensitivity_offset = float(sensitivity - 5)
adjusted_zone = clamp(float(exhaustion_zone) - sensitivity_offset * 4.0, 8.0, 42.0)
release_buffer = clamp(8.0 + sensitivity_offset * 1.75, 1.0, 18.0)
confirmation_bars = 1 if sensitivity <= 5 else 2 if sensitivity <= 7 else 3 if sensitivity <= 9 else 4
fast_exit = sensitivity <= 2
upper_level = -adjusted_zone
lower_level = -100.0 + adjusted_zone
upper_release_level = upper_level - release_buffer
lower_release_level = lower_level + release_buffer

state = np.zeros(n, dtype=int)
upper_count = 0
lower_count = 0
upper_active = np.zeros(n, dtype=bool)
lower_active = np.zeros(n, dtype=bool)
upper_start = np.zeros(n, dtype=bool)
lower_start = np.zeros(n, dtype=bool)
upper_release = np.zeros(n, dtype=bool)
lower_release = np.zeros(n, dtype=bool)
cross_up = np.zeros(n, dtype=bool)
cross_down = np.zeros(n, dtype=bool)

for index in range(n):
    previous_state = state[index - 1] if index > 0 else 0
    current_state = previous_state
    upper_condition = np.isfinite(pulse[index]) and np.isfinite(trend[index]) and pulse[index] >= upper_level and trend[index] >= upper_level
    lower_condition = np.isfinite(pulse[index]) and np.isfinite(trend[index]) and pulse[index] <= lower_level and trend[index] <= lower_level
    upper_count = upper_count + 1 if upper_condition else 0
    lower_count = lower_count + 1 if lower_condition else 0
    upper_exit = np.isfinite(pulse[index]) and np.isfinite(trend[index]) and (
        (pulse[index] < upper_release_level or trend[index] < upper_release_level)
        if fast_exit else
        (pulse[index] < upper_release_level and trend[index] < upper_release_level)
    )
    lower_exit = np.isfinite(pulse[index]) and np.isfinite(trend[index]) and (
        (pulse[index] > lower_release_level or trend[index] > lower_release_level)
        if fast_exit else
        (pulse[index] > lower_release_level and trend[index] > lower_release_level)
    )
    if current_state == 0:
        if upper_count >= confirmation_bars:
            current_state = 1
        elif lower_count >= confirmation_bars:
            current_state = -1
    elif current_state == 1 and upper_exit:
        current_state = 0
    elif current_state == -1 and lower_exit:
        current_state = 0
    state[index] = current_state
    upper_active[index] = current_state == 1
    lower_active[index] = current_state == -1
    upper_start[index] = current_state == 1 and previous_state != 1
    lower_start[index] = current_state == -1 and previous_state != -1
    upper_release[index] = current_state == 0 and previous_state == 1
    lower_release[index] = current_state == 0 and previous_state == -1
    if index > 0 and all(np.isfinite(value) for value in (pulse[index - 1], trend[index - 1], pulse[index], trend[index])):
        cross_up[index] = pulse[index - 1] <= trend[index - 1] and pulse[index] > trend[index]
        cross_down[index] = pulse[index - 1] >= trend[index - 1] and pulse[index] < trend[index]

layers = []
price_boxes = []
active_box = None
upper_top = np.nan
upper_bottom = np.nan
lower_top = np.nan
lower_bottom = np.nan

if show_price_boxes:
    for index in range(n):
        if upper_start[index]:
            if active_box is not None:
                price_boxes = [box for box in price_boxes if box is not active_box]
            upper_top = float(high[index])
            upper_bottom = float(low[index])
            active_box = {"side": "upper", "start": max(0, index - 1), "end": index, "top": upper_top, "bottom": upper_bottom, "color": hot_color}
            price_boxes.append(active_box)
        elif upper_active[index] and active_box is not None and active_box["side"] == "upper":
            upper_top = max(upper_top, float(high[index]))
            upper_bottom = min(upper_bottom, float(low[index]))
            active_box.update({"end": index, "top": upper_top, "bottom": upper_bottom})
        elif upper_release[index] and active_box is not None and active_box["side"] == "upper":
            active_box["end"] = max(active_box["start"], index - 1)
            layers.append({"type": "label", "index": index, "price": float(upper_top), "text": "▼", "color": "rgba(0,0,0,0)", "textColor": hot_color, "side": "down"})
            active_box = None

        if lower_start[index]:
            if active_box is not None:
                price_boxes = [box for box in price_boxes if box is not active_box]
            lower_top = float(high[index])
            lower_bottom = float(low[index])
            active_box = {"side": "lower", "start": max(0, index - 1), "end": index, "top": lower_top, "bottom": lower_bottom, "color": cold_color}
            price_boxes.append(active_box)
        elif lower_active[index] and active_box is not None and active_box["side"] == "lower":
            lower_top = max(lower_top, float(high[index]))
            lower_bottom = min(lower_bottom, float(low[index]))
            active_box.update({"end": index, "top": lower_top, "bottom": lower_bottom})
        elif lower_release[index] and active_box is not None and active_box["side"] == "lower":
            active_box["end"] = max(active_box["start"], index - 1)
            layers.append({"type": "label", "index": index, "price": float(lower_bottom), "text": "▲", "color": "rgba(0,0,0,0)", "textColor": cold_color, "side": "up"})
            active_box = None

        if len(price_boxes) > max_boxes:
            removed = price_boxes.pop(0)
            if removed is active_box:
                active_box = None

    for box in price_boxes:
        layers.append(
            {
                "type": "zone",
                "startIndex": int(box["start"]),
                "endIndex": int(box["end"]),
                "top": float(box["top"]),
                "bottom": float(box["bottom"]),
                "fillColor": box["color"],
                "opacity": 0.20,
                "borderColor": box["color"],
                "borderSize": 1,
                "extendRight": False,
            }
        )

def finite_list(values):
    return [float(value) if np.isfinite(value) else None for value in values]

core_data = []
cross_data = []
upper_gradient = []
lower_gradient = []
for index in range(n):
    if np.isfinite(core_plot[index]):
        if core_value[index] > 50.0 and global_core[index] > 50.0:
            color = core_bull_color
        elif core_value[index] < 50.0 and global_core[index] < 50.0:
            color = core_bear_color
        else:
            color = core_neutral_color
        core_data.append({"value": float(core_plot[index]), "color": color})
    else:
        core_data.append(None)
    if show_crosses and (cross_up[index] or cross_down[index]) and np.isfinite(trend[index]):
        cross_data.append({"value": float(trend[index]), "color": cold_color if cross_up[index] else hot_color})
    else:
        cross_data.append(None)
    if show_gradient and np.isfinite(pulse[index]) and np.isfinite(trend[index]):
        top = max(pulse[index], trend[index])
        bottom = min(pulse[index], trend[index])
        clipped_top = min(0.0, top)
        clipped_bottom = max(-30.0, bottom)
        upper_gradient.append(float((clipped_top + clipped_bottom) * 0.5) if clipped_top > clipped_bottom else None)
        clipped_top = min(-70.0, top)
        clipped_bottom = max(-100.0, bottom)
        lower_gradient.append(float((clipped_top + clipped_bottom) * 0.5) if clipped_top > clipped_bottom else None)
    else:
        upper_gradient.append(None)
        lower_gradient.append(None)

plots = [
    {"name": "Upper Bound", "data": [0.0 for _ in range(n)], "color": upper_level_color, "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Upper Pressure Level", "data": [float(upper_level) for _ in range(n)], "color": upper_level_color, "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Equilibrium", "data": [-50.0 for _ in range(n)], "color": "rgba(148,163,184,0.20)", "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Lower Pressure Level", "data": [float(lower_level) for _ in range(n)], "color": lower_level_color, "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Lower Bound", "data": [-100.0 for _ in range(n)], "color": lower_level_color, "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Z Pulse", "data": finite_list(pulse), "color": pulse_color, "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Z Trend", "data": finite_list(trend), "color": trend_color, "type": "line", "lineWidth": 2, "overlay": False},
    {"name": "Pressure Core", "data": core_data, "color": core_neutral_color, "type": "line", "lineWidth": core_width, "overlay": False},
    {"name": "Upper Pressure Gradient", "title": False, "data": upper_gradient, "color": hot_color, "type": "line", "lineWidth": 4, "overlay": False},
    {"name": "Lower Pressure Gradient", "title": False, "data": lower_gradient, "color": cold_color, "type": "line", "lineWidth": 4, "overlay": False},
    {"name": "Upper Pressure Active", "data": [float(upper_level + 2.0) if upper_active[index] else None for index in range(n)], "color": hot_color, "type": "line", "lineWidth": 3, "overlay": False},
    {"name": "Lower Pressure Active", "data": [float(lower_level - 2.0) if lower_active[index] else None for index in range(n)], "color": cold_color, "type": "line", "lineWidth": 3, "overlay": False},
    {"name": "Pulse Trend Cross", "data": cross_data, "color": cold_color, "type": "column", "lineWidth": 5, "overlay": False},
]

output = {"name": my_indicator_name, "plots": plots, "signals": [], "layers": layers[-500:]}
