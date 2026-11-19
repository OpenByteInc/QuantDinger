# ============================================================
# QuantDinger Hull Butterfly Oscillator
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Hull Butterfly Oscillator"
my_indicator_description = (
    "Uses a forward-versus-reversed Hull convolution to expose asymmetric "
    "momentum, adaptive mean-absolute levels, and squeeze reversal dots."
)

# @param length int 14 Hull length range=2:200:1
# @param level_multiplier float 2.0 Levels multiplier range=-10.0:10.0:0.1
# @param source str close Source values=close,open,high,low,hl2,hlc3,ohlc4
# @param bullish_color color #0CB51A Bullish color
# @param bearish_color color #FF1100 Bearish color
# @param line_color color #D1D4DC Oscillator line color
# @param level_color color #787B86 Adaptive level color

length = min(200, max(2, int(params.get("length", 14))))
level_multiplier = min(10.0, max(-10.0, float(params.get("level_multiplier", 2.0))))
source_name = str(params.get("source", "close"))
bullish_color = str(params.get("bullish_color", "#0CB51A"))
bearish_color = str(params.get("bearish_color", "#FF1100"))
line_color = str(params.get("line_color", "#D1D4DC"))
level_color = str(params.get("level_color", "#787B86"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

source_map = {
    "close": close,
    "open": open_price,
    "high": high,
    "low": low,
    "hl2": (high + low) / 2.0,
    "hlc3": (high + low + close) / 3.0,
    "ohlc4": (open_price + high + low + close) / 4.0,
}
source = source_map.get(source_name, close)

short_length = max(1, int(length / 2))
hull_length = max(1, int(np.sqrt(length)))
denominator_1 = short_length * (short_length + 1) / 2.0
denominator_2 = length * (length + 1) / 2.0
denominator_3 = hull_length * (hull_length + 1) / 2.0

combined_coefficients = [0.0 for _ in range(hull_length)]
for index in range(length):
    first_weight = max(short_length - index, 0)
    second_weight = length - index
    coefficient = 2.0 * (first_weight / denominator_1) - second_weight / denominator_2
    combined_coefficients.insert(0, coefficient)
for _ in range(max(0, hull_length - 1)):
    combined_coefficients.insert(0, 0.0)

hull_coefficients = []
for index in range(hull_length, len(combined_coefficients)):
    weighted_sum = 0.0
    for inner in range(index - hull_length, index):
        weighted_sum += combined_coefficients[inner] * (index - inner)
    hull_coefficients.insert(0, weighted_sum / denominator_3)

coefficient_count = len(hull_coefficients)
last_offset = coefficient_count - 1
hull_value = np.full(n, np.nan, dtype=float)
inverse_hull_value = np.full(n, np.nan, dtype=float)
oscillator = np.full(n, np.nan, dtype=float)

for index in range(last_offset, n):
    forward_value = 0.0
    reverse_value = 0.0
    for offset, coefficient in enumerate(hull_coefficients):
        forward_value += source[index - offset] * coefficient
        reverse_value += source[index - (last_offset - offset)] * coefficient
    hull_value[index] = forward_value
    inverse_hull_value[index] = reverse_value
    oscillator[index] = forward_value - reverse_value

adaptive_level = np.full(n, np.nan, dtype=float)
cumulative_absolute = 0.0
for index in range(n):
    if not np.isfinite(oscillator[index]):
        continue
    cumulative_absolute += abs(float(oscillator[index]))
    if index > 0:
        adaptive_level[index] = cumulative_absolute / index * level_multiplier

state = np.zeros(n, dtype=int)
bullish_dot = np.zeros(n, dtype=bool)
bearish_dot = np.zeros(n, dtype=bool)


def crossed(first_now, second_now, first_previous, second_previous):
    if not all(np.isfinite(value) for value in (first_now, second_now, first_previous, second_previous)):
        return False
    return (
        (first_now > second_now and first_previous <= second_previous)
        or (first_now < second_now and first_previous >= second_previous)
    )


current_state = 0
for index in range(n):
    previous_state = current_state
    if index > 0 and np.isfinite(oscillator[index]) and np.isfinite(adaptive_level[index]):
        reset_state = crossed(
            oscillator[index],
            adaptive_level[index],
            oscillator[index - 1],
            adaptive_level[index - 1],
        ) or crossed(
            oscillator[index],
            -adaptive_level[index],
            oscillator[index - 1],
            -adaptive_level[index - 1],
        )
        if reset_state:
            current_state = 0
        elif oscillator[index] < oscillator[index - 1] and oscillator[index] > adaptive_level[index]:
            current_state = -1
        elif oscillator[index] > oscillator[index - 1] and oscillator[index] < -adaptive_level[index]:
            current_state = 1
    state[index] = current_state
    bullish_dot[index] = current_state > previous_state and current_state == 1
    bearish_dot[index] = current_state < previous_state and current_state == -1


def color_with_alpha(color, alpha):
    value = str(color).strip()
    if value.startswith("#") and len(value) in (4, 7):
        if len(value) == 4:
            red = int(value[1] * 2, 16)
            green = int(value[2] * 2, 16)
            blue = int(value[3] * 2, 16)
        else:
            red = int(value[1:3], 16)
            green = int(value[3:5], 16)
            blue = int(value[5:7], 16)
        return f"rgba({red},{green},{blue},{max(0.0, min(1.0, float(alpha))):.3f})"
    return value

histogram = []
oscillator_line = []
upper_level = []
upper_half = []
lower_half = []
lower_level = []
for index in range(n):
    value = oscillator[index]
    level = adaptive_level[index]
    if not np.isfinite(value):
        histogram.append(None)
        oscillator_line.append(None)
    else:
        magnitude = min(1.0, abs(float(value)) / level) if np.isfinite(level) and level > 0.0 else 0.0
        alpha = 0.5 + 0.5 * magnitude
        color = color_with_alpha(bullish_color if value > 0.0 else bearish_color, alpha)
        histogram.append({"value": float(value), "color": color})
        oscillator_line.append(float(value))
    if np.isfinite(level):
        upper_level.append(float(level))
        upper_half.append(float(level / 2.0))
        lower_half.append(float(-level / 2.0))
        lower_level.append(float(-level))
    else:
        upper_level.append(None)
        upper_half.append(None)
        lower_half.append(None)
        lower_level.append(None)

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Butterfly Histogram", "data": histogram, "color": bullish_color, "type": "bar", "baseValue": 0.0, "lineWidth": 2, "overlay": False},
        {"name": "Butterfly Line", "data": oscillator_line, "color": line_color, "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Bullish Dot", "title": False, "data": [float(oscillator[index]) if bullish_dot[index] else None for index in range(n)], "color": bullish_color, "type": "circle", "size": 4, "overlay": False},
        {"name": "Bearish Dot", "title": False, "data": [float(oscillator[index]) if bearish_dot[index] else None for index in range(n)], "color": bearish_color, "type": "circle", "size": 4, "overlay": False},
        {"name": "Upper Level", "data": upper_level, "color": level_color, "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Upper Half Level", "data": upper_half, "color": level_color, "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Lower Half Level", "data": lower_half, "color": level_color, "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Lower Level", "data": lower_level, "color": level_color, "type": "line", "lineWidth": 1, "overlay": False},
    ],
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "combinedCoefficients": [float(value) for value in combined_coefficients],
        "hullCoefficients": [float(value) for value in hull_coefficients],
        "hull": [float(value) if np.isfinite(value) else None for value in hull_value],
        "inverseHull": [float(value) if np.isfinite(value) else None for value in inverse_hull_value],
        "oscillator": [float(value) if np.isfinite(value) else None for value in oscillator],
        "adaptiveLevel": [float(value) if np.isfinite(value) else None for value in adaptive_level],
        "state": [int(value) for value in state],
        "bullishDot": bullish_dot.tolist(), "bearishDot": bearish_dot.tolist(),
        "coefficientCount": int(coefficient_count), "lastOffset": int(last_offset),
    },
}
