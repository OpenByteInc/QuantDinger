# ============================================================
# QuantDinger Volume Heatmap Divergence
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Volume Heatmap Divergence"
my_indicator_description = (
    "Normalizes volume against its rolling extreme, colors a heatmap histogram, "
    "and marks price disagreement between consecutive volume pivot highs."
)

# @param show_divergence bool true Show divergence
# @param label_volume bool true Include raw volume in labels
# @param label_percent bool true Include normalized percent in labels
# @param range_min int 5 Minimum pivot distance range=1:100:1
# @param range_max int 60 Maximum pivot distance range=2:300:1
# @param pivot_left int 10 Pivot left bars range=1:50:1
# @param pivot_right int 5 Pivot right bars range=1:50:1
# @param chart_scale int 400 Heatmap chart scale range=100:1000:25

show_divergence = bool(params.get("show_divergence", True))
label_volume = bool(params.get("label_volume", True))
label_percent = bool(params.get("label_percent", True))
range_min = max(1, int(params.get("range_min", 5)))
range_max = max(range_min + 1, int(params.get("range_max", 60)))
pivot_left = max(1, int(params.get("pivot_left", 10)))
pivot_right = max(1, int(params.get("pivot_right", 5)))
chart_scale = max(100, int(params.get("chart_scale", 400)))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)


def rolling_maximum(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(len(source)):
        start = max(0, index - period + 1)
        window = source[start : index + 1]
        finite = window[np.isfinite(window)]
        if finite.size:
            result[index] = float(np.max(finite))
    return result


def interpolate_color(start, end, fraction):
    ratio = min(1.0, max(0.0, float(fraction)))
    red = int(round(start[0] + (end[0] - start[0]) * ratio))
    green = int(round(start[1] + (end[1] - start[1]) * ratio))
    blue = int(round(start[2] + (end[2] - start[2]) * ratio))
    return f"#{red:02X}{green:02X}{blue:02X}"


def readable_volume(value):
    amount = float(value)
    absolute = abs(amount)
    if absolute >= 1_000_000_000.0:
        return f"{amount / 1_000_000_000.0:.2f}B"
    if absolute >= 1_000_000.0:
        return f"{amount / 1_000_000.0:.2f}M"
    if absolute >= 1_000.0:
        return f"{amount / 1_000.0:.2f}K"
    return f"{amount:.0f}"


maximum_volume = rolling_maximum(volume, 1000)
normalized_volume = np.full(n, np.nan, dtype=float)
valid = np.isfinite(volume) & np.isfinite(maximum_volume) & (maximum_volume > 0.0)
normalized_volume[valid] = volume[valid] / maximum_volume[valid] * 100.0

heatmap = []
for value in normalized_volume:
    if not np.isfinite(value):
        heatmap.append(None)
        continue
    if value < 20.0:
        color = interpolate_color((0, 255, 255), (255, 255, 0), value / 20.0)
    else:
        color = interpolate_color((255, 255, 0), (255, 0, 0), (value - 20.0) / 80.0)
    heatmap.append({"value": float(value), "color": color})

pivot_high = np.zeros(n, dtype=bool)
confirmation_index = np.full(n, -1, dtype=int)
window = pivot_left + pivot_right + 1
for confirmation in range(window - 1, n):
    pivot = confirmation - pivot_right
    segment = normalized_volume[pivot - pivot_left : pivot + pivot_right + 1]
    if np.all(np.isfinite(segment)) and normalized_volume[pivot] == np.max(segment):
        pivot_high[pivot] = True
        confirmation_index[pivot] = confirmation

bullish_divergence = np.zeros(n, dtype=bool)
bearish_divergence = np.zeros(n, dtype=bool)
bullish_line = [None for _ in range(n)]
bearish_line = [None for _ in range(n)]
bullish_points = [None for _ in range(n)]
bearish_points = [None for _ in range(n)]
bullish_signals = [None for _ in range(n)]
bearish_signals = [None for _ in range(n)]
previous_pivot = -1

for pivot in range(n):
    if not pivot_high[pivot]:
        continue
    if previous_pivot >= 0:
        distance = confirmation_index[pivot] - confirmation_index[previous_pivot]
        in_range = range_min <= distance <= range_max
        bullish_divergence[pivot] = show_divergence and in_range and normalized_volume[pivot] > normalized_volume[previous_pivot] and low[pivot] < low[previous_pivot]
        bearish_divergence[pivot] = show_divergence and in_range and normalized_volume[pivot] < normalized_volume[previous_pivot] and high[pivot] > high[previous_pivot]

        if bullish_divergence[pivot] or bearish_divergence[pivot]:
            target = bullish_line if bullish_divergence[pivot] else bearish_line
            span = max(1, pivot - previous_pivot)
            for index in range(previous_pivot, pivot + 1):
                ratio = (index - previous_pivot) / span
                target[index] = float(normalized_volume[previous_pivot] + (normalized_volume[pivot] - normalized_volume[previous_pivot]) * ratio)

    if bullish_divergence[pivot]:
        bullish_points[pivot] = float(normalized_volume[pivot])
        parts = ["Bull"]
        if label_volume:
            parts.append(readable_volume(volume[pivot]))
        if label_percent:
            parts.append(f"{normalized_volume[pivot]:.1f}%")
        bullish_signals[pivot] = {"value": float(low[pivot]), "text": "\n".join(parts), "color": "#19B493"}
    if bearish_divergence[pivot]:
        bearish_points[pivot] = float(normalized_volume[pivot])
        parts = ["Bear"]
        if label_volume:
            parts.append(readable_volume(volume[pivot]))
        if label_percent:
            parts.append(f"{normalized_volume[pivot]:.1f}%")
        bearish_signals[pivot] = {"value": float(high[pivot]), "text": "\n".join(parts), "color": "#B95919"}
    previous_pivot = pivot

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Volume Heatmap", "data": heatmap, "color": "#FFFF00", "type": "column", "baseValue": 0.0, "lineWidth": 1, "overlay": False},
        {"name": "Scale Guide", "title": False, "data": [float(chart_scale) for _ in range(n)], "color": "rgba(0, 0, 0, 0)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Bullish Divergence", "data": bullish_line, "color": "#19B493", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Bearish Divergence", "data": bearish_line, "color": "#B95919", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Bullish Pivot", "data": bullish_points, "color": "#19B493", "type": "circle", "size": 5, "overlay": False},
        {"name": "Bearish Pivot", "data": bearish_points, "color": "#B95919", "type": "circle", "size": 5, "overlay": False},
    ],
    "signals": [
        {"type": "buy", "text": "Bull", "name": "Bullish Price Divergence", "data": bullish_signals, "color": "#19B493", "renderMode": "events"},
        {"type": "sell", "text": "Bear", "name": "Bearish Price Divergence", "data": bearish_signals, "color": "#B95919", "renderMode": "events"},
    ],
    "layers": [],
}
