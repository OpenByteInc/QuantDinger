# ============================================================
# QuantDinger Trend Lines
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Trend Lines"
my_indicator_description = (
    "Connects qualifying lower swing highs and higher swing lows, validates "
    "trendline integrity, filters by normalized angle, and marks line breaks."
)

# @param swing_length int 50 Swing length range=2:250:1
# @param validation_mode str Disabled Break validation mode values=Disabled,Point_A_Point_B,Point_A_Current_bar
# @param break_source str close Break source values=close,H/L
# @param minimum_bars int 3 Minimum uninterrupted bars range=0:100:1
# @param show_angles bool false Show angle labels
# @param axis_ratio float 3.0 X-Y normalization ratio range=0.1:20.0:0.1
# @param minimum_angle float 0.1 Minimum absolute angle range=0.1:89.9:0.1
# @param maximum_angle float 90.0 Maximum absolute angle range=0.2:90.0:0.1

swing_length = min(250, max(2, int(params.get("swing_length", 50))))
validation_mode = str(params.get("validation_mode", "Disabled")).replace("_", " ")
break_source = str(params.get("break_source", "close"))
minimum_bars = min(100, max(0, int(params.get("minimum_bars", 3))))
show_angles = bool(params.get("show_angles", False))
axis_ratio = min(20.0, max(0.1, float(params.get("axis_ratio", 3.0))))
minimum_angle = min(89.9, max(0.1, float(params.get("minimum_angle", 0.1))))
maximum_angle = min(90.0, max(minimum_angle, float(params.get("maximum_angle", 90.0))))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

pivot_high = np.full(n, np.nan, dtype=float)
pivot_low = np.full(n, np.nan, dtype=float)
for confirmation_index in range(2 * swing_length, n):
    pivot_index = confirmation_index - swing_length
    left = pivot_index - swing_length
    right = pivot_index + swing_length + 1
    high_window = high[left:right]
    low_window = low[left:right]
    if high[pivot_index] >= float(np.max(high_window)):
        pivot_high[confirmation_index] = float(high[pivot_index])
    if low[pivot_index] <= float(np.min(low_window)):
        pivot_low[confirmation_index] = float(low[pivot_index])

upper = np.full(n, np.nan, dtype=float)
lower = np.full(n, np.nan, dtype=float)
plot_high = np.full(n, np.nan, dtype=float)
plot_low = np.full(n, np.nan, dtype=float)
bullish_break = np.zeros(n, dtype=bool)
bearish_break = np.zeros(n, dtype=bool)
layers = []

previous_high_index = None
previous_high_value = np.nan
previous_low_index = None
previous_low_value = np.nan
high_slope = np.nan
low_slope = np.nan
high_slope_changed = False
low_slope_changed = False
bars_since_high_gap = 0
bars_since_low_gap = 0

def normalized_angle(index, x1, x2, y1, y2):
    difference_x = float(x2 - x1)
    difference_y = float(y2 - y1)
    if difference_x == 0.0 or difference_y == 0.0:
        return 0.0, 0.0
    x_axis = min(max(1, index), 500)
    start = max(0, index - x_axis + 1)
    y_axis = float(np.max(high[start:index + 1]) - np.min(low[start:index + 1]))
    slope = difference_y / difference_x
    if y_axis <= 0.0:
        return slope, 0.0
    normalized = (500.0 / axis_ratio) * difference_y / (y_axis * difference_x)
    angle = round(float(np.degrees(np.arctan(normalized))), 2)
    return slope, angle

def line_is_broken(index, x1, x2, y1, slope, direction):
    if validation_mode == "Disabled":
        return False
    first_index = x2 if validation_mode == "Point A Point B" else x1
    first_index = max(0, int(first_index))
    last_index = int(index)
    for bar_index in range(first_index, last_index + 1):
        line_price = float(y1 + slope * (bar_index - x1))
        if direction == "down":
            source_value = close[bar_index] if break_source == "close" else high[bar_index]
            if source_value > line_price:
                return True
        else:
            source_value = close[bar_index] if break_source == "close" else low[bar_index]
            if source_value < line_price:
                return True
    return False

for index in range(n):
    high_slope_changed = False
    low_slope_changed = False

    if np.isfinite(pivot_high[index]):
        pivot_index = index - swing_length
        pivot_value = float(pivot_high[index])
        if previous_high_index is not None and pivot_value < previous_high_value:
            slope, angle = normalized_angle(
                index,
                previous_high_index,
                pivot_index,
                previous_high_value,
                pivot_value,
            )
            angle_ok = minimum_angle < abs(angle) < maximum_angle
            broken = line_is_broken(
                index,
                previous_high_index,
                pivot_index,
                previous_high_value,
                slope,
                "down",
            )
            if angle_ok and not broken:
                high_slope = slope
                high_slope_changed = True
                upper[index] = pivot_value + slope * swing_length + slope
                layers.append(
                    {
                        "type": "line",
                        "startIndex": int(previous_high_index),
                        "endIndex": int(index),
                        "y1": float(previous_high_value),
                        "y2": float(pivot_value + slope * swing_length),
                        "color": "#F23645",
                        "lineWidth": 1,
                        "dashed": True,
                    }
                )
                if show_angles:
                    layers.append(
                        {
                            "type": "label",
                            "index": int(previous_high_index),
                            "price": float(previous_high_value),
                            "text": f"{angle:.2f}",
                            "color": "rgba(0, 0, 0, 0.0)",
                            "textColor": "#F23645",
                            "side": "above",
                        }
                    )
        previous_high_value = pivot_value
        previous_high_index = pivot_index

    if np.isfinite(pivot_low[index]):
        pivot_index = index - swing_length
        pivot_value = float(pivot_low[index])
        if previous_low_index is not None and pivot_value > previous_low_value:
            slope, angle = normalized_angle(
                index,
                previous_low_index,
                pivot_index,
                previous_low_value,
                pivot_value,
            )
            angle_ok = minimum_angle < angle < maximum_angle
            broken = line_is_broken(
                index,
                previous_low_index,
                pivot_index,
                previous_low_value,
                slope,
                "up",
            )
            if angle_ok and not broken:
                low_slope = slope
                low_slope_changed = True
                lower[index] = pivot_value + slope * swing_length + slope
                layers.append(
                    {
                        "type": "line",
                        "startIndex": int(previous_low_index),
                        "endIndex": int(index),
                        "y1": float(previous_low_value),
                        "y2": float(pivot_value + slope * swing_length),
                        "color": "#2962FF",
                        "lineWidth": 1,
                        "dashed": True,
                    }
                )
                if show_angles:
                    layers.append(
                        {
                            "type": "label",
                            "index": int(previous_low_index),
                            "price": float(previous_low_value),
                            "text": f"{angle:.2f}",
                            "color": "rgba(0, 0, 0, 0.0)",
                            "textColor": "#2962FF",
                            "side": "below",
                        }
                    )
        previous_low_value = pivot_value
        previous_low_index = pivot_index

    if index > 0:
        if not np.isfinite(upper[index]) and np.isfinite(upper[index - 1]) and np.isfinite(high_slope):
            upper[index] = upper[index - 1] + high_slope
        if not np.isfinite(lower[index]) and np.isfinite(lower[index - 1]) and np.isfinite(low_slope):
            lower[index] = lower[index - 1] + low_slope

    if np.isfinite(upper[index]):
        previous_source = close[index - 1] if break_source == "close" and index > 0 else high[index - 1] if index > 0 else high[index]
        previous_upper = upper[index - 1] if index > 0 else np.nan
        if not high_slope_changed and np.isfinite(previous_upper) and previous_source > previous_upper:
            plot_high[index] = np.nan
        else:
            plot_high[index] = upper[index]
    if np.isfinite(lower[index]):
        previous_source = close[index - 1] if break_source == "close" and index > 0 else low[index - 1] if index > 0 else low[index]
        previous_lower = lower[index - 1] if index > 0 else np.nan
        if not low_slope_changed and np.isfinite(previous_lower) and previous_source < previous_lower:
            plot_low[index] = np.nan
        else:
            plot_low[index] = lower[index]

    bars_since_high_gap = 0 if not np.isfinite(plot_high[index]) else bars_since_high_gap + 1
    bars_since_low_gap = 0 if not np.isfinite(plot_low[index]) else bars_since_low_gap + 1

    high_source_value = close[index] if break_source == "close" else high[index]
    low_source_value = close[index] if break_source == "close" else low[index]
    if index > 0 and np.isfinite(upper[index]) and np.isfinite(upper[index - 1]):
        previous_high_source = close[index - 1] if break_source == "close" else high[index - 1]
        bullish_break[index] = (
            bars_since_high_gap > minimum_bars
            and high_source_value > upper[index]
            and previous_high_source <= upper[index - 1]
        )
    if index > 0 and np.isfinite(lower[index]) and np.isfinite(lower[index - 1]):
        previous_low_source = close[index - 1] if break_source == "close" else low[index - 1]
        bearish_break[index] = (
            bars_since_low_gap > minimum_bars
            and low_source_value < lower[index]
            and previous_low_source >= lower[index - 1]
        )

layers = layers[-1000:]

plots = [
    {"name": "Down Trendline", "data": [None if not np.isfinite(value) else float(value) for value in plot_high], "color": "#F23645", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Up Trendline", "data": [None if not np.isfinite(value) else float(value) for value in plot_low], "color": "#2962FF", "type": "line", "lineWidth": 1, "overlay": True},
]

plots = [plot for plot in plots if any(item is not None for item in plot["data"])]
plots.append(
    {
        "name": "Price Reference",
        "title": False,
        "data": [float(value) for value in close],
        "color": "rgba(255, 255, 255, 0.01)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    }
)

bullish_data = [None for _ in range(n)]
bearish_data = [None for _ in range(n)]
for index in range(n):
    if bullish_break[index]:
        bullish_data[index] = {"value": float(low[index]), "text": "UP", "color": "#F23645"}
    if bearish_break[index]:
        bearish_data[index] = {"value": float(high[index]), "text": "DN", "color": "#2962FF"}

signals = [
    {"type": "buy", "text": "UP", "name": "Bullish Break", "data": bullish_data, "color": "#F23645", "renderMode": "events"},
    {"type": "sell", "text": "DN", "name": "Bearish Break", "data": bearish_data, "color": "#2962FF", "renderMode": "events"},
]

signals = [signal for signal in signals if any(item is not None for item in signal["data"])]

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers}
