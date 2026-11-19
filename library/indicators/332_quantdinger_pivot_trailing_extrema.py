# ============================================================
# QuantDinger Pivot Trailing Extrema
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Pivot Trailing Extrema"
my_indicator_description = "Resets trailing high/low envelopes at confirmed pivots and optionally backpaints them with a reconstructed live tail."

# @param pivot_length int 24 Symmetric pivot length range=2:500:1
# @param backpaint bool false Shift confirmed values to their pivot bars

pivot_length = min(500, max(2, int(params.get("pivot_length", 24))))
backpaint = bool(params.get("backpaint", False))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)

pivot_high = np.zeros(n, dtype=bool)
pivot_low = np.zeros(n, dtype=bool)
pivot_high_value = np.full(n, np.nan, dtype=float)
pivot_low_value = np.full(n, np.nan, dtype=float)
trailing_max = np.full(n, np.nan, dtype=float)
trailing_min = np.full(n, np.nan, dtype=float)
average = np.full(n, np.nan, dtype=float)
fill_side = np.zeros(n, dtype=int)
current_max = np.nan
current_min = np.nan
current_fill = 0
offset = pivot_length if backpaint else 0

for index in range(n):
    center = index - pivot_length
    if index >= pivot_length * 2:
        high_window = high[center - pivot_length:center + pivot_length + 1]
        low_window = low[center - pivot_length:center + pivot_length + 1]
        pivot_high[index] = high[center] == np.max(high_window)
        pivot_low[index] = low[center] == np.min(low_window)
        if pivot_high[index]:
            pivot_high_value[index] = high[center]
        if pivot_low[index]:
            pivot_low_value[index] = low[center]
    if pivot_high[index] or pivot_low[index]:
        current_max = float(high[center])
        current_min = float(low[center])
        current_fill = -1 if pivot_high[index] else 1
    source_index = index - offset
    if 0 <= source_index < n and np.isfinite(current_max):
        current_max = max(float(high[source_index]), current_max)
        current_min = min(float(low[source_index]), current_min)
    trailing_max[index], trailing_min[index] = current_max, current_min
    average[index] = (current_max + current_min) / 2.0 if np.all(np.isfinite([current_max, current_min])) else np.nan
    fill_side[index] = current_fill

display_max = np.full(n, np.nan, dtype=float)
display_min = np.full(n, np.nan, dtype=float)
display_average = np.full(n, np.nan, dtype=float)
display_fill_side = np.zeros(n, dtype=int)
for index in range(n):
    target = index - offset
    if target < 0 or target >= n or pivot_high[index] or pivot_low[index]:
        continue
    display_max[target] = trailing_max[index]
    display_min[target] = trailing_min[index]
    display_average[target] = average[index]
    display_fill_side[target] = fill_side[index]

tail_segments = []
if backpaint and n and np.isfinite(trailing_max[-1]):
    tail_max = float(trailing_max[-1])
    tail_min = float(trailing_min[-1])
    tail_average = (tail_max + tail_min) / 2.0
    start = max(0, n - 1 - pivot_length)
    for target in range(start + 1, n):
        next_max = max(float(high[target]), tail_max)
        next_min = min(float(low[target]), tail_min)
        next_average = (next_max + next_min) / 2.0
        tail_segments.append({
            "startIndex": target - 1, "endIndex": target,
            "max1": tail_max, "max2": next_max, "min1": tail_min, "min2": next_min,
            "avg1": tail_average, "avg2": next_average, "fillSide": int(fill_side[-1]),
        })
        display_max[target - 1], display_min[target - 1], display_average[target - 1] = tail_max, tail_min, tail_average
        display_fill_side[target - 1] = fill_side[-1]
        tail_max, tail_min, tail_average = next_max, next_min, next_average
    if tail_segments:
        display_max[-1], display_min[-1], display_average[-1] = tail_max, tail_min, tail_average
        display_fill_side[-1] = fill_side[-1]

fill_data = []
for index in range(n):
    if np.all(np.isfinite([display_max[index], display_min[index]])) and display_fill_side[index] != 0:
        fill_data.append({
            "upper": float(display_max[index]), "lower": float(display_min[index]),
            "color": "rgba(0,128,128,0.20)" if display_fill_side[index] > 0 else "rgba(255,0,0,0.20)",
        })
    else:
        fill_data.append(None)

layers = []
for segment in tail_segments:
    layers.extend([
        {"type": "line", "role": "Backpaint Maximum", "startIndex": segment["startIndex"], "endIndex": segment["endIndex"], "y1": segment["max1"], "y2": segment["max2"], "color": "#008080", "lineWidth": 1},
        {"type": "line", "role": "Backpaint Minimum", "startIndex": segment["startIndex"], "endIndex": segment["endIndex"], "y1": segment["min1"], "y2": segment["min2"], "color": "#FF0000", "lineWidth": 1},
        {"type": "line", "role": "Backpaint Average", "startIndex": segment["startIndex"], "endIndex": segment["endIndex"], "y1": segment["avg1"], "y2": segment["avg2"], "color": "#FF5D00", "lineWidth": 1},
    ])


def _pivot_points(events, values, bullish):
    result = [None] * n
    for confirmation in np.flatnonzero(events):
        target = confirmation - offset
        if 0 <= target < n and np.isfinite(values[confirmation]):
            result[target] = {
                "active": True, "price": float(values[confirmation]), "text": "▲" if bullish else "▼",
                "color": "#008080" if bullish else "#FF0000",
            }
    return result


output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "Trailing Maximum", "data": _finite(display_max), "color": "#008080", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Trailing Minimum", "data": _finite(display_min), "color": "#FF0000", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Average", "data": _finite(display_average), "color": "#FF5D00", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Trend Area", "title": False, "data": fill_data, "color": "#008080", "type": "cloud", "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "name": "Pivot Low", "data": _pivot_points(pivot_low, pivot_low_value, True), "color": "#008080", "renderMode": "events"},
        {"type": "sell", "name": "Pivot High", "data": _pivot_points(pivot_high, pivot_high_value, False), "color": "#FF0000", "renderMode": "events"},
    ],
    "layers": layers, "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "pivotHigh": pivot_high.tolist(), "pivotLow": pivot_low.tolist(),
        "pivotHighValue": _finite(pivot_high_value), "pivotLowValue": _finite(pivot_low_value),
        "trailingMax": _finite(trailing_max), "trailingMin": _finite(trailing_min), "average": _finite(average), "fillSide": fill_side.tolist(),
        "displayMax": _finite(display_max), "displayMin": _finite(display_min), "displayAverage": _finite(display_average),
        "displayFillSide": display_fill_side.tolist(), "tailSegments": tail_segments, "offset": offset,
    },
}
