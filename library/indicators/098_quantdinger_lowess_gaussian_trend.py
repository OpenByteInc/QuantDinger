# ============================================================
# QuantDinger LOWESS Gaussian Trend
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger LOWESS Gaussian Trend"
my_indicator_description = (
    "Builds a volatility-adaptive Gaussian average, smooths it with local "
    "tricube weights, and tracks trend-confirmed pivot breaks."
)

# @param length int 30 Gaussian length range=1:2000:1
# @param pivot_length int 5 Pivot length range=3:500:1
# @param show_count bool false Count consecutive breaks
# @param gaussian_scatter bool false Show Gaussian scatter

length = min(2000, max(1, int(params.get("length", 30))))
pivot_length = min(500, max(3, int(params.get("pivot_length", 5))))
show_count = bool(params.get("show_count", False))
gaussian_scatter = bool(params.get("gaussian_scatter", False))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def pine_rma(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    seed = []
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed.append(float(value))
            if len(seed) < period:
                continue
            previous = float(np.mean(seed[-period:]))
        else:
            previous = (previous * (period - 1) + float(value)) / period
        result[index] = previous
    return result


true_range = np.full(n, np.nan, dtype=float)
if n:
    true_range[0] = high[0] - low[0]
for index in range(1, n):
    true_range[index] = max(
        high[index] - low[index],
        abs(high[index] - close[index - 1]),
        abs(low[index] - close[index - 1]),
    )
atr = pine_rma(true_range, length)

rolling_high = np.full(n, np.nan, dtype=float)
rolling_low = np.full(n, np.nan, dtype=float)
rolling_std = np.full(n, np.nan, dtype=float)
for index in range(n):
    partial_window = close[max(0, index - length + 1) : index + 1]
    rolling_high[index] = float(np.max(partial_window))
    rolling_low[index] = float(np.min(partial_window))
    if index < length - 1:
        continue
    window = close[index - length + 1:index + 1]
    rolling_std[index] = float(np.std(window, ddof=0))

sigma = (atr + rolling_std) / 2.0
gaussian = np.full(n, np.nan, dtype=float)
for index in range(length - 1, n):
    if not np.isfinite(sigma[index]) or sigma[index] <= 0.0:
        continue
    progressive_high = -np.inf
    progressive_low = np.inf
    weighted_sum = 0.0
    weight_total = 0.0
    for offset in range(length):
        sample = float(close[index - offset])
        progressive_high = max(progressive_high, sample)
        progressive_low = min(progressive_low, sample)
        history_index = index - offset
        history_high = rolling_high[history_index]
        history_low = rolling_low[history_index]
        normalized = (offset - (length - 1)) / (2.0 * sigma[index])
        weight = float(np.exp(-(normalized * normalized) / 2.0))
        value = max(float(history_high), progressive_high) + min(float(history_low), progressive_low)
        weighted_sum += value * weight
        weight_total += weight
    if weight_total > 0.0:
        gaussian[index] = weighted_sum / weight_total / 2.0

smooth_length = 10
tricube_weights = [float((1.0 - (offset / smooth_length) ** 3) ** 3) for offset in range(smooth_length)]
weight_sum = float(sum(tricube_weights))
weighted_offset = float(sum(weight * offset for offset, weight in enumerate(tricube_weights)))
smoothed = np.full(n, np.nan, dtype=float)
for index in range(smooth_length - 1, n):
    window = gaussian[index - smooth_length + 1 : index + 1]
    if not np.all(np.isfinite(window)):
        continue
    weighted_price = 0.0
    for offset, weight in enumerate(tricube_weights):
        weighted_price += weight * gaussian[index - offset]
    smoothed[index] = weighted_price / weight_sum + weighted_offset / weight_sum / (smooth_length - 1) / 2000.0

up_color = "#60BEFD"
down_color = "#AB61FF"
trend_up = np.zeros(n, dtype=bool)
trend_down = np.zeros(n, dtype=bool)
turn_up = np.zeros(n, dtype=bool)
turn_down = np.zeros(n, dtype=bool)
for index in range(2, n):
    if not np.isfinite(smoothed[index]) or not np.isfinite(smoothed[index - 2]):
        continue
    trend_up[index] = smoothed[index] > smoothed[index - 2]
    trend_down[index] = not trend_up[index]
    if index >= 3 and np.isfinite(smoothed[index - 1]) and np.isfinite(smoothed[index - 3]):
        previous_up = smoothed[index - 1] > smoothed[index - 3]
        turn_up[index] = trend_up[index] and not previous_up
        turn_down[index] = trend_down[index] and previous_up

right_bars = pivot_length - 2
pivot_high = np.full(n, np.nan, dtype=float)
pivot_low = np.full(n, np.nan, dtype=float)
for confirmation in range(pivot_length + right_bars, n):
    center = confirmation - right_bars
    start = center - pivot_length
    high_window = high[start : confirmation + 1]
    low_window = low[start : confirmation + 1]
    if high[center] == np.max(high_window):
        pivot_high[confirmation] = float(high[center])
    if low[center] == np.min(low_window):
        pivot_low[confirmation] = float(low[center])

break_plots = []
high_breaks = []
low_breaks = []
active_high = None
active_low = None
count_up = 0
count_down = 0
pivot_high_created = np.zeros(n, dtype=bool)
pivot_low_created = np.zeros(n, dtype=bool)
high_break_event = np.zeros(n, dtype=bool)
low_break_event = np.zeros(n, dtype=bool)
count_up_history = np.zeros(n, dtype=int)
count_down_history = np.zeros(n, dtype=int)
active_high_history = np.full(n, np.nan, dtype=float)
active_low_history = np.full(n, np.nan, dtype=float)

for index in range(n):
    if trend_up[index] and np.isfinite(pivot_high[index]):
        active_high = (index - right_bars, float(pivot_high[index]))
        pivot_high_created[index] = True
    if trend_down[index] and np.isfinite(pivot_low[index]):
        active_low = (index - right_bars, float(pivot_low[index]))
        pivot_low_created[index] = True

    if active_high is not None and index > 0:
        start_index, level = active_high
        if close[index] > level and close[index - 1] <= level:
            count_down = 0
            count_up += 1
            segment = [None for _ in range(n)]
            for point_index in range(max(0, start_index), index + 1):
                segment[point_index] = level
            break_plots.append(
                {"name": f"Upper Break {len(high_breaks) + 1}", "title": False, "data": segment, "color": "rgba(96, 190, 253, 0.70)", "type": "line", "lineWidth": 1, "overlay": True}
            )
            marker_price = float(high[index]) if show_count else level
            high_breaks.append((index, marker_price, count_up))
            high_break_event[index] = True
            active_high = None

    if active_low is not None and index > 0:
        start_index, level = active_low
        if close[index] < level and close[index - 1] >= level:
            count_up = 0
            count_down += 1
            segment = [None for _ in range(n)]
            for point_index in range(max(0, start_index), index + 1):
                segment[point_index] = level
            break_plots.append(
                {"name": f"Lower Break {len(low_breaks) + 1}", "title": False, "data": segment, "color": "rgba(171, 97, 255, 0.70)", "type": "line", "lineWidth": 1, "overlay": True}
            )
            marker_price = float(low[index]) if show_count else level
            low_breaks.append((index, marker_price, count_down))
            low_break_event[index] = True
            active_low = None

    active_high_history[index] = active_high[1] if active_high is not None else np.nan
    active_low_history[index] = active_low[1] if active_low is not None else np.nan
    count_up_history[index] = count_up
    count_down_history[index] = count_down

gaussian_points = []
smoothed_glow = []
smoothed_line = []
turn_points = []
for index in range(n):
    if gaussian_scatter and index % 4 == 0 and np.isfinite(gaussian[index]):
        shade = int((index * 73 + 41) % 256)
        gaussian_points.append({"value": float(gaussian[index]), "color": f"rgba({shade}, {shade}, {shade}, 0.85)", "size": 3})
    else:
        gaussian_points.append(None)

    if np.isfinite(smoothed[index]):
        color = up_color if trend_up[index] else down_color
        glow = "rgba(96, 190, 253, 0.20)" if trend_up[index] else "rgba(171, 97, 255, 0.20)"
        smoothed_line.append({"value": float(smoothed[index]), "color": color})
        smoothed_glow.append({"value": float(smoothed[index]), "color": glow})
    else:
        smoothed_line.append(None)
        smoothed_glow.append(None)

    if turn_up[index] or turn_down[index]:
        turn_points.append({"value": float(smoothed[index]), "color": up_color if turn_up[index] else down_color, "size": 4})
    else:
        turn_points.append(None)

signals = []
if show_count:
    for event_index, marker_price, event_count in high_breaks:
        data = [None for _ in range(n)]
        data[event_index] = marker_price
        signals.append({"type": "sell", "text": str(event_count), "name": f"Upper Break Count {event_index}", "data": data, "color": up_color, "renderMode": "events"})
    for event_index, marker_price, event_count in low_breaks:
        data = [None for _ in range(n)]
        data[event_index] = marker_price
        signals.append({"type": "buy", "text": str(event_count), "name": f"Lower Break Count {event_index}", "data": data, "color": down_color, "renderMode": "events"})
else:
    upper_markers = [None for _ in range(n)]
    lower_markers = [None for _ in range(n)]
    for event_index, marker_price, _ in high_breaks:
        upper_markers[event_index] = marker_price
    for event_index, marker_price, _ in low_breaks:
        lower_markers[event_index] = marker_price
    signals = [
        {"type": "sell", "text": "⬥", "name": "Upper Pivot Break", "data": upper_markers, "color": up_color, "renderMode": "events"},
        {"type": "buy", "text": "⬥", "name": "Lower Pivot Break", "data": lower_markers, "color": down_color, "renderMode": "events"},
    ]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Gaussian Scatter", "data": gaussian_points, "color": "#787B86", "type": "circle", "size": 3, "overlay": True},
        {"name": "LOWESS Glow", "title": False, "data": smoothed_glow, "color": up_color, "type": "line", "lineWidth": 5, "overlay": True},
        {"name": "LOWESS Trend", "data": smoothed_line, "color": up_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Trend Turn", "data": turn_points, "color": up_color, "type": "circle", "size": 4, "overlay": True},
    ] + break_plots,
    "signals": signals,
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "trueRange": [float(value) if np.isfinite(value) else None for value in true_range],
        "atr": [float(value) if np.isfinite(value) else None for value in atr],
        "rollingHigh": [float(value) if np.isfinite(value) else None for value in rolling_high],
        "rollingLow": [float(value) if np.isfinite(value) else None for value in rolling_low],
        "rollingStd": [float(value) if np.isfinite(value) else None for value in rolling_std],
        "sigma": [float(value) if np.isfinite(value) else None for value in sigma],
        "gaussian": [float(value) if np.isfinite(value) else None for value in gaussian],
        "smoothed": [float(value) if np.isfinite(value) else None for value in smoothed],
        "trendUp": trend_up.tolist(), "trendDown": trend_down.tolist(),
        "turnUp": turn_up.tolist(), "turnDown": turn_down.tolist(),
        "pivotHigh": [float(value) if np.isfinite(value) else None for value in pivot_high],
        "pivotLow": [float(value) if np.isfinite(value) else None for value in pivot_low],
        "pivotHighCreated": pivot_high_created.tolist(), "pivotLowCreated": pivot_low_created.tolist(),
        "highBreak": high_break_event.tolist(), "lowBreak": low_break_event.tolist(),
        "countUp": count_up_history.tolist(), "countDown": count_down_history.tolist(),
        "activeHigh": [float(value) if np.isfinite(value) else None for value in active_high_history],
        "activeLow": [float(value) if np.isfinite(value) else None for value in active_low_history],
    },
}
