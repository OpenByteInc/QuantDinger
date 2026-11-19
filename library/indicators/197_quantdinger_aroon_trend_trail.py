# ============================================================
# QuantDinger Aroon Trend Trail
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Aroon Trend Trail"
my_indicator_description = (
    "Builds a hysteresis-controlled Aroon trend state and an ATR-scaled trailing rail, "
    "with layered price fill, rail glow, optional diagnostics, and trend candle colors."
)

# @param aroon_length int 25 Aroon lookback length range=2:500:1
# @param flip_threshold float 0.15 Opposite bias required to flip range=0.0:1.0:0.01
# @param atr_length int 14 ATR length range=1:500:1
# @param atr_factor float 1.8 Base rail width range=0.1:20.0:0.05
# @param strong_width float 0.65 Strong trend width scale range=0.1:3.0:0.05
# @param weak_width float 1.25 Weak trend width scale range=0.1:3.0:0.05
# @param break_on_flip bool true Break the rail on trend flips
# @param bullish_color str #00FF00 Bullish rail color
# @param bearish_color str #FF0000 Bearish rail color
# @param line_width int 3 Rail line width range=1:6:1
# @param show_fill bool true Show layered gradient fill
# @param show_glow bool true Show rail glow
# @param show_aroon_values bool false Show Aroon diagnostic table
# @param color_candles bool true Color candles by trend

aroon_length = min(500, max(2, int(params.get("aroon_length", 25))))
flip_threshold = min(1.0, max(0.0, float(params.get("flip_threshold", 0.15))))
atr_length = min(500, max(1, int(params.get("atr_length", 14))))
atr_factor = min(20.0, max(0.1, float(params.get("atr_factor", 1.8))))
strong_width = min(3.0, max(0.1, float(params.get("strong_width", 0.65))))
weak_width = min(3.0, max(0.1, float(params.get("weak_width", 1.25))))
break_on_flip = bool(params.get("break_on_flip", True))
bullish_color = str(params.get("bullish_color", "#00FF00"))
bearish_color = str(params.get("bearish_color", "#FF0000"))
line_width = min(6, max(1, int(params.get("line_width", 3))))
show_fill = bool(params.get("show_fill", True))
show_glow = bool(params.get("show_glow", True))
show_aroon_values = bool(params.get("show_aroon_values", False))
color_candles = bool(params.get("color_candles", True))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def rgba(color, alpha):
    value = str(color).strip()
    if len(value) == 7 and value.startswith("#"):
        try:
            red = int(value[1:3], 16)
            green = int(value[3:5], 16)
            blue = int(value[5:7], 16)
            return f"rgba({red},{green},{blue},{min(1.0, max(0.0, float(alpha))):.3f})"
        except ValueError:
            pass
    return value


def wilder_average(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) < length:
        return result
    seed = source[:length]
    if np.all(np.isfinite(seed)):
        result[length - 1] = float(np.mean(seed))
    for index in range(length, len(source)):
        if np.isfinite(result[index - 1]) and np.isfinite(source[index]):
            result[index] = (result[index - 1] * (length - 1) + source[index]) / length
    return result


def line_points(values, colors):
    points = []
    for value, color in zip(values, colors):
        if np.isfinite(value):
            points.append({"value": float(value), "color": color})
        else:
            points.append(None)
    return points


def cloud_points(upper_values, lower_values, colors, visible):
    points = []
    for upper, lower, color in zip(upper_values, lower_values, colors):
        if visible and np.isfinite(upper) and np.isfinite(lower):
            points.append({
                "upper": float(max(upper, lower)),
                "lower": float(min(upper, lower)),
                "color": color,
            })
        else:
            points.append(None)
    return points


true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(
            high[index] - low[index],
            abs(high[index] - close[index - 1]),
            abs(low[index] - close[index - 1]),
        )
atr = wilder_average(true_range, atr_length)

aroon_up = np.full(n, np.nan, dtype=float)
aroon_down = np.full(n, np.nan, dtype=float)
aroon_bias = np.full(n, np.nan, dtype=float)
aroon_strength = np.full(n, np.nan, dtype=float)
for index in range(aroon_length - 1, n):
    high_window = high[index - aroon_length + 1:index + 1]
    low_window = low[index - aroon_length + 1:index + 1]
    since_high = int(np.argmax(high_window[::-1]))
    since_low = int(np.argmin(low_window[::-1]))
    aroon_up[index] = 100.0 * (aroon_length - since_high) / aroon_length
    aroon_down[index] = 100.0 * (aroon_length - since_low) / aroon_length
    aroon_bias[index] = (aroon_up[index] - aroon_down[index]) / 100.0
    aroon_strength[index] = min(abs(aroon_bias[index]), 1.0)

trend = np.zeros(n, dtype=int)
bull_flip = np.zeros(n, dtype=bool)
bear_flip = np.zeros(n, dtype=bool)
active_rail = np.full(n, np.nan, dtype=float)
plotted_rail = np.full(n, np.nan, dtype=float)
rail_width = np.full(n, np.nan, dtype=float)
trend_strength = np.full(n, np.nan, dtype=float)
gradient_1 = np.full(n, np.nan, dtype=float)
gradient_2 = np.full(n, np.nan, dtype=float)
gradient_3 = np.full(n, np.nan, dtype=float)
gradient_4 = np.full(n, np.nan, dtype=float)
glow_lower = np.full(n, np.nan, dtype=float)
glow_upper = np.full(n, np.nan, dtype=float)
outer_glow_lower = np.full(n, np.nan, dtype=float)
outer_glow_upper = np.full(n, np.nan, dtype=float)

current_trend = 0
lower_rail = np.nan
upper_rail = np.nan
for index in range(n):
    if not np.isfinite(aroon_bias[index]):
        continue

    previous_trend = current_trend
    if current_trend == 0:
        current_trend = 1 if aroon_bias[index] >= 0.0 else -1
    elif current_trend != 1 and aroon_bias[index] > flip_threshold:
        current_trend = 1
    elif current_trend != -1 and aroon_bias[index] < -flip_threshold:
        current_trend = -1

    trend[index] = current_trend
    bull_flip[index] = current_trend == 1 and previous_trend == -1
    bear_flip[index] = current_trend == -1 and previous_trend == 1
    trend_flipped = bool(bull_flip[index] or bear_flip[index])

    if not np.isfinite(atr[index]):
        continue

    width_scale = weak_width + (strong_width - weak_width) * aroon_strength[index]
    rail_width[index] = atr[index] * atr_factor * width_scale
    midpoint = (high[index] + low[index]) * 0.5
    raw_lower = midpoint - rail_width[index]
    raw_upper = midpoint + rail_width[index]

    if not np.isfinite(lower_rail):
        lower_rail = raw_lower
        upper_rail = raw_upper
    else:
        if current_trend == 1:
            lower_rail = raw_lower if bull_flip[index] else max(lower_rail, raw_lower)
        else:
            lower_rail = raw_lower
        if current_trend == -1:
            upper_rail = raw_upper if bear_flip[index] else min(upper_rail, raw_upper)
        else:
            upper_rail = raw_upper

    active_rail[index] = lower_rail if current_trend == 1 else upper_rail
    if not (break_on_flip and trend_flipped):
        plotted_rail[index] = active_rail[index]

    rail_distance = abs(close[index] - active_rail[index])
    distance_strength = min(rail_distance / rail_width[index], 1.0) if rail_width[index] > 0.0 else 0.0
    trend_strength[index] = min(aroon_strength[index] * 0.75 + distance_strength * 0.25, 1.0)

    difference = close[index] - active_rail[index]
    gradient_1[index] = active_rail[index] + difference * 0.15
    gradient_2[index] = active_rail[index] + difference * 0.35
    gradient_3[index] = active_rail[index] + difference * 0.60
    gradient_4[index] = active_rail[index] + difference * 0.82

    glow = atr[index] * (0.045 + trend_strength[index] * 0.025)
    outer_glow = glow * 2.3
    glow_lower[index] = active_rail[index] - glow
    glow_upper[index] = active_rail[index] + glow
    outer_glow_lower[index] = active_rail[index] - outer_glow
    outer_glow_upper[index] = active_rail[index] + outer_glow

for index in range(1, n):
    if trend[index] == 0:
        trend[index] = trend[index - 1]

trend_colors = [
    bullish_color if direction == 1 else bearish_color if direction == -1 else None
    for direction in trend
]
rail_colors = [color if color is not None else bullish_color for color in trend_colors]
bar_colors = trend_colors if color_candles else [None for _ in range(n)]

fill_colors_1 = [rgba(color or bullish_color, 0.22 + (0.10 * strength if np.isfinite(strength) else 0.0)) for color, strength in zip(trend_colors, trend_strength)]
fill_colors_2 = [rgba(color or bullish_color, 0.15) for color in trend_colors]
fill_colors_3 = [rgba(color or bullish_color, 0.10) for color in trend_colors]
fill_colors_4 = [rgba(color or bullish_color, 0.065) for color in trend_colors]
fill_colors_5 = [rgba(color or bullish_color, 0.035) for color in trend_colors]
outer_glow_colors = [rgba(color or bullish_color, 0.05) for color in trend_colors]
inner_glow_colors = [
    rgba(color or bullish_color, 0.12 + (0.10 * strength if np.isfinite(strength) else 0.0))
    for color, strength in zip(trend_colors, trend_strength)
]

plots = [
    {
        "name": "Aroon Trend Rail",
        "data": line_points(plotted_rail, rail_colors),
        "color": bullish_color,
        "type": "line",
        "lineWidth": line_width,
        "overlay": True,
    },
    {
        "name": "Gradient Layer 1",
        "title": False,
        "data": cloud_points(plotted_rail, gradient_1, fill_colors_1, show_fill),
        "type": "cloud",
        "overlay": True,
    },
    {
        "name": "Gradient Layer 2",
        "title": False,
        "data": cloud_points(gradient_1, gradient_2, fill_colors_2, show_fill),
        "type": "cloud",
        "overlay": True,
    },
    {
        "name": "Gradient Layer 3",
        "title": False,
        "data": cloud_points(gradient_2, gradient_3, fill_colors_3, show_fill),
        "type": "cloud",
        "overlay": True,
    },
    {
        "name": "Gradient Layer 4",
        "title": False,
        "data": cloud_points(gradient_3, gradient_4, fill_colors_4, show_fill),
        "type": "cloud",
        "overlay": True,
    },
    {
        "name": "Gradient Layer 5",
        "title": False,
        "data": cloud_points(gradient_4, close, fill_colors_5, show_fill),
        "type": "cloud",
        "overlay": True,
    },
    {
        "name": "Outer Rail Glow",
        "title": False,
        "data": cloud_points(outer_glow_upper, outer_glow_lower, outer_glow_colors, show_glow),
        "type": "cloud",
        "overlay": True,
    },
    {
        "name": "Inner Rail Glow",
        "title": False,
        "data": cloud_points(glow_upper, glow_lower, inner_glow_colors, show_glow),
        "type": "cloud",
        "overlay": True,
    },
]

tables = []
if show_aroon_values and n:
    finite_indices = np.flatnonzero(np.isfinite(aroon_bias))
    if len(finite_indices):
        final_index = int(finite_indices[-1])
        tables.append({
            "title": "AROON TRAIL",
            "position": "top-right",
            "compact": True,
            "borderColor": trend_colors[final_index] or bullish_color,
            "accentColor": trend_colors[final_index] or bullish_color,
            "rows": [
                ["Trend", "BULLISH" if trend[final_index] == 1 else "BEARISH"],
                ["Aroon Up", f"{aroon_up[final_index]:.2f}"],
                ["Aroon Down", f"{aroon_down[final_index]:.2f}"],
                ["Bias", f"{aroon_bias[final_index]:.4f}"],
                ["Strength", f"{aroon_strength[final_index] * 100.0:.2f}%"],
                ["Rail Width", f"{rail_width[final_index]:.4f}" if np.isfinite(rail_width[final_index]) else "—"],
            ],
        })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "tables": tables,
    "bar_colors": bar_colors,
}
