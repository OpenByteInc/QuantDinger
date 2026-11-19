# ============================================================
# QuantDinger Machine Learning Adaptive SuperTrend
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Machine Learning Adaptive SuperTrend"
my_indicator_description = "Clusters recent ATR into three volatility regimes and feeds the assigned centroid into an adaptive SuperTrend."

# @param atr_length int 10 ATR length range=1:1000:1
# @param supertrend_factor float 3 SuperTrend factor range=0:100:0.1
# @param training_length int 100 K-Means training length range=3:2000:1
# @param high_volatility_guess float 0.75 Initial high-volatility range guess range=0:1:0.01
# @param medium_volatility_guess float 0.5 Initial medium-volatility range guess range=0:1:0.01
# @param low_volatility_guess float 0.25 Initial low-volatility range guess range=0:1:0.01
# @param line_transparency int 70 Trend-line transparency range=0:100:1
# @param fill_transparency int 95 Trend-fill transparency range=0:100:1
# @param bullish_color color #00FFBB Bullish color
# @param bearish_color color #FF1100 Bearish color
# @param show_cluster_markers bool true Show compact per-bar cluster markers

atr_length = min(1000, max(1, int(params.get("atr_length", 10))))
supertrend_factor = min(100.0, max(0.0, float(params.get("supertrend_factor", 3.0))))
training_length = min(2000, max(3, int(params.get("training_length", 100))))
high_guess = min(1.0, max(0.0, float(params.get("high_volatility_guess", 0.75))))
medium_guess = min(1.0, max(0.0, float(params.get("medium_volatility_guess", 0.5))))
low_guess = min(1.0, max(0.0, float(params.get("low_volatility_guess", 0.25))))
line_transparency = min(100, max(0, int(params.get("line_transparency", 70))))
fill_transparency = min(100, max(0, int(params.get("fill_transparency", 95))))
bullish_color = str(params.get("bullish_color", "#00FFBB"))
bearish_color = str(params.get("bearish_color", "#FF1100"))
show_cluster_markers = bool(params.get("show_cluster_markers", True))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _rgba(color, transparency):
    value = str(color).strip().lstrip("#")
    if len(value) != 6:
        return str(color)
    try:
        red, green, blue = (int(value[index:index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return str(color)
    opacity = 1.0 - min(100.0, max(0.0, float(transparency))) / 100.0
    return f"rgba({red},{green},{blue},{opacity:.3f})"


true_range = np.full(n, np.nan, dtype=float)
if n:
    true_range[0] = high[0] - low[0]
for index in range(1, n):
    true_range[index] = max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))

volatility = np.full(n, np.nan, dtype=float)
if n >= atr_length:
    previous_atr = float(np.mean(true_range[:atr_length]))
    volatility[atr_length - 1] = previous_atr
    for index in range(atr_length, n):
        previous_atr = (previous_atr * (atr_length - 1.0) + true_range[index]) / atr_length
        volatility[index] = previous_atr

high_centroid = np.full(n, np.nan, dtype=float)
medium_centroid = np.full(n, np.nan, dtype=float)
low_centroid = np.full(n, np.nan, dtype=float)
high_size = np.zeros(n, dtype=int)
medium_size = np.zeros(n, dtype=int)
low_size = np.zeros(n, dtype=int)
iteration_count = np.zeros(n, dtype=int)
cluster = np.full(n, -1, dtype=int)
assigned_centroid = np.full(n, np.nan, dtype=float)

for index in range(training_length - 1, n):
    current_window = volatility[index - training_length + 1:index + 1]
    finite_window = current_window[np.isfinite(current_window)]
    if not len(finite_window) or not np.isfinite(volatility[index]) or volatility[index] <= 0.0:
        continue
    upper = float(np.max(finite_window)); lower = float(np.min(finite_window))
    centroids = np.array([lower + (upper - lower) * high_guess, lower + (upper - lower) * medium_guess, lower + (upper - lower) * low_guess], dtype=float)
    if len(np.unique(centroids)) < 3:
        centroids = np.percentile(finite_window, [75.0, 50.0, 25.0], method="linear").astype(float)
    if len(np.unique(centroids)) < 3:
        shared = float(np.mean(finite_window))
        centroids = np.array([shared, shared, shared], dtype=float)
        sizes = np.array([len(finite_window), 0, 0], dtype=int)
        high_centroid[index], medium_centroid[index], low_centroid[index] = centroids
        high_size[index], medium_size[index], low_size[index] = sizes
        iteration_count[index] = 1
        cluster[index] = 0
        assigned_centroid[index] = shared
        continue
    sizes = np.zeros(3, dtype=int)
    for iteration in range(1, 101):
        groups = [[], [], []]
        for value in current_window:
            if not np.isfinite(value):
                continue
            distances = np.abs(value - centroids)
            smallest = float(np.min(distances))
            winners = np.flatnonzero(distances == smallest)
            if len(winners) == 1:
                groups[int(winners[0])].append(float(value))
        new_centroids = np.array([float(np.mean(group)) if group else np.nan for group in groups], dtype=float)
        sizes = np.array([len(group) for group in groups], dtype=int)
        if not np.all(np.isfinite(new_centroids)):
            ordered = np.sort(finite_window)
            low_group, medium_group, high_group = np.array_split(ordered, 3)
            groups = [high_group, medium_group, low_group]
            new_centroids = np.array([float(np.mean(group)) for group in groups], dtype=float)
            sizes = np.array([len(group) for group in groups], dtype=int)
        iteration_count[index] = iteration
        if np.array_equal(new_centroids, centroids, equal_nan=True):
            centroids = new_centroids
            break
        centroids = new_centroids
    high_centroid[index], medium_centroid[index], low_centroid[index] = centroids
    high_size[index], medium_size[index], low_size[index] = sizes
    if np.all(np.isfinite(centroids)):
        current_cluster = int(np.argmin(np.abs(volatility[index] - centroids)))
        cluster[index] = current_cluster
        assigned_centroid[index] = centroids[current_cluster]

upper_band = np.full(n, np.nan, dtype=float)
lower_band = np.full(n, np.nan, dtype=float)
supertrend = np.full(n, np.nan, dtype=float)
direction = np.zeros(n, dtype=int)
midpoint = (high + low) * 0.5
for index in range(n):
    atr_value = assigned_centroid[index]
    if not np.isfinite(atr_value):
        direction[index] = 1
        continue
    raw_upper = midpoint[index] + supertrend_factor * atr_value
    raw_lower = midpoint[index] - supertrend_factor * atr_value
    previous_lower = lower_band[index - 1] if index > 0 and np.isfinite(lower_band[index - 1]) else 0.0
    previous_upper = upper_band[index - 1] if index > 0 and np.isfinite(upper_band[index - 1]) else 0.0
    previous_close = close[index - 1] if index > 0 else np.nan
    lower_band[index] = raw_lower if raw_lower > previous_lower or (np.isfinite(previous_close) and previous_close < previous_lower) else previous_lower
    upper_band[index] = raw_upper if raw_upper < previous_upper or (np.isfinite(previous_close) and previous_close > previous_upper) else previous_upper
    previous_assigned = assigned_centroid[index - 1] if index > 0 else np.nan
    previous_supertrend = supertrend[index - 1] if index > 0 else np.nan
    if not np.isfinite(previous_assigned):
        direction[index] = 1
    elif previous_supertrend == previous_upper:
        direction[index] = -1 if close[index] > upper_band[index] else 1
    else:
        direction[index] = 1 if close[index] < lower_band[index] else -1
    supertrend[index] = lower_band[index] if direction[index] == -1 else upper_band[index]

trend_bull = np.isfinite(supertrend) & (close > supertrend)
trend_bear = np.isfinite(supertrend) & (close < supertrend)
bullish_shift = np.zeros(n, dtype=bool)
bearish_shift = np.zeros(n, dtype=bool)
high_volatility_shift = np.zeros(n, dtype=bool)
medium_volatility_shift = np.zeros(n, dtype=bool)
low_volatility_shift = np.zeros(n, dtype=bool)
for index in range(1, n):
    bullish_shift[index] = direction[index] < 0 and direction[index - 1] >= 0
    bearish_shift[index] = direction[index] > 0 and direction[index - 1] <= 0
    high_volatility_shift[index] = cluster[index] == 0 and cluster[index - 1] != 0
    medium_volatility_shift[index] = cluster[index] == 1 and cluster[index - 1] != 1
    low_volatility_shift[index] = cluster[index] == 2 and cluster[index - 1] != 2

body_middle = (open_price + close) * 0.5
line_up_color = _rgba(bullish_color, line_transparency)
line_down_color = _rgba(bearish_color, line_transparency)
up_line = [float(supertrend[index]) if trend_bull[index] else None for index in range(n)]
down_line = [float(supertrend[index]) if trend_bear[index] else None for index in range(n)]
up_cloud = [{"upper": float(max(body_middle[index], supertrend[index])), "lower": float(min(body_middle[index], supertrend[index])), "color": bullish_color, "topColor": _rgba(bullish_color, fill_transparency), "bottomColor": line_up_color} if trend_bull[index] else None for index in range(n)]
down_cloud = [{"upper": float(max(body_middle[index], supertrend[index])), "lower": float(min(body_middle[index], supertrend[index])), "color": bearish_color, "topColor": line_down_color, "bottomColor": _rgba(bearish_color, fill_transparency)} if trend_bear[index] else None for index in range(n)]

atr_seven = np.full(n, np.nan, dtype=float)
if n >= 7:
    current_atr = float(np.mean(true_range[:7])); atr_seven[6] = current_atr
    for index in range(7, n):
        current_atr = (current_atr * 6.0 + true_range[index]) / 7.0
        atr_seven[index] = current_atr
cluster_markers = []
for index in range(n):
    if show_cluster_markers and cluster[index] >= 0 and np.all(np.isfinite([supertrend[index], atr_seven[index]])):
        price = supertrend[index] + atr_seven[index] if direction[index] > 0 else supertrend[index] - atr_seven[index]
        marker_color = bearish_color if direction[index] > 0 else bullish_color
        cluster_markers.append({"value": float(price), "color": _rgba(marker_color, 30 + cluster[index] * 30), "text": str(3 - cluster[index]), "size": 2})
    else:
        cluster_markers.append(None)

plots = [
    {"name": "Bullish SuperTrend", "data": up_line, "color": line_up_color, "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Bearish SuperTrend", "data": down_line, "color": line_down_color, "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Bullish Trend Fill", "title": False, "data": up_cloud, "color": bullish_color, "type": "cloud", "opacity": 1.0, "overlay": True},
    {"name": "Bearish Trend Fill", "title": False, "data": down_cloud, "color": bearish_color, "type": "cloud", "opacity": 1.0, "overlay": True},
    {"name": "Volatility Cluster", "title": False, "data": cluster_markers, "color": bullish_color, "type": "scatter", "size": 2, "overlay": True},
]
signals = [
    {"type": "buy", "name": "Bullish Trend Shift", "text": "▲", "data": [float(supertrend[index]) if bullish_shift[index] else None for index in range(n)], "color": bullish_color, "renderMode": "events"},
    {"type": "sell", "name": "Bearish Trend Shift", "text": "▼", "data": [float(supertrend[index]) if bearish_shift[index] else None for index in range(n)], "color": bearish_color, "renderMode": "events"},
    {"type": "buy", "name": "High Volatility", "text": "", "data": [float(supertrend[index]) if high_volatility_shift[index] else None for index in range(n)], "color": "rgba(0,0,0,0)", "renderMode": "hidden"},
    {"type": "buy", "name": "Medium Volatility", "text": "", "data": [float(supertrend[index]) if medium_volatility_shift[index] else None for index in range(n)], "color": "rgba(0,0,0,0)", "renderMode": "hidden"},
    {"type": "buy", "name": "Low Volatility", "text": "", "data": [float(supertrend[index]) if low_volatility_shift[index] else None for index in range(n)], "color": "rgba(0,0,0,0)", "renderMode": "hidden"},
]

latest_cluster = int(cluster[-1]) if n else -1
tables = [{
    "title": "Volatility Clusters", "position": "top-right", "compact": True,
    "rows": [
        ["Level", "Centroid", "Size", "Current"],
        ["3 (High)", f"{high_centroid[-1]:.2f}" if n and np.isfinite(high_centroid[-1]) else "N/A", str(int(low_size[-1])) if n else "0", "HIGH" if latest_cluster == 0 else ""],
        ["2 (Medium)", f"{medium_centroid[-1]:.2f}" if n and np.isfinite(medium_centroid[-1]) else "N/A", str(int(medium_size[-1])) if n else "0", "MEDIUM" if latest_cluster == 1 else ""],
        ["1 (Low)", f"{low_centroid[-1]:.2f}" if n and np.isfinite(low_centroid[-1]) else "N/A", str(int(high_size[-1])) if n else "0", "LOW" if latest_cluster == 2 else ""],
    ],
}]

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": signals, "layers": [], "tables": tables,
    "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "trueRange": _finite(true_range), "volatility": _finite(volatility), "highCentroid": _finite(high_centroid),
        "mediumCentroid": _finite(medium_centroid), "lowCentroid": _finite(low_centroid), "highSize": high_size.tolist(),
        "mediumSize": medium_size.tolist(), "lowSize": low_size.tolist(), "iterations": iteration_count.tolist(),
        "cluster": cluster.tolist(), "assignedCentroid": _finite(assigned_centroid), "upperBand": _finite(upper_band),
        "lowerBand": _finite(lower_band), "supertrend": _finite(supertrend), "direction": direction.tolist(),
        "bullishShift": bullish_shift.tolist(), "bearishShift": bearish_shift.tolist(),
        "highVolatilityShift": high_volatility_shift.tolist(), "mediumVolatilityShift": medium_volatility_shift.tolist(), "lowVolatilityShift": low_volatility_shift.tolist(),
    },
}
