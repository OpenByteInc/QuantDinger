# ============================================================
# QuantDinger Adaptive Range Filter
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Adaptive Range Filter"
my_indicator_description = "Filters minor price movement with selectable range scales, two movement algorithms, optional smoothing, and change-sampled averaging."

# @param filter_type str Type-1 Filter algorithm values=Type-1,Type-2
# @param movement_source str Close Movement source values=Close,Wicks
# @param range_size float 2.618 Range size range=0.0000001:1000000:0.001
# @param range_scale str Average-Change Range scale values=Points,Pips,Ticks,Percent-of-Price,ATR,Average-Change,Standard-Deviation,Absolute
# @param range_period int 14 Dynamic range period range=1:10000:1
# @param smooth_range bool true Smooth dynamic range
# @param smoothing_period int 27 Range smoothing period range=1:10000:1
# @param average_filter_changes bool false Average filter changes
# @param average_samples int 2 Number of changes to average range=1:10000:1
# @param point_value float 1.0 Point value range=0.0000001:1000000:0.0001
# @param minimum_tick float 0.01 Minimum tick range=0.0000001:1000000:0.0001
# @param rising_color str #05FF9B Rising color
# @param falling_color str #FF0583 Falling color
# @param neutral_color str #CCCCCC Neutral color

filter_type = str(params.get("filter_type", "Type-1")).replace("-", " ")
if filter_type not in ("Type 1", "Type 2"):
    filter_type = "Type 1"
movement_source = str(params.get("movement_source", "Close"))
if movement_source not in ("Close", "Wicks"):
    movement_source = "Close"
range_size = min(1000000.0, max(0.0000001, float(params.get("range_size", 2.618))))
range_scale = str(params.get("range_scale", "Average-Change")).replace("-", " ")
if range_scale == "Percent of Price":
    range_scale = "% of Price"
valid_scales = ("Points", "Pips", "Ticks", "% of Price", "ATR", "Average Change", "Standard Deviation", "Absolute")
if range_scale not in valid_scales:
    range_scale = "Average Change"
range_period = min(10000, max(1, int(params.get("range_period", 14))))
smooth_range = bool(params.get("smooth_range", True))
smoothing_period = min(10000, max(1, int(params.get("smoothing_period", 27))))
average_filter_changes = bool(params.get("average_filter_changes", False))
average_samples = min(10000, max(1, int(params.get("average_samples", 2))))
point_value = min(1000000.0, max(0.0000001, float(params.get("point_value", 1.0))))
minimum_tick = min(1000000.0, max(0.0000001, float(params.get("minimum_tick", 0.01))))
rising_color = str(params.get("rising_color", "#05FF9B"))
falling_color = str(params.get("falling_color", "#FF0583"))
neutral_color = str(params.get("neutral_color", "#CCCCCC"))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _conditional_ema(values, conditions, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    previous = np.nan
    for index, (value, condition) in enumerate(zip(values, conditions)):
        if condition:
            if not np.isfinite(previous):
                previous = float(value) if np.isfinite(value) else np.nan
            else:
                previous = (float(value) - previous) * alpha + previous
        result[index] = previous
    return result


def _rolling_population_deviation(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    samples = []
    for index, value in enumerate(values):
        samples.append(float(value))
        if len(samples) > length:
            samples.pop(0)
        mean = sum(samples) / len(samples)
        variance = max(0.0, sum(item * item for item in samples) / len(samples) - mean * mean)
        result[index] = variance ** 0.5
    return result


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
all_bars = np.ones(n, dtype=bool)

true_range = np.empty(n, dtype=float)
for index in range(n):
    true_range[index] = high[index] - low[index] if index == 0 else max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
mid_price = (high + low) / 2.0 if movement_source == "Wicks" else close.copy()
movement = np.full(n, np.nan, dtype=float)
if n > 1:
    movement[1:] = np.abs(np.diff(mid_price))
average_true_range = _conditional_ema(true_range, all_bars, range_period)
average_change = _conditional_ema(movement, all_bars, range_period)
standard_deviation = _rolling_population_deviation(mid_price, range_period)

if range_scale == "Pips":
    raw_range = np.full(n, range_size * 0.0001, dtype=float)
elif range_scale == "Points":
    raw_range = np.full(n, range_size * point_value, dtype=float)
elif range_scale == "Ticks":
    raw_range = np.full(n, range_size * minimum_tick, dtype=float)
elif range_scale == "% of Price":
    raw_range = close * range_size / 100.0
elif range_scale == "ATR":
    raw_range = average_true_range * range_size
elif range_scale == "Average Change":
    raw_range = average_change * range_size
elif range_scale == "Standard Deviation":
    raw_range = standard_deviation * range_size
else:
    raw_range = np.full(n, range_size, dtype=float)

smoothed_range = _conditional_ema(raw_range, all_bars, smoothing_period)
effective_range = smoothed_range if smooth_range else raw_range.copy()
movement_high = high if movement_source == "Wicks" else close
movement_low = low if movement_source == "Wicks" else close
raw_filter = np.full(n, np.nan, dtype=float)
raw_upper = np.full(n, np.nan, dtype=float)
raw_lower = np.full(n, np.nan, dtype=float)
current = float((movement_high[0] + movement_low[0]) / 2.0) if n else np.nan
for index in range(n):
    prior = current
    width = effective_range[index]
    if np.isfinite(width):
        if filter_type == "Type 1":
            if movement_high[index] - width > prior:
                current = float(movement_high[index] - width)
            if movement_low[index] + width < prior:
                current = float(movement_low[index] + width)
        elif width > 0.0:
            if movement_high[index] >= prior + width:
                current = float(prior + np.floor(abs(movement_high[index] - prior) / width) * width)
            if movement_low[index] <= prior - width:
                current = float(prior - np.floor(abs(movement_low[index] - prior) / width) * width)
    raw_filter[index] = current
    raw_upper[index] = current + width if np.isfinite(width) else np.nan
    raw_lower[index] = current - width if np.isfinite(width) else np.nan

filter_changed = np.zeros(n, dtype=bool)
if n > 1:
    filter_changed[1:] = raw_filter[1:] != raw_filter[:-1]
averaged_filter = _conditional_ema(raw_filter, filter_changed, average_samples)
averaged_upper = _conditional_ema(raw_upper, filter_changed, average_samples)
averaged_lower = _conditional_ema(raw_lower, filter_changed, average_samples)
filtered = averaged_filter if average_filter_changes else raw_filter
upper_band = averaged_upper if average_filter_changes else raw_upper
lower_band = averaged_lower if average_filter_changes else raw_lower

direction = np.zeros(n, dtype=int)
for index in range(1, n):
    direction[index] = direction[index - 1]
    if np.all(np.isfinite([filtered[index], filtered[index - 1]])):
        if filtered[index] > filtered[index - 1]:
            direction[index] = 1
        elif filtered[index] < filtered[index - 1]:
            direction[index] = -1

filter_colors = [rising_color if value == 1 else falling_color if value == -1 else neutral_color for value in direction]
bar_colors = []
for index in range(n):
    prior_close = close[index - 1] if index else np.nan
    if direction[index] == 1 and np.isfinite(filtered[index]) and close[index] > filtered[index]:
        bar_colors.append(rising_color if np.isfinite(prior_close) and close[index] > prior_close else "#00B36B")
    elif direction[index] == -1 and np.isfinite(filtered[index]) and close[index] < filtered[index]:
        bar_colors.append(falling_color if np.isfinite(prior_close) and close[index] < prior_close else "#B8005D")
    else:
        bar_colors.append(neutral_color)

upper_cloud = []
lower_cloud = []
for index in range(n):
    if np.all(np.isfinite([upper_band[index], filtered[index], lower_band[index]])):
        upper_cloud.append({"upper": float(upper_band[index]), "lower": float(filtered[index]), "color": "#00B36B", "opacity": 0.15})
        lower_cloud.append({"upper": float(filtered[index]), "lower": float(lower_band[index]), "color": "#B8005D", "opacity": 0.15})
    else:
        upper_cloud.append(None)
        lower_cloud.append(None)

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Filter", "data": _finite(filtered), "color": filter_colors, "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "High Band Fill", "title": False, "data": upper_cloud, "color": "#00B36B", "type": "cloud", "overlay": True},
        {"name": "Low Band Fill", "title": False, "data": lower_cloud, "color": "#B8005D", "type": "cloud", "overlay": True},
    ],
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": bar_colors,
    "background_colors": [None] * n,
    "calculatedVars": {
        "trueRange": _finite(true_range),
        "midPrice": _finite(mid_price),
        "movement": _finite(movement),
        "averageTrueRange": _finite(average_true_range),
        "averageChange": _finite(average_change),
        "standardDeviation": _finite(standard_deviation),
        "rawRange": _finite(raw_range),
        "smoothedRange": _finite(smoothed_range),
        "effectiveRange": _finite(effective_range),
        "rawFilter": _finite(raw_filter),
        "rawUpper": _finite(raw_upper),
        "rawLower": _finite(raw_lower),
        "filterChanged": filter_changed.tolist(),
        "averagedFilter": _finite(averaged_filter),
        "averagedUpper": _finite(averaged_upper),
        "averagedLower": _finite(averaged_lower),
        "filter": _finite(filtered),
        "upperBand": _finite(upper_band),
        "lowerBand": _finite(lower_band),
        "direction": [int(value) for value in direction],
    },
}
