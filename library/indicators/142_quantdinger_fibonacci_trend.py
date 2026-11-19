# ============================================================
# QuantDinger Fibonacci Trend
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Fibonacci Trend"
my_indicator_description = (
    "Tracks the active SuperTrend leg, updates its extreme range, and projects "
    "configurable Fibonacci levels, fills, endpoint labels and an extrema diagonal."
)

# @param show_trend bool true Show SuperTrend overlay
# @param trend_multiplier float 4.0 SuperTrend multiplier range=0.1:20.0:0.1
# @param extension_bars int 15 Fibonacci extension bars range=1:200:1
# @param level_1 float 0.236 Fibonacci level 1 range=-5.0:5.0:0.001
# @param level_2 float 0.382 Fibonacci level 2 range=-5.0:5.0:0.001
# @param level_3 float 0.618 Fibonacci level 3 range=-5.0:5.0:0.001
# @param level_4 float 0.786 Fibonacci level 4 range=-5.0:5.0:0.001

show_trend = bool(params.get("show_trend", True))
trend_multiplier = min(20.0, max(0.1, float(params.get("trend_multiplier", 4.0))))
extension_bars = min(200, max(1, int(params.get("extension_bars", 15))))
level_1 = min(5.0, max(-5.0, float(params.get("level_1", 0.236))))
level_2 = min(5.0, max(-5.0, float(params.get("level_2", 0.382))))
level_3 = min(5.0, max(-5.0, float(params.get("level_3", 0.618))))
level_4 = min(5.0, max(-5.0, float(params.get("level_4", 0.786))))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
time_values = df["time"].astype(float).to_numpy(dtype=float)
n = len(df)
midpoint = (high + low) / 2.0

true_range = np.zeros(n, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(
            high[index] - low[index],
            abs(high[index] - close[index - 1]),
            abs(low[index] - close[index - 1]),
        )

def wilder_average(values, length):
    result = np.full(values.size, np.nan, dtype=float)
    if values.size >= length:
        result[length - 1] = float(np.mean(values[:length]))
        for value_index in range(length, values.size):
            result[value_index] = (
                result[value_index - 1] * (length - 1) + values[value_index]
            ) / length
    return result

atr_25 = wilder_average(true_range, 25)
atr_200 = wilder_average(true_range, 200)
upper_band = np.full(n, np.nan, dtype=float)
lower_band = np.full(n, np.nan, dtype=float)
supertrend = np.full(n, np.nan, dtype=float)
direction = np.ones(n, dtype=int)

for index in range(n):
    if not np.isfinite(atr_25[index]):
        continue
    raw_upper = midpoint[index] + trend_multiplier * atr_25[index]
    raw_lower = midpoint[index] - trend_multiplier * atr_25[index]
    if index == 0 or not np.isfinite(upper_band[index - 1]):
        upper_band[index] = raw_upper
        lower_band[index] = raw_lower
        direction[index] = 1
    else:
        upper_band[index] = (
            raw_upper
            if raw_upper < upper_band[index - 1] or close[index - 1] > upper_band[index - 1]
            else upper_band[index - 1]
        )
        lower_band[index] = (
            raw_lower
            if raw_lower > lower_band[index - 1] or close[index - 1] < lower_band[index - 1]
            else lower_band[index - 1]
        )
        if supertrend[index - 1] == upper_band[index - 1]:
            direction[index] = -1 if close[index] > upper_band[index] else 1
        else:
            direction[index] = 1 if close[index] < lower_band[index] else -1
    supertrend[index] = lower_band[index] if direction[index] == -1 else upper_band[index]

trend_start = None
fib_edge = np.nan
fib_zero = np.nan
extreme_high = np.nan
extreme_low = np.nan
extreme_high_index = None
extreme_low_index = None
high_label_visible = False
low_label_visible = False

for index in range(n):
    if not np.isfinite(supertrend[index]):
        continue
    changed = index > 0 and np.isfinite(supertrend[index - 1]) and direction[index] != direction[index - 1]
    if changed:
        trend_start = index
        high_label_visible = False
        low_label_visible = False
        if np.isfinite(atr_200[index]):
            if direction[index] == -1:
                fib_edge = float(low[index])
                fib_zero = float(high[index] + atr_200[index] * 3.0)
            else:
                fib_edge = float(high[index])
                fib_zero = float(low[index] - atr_200[index] * 3.0)
        else:
            fib_edge = np.nan
            fib_zero = np.nan
        continue

    if trend_start is None or not np.isfinite(fib_edge) or not np.isfinite(fib_zero):
        continue
    if low[index] < fib_edge:
        fib_edge = float(low[index])
    if high[index] > fib_zero:
        fib_zero = float(high[index])
    if high[index] == fib_zero:
        extreme_high = float(high[index])
        extreme_high_index = index
        high_label_visible = True
    if low[index] == fib_edge:
        extreme_low = float(low[index])
        extreme_low_index = index
        low_label_visible = True

positive_intervals = np.diff(time_values)
positive_intervals = positive_intervals[positive_intervals > 0.0]
interval = float(np.median(positive_intervals)) if positive_intervals.size else 0.0

layers = []
if trend_start is not None and np.isfinite(fib_edge) and np.isfinite(fib_zero) and n > 0:
    future_time = float(time_values[-1] + interval * extension_bars)
    size_step = (fib_zero - fib_edge) / 100.0
    if direction[-1] == -1:
        value_zero = float(fib_zero)
        value_one = float(fib_edge)
    else:
        value_zero = float(fib_edge)
        value_one = float(fib_zero)

    level_values = [
        ("0", value_zero),
        (f"{level_1:g}", value_zero + size_step * level_1 * direction[-1] * 100.0),
        (f"{level_2:g}", value_zero + size_step * level_2 * direction[-1] * 100.0),
        ("0.5", value_zero + size_step * 50.0 * direction[-1]),
        (f"{level_3:g}", value_zero + size_step * level_3 * direction[-1] * 100.0),
        (f"{level_4:g}", value_zero + size_step * level_4 * direction[-1] * 100.0),
        ("1", value_one),
    ]
    for level_index, (level_text, level_price) in enumerate(level_values):
        layers.append(
            {
                "type": "line",
                "startIndex": int(trend_start),
                "end": future_time,
                "y1": float(level_price),
                "y2": float(level_price),
                "color": "rgba(220,220,220,0.50)" if level_index == 3 else "#D9D9D9",
                "lineWidth": 2 if level_index in (0, 6) else 1,
                "dashed": level_index == 3,
            }
        )
        if level_index != 3:
            layers.append(
                {
                    "type": "label",
                    "timestamp": future_time,
                    "price": float(level_price),
                    "text": f"{level_text} ({level_price:,.4f})",
                    "color": "rgba(0,0,0,0.0)",
                    "textColor": "#D9D9D9",
                    "side": "right",
                }
            )

    lower_fill = min(level_values[4][1], level_values[5][1])
    upper_fill = max(level_values[4][1], level_values[5][1])
    layers.append(
        {
            "type": "zone",
            "startIndex": int(trend_start),
            "end": future_time,
            "top": float(upper_fill),
            "bottom": float(lower_fill),
            "fillColor": "rgba(38,137,144,0.11)",
            "borderColor": "rgba(0,0,0,0.0)",
            "borderSize": 0,
        }
    )
    second_lower = min(level_values[3][1], level_values[5][1])
    second_upper = max(level_values[3][1], level_values[5][1])
    layers.append(
        {
            "type": "zone",
            "startIndex": int(trend_start),
            "end": future_time,
            "top": float(second_upper),
            "bottom": float(second_lower),
            "fillColor": "rgba(38,137,144,0.11)",
            "borderColor": "rgba(0,0,0,0.0)",
            "borderSize": 0,
        }
    )

    if (
        extreme_high_index is not None
        and extreme_low_index is not None
        and extreme_high_index >= trend_start
        and extreme_low_index >= trend_start
    ):
        layers.append(
            {
                "type": "line",
                "startIndex": int(extreme_low_index),
                "endIndex": int(extreme_high_index),
                "y1": float(extreme_low),
                "y2": float(extreme_high),
                "color": "#D9D9D9",
                "lineWidth": 1,
                "dashed": True,
            }
        )
    if high_label_visible and extreme_high_index is not None:
        layers.append(
            {
                "type": "label",
                "index": int(extreme_high_index),
                "price": float(extreme_high),
                "text": f"{extreme_high:,.4f}",
                "color": "#15373B",
                "textColor": "#FFFFFF",
                "side": "above",
            }
        )
    if low_label_visible and extreme_low_index is not None:
        layers.append(
            {
                "type": "label",
                "index": int(extreme_low_index),
                "price": float(extreme_low),
                "text": f"{extreme_low:,.4f}",
                "color": "#15373B",
                "textColor": "#FFFFFF",
                "side": "below",
            }
        )

trend_data = []
trend_upper = []
trend_lower = []
for index in range(n):
    changed = index == 0 or direction[index] != direction[index - 1]
    if show_trend and np.isfinite(supertrend[index]) and not changed:
        color = "#74286D" if direction[index] == 1 else "#26905D"
        trend_data.append({"value": float(supertrend[index]), "color": color})
        boundary_color = "rgba(116,40,109,0.35)" if direction[index] == 1 else "rgba(38,144,93,0.35)"
        trend_upper.append({"value": float(max(midpoint[index], supertrend[index])), "color": boundary_color})
        trend_lower.append({"value": float(min(midpoint[index], supertrend[index])), "color": boundary_color})
    else:
        trend_data.append(None)
        trend_upper.append(None)
        trend_lower.append(None)

plots = [
    {"name": "Trend", "data": trend_data, "color": "#26905D", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Trend Upper Boundary", "title": False, "data": trend_upper, "color": "rgba(38,144,93,0.35)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Trend Lower Boundary", "title": False, "data": trend_lower, "color": "rgba(38,144,93,0.35)", "type": "line", "lineWidth": 1, "overlay": True},
]
plots = [plot for plot in plots if any(item is not None for item in plot["data"])]
plots.append(
    {"name": "Price Reference", "title": False, "data": [float(value) for value in close], "color": "rgba(255,255,255,0.01)", "type": "line", "lineWidth": 1, "overlay": True}
)

output = {"name": my_indicator_name, "plots": plots, "signals": [], "layers": layers}
