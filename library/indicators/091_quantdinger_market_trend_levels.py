# ============================================================
# QuantDinger Market Trend Levels
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Market Trend Levels"
my_indicator_description = (
    "Uses fast and slow EMA transitions to project recent swing levels and "
    "shade trend strength between the two averages."
)

# @param fast_length int 12 Fast EMA length range=1:200:1
# @param slow_length int 25 Slow EMA length range=2:300:1
# @param extend_lines bool false Extend levels until broken
# @param display_last int 50 Maximum recent levels range=1:100:1

fast_length = max(1, int(params.get("fast_length", 12)))
slow_length = max(2, int(params.get("slow_length", 25)))
extend_lines = bool(params.get("extend_lines", False))
display_last = max(1, int(params.get("display_last", 50)))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def pine_ema(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


fast_ema = pine_ema(close, fast_length)
slow_ema = pine_ema(close, slow_length)
difference = fast_ema - slow_ema
cross_up = np.zeros(n, dtype=bool)
cross_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    if not all(
        np.isfinite(value)
        for value in (fast_ema[index], fast_ema[index - 1], slow_ema[index], slow_ema[index - 1])
    ):
        continue
    cross_up[index] = fast_ema[index] > slow_ema[index] and fast_ema[index - 1] <= slow_ema[index - 1]
    cross_down[index] = fast_ema[index] < slow_ema[index] and fast_ema[index - 1] >= slow_ema[index - 1]

levels = []
support_markers = [None for _ in range(n)]
resistance_markers = [None for _ in range(n)]
cross_markers = [None for _ in range(n)]

for index in range(n):
    if cross_up[index]:
        window_start = max(0, index - slow_length + 1)
        valid_lows = low[window_start : index + 1]
        valid_lows = valid_lows[np.isfinite(valid_lows) & (valid_lows > 0.0)]
        lowest = float(np.min(valid_lows)) if valid_lows.size else np.nan
        for offset in range(slow_length + 1):
            source_index = index - offset
            if source_index < 0:
                break
            if np.isfinite(lowest) and low[source_index] == lowest:
                levels.append(
                    {
                        "start": source_index,
                        "end": min(n - 1, source_index + 25),
                        "value": lowest,
                        "support": True,
                        "active": extend_lines,
                        "broken": False,
                    }
                )
                support_markers[source_index] = lowest
        if index > 0:
            cross_markers[index - 1] = float(fast_ema[index - 1])

    if cross_down[index]:
        window_start = max(0, index - slow_length + 1)
        valid_highs = high[window_start : index + 1]
        valid_highs = valid_highs[np.isfinite(valid_highs) & (valid_highs > 0.0)]
        highest = float(np.max(valid_highs)) if valid_highs.size else np.nan
        for offset in range(slow_length + 1):
            source_index = index - offset
            if source_index < 0:
                break
            if np.isfinite(highest) and high[source_index] == highest:
                levels.append(
                    {
                        "start": source_index,
                        "end": min(n - 1, source_index + 25),
                        "value": highest,
                        "support": False,
                        "active": extend_lines,
                        "broken": False,
                    }
                )
                resistance_markers[source_index] = highest
        if index > 0:
            cross_markers[index - 1] = float(fast_ema[index - 1])

    if len(levels) > display_last:
        levels = levels[-display_last:]

    if extend_lines:
        for level in levels:
            if not level["active"] or index < level["start"]:
                continue
            level["end"] = index
            if level["support"] and low[index] < level["value"]:
                level["active"] = False
                level["broken"] = True
            elif not level["support"] and high[index] > level["value"]:
                level["active"] = False
                level["broken"] = True

up_color = "#26BA9F"
down_color = "#6626BA"


def rolling_extreme(values, index, period, maximum):
    start = max(0, index - period + 1)
    window = values[start : index + 1]
    return float(np.max(window) if maximum else np.min(window))


trend_cloud = []
for index in range(n):
    if not np.isfinite(fast_ema[index]) or not np.isfinite(slow_ema[index]):
        trend_cloud.append(None)
        continue
    if fast_ema[index] > slow_ema[index]:
        positive_peak = max(rolling_extreme(difference, index, 100, True), 0.0)
        ratio = 0.0 if positive_peak == 0.0 else min(1.0, max(0.0, difference[index] / positive_peak))
        alpha = 0.30 + 0.70 * ratio
        color = f"rgba(38, 186, 159, {alpha:.3f})"
    else:
        negative_peak = min(rolling_extreme(difference, index, 100, False), 0.0)
        ratio = 0.0 if negative_peak == 0.0 else min(1.0, max(0.0, difference[index] / negative_peak))
        alpha = 0.30 + 0.70 * ratio
        color = f"rgba(102, 38, 186, {alpha:.3f})"
    trend_cloud.append(
        {
            "upper": float(max(fast_ema[index], slow_ema[index])),
            "lower": float(min(fast_ema[index], slow_ema[index])),
            "color": color,
        }
    )

plots = [
    {"name": "EMA Trend", "title": False, "data": trend_cloud, "color": up_color, "type": "cloud", "opacity": 1.0, "overlay": True},
    {"name": "Fast EMA Guide", "title": False, "data": [float(value) if np.isfinite(value) else None for value in fast_ema], "color": "rgba(0, 0, 0, 0)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Slow EMA Guide", "title": False, "data": [float(value) if np.isfinite(value) else None for value in slow_ema], "color": "rgba(0, 0, 0, 0)", "type": "line", "lineWidth": 1, "overlay": True},
]

for level_index, level in enumerate(levels):
    if not np.isfinite(level["value"]) or level["value"] <= 0.0:
        continue
    level_data = [None for _ in range(n)]
    start = max(0, int(level["start"]))
    end = min(n - 1, int(level["end"]))
    color = up_color if level["support"] else down_color
    for index in range(start, end + 1):
        if level["broken"] and (index - start) % 2 == 1:
            continue
        level_data[index] = {"value": float(level["value"]), "color": color}
    plots.append(
        {
            "name": f"Price Rail {level_index + 1}",
            "title": False,
            "data": level_data,
            "color": color,
            "type": "line",
            "lineWidth": 2,
            "overlay": True,
        }
    )

output = {
    "name": my_indicator_name,
    "plots": plots,
    "signals": [
        {"type": "buy", "text": "SUPPORT", "name": "Support Origin", "data": support_markers, "color": up_color, "renderMode": "events"},
        {"type": "sell", "text": "RESIST", "name": "Resistance Origin", "data": resistance_markers, "color": down_color, "renderMode": "events"},
        {"type": "buy", "text": "EMA X", "name": "EMA Transition", "data": cross_markers, "color": "#D1D5DB", "renderMode": "events"},
    ],
    "layers": [],
}
