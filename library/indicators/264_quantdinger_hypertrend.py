# ============================================================
# QuantDinger HyperTrend
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger HyperTrend"
my_indicator_description = (
    "Tracks price with an ATR-gated adaptive average and expands a persistent "
    "trend-width envelope around direction changes."
)

# @param factor float 5.0 Multiplicative factor range=0.0:50.0:0.1
# @param slope float 14.0 Slope range=0.0:100.0:0.1
# @param width_percent float 80.0 Width percent range=0.0:100.0:1.0
# @param bull_color color #008080 Rising average color
# @param bear_color color #FF0000 Falling average color
# @param area_style str Gradient Area style values=Gradient,Solid
# @param upper_color color #FF0000 Upper area color
# @param lower_color color #008080 Lower area color

factor = min(50.0, max(0.0, float(params.get("factor", 5.0))))
slope = min(100.0, max(0.0, float(params.get("slope", 14.0))))
width = min(1.0, max(0.0, float(params.get("width_percent", 80.0)) / 100.0))
bull_color = str(params.get("bull_color", "#008080"))
bear_color = str(params.get("bear_color", "#FF0000"))
area_style = str(params.get("area_style", "Gradient"))
if area_style not in ("Gradient", "Solid"):
    area_style = "Gradient"
upper_color = str(params.get("upper_color", "#FF0000"))
lower_color = str(params.get("lower_color", "#008080"))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

true_range = np.full(n, np.nan, dtype=float)
atr_200 = np.full(n, np.nan, dtype=float)
for index in range(n):
    previous_close = close[index - 1] if index else close[index]
    true_range[index] = max(
        high[index] - low[index],
        abs(high[index] - previous_close),
        abs(low[index] - previous_close),
    )
if n >= 200:
    atr_200[199] = float(np.mean(true_range[:200]))
    for index in range(200, n):
        atr_200[index] = (atr_200[index - 1] * 199.0 + true_range[index]) / 200.0

average = np.full(n, np.nan, dtype=float)
direction = np.full(n, np.nan, dtype=float)
held_width = np.zeros(n, dtype=float)
upper = np.full(n, np.nan, dtype=float)
lower = np.full(n, np.nan, dtype=float)
reversal = np.zeros(n, dtype=bool)

average_state = float(close[0]) if n else np.nan
direction_state = 1.0
hold_state = 0.0
for index in range(n):
    atr = (float(atr_200[index]) if np.isfinite(atr_200[index]) else 0.0) * factor
    previous_average = average_state
    previous_direction = direction_state
    if abs(close[index] - average_state) > atr:
        average_state = float((close[index] + average_state) * 0.5)
    else:
        denominator = factor * slope
        drift = direction_state * (hold_state / denominator) if denominator > 0.0 and np.isfinite(direction_state) else 0.0
        average_state = float(average_state + drift)

    if index == 0 or not np.isfinite(previous_average):
        direction_state = np.nan
    else:
        delta = average_state - previous_average
        direction_state = 1.0 if delta > 0.0 else -1.0 if delta < 0.0 else 0.0

    changed = bool(
        index > 0 and np.isfinite(direction_state) and np.isfinite(previous_direction)
        and direction_state != previous_direction
    )
    if changed:
        hold_state = atr
    average[index] = average_state
    direction[index] = direction_state
    held_width[index] = hold_state
    upper[index] = average_state + width * hold_state
    lower[index] = average_state - width * hold_state
    reversal[index] = changed

def _color_with_alpha(color, alpha):
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

average_plot = []
upper_plot = []
lower_plot = []
upper_cloud = []
lower_cloud = []
for index in range(n):
    color = bull_color if direction[index] == 1.0 else bear_color
    average_plot.append(None if reversal[index] else {"value": float(average[index]), "color": color})
    upper_plot.append(float(upper[index]) if np.isfinite(upper[index]) else None)
    lower_plot.append(float(lower[index]) if np.isfinite(lower[index]) else None)
    if reversal[index] or not all(np.isfinite(value) for value in (upper[index], average[index], lower[index])):
        upper_cloud.append(None)
        lower_cloud.append(None)
    else:
        opacity = 0.18 if area_style == "Gradient" else 0.30
        upper_cloud.append({"upper": float(upper[index]), "lower": float(average[index]), "color": _color_with_alpha(upper_color, opacity)})
        lower_cloud.append({"upper": float(average[index]), "lower": float(lower[index]), "color": _color_with_alpha(lower_color, opacity)})

def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Average", "data": average_plot, "color": bull_color, "type": "line", "lineWidth": 2, "overlay": True, "connectNulls": False},
        {"name": "Upper Area", "title": False, "data": upper_cloud, "type": "cloud", "opacity": 0.18 if area_style == "Gradient" else 0.30, "overlay": True},
        {"name": "Lower Area", "title": False, "data": lower_cloud, "type": "cloud", "opacity": 0.18 if area_style == "Gradient" else 0.30, "overlay": True},
        {"name": "Upper Boundary", "title": False, "data": upper_plot, "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Lower Boundary", "title": False, "data": lower_plot, "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite_or_none(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "trueRange": _finite_or_none(true_range), "atr200": _finite_or_none(atr_200),
        "average": _finite_or_none(average), "direction": _finite_or_none(direction),
        "heldWidth": _finite_or_none(held_width), "upper": _finite_or_none(upper),
        "lower": _finite_or_none(lower), "reversal": reversal.tolist(),
    },
}
