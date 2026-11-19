# ============================================================
# QuantDinger Fair Value Bands
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Fair Value Bands"
my_indicator_description = (
    "Builds a configurable fair-value basis, candle-derived threshold bands, "
    "pivot-derived deviation bands, cross events, and time/volume distribution metrics."
)

# @param highlight_metrics bool false Color bars by fair-value region
# @param threshold_width float 1.0 Threshold band width range=0.0:10.0:0.1
# @param deviation_width float 1.0 Deviation band width range=0.0:10.0:0.1
# @param vwap_anchor str 1D VWAP anchor values=1H,4H,1D,1W,1M
# @param basis_source str OHLC4 Fair-value source values=OHLC4,Open,High,Low,Close,HL2,HLC3,HLCC4
# @param smoothing_type str SMA Smoothing type values=SMA,EMA,HMA,RMA,WMA,VWMA,Median,VWAP
# @param length int 20 Smoothing length range=1:1000:1
# @param trend_mode str Cross Trend mode values=Cross,Direction
# @param threshold_up_source str Low Up-threshold source values=OHLC4,Open,High,Low,Close,HL2,HLC3,HLCC4
# @param threshold_down_source str High Down-threshold source values=OHLC4,Open,High,Low,Close,HL2,HLC3,HLCC4
# @param cross_source_1 str high Cross 1 source values=open,high,low,close,hl2,hlc3,ohlc4
# @param cross_method_1 str Above Cross 1 method values=Above,Below
# @param cross_reference_1 str +2 Cross 1 reference values=Off,+1,+2,+3,-1,-2,-3
# @param cross_source_2 str low Cross 2 source values=open,high,low,close,hl2,hlc3,ohlc4
# @param cross_method_2 str Below Cross 2 method values=Above,Below
# @param cross_reference_2 str -2 Cross 2 reference values=Off,+1,+2,+3,-1,-2,-3
# @param cross_source_3 str close Cross 3 source values=open,high,low,close,hl2,hlc3,ohlc4
# @param cross_method_3 str Above Cross 3 method values=Above,Below
# @param cross_reference_3 str Off Cross 3 reference values=Off,+1,+2,+3,-1,-2,-3
# @param cross_source_4 str close Cross 4 source values=open,high,low,close,hl2,hlc3,ohlc4
# @param cross_method_4 str Above Cross 4 method values=Above,Below
# @param cross_reference_4 str Off Cross 4 reference values=Off,+1,+2,+3,-1,-2,-3
# @param cross_above_color str #2962FF Above-cross color
# @param cross_below_color str #E040FB Below-cross color
# @param up_line_color str #FFFFFF Uptrend basis color
# @param down_line_color str #FF5252 Downtrend basis color
# @param table_position str top-right Statistics position values=top-right,bottom-right,bottom-left,top-left
# @param table_size int 3 Table size range=1:5:1


def clamp(value, lower, upper):
    return min(upper, max(lower, value))


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def pine_ema(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan)
    alpha = 2.0 / (length + 1.0)
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def pine_rma(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan)
    seed = []
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed.append(float(value))
            if len(seed) < length:
                continue
            previous = float(np.mean(seed[-length:]))
        else:
            previous = (previous * (length - 1.0) + float(value)) / length
        result[index] = previous
    return result


def rolling_wma(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan)
    weights = np.arange(1, length + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(length - 1, len(source)):
        window = source[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.sum(window * weights) / denominator)
    return result


def rolling_cross(first, second, above=True):
    result = np.zeros(len(first), dtype=bool)
    for index in range(1, len(first)):
        if not all(np.isfinite(value) for value in (first[index], first[index - 1], second[index], second[index - 1])):
            continue
        if above:
            result[index] = first[index] > second[index] and first[index - 1] <= second[index - 1]
        else:
            result[index] = first[index] < second[index] and first[index - 1] >= second[index - 1]
    return result


def median_value(values):
    ordered = sorted(float(value) for value in values if np.isfinite(value))
    count = len(ordered)
    if count == 0:
        return np.nan
    middle = count // 2
    return ordered[middle] if count % 2 else (ordered[middle - 1] + ordered[middle]) * 0.5


def timeframe_key(timestamp, timeframe_name):
    seconds = float(timestamp) / 1000.0 if abs(float(timestamp)) > 100_000_000_000.0 else float(timestamp)
    name = str(timeframe_name).upper()
    if name.endswith("H"):
        hours = max(1, int(name[:-1] or 1))
        return int(seconds // (hours * 3600))
    moment = pd.Timestamp(seconds, unit="s", tz="UTC")
    if name.endswith("W"):
        iso = moment.isocalendar()
        return int(iso.year), int(iso.week)
    if name.endswith("M"):
        months = max(1, int(name[:-1] or 1))
        absolute_month = moment.year * 12 + moment.month - 1
        return absolute_month // months
    days = max(1, int(name[:-1] or 1)) if name.endswith("D") else 1
    return int(seconds // (days * 86400))


highlight_metrics = bool(params.get("highlight_metrics", False))
threshold_width = clamp(float(params.get("threshold_width", 1.0)), 0.0, 10.0)
deviation_width = clamp(float(params.get("deviation_width", 1.0)), 0.0, 10.0)
vwap_anchor = str(params.get("vwap_anchor", "1D"))
basis_source_name = str(params.get("basis_source", "OHLC4"))
smoothing_type = str(params.get("smoothing_type", "SMA"))
length = clamp(int(params.get("length", 20)), 1, 1000)
trend_mode = str(params.get("trend_mode", "Cross"))
threshold_up_source_name = str(params.get("threshold_up_source", "Low"))
threshold_down_source_name = str(params.get("threshold_down_source", "High"))
cross_source_names = [
    str(params.get("cross_source_1", "high")), str(params.get("cross_source_2", "low")),
    str(params.get("cross_source_3", "close")), str(params.get("cross_source_4", "close")),
]
cross_methods = [
    str(params.get("cross_method_1", "Above")), str(params.get("cross_method_2", "Below")),
    str(params.get("cross_method_3", "Above")), str(params.get("cross_method_4", "Above")),
]
cross_references = [
    str(params.get("cross_reference_1", "+2")), str(params.get("cross_reference_2", "-2")),
    str(params.get("cross_reference_3", "Off")), str(params.get("cross_reference_4", "Off")),
]
cross_above_color = str(params.get("cross_above_color", "#2962FF"))
cross_below_color = str(params.get("cross_below_color", "#E040FB"))
up_line_color = str(params.get("up_line_color", "#FFFFFF"))
down_line_color = str(params.get("down_line_color", "#FF5252"))
table_position = str(params.get("table_position", "top-right"))
table_size = clamp(int(params.get("table_size", 3)), 1, 5)

if smoothing_type not in ("SMA", "EMA", "HMA", "RMA", "WMA", "VWMA", "Median", "VWAP"):
    smoothing_type = "SMA"
if trend_mode not in ("Cross", "Direction"):
    trend_mode = "Cross"
if table_position not in ("top-right", "bottom-right", "bottom-left", "top-left"):
    table_position = "top-right"

df = df.copy()
if "time" not in df.columns:
    df["time"] = np.arange(len(df), dtype=float) * 3600.0
open_values = pd.to_numeric(df["open"], errors="coerce").to_numpy(dtype=float)
high_values = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype=float)
low_values = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype=float)
close_values = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
volume_values = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
time_values = pd.to_numeric(df["time"], errors="coerce").to_numpy(dtype=float)
n = len(df)
hl2 = (high_values + low_values) * 0.5
hlc3 = (high_values + low_values + close_values) / 3.0
hlcc4 = (high_values + low_values + close_values * 2.0) * 0.25
ohlc4 = (open_values + high_values + low_values + close_values) * 0.25

named_sources = {
    "Open": open_values, "High": high_values, "Low": low_values, "Close": close_values,
    "HL2": hl2, "HLC3": hlc3, "OHLC4": ohlc4,
    # The source selector's original branch spells HLC44, so HLCC4 falls back to OHLC4.
    "HLCC4": ohlc4,
}
standard_sources = {
    "open": open_values, "high": high_values, "low": low_values, "close": close_values,
    "hl2": hl2, "hlc3": hlc3, "ohlc4": ohlc4,
}
basis_source = named_sources.get(basis_source_name, ohlc4)
threshold_up_source = named_sources.get(threshold_up_source_name, ohlc4)
threshold_down_source = named_sources.get(threshold_down_source_name, ohlc4)

if smoothing_type == "SMA":
    fair_value = pd.Series(basis_source).rolling(length, min_periods=length).mean().to_numpy(dtype=float)
elif smoothing_type == "EMA":
    fair_value = pine_ema(basis_source, length)
elif smoothing_type == "RMA":
    fair_value = pine_rma(basis_source, length)
elif smoothing_type == "WMA":
    fair_value = rolling_wma(basis_source, length)
elif smoothing_type == "VWMA":
    numerator = pd.Series(basis_source * volume_values).rolling(length, min_periods=length).sum().to_numpy(dtype=float)
    denominator = pd.Series(volume_values).rolling(length, min_periods=length).sum().to_numpy(dtype=float)
    fair_value = np.divide(numerator, denominator, out=np.full(n, np.nan), where=denominator != 0.0)
elif smoothing_type == "Median":
    fair_value = np.full(n, np.nan)
    for index in range(length - 1, n):
        fair_value[index] = median_value(basis_source[index - length + 1:index + 1])
elif smoothing_type == "HMA":
    half = max(1, int(length / 2))
    root = max(1, int(round(np.sqrt(length))))
    fair_value = rolling_wma(2.0 * rolling_wma(basis_source, half) - rolling_wma(basis_source, length), root)
else:
    fair_value = np.full(n, np.nan)
    cumulative_value = 0.0
    cumulative_volume = 0.0
    previous_key = None
    for index in range(n):
        key = timeframe_key(time_values[index], vwap_anchor)
        if index == 0 or key != previous_key:
            cumulative_value = 0.0
            cumulative_volume = 0.0
        cumulative_value += float(basis_source[index]) * float(volume_values[index])
        cumulative_volume += float(volume_values[index])
        fair_value[index] = cumulative_value / cumulative_volume if cumulative_volume != 0.0 else np.nan
        previous_key = key

deviation_up_values = []
deviation_down_values = []
median_up_deviation = np.full(n, np.nan)
median_down_deviation = np.full(n, np.nan)
for index in range(n):
    valid = np.isfinite(fair_value[index]) and low_values[index] < fair_value[index] < high_values[index]
    deviation_up_values.append(high_values[index] / fair_value[index] if valid else np.nan)
    deviation_down_values.append(low_values[index] / fair_value[index] if valid else np.nan)
    if len(deviation_up_values) > 1001:
        deviation_up_values.pop(0)
        deviation_down_values.pop(0)
    median_up_deviation[index] = median_value(deviation_up_values)
    median_down_deviation[index] = median_value(deviation_down_values)

upper_band = fair_value * median_up_deviation
lower_band = fair_value * median_down_deviation
upper_threshold = fair_value + (upper_band - fair_value) * threshold_width
lower_threshold = fair_value - (fair_value - lower_band) * threshold_width

direction = np.zeros(n, dtype=int)
prior_direction = 0
for index in range(n):
    if trend_mode == "Cross":
        up_rule = np.isfinite(upper_threshold[index]) and threshold_up_source[index] > upper_threshold[index]
        down_rule = np.isfinite(lower_threshold[index]) and threshold_down_source[index] < lower_threshold[index]
    else:
        up_rule = index > 0 and np.isfinite(fair_value[index]) and np.isfinite(fair_value[index - 1]) and fair_value[index] > fair_value[index - 1]
        down_rule = index > 0 and np.isfinite(fair_value[index]) and np.isfinite(fair_value[index - 1]) and fair_value[index] < fair_value[index - 1]
    if down_rule:
        prior_direction = -1
    elif up_rule:
        prior_direction = 1
    direction[index] = prior_direction

ohlc_spread = np.divide(ohlc4, fair_value, out=np.full(n, np.nan), where=np.isfinite(fair_value) & (fair_value != 0.0))
pivot_up_event = np.full(n, np.nan)
pivot_down_event = np.full(n, np.nan)
for confirmation in range(10, n):
    center = confirmation - 5
    window = ohlc_spread[center - 5:center + 6]
    if np.all(np.isfinite(window)):
        if ohlc_spread[center] >= np.max(window):
            pivot_up_event[confirmation] = ohlc_spread[center]
        if ohlc_spread[center] <= np.min(window):
            pivot_down_event[confirmation] = ohlc_spread[center]

pivot_ups = []
pivot_downs = []
median_pivot_up = np.full(n, np.nan)
median_pivot_down = np.full(n, np.nan)
for index in range(n):
    if np.isfinite(upper_band[index]) and low_values[index] > upper_band[index]:
        pivot_ups.append(pivot_up_event[index])
        if len(pivot_ups) > 2001:
            pivot_ups.pop(0)
    elif np.isfinite(lower_band[index]) and high_values[index] < lower_band[index]:
        pivot_downs.append(pivot_down_event[index])
        if len(pivot_downs) > 2001:
            pivot_downs.pop(0)
    median_pivot_up[index] = median_value(pivot_ups)
    median_pivot_down[index] = median_value(pivot_downs)

pivot_band_up_base = fair_value * median_pivot_up
pivot_band_down_base = fair_value * median_pivot_down
up_spread = (pivot_band_up_base - fair_value) * deviation_width
down_spread = (fair_value - pivot_band_down_base) * deviation_width
pivot_up_1 = fair_value + up_spread
pivot_down_1 = fair_value - down_spread
pivot_up_2 = pivot_up_1 + up_spread
pivot_down_2 = pivot_down_1 - down_spread
pivot_up_3 = pivot_up_2 + up_spread
pivot_down_3 = pivot_down_2 - down_spread

volume_average = pd.Series(volume_values).rolling(20, min_periods=20).mean().to_numpy(dtype=float)
relative_volume = np.divide(volume_values, volume_average, out=np.zeros(n), where=np.isfinite(volume_average) & (volume_average != 0.0))
region_flags = np.zeros((6, n), dtype=bool)
region_counts = np.zeros((6, n), dtype=int)
region_volume = np.zeros((6, n), dtype=float)
running_counts = np.zeros(6, dtype=int)
running_volume = np.zeros(6, dtype=float)
for index in range(n):
    source = basis_source[index]
    bands = (pivot_up_2[index], pivot_up_1[index], fair_value[index], pivot_down_1[index], pivot_down_2[index])
    if all(np.isfinite(value) for value in bands):
        region_flags[0, index] = source > bands[0]
        region_flags[1, index] = source > bands[1] and source <= bands[0]
        region_flags[2, index] = source > bands[2] and source <= bands[1]
        region_flags[3, index] = source < bands[2] and source >= bands[3]
        region_flags[4, index] = source < bands[3] and source >= bands[4]
        region_flags[5, index] = source < bands[4]
    for region in range(6):
        if region_flags[region, index]:
            running_counts[region] += 1
            running_volume[region] += relative_volume[index]
        region_counts[region, index] = running_counts[region]
        region_volume[region, index] = running_volume[region]

deviation_cross_up = [rolling_cross(high_values, band, True) for band in (pivot_up_1, pivot_up_2, pivot_up_3)]
deviation_cross_down = [rolling_cross(low_values, band, False) for band in (pivot_down_1, pivot_down_2, pivot_down_3)]
fair_price_in_up = (direction == 1) & np.isfinite(upper_threshold) & (low_values <= upper_threshold)
fair_price_in_down = (direction == -1) & np.isfinite(lower_threshold) & (high_values >= lower_threshold)
new_trend_up = np.zeros(n, dtype=bool)
new_trend_down = np.zeros(n, dtype=bool)
if n > 1:
    new_trend_up[1:] = (direction[:-1] == -1) & (direction[1:] == 1)
    new_trend_down[1:] = (direction[:-1] == 1) & (direction[1:] == -1)

reference_map = {
    "+1": pivot_up_1, "+2": pivot_up_2, "+3": pivot_up_3,
    "-1": pivot_down_1, "-2": pivot_down_2, "-3": pivot_down_3,
}
custom_crosses = []
signals = []
for number, (source_name, method, reference_name) in enumerate(zip(cross_source_names, cross_methods, cross_references), start=1):
    source = standard_sources.get(source_name.lower(), close_values)
    reference = reference_map.get(reference_name)
    crossed = np.zeros(n, dtype=bool) if reference is None else rolling_cross(source, reference, method == "Above")
    custom_crosses.append(crossed)
    if reference is not None:
        is_above = method == "Above"
        signals.append({
            "type": "sell" if is_above else "buy",
            "name": f"Custom Cross {number}",
            "text": f"{reference_name}\n▼" if is_above else f"▲\n{reference_name}",
            "color": cross_above_color if is_above else cross_below_color,
            "renderMode": "events",
            "data": [
                {"value": float(high_values[index] if is_above else low_values[index]), "color": cross_above_color if is_above else cross_below_color}
                if crossed[index] else None for index in range(n)
            ],
        })

for level_index in range(3):
    signals.append({
        "type": "sell", "name": f"Deviation +{level_index + 1} Cross", "text": f"+{level_index + 1}",
        "color": "#FF5252", "renderMode": "hidden",
        "data": [float(high_values[index]) if deviation_cross_up[level_index][index] else None for index in range(n)],
    })
    signals.append({
        "type": "buy", "name": f"Deviation -{level_index + 1} Cross", "text": f"-{level_index + 1}",
        "color": "#3C699E", "renderMode": "hidden",
        "data": [float(low_values[index]) if deviation_cross_down[level_index][index] else None for index in range(n)],
    })
signals.extend([
    {"type": "info", "name": "Fair Value Touch Uptrend", "text": "FV", "color": up_line_color, "renderMode": "hidden", "data": [float(low_values[index]) if fair_price_in_up[index] else None for index in range(n)]},
    {"type": "info", "name": "Fair Value Touch Downtrend", "text": "FV", "color": down_line_color, "renderMode": "hidden", "data": [float(high_values[index]) if fair_price_in_down[index] else None for index in range(n)]},
    {"type": "buy", "name": "New Trend Up", "text": "UP", "color": up_line_color, "renderMode": "hidden", "data": [float(low_values[index]) if new_trend_up[index] else None for index in range(n)]},
    {"type": "sell", "name": "New Trend Down", "text": "DOWN", "color": down_line_color, "renderMode": "hidden", "data": [float(high_values[index]) if new_trend_down[index] else None for index in range(n)]},
])

basis_points = [
    {"value": float(value), "color": up_line_color if direction[index] == 1 else down_line_color}
    if np.isfinite(value) else None for index, value in enumerate(fair_value)
]
threshold_cloud = [
    {
        "upper": float(upper_threshold[index]), "lower": float(lower_threshold[index]),
        "color": "rgba(255,255,255,0.05)" if direction[index] == 1 else "rgba(255,82,82,0.05)",
    }
    if np.isfinite(upper_threshold[index]) and np.isfinite(lower_threshold[index]) else None
    for index in range(n)
]
upper_extreme_cloud = [
    {"upper": float(max(pivot_up_2[index], pivot_up_3[index])), "lower": float(min(pivot_up_2[index], pivot_up_3[index])), "color": "rgba(255,82,82,0.10)"}
    if np.isfinite(pivot_up_2[index]) and np.isfinite(pivot_up_3[index]) else None for index in range(n)
]
lower_extreme_cloud = [
    {"upper": float(max(pivot_down_2[index], pivot_down_3[index])), "lower": float(min(pivot_down_2[index], pivot_down_3[index])), "color": "rgba(32,84,151,0.12)"}
    if np.isfinite(pivot_down_2[index]) and np.isfinite(pivot_down_3[index]) else None for index in range(n)
]
upper_hue = [
    {"upper": float(max(ohlc4[index], upper_threshold[index])), "lower": float(min(ohlc4[index], upper_threshold[index])), "color": "rgba(255,82,82,0.12)"}
    if np.isfinite(upper_threshold[index]) and ohlc4[index] > upper_threshold[index] else None for index in range(n)
]
lower_hue = [
    {"upper": float(max(ohlc4[index], lower_threshold[index])), "lower": float(min(ohlc4[index], lower_threshold[index])), "color": "rgba(60,105,158,0.12)"}
    if np.isfinite(lower_threshold[index]) and ohlc4[index] < lower_threshold[index] else None for index in range(n)
]

plots = [
    {"name": "Fair Value Basis", "data": basis_points, "color": up_line_color, "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Threshold Band", "title": False, "data": threshold_cloud, "type": "cloud", "overlay": True},
    {"name": "Upper Deviation 1", "data": finite(pivot_up_1), "color": "rgba(255,255,255,0.28)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Lower Deviation 1", "data": finite(pivot_down_1), "color": "rgba(255,255,255,0.28)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Upper Deviation 2", "title": False, "data": finite(pivot_up_2), "color": "rgba(255,255,255,0.20)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Lower Deviation 2", "title": False, "data": finite(pivot_down_2), "color": "rgba(255,255,255,0.20)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Upper Deviation 3", "title": False, "data": finite(pivot_up_3), "color": "rgba(255,255,255,0.15)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Lower Deviation 3", "title": False, "data": finite(pivot_down_3), "color": "rgba(255,255,255,0.15)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Upper Extreme", "title": False, "data": upper_extreme_cloud, "type": "cloud", "overlay": True},
    {"name": "Lower Extreme", "title": False, "data": lower_extreme_cloud, "type": "cloud", "overlay": True},
    {"name": "Upper Deviation Hue", "title": False, "data": upper_hue, "type": "cloud", "overlay": True},
    {"name": "Lower Deviation Hue", "title": False, "data": lower_hue, "type": "cloud", "overlay": True},
]

bar_palette = ("#F23645", "#FF9800", "#FDD835", "#26C6DA", "#7C93EF", "#2962FF")
bar_colors = [None for _ in range(n)]
if highlight_metrics:
    for index in range(n):
        for region in range(6):
            if region_flags[region, index]:
                bar_colors[index] = bar_palette[region]
                break

final_counts = region_counts[:, -1] if n else np.zeros(6, dtype=int)
final_volume = region_volume[:, -1] if n else np.zeros(6, dtype=float)
all_counts = int(np.sum(final_counts))
region_names = ("+3", "+2", "+1", "-1", "-2", "-3")
rows = []
for region, name in enumerate(region_names):
    time_percent = 100.0 * final_counts[region] / all_counts if all_counts else 0.0
    average_volume = final_volume[region] / final_counts[region] if final_counts[region] else 0.0
    rows.append([
        {"text": name, "color": bar_palette[region]},
        {"text": f"{time_percent:.1f}%", "align": "right"},
        {"text": f"{average_volume:.2f}x", "align": "right"},
    ])
tables = [{
    "title": f"Fair Value · {basis_source_name}/{smoothing_type}/{vwap_anchor if smoothing_type == 'VWAP' else length}",
    "position": table_position,
    "compact": table_size <= 3,
    "columns": ["Zone", "Time", "Rel Vol"],
    "rows": rows,
}]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": [],
    "tables": tables,
    "bar_colors": bar_colors,
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "basisSource": finite(basis_source), "thresholdUpSource": finite(threshold_up_source), "thresholdDownSource": finite(threshold_down_source),
        "smoothingType": smoothing_type, "length": int(length), "vwapAnchor": vwap_anchor,
        "fairValue": finite(fair_value), "medianUpDeviation": finite(median_up_deviation), "medianDownDeviation": finite(median_down_deviation),
        "upperBand": finite(upper_band), "lowerBand": finite(lower_band), "upperThreshold": finite(upper_threshold), "lowerThreshold": finite(lower_threshold),
        "direction": direction.tolist(), "ohlcSpread": finite(ohlc_spread),
        "pivotUpEvent": finite(pivot_up_event), "pivotDownEvent": finite(pivot_down_event),
        "medianPivotUp": finite(median_pivot_up), "medianPivotDown": finite(median_pivot_down),
        "pivotUp1": finite(pivot_up_1), "pivotDown1": finite(pivot_down_1),
        "pivotUp2": finite(pivot_up_2), "pivotDown2": finite(pivot_down_2),
        "pivotUp3": finite(pivot_up_3), "pivotDown3": finite(pivot_down_3),
        "relativeVolume": finite(relative_volume),
        "regionFlags": [values.tolist() for values in region_flags],
        "regionCounts": [values.tolist() for values in region_counts],
        "regionVolume": [values.tolist() for values in region_volume],
        "deviationCrossUp": [values.tolist() for values in deviation_cross_up],
        "deviationCrossDown": [values.tolist() for values in deviation_cross_down],
        "fairPriceInUp": fair_price_in_up.tolist(), "fairPriceInDown": fair_price_in_down.tolist(),
        "newTrendUp": new_trend_up.tolist(), "newTrendDown": new_trend_down.tolist(),
        "customCrosses": [values.tolist() for values in custom_crosses],
    },
}
