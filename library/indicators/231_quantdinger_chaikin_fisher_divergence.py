# ============================================================
# QuantDinger Chaikin Fisher Divergence
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Chaikin Fisher Divergence"
my_indicator_description = (
    "Transforms the Chaikin accumulation/distribution oscillator with a Fisher "
    "normalization, then detects confirmed divergences and early reversal curls."
)

# @param fast_length int 3 Chaikin fast EMA length range=1:200:1
# @param slow_length int 10 Chaikin slow EMA length range=1:500:1
# @param fisher_length int 10 Fisher normalization length range=2:500:1
# @param overbought float 1.5 Overbought level range=0.1:10.0:0.1
# @param oversold float -1.5 Oversold level range=-10.0:-0.1:0.1
# @param show_divergences bool true Show divergences
# @param pivot_left int 4 Pivot left bars range=1:50:1
# @param pivot_right int 4 Pivot right bars range=1:50:1
# @param max_pivot_bars int 60 Maximum bars between pivots range=5:500:1
# @param show_early_reversals bool true Show early reversal signals
# @param cooldown_bars int 5 Early signal cooldown range=0:100:1
# @param max_divergences int 12 Maximum visible divergence lines range=1:30:1
# @param max_markers int 30 Maximum visible event markers range=1:100:1

fast_length = min(200, max(1, int(params.get("fast_length", 3))))
slow_length = min(500, max(1, int(params.get("slow_length", 10))))
fisher_length = min(500, max(2, int(params.get("fisher_length", 10))))
overbought = min(10.0, max(0.1, float(params.get("overbought", 1.5))))
oversold = min(-0.1, max(-10.0, float(params.get("oversold", -1.5))))
show_divergences = bool(params.get("show_divergences", True))
pivot_left = min(50, max(1, int(params.get("pivot_left", 4))))
pivot_right = min(50, max(1, int(params.get("pivot_right", 4))))
max_pivot_bars = min(500, max(5, int(params.get("max_pivot_bars", 60))))
show_early_reversals = bool(params.get("show_early_reversals", True))
cooldown_bars = min(100, max(0, int(params.get("cooldown_bars", 5))))
max_divergences = min(30, max(1, int(params.get("max_divergences", 12))))
max_markers = min(100, max(1, int(params.get("max_markers", 30))))

frame = df.copy().reset_index(drop=True)
n = len(frame)
high_values = pd.to_numeric(frame["high"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
low_values = pd.to_numeric(frame["low"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
close_values = pd.to_numeric(frame["close"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
volume_values = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0).to_numpy(dtype=float)


def ema(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    if not len(values):
        return result
    alpha = 2.0 / (float(length) + 1.0)
    result[0] = float(values[0])
    for index in range(1, len(values)):
        result[index] = alpha * float(values[index]) + (1.0 - alpha) * result[index - 1]
    return result


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


money_flow_multiplier = np.zeros(n, dtype=float)
candle_range = high_values - low_values
valid_range = candle_range != 0.0
money_flow_multiplier[valid_range] = (
    2.0 * close_values[valid_range] - high_values[valid_range] - low_values[valid_range]
) / candle_range[valid_range]
money_flow_volume = money_flow_multiplier * volume_values
accumulation_distribution = np.cumsum(money_flow_volume)
chaikin = ema(accumulation_distribution, fast_length) - ema(accumulation_distribution, slow_length)

chaikin_series = pd.Series(chaikin, dtype=float)
rolling_high = chaikin_series.rolling(fisher_length, min_periods=1).max().to_numpy(dtype=float)
rolling_low = chaikin_series.rolling(fisher_length, min_periods=1).min().to_numpy(dtype=float)
fisher_value = np.zeros(n, dtype=float)
fisher = np.zeros(n, dtype=float)
for index in range(n):
    range_value = rolling_high[index] - rolling_low[index]
    raw_value = (chaikin[index] - rolling_low[index]) / range_value - 0.5 if range_value != 0.0 else 0.0
    previous_value = fisher_value[index - 1] if index else 0.0
    fisher_value[index] = min(0.999, max(-0.999, 0.66 * raw_value + 0.67 * previous_value))
    previous_fisher = fisher[index - 1] if index else 0.0
    fisher[index] = 0.5 * np.log((1.0 + fisher_value[index]) / (1.0 - fisher_value[index])) + 0.5 * previous_fisher
trigger = np.zeros(n, dtype=float)
if n > 1:
    trigger[1:] = fisher[:-1]

long_cross = np.zeros(n, dtype=bool)
short_cross = np.zeros(n, dtype=bool)
for index in range(1, n):
    long_cross[index] = fisher[index] > trigger[index] and fisher[index - 1] <= trigger[index - 1]
    short_cross[index] = fisher[index] < trigger[index] and fisher[index - 1] >= trigger[index - 1]

pivot_low_confirmation = np.zeros(n, dtype=bool)
pivot_high_confirmation = np.zeros(n, dtype=bool)
regular_bull = np.zeros(n, dtype=bool)
hidden_bull = np.zeros(n, dtype=bool)
regular_bear = np.zeros(n, dtype=bool)
hidden_bear = np.zeros(n, dtype=bool)
regular_bull_pivot = np.zeros(n, dtype=bool)
hidden_bull_pivot = np.zeros(n, dtype=bool)
regular_bear_pivot = np.zeros(n, dtype=bool)
hidden_bear_pivot = np.zeros(n, dtype=bool)
divergence_segments = []
previous_low = None
previous_high = None

for confirmation in range(pivot_left + pivot_right, n):
    pivot_index = confirmation - pivot_right
    start = pivot_index - pivot_left
    end = pivot_index + pivot_right + 1
    candidate = fisher[pivot_index]
    low_window = fisher[start:end]
    high_window = fisher[start:end]
    is_low = candidate == np.min(low_window) and np.count_nonzero(low_window == candidate) == 1
    is_high = candidate == np.max(high_window) and np.count_nonzero(high_window == candidate) == 1
    if is_low:
        pivot_low_confirmation[confirmation] = True
        price = low_values[pivot_index]
        if previous_low is not None and pivot_index - previous_low[0] <= max_pivot_bars:
            if price < previous_low[2] and candidate > previous_low[1]:
                regular_bull[confirmation] = True
                regular_bull_pivot[pivot_index] = True
                divergence_segments.append((previous_low[0], pivot_index, previous_low[1], candidate, "#26A69A", False, "Regular Bullish"))
            if price > previous_low[2] and candidate < previous_low[1]:
                hidden_bull[confirmation] = True
                hidden_bull_pivot[pivot_index] = True
                divergence_segments.append((previous_low[0], pivot_index, previous_low[1], candidate, "rgba(38,166,154,0.70)", True, "Hidden Bullish"))
        previous_low = (pivot_index, candidate, price)
    if is_high:
        pivot_high_confirmation[confirmation] = True
        price = high_values[pivot_index]
        if previous_high is not None and pivot_index - previous_high[0] <= max_pivot_bars:
            if price > previous_high[2] and candidate < previous_high[1]:
                regular_bear[confirmation] = True
                regular_bear_pivot[pivot_index] = True
                divergence_segments.append((previous_high[0], pivot_index, previous_high[1], candidate, "#EF5350", False, "Regular Bearish"))
            if price < previous_high[2] and candidate > previous_high[1]:
                hidden_bear[confirmation] = True
                hidden_bear_pivot[pivot_index] = True
                divergence_segments.append((previous_high[0], pivot_index, previous_high[1], candidate, "rgba(239,83,80,0.70)", True, "Hidden Bearish"))
        previous_high = (pivot_index, candidate, price)

early_bull = np.zeros(n, dtype=bool)
early_bear = np.zeros(n, dtype=bool)
last_early_bull = None
last_early_bear = None
if show_early_reversals:
    for index in range(2, n):
        curl_up = fisher[index] > fisher[index - 1] and fisher[index - 1] <= fisher[index - 2]
        curl_down = fisher[index] < fisher[index - 1] and fisher[index - 1] >= fisher[index - 2]
        raw_bull = fisher[index - 1] <= oversold and curl_up
        raw_bear = fisher[index - 1] >= overbought and curl_down
        if raw_bull and (last_early_bull is None or index - last_early_bull >= cooldown_bars):
            early_bull[index] = True
            last_early_bull = index
        if raw_bear and (last_early_bear is None or index - last_early_bear >= cooldown_bars):
            early_bear[index] = True
            last_early_bear = index

marker_union = long_cross | short_cross | early_bull | early_bear
if show_divergences:
    marker_union |= regular_bull_pivot | hidden_bull_pivot | regular_bear_pivot | hidden_bear_pivot
visible_marker_indexes = set(np.flatnonzero(marker_union)[-max_markers:].tolist())


def marker_points(mask, values, color, size):
    return [
        {"value": float(values[index]), "color": color, "size": float(size)}
        if index in visible_marker_indexes and mask[index] and np.isfinite(values[index])
        else None
        for index in range(n)
    ]


fisher_points = [
    {"value": float(value), "color": "#26A69A" if value >= 0.0 else "#EF5350"}
    for value in fisher
]
plots = [
    {"name": "Fisher", "data": fisher_points, "color": "#26A69A", "type": "line", "lineWidth": 2, "overlay": False},
    {"name": "Trigger", "data": finite(trigger), "color": "#787B86", "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Zero", "data": [0.0] * n, "color": "#64748B", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": False},
    {"name": "Overbought", "data": [overbought] * n, "color": "rgba(239,83,80,0.55)", "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": False},
    {"name": "Oversold", "data": [oversold] * n, "color": "rgba(38,166,154,0.55)", "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": False},
    {"name": "Long Crossover", "title": False, "data": marker_points(long_cross, fisher - 0.08, "#26A69A", 4.5), "color": "#26A69A", "type": "scatter", "overlay": False},
    {"name": "Short Crossover", "title": False, "data": marker_points(short_cross, fisher + 0.08, "#EF5350", 4.5), "color": "#EF5350", "type": "scatter", "overlay": False},
]
if show_divergences:
    divergence_segments = divergence_segments[-max_divergences:]
    for start, end, start_value, end_value, color_value, dashed, name_value in divergence_segments:
        data = [None] * n
        width = max(1, end - start)
        for index in range(start, end + 1):
            data[index] = float(start_value + (end_value - start_value) * (index - start) / width)
        plots.append({"name": f"{name_value} {start}-{end}", "title": False, "data": data, "color": color_value, "type": "line", "lineWidth": 2, "lineStyle": "dashed" if dashed else "solid", "overlay": False})
    plots.extend([
        {"name": "Regular Bullish", "title": False, "data": marker_points(regular_bull_pivot, fisher, "#26A69A", 7.0), "color": "#26A69A", "type": "scatter", "overlay": False},
        {"name": "Hidden Bullish", "title": False, "data": marker_points(hidden_bull_pivot, fisher, "rgba(38,166,154,0.70)", 6.0), "color": "#26A69A", "type": "scatter", "overlay": False},
        {"name": "Regular Bearish", "title": False, "data": marker_points(regular_bear_pivot, fisher, "#EF5350", 7.0), "color": "#EF5350", "type": "scatter", "overlay": False},
        {"name": "Hidden Bearish", "title": False, "data": marker_points(hidden_bear_pivot, fisher, "rgba(239,83,80,0.70)", 6.0), "color": "#EF5350", "type": "scatter", "overlay": False},
    ])
if show_early_reversals:
    plots.extend([
        {"name": "Early Bullish", "title": False, "data": marker_points(early_bull, fisher, "#00E5FF", 5.5), "color": "#00E5FF", "type": "scatter", "overlay": False},
        {"name": "Early Bearish", "title": False, "data": marker_points(early_bear, fisher, "#FF6D00", 5.5), "color": "#FF6D00", "type": "scatter", "overlay": False},
    ])

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "volumeAvailable": bool(np.sum(np.abs(volume_values)) > 0.0),
        "accumulationDistribution": finite(accumulation_distribution),
        "chaikin": finite(chaikin),
        "fisherValue": finite(fisher_value),
        "fisher": finite(fisher),
        "trigger": finite(trigger),
        "longCross": [bool(value) for value in long_cross],
        "shortCross": [bool(value) for value in short_cross],
        "pivotLowConfirmation": [bool(value) for value in pivot_low_confirmation],
        "pivotHighConfirmation": [bool(value) for value in pivot_high_confirmation],
        "regularBull": [bool(value) for value in regular_bull],
        "hiddenBull": [bool(value) for value in hidden_bull],
        "regularBear": [bool(value) for value in regular_bear],
        "hiddenBear": [bool(value) for value in hidden_bear],
        "earlyBull": [bool(value) for value in early_bull],
        "earlyBear": [bool(value) for value in early_bear],
        "divergenceCount": int(len(divergence_segments)),
    },
}
