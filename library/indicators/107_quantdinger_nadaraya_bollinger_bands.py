# ============================================================
# QuantDinger Nadaraya Bollinger Bands
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Nadaraya Bollinger Bands"
my_indicator_description = (
    "Builds four Bollinger envelopes from typical price, applies Gaussian "
    "Nadaraya-Watson smoothing, and marks first-band price excursions."
)

# @param smoothing_factor float 6.0 Gaussian smoothing factor range=0.5:50:0.5
# @param repaint bool false Use centered repainting smoother
# @param short_period int 20 Short band period range=2:300:1
# @param short_deviation float 3.0 Short band deviation range=0.1:10:0.05
# @param medium_period int 75 Medium band period range=2:500:1
# @param medium_deviation float 4.0 Medium band deviation range=0.1:10:0.05
# @param long_period int 100 Long band period range=2:700:1
# @param long_deviation float 4.25 Long band deviation range=0.1:10:0.05
# @param label_source str Close Label source values=Pivots,Close,High/Low
# @param show_labels bool true Show band-break labels
# @param show_lines bool true Show first band lines
# @param line_width int 2 Band line width range=1:5:1
# @param show_level1 bool false Show level-one clouds
# @param show_level2 bool false Show level-two clouds
# @param bearish_color str #F23645 Bearish band color
# @param bullish_color str #089981 Bullish band color
# @param level1_opacity float 0.10 Level-one cloud opacity range=0.02:0.80:0.01
# @param level2_opacity float 0.15 Level-two cloud opacity range=0.02:0.80:0.01
# @param notifications bool false Emit band-break events when labels are hidden

smoothing_factor = min(50.0, max(0.5, float(params.get("smoothing_factor", 6.0))))
repaint = bool(params.get("repaint", False))
short_period = min(300, max(2, int(params.get("short_period", 20))))
short_deviation = min(10.0, max(0.1, float(params.get("short_deviation", 3.0))))
medium_period = min(500, max(2, int(params.get("medium_period", 75))))
medium_deviation = min(10.0, max(0.1, float(params.get("medium_deviation", 4.0))))
long_period = min(700, max(2, int(params.get("long_period", 100))))
long_deviation = min(10.0, max(0.1, float(params.get("long_deviation", 4.25))))
label_source = str(params.get("label_source", "Close"))
if label_source not in ("Pivots", "Close", "High/Low"):
    label_source = "Close"
show_labels = bool(params.get("show_labels", True))
show_lines = bool(params.get("show_lines", True))
line_width = min(5, max(1, int(params.get("line_width", 2))))
show_level1 = bool(params.get("show_level1", False))
show_level2 = bool(params.get("show_level2", False))
bearish_color = str(params.get("bearish_color", "#F23645"))
bullish_color = str(params.get("bullish_color", "#089981"))
level1_opacity = min(0.80, max(0.02, float(params.get("level1_opacity", 0.10))))
level2_opacity = min(0.80, max(0.02, float(params.get("level2_opacity", 0.15))))
notifications = bool(params.get("notifications", False))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)
typical = (high + low + close) / 3.0


def rolling_mean_std(values, period):
    source = np.asarray(values, dtype=float)
    mean = np.full(len(source), np.nan, dtype=float)
    deviation = np.full(len(source), np.nan, dtype=float)
    for index in range(period - 1, len(source)):
        window = source[index - period + 1 : index + 1]
        if np.all(np.isfinite(window)):
            mean[index] = float(np.mean(window))
            deviation[index] = float(np.std(window, ddof=0))
    return mean, deviation


def bollinger(period, factor):
    basis, deviation = rolling_mean_std(typical, period)
    return basis + factor * deviation, basis - factor * deviation


def causal_gaussian(values):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    lags = np.arange(500, dtype=float)
    weights = np.exp(-(lags * lags) / (2.0 * smoothing_factor * smoothing_factor))
    for index in range(len(source)):
        count = min(500, index + 1)
        window = source[index - count + 1 : index + 1][::-1]
        valid = np.isfinite(window)
        if np.any(valid):
            active = weights[:count][valid]
            result[index] = float(np.dot(window[valid], active) / np.sum(active))
    return result


def centered_gaussian(values):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    radius = min(499, max(2, int(np.ceil(smoothing_factor * 4.0))))
    for index in range(len(source)):
        start = max(0, index - radius)
        end = min(len(source), index + radius + 1)
        window = source[start:end]
        offsets = np.arange(start, end, dtype=float) - float(index)
        weights = np.exp(-(offsets * offsets) / (2.0 * smoothing_factor * smoothing_factor))
        valid = np.isfinite(window)
        if np.any(valid):
            result[index] = float(np.dot(window[valid], weights[valid]) / np.sum(weights[valid]))
    return result


def smooth(values):
    return centered_gaussian(values) if repaint else causal_gaussian(values)


def finite_list(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def cloud_data(upper_values, lower_values, enabled):
    points = []
    for index in range(n):
        if enabled and np.isfinite(upper_values[index]) and np.isfinite(lower_values[index]):
            points.append(
                {
                    "upper": float(max(upper_values[index], lower_values[index])),
                    "lower": float(min(upper_values[index], lower_values[index])),
                }
            )
        else:
            points.append(None)
    return points


upper_1, lower_1 = bollinger(short_period, short_deviation)
upper_2, lower_2 = bollinger(medium_period, short_deviation)
upper_3, lower_3 = bollinger(long_period, medium_deviation)
upper_4, lower_4 = bollinger(long_period, long_deviation)

smooth_upper_1 = smooth(upper_1)
smooth_lower_1 = smooth(lower_1)
smooth_upper_2 = smooth(upper_2)
smooth_lower_2 = smooth(lower_2)
smooth_upper_3 = smooth(upper_3)
smooth_lower_3 = smooth(lower_3)
smooth_upper_4 = smooth(upper_4)
smooth_lower_4 = smooth(lower_4)

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

spacing = np.full(n, np.nan, dtype=float)
atr_period = 300
if n >= atr_period and np.all(np.isfinite(true_range[:atr_period])):
    spacing[atr_period - 1] = float(np.mean(true_range[:atr_period]))
    for index in range(atr_period, n):
        spacing[index] = (
            spacing[index - 1] * (atr_period - 1) + true_range[index]
        ) / atr_period

upper_break = np.full(n, False, dtype=bool)
lower_break = np.full(n, False, dtype=bool)
pivot_radius = 4

if label_source == "Pivots":
    for confirmed_at in range(pivot_radius * 2, n):
        pivot = confirmed_at - pivot_radius
        high_window = high[pivot - pivot_radius : pivot + pivot_radius + 1]
        low_window = low[pivot - pivot_radius : pivot + pivot_radius + 1]
        if np.isfinite(smooth_upper_1[pivot]) and high[pivot] >= smooth_upper_1[pivot] and high[pivot] >= float(np.max(high_window)):
            upper_break[pivot] = True
        if np.isfinite(smooth_lower_1[pivot]) and low[pivot] <= smooth_lower_1[pivot] and low[pivot] <= float(np.min(low_window)):
            lower_break[pivot] = True
else:
    upper_source = close if label_source == "Close" else high
    lower_source = close if label_source == "Close" else low
    for index in range(n):
        if not np.isfinite(smooth_upper_1[index]) or not np.isfinite(smooth_lower_1[index]):
            continue
        prior_upper = index == 0 or not np.isfinite(upper_source[index - 1]) or upper_source[index - 1] <= smooth_upper_1[index]
        prior_lower = index == 0 or not np.isfinite(lower_source[index - 1]) or lower_source[index - 1] >= smooth_lower_1[index]
        upper_break[index] = upper_source[index] >= smooth_upper_1[index] and (index == 0 or prior_upper)
        lower_break[index] = lower_source[index] <= smooth_lower_1[index] and (index == 0 or prior_lower)

emit_events = not repaint and (show_labels or notifications)
upper_marks = [
    float(high[index] + spacing[index] * 1.01)
    if emit_events and upper_break[index] and np.isfinite(spacing[index])
    else None
    for index in range(n)
]
lower_marks = [
    float(low[index] - spacing[index] * 1.01)
    if emit_events and lower_break[index] and np.isfinite(spacing[index])
    else None
    for index in range(n)
]

line_active = show_lines or repaint
upper_line = finite_list(smooth_upper_1) if line_active else [None for _ in range(n)]
lower_line = finite_list(smooth_lower_1) if line_active else [None for _ in range(n)]


def latest_finite(values):
    for value in values[::-1]:
        if np.isfinite(value):
            return float(value)
    return None


last_index = n - 1

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Upper Nadaraya Band", "data": upper_line, "color": bearish_color, "type": "line", "lineWidth": line_width, "overlay": True},
        {"name": "Lower Nadaraya Band", "data": lower_line, "color": bullish_color, "type": "line", "lineWidth": line_width, "overlay": True},
        {"name": "Upper Level One", "title": False, "data": cloud_data(smooth_upper_1, smooth_upper_2, show_level1), "color": bearish_color, "type": "cloud", "opacity": level1_opacity, "overlay": True},
        {"name": "Lower Level One", "title": False, "data": cloud_data(smooth_lower_1, smooth_lower_2, show_level1), "color": bullish_color, "type": "cloud", "opacity": level1_opacity, "overlay": True},
        {"name": "Upper Level Two", "title": False, "data": cloud_data(smooth_upper_2, smooth_upper_3, show_level2), "color": bearish_color, "type": "cloud", "opacity": level2_opacity, "overlay": True},
        {"name": "Lower Level Two", "title": False, "data": cloud_data(smooth_lower_2, smooth_lower_3, show_level2), "color": bullish_color, "type": "cloud", "opacity": level2_opacity, "overlay": True},
    ],
    "signals": [
        {"type": "sell", "text": "", "name": "Upper Band Break", "data": upper_marks, "color": bearish_color, "renderMode": "events"},
        {"type": "buy", "text": "", "name": "Lower Band Break", "data": lower_marks, "color": bullish_color, "renderMode": "events"},
    ],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "layers": [],
    "calculatedVars": {
        "labelSource": label_source,
        "repainting": repaint,
        "upperBand": latest_finite(smooth_upper_1),
        "lowerBand": latest_finite(smooth_lower_1),
        "upperBreak": bool(upper_break[last_index]) if n else False,
        "lowerBreak": bool(lower_break[last_index]) if n else False,
    },
}
