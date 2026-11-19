# ============================================================
# QuantDinger Chaikin-Fisher Reversal
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Chaikin-Fisher Reversal"
my_indicator_description = (
    "Transforms Chaikin accumulation pressure with a Fisher filter, then marks "
    "crossovers, pivot divergences, and overextended curls."
)

# @param fast_length int 3 Chaikin fast EMA range=1:200:1
# @param slow_length int 10 Chaikin slow EMA range=1:300:1
# @param fisher_length int 10 Fisher normalization length range=2:300:1
# @param overbought float 1.5 Overbought level range=0.1:10:0.1
# @param oversold float -1.5 Oversold level range=-10:-0.1:0.1
# @param show_divergences bool true Show pivot divergences
# @param pivot_left int 4 Pivot left bars range=1:50:1
# @param pivot_right int 4 Pivot right bars range=1:50:1
# @param max_pivot_bars int 60 Maximum bars between pivots range=5:500:1
# @param show_early bool true Show early reversal signals
# @param cooldown_bars int 5 Early signal cooldown range=0:100:1

fast_length = min(200, max(1, int(params.get("fast_length", 3))))
slow_length = min(300, max(1, int(params.get("slow_length", 10))))
fisher_length = min(300, max(2, int(params.get("fisher_length", 10))))
overbought = min(10.0, max(0.1, float(params.get("overbought", 1.5))))
oversold = min(-0.1, max(-10.0, float(params.get("oversold", -1.5))))
show_divergences = bool(params.get("show_divergences", True))
pivot_left = min(50, max(1, int(params.get("pivot_left", 4))))
pivot_right = min(50, max(1, int(params.get("pivot_right", 4))))
max_pivot_bars = min(500, max(5, int(params.get("max_pivot_bars", 60))))
show_early = bool(params.get("show_early", True))
cooldown_bars = min(100, max(0, int(params.get("cooldown_bars", 5))))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)


def recursive_ema(values, period):
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


def crossed_above(first, second, index):
    return (
        index > 0
        and np.isfinite(first[index])
        and np.isfinite(second[index])
        and np.isfinite(first[index - 1])
        and np.isfinite(second[index - 1])
        and first[index] > second[index]
        and first[index - 1] <= second[index - 1]
    )


def crossed_below(first, second, index):
    return (
        index > 0
        and np.isfinite(first[index])
        and np.isfinite(second[index])
        and np.isfinite(first[index - 1])
        and np.isfinite(second[index - 1])
        and first[index] < second[index]
        and first[index - 1] >= second[index - 1]
    )


def draw_segment(target, start_index, start_value, end_index, end_value):
    width = end_index - start_index
    if width <= 0:
        return
    for index in range(start_index, end_index + 1):
        fraction = (index - start_index) / width
        target[index] = float(start_value + (end_value - start_value) * fraction)


money_flow_volume = np.zeros(n, dtype=float)
for index in range(n):
    spread = high[index] - low[index]
    if np.isfinite(spread) and spread != 0.0 and np.isfinite(volume[index]):
        multiplier = ((close[index] - low[index]) - (high[index] - close[index])) / spread
        money_flow_volume[index] = float(multiplier * volume[index])

accumulation_distribution = np.cumsum(money_flow_volume)
chaikin = recursive_ema(accumulation_distribution, fast_length) - recursive_ema(
    accumulation_distribution,
    slow_length,
)

normalized_state = np.zeros(n, dtype=float)
fisher = np.zeros(n, dtype=float)
trigger = np.full(n, np.nan, dtype=float)
for index in range(n):
    start = max(0, index - fisher_length + 1)
    window = chaikin[start : index + 1]
    finite_window = window[np.isfinite(window)]
    if finite_window.size == 0 or not np.isfinite(chaikin[index]):
        normalized_state[index] = normalized_state[index - 1] if index > 0 else 0.0
        fisher[index] = fisher[index - 1] if index > 0 else 0.0
        continue
    highest = float(np.max(finite_window))
    lowest = float(np.min(finite_window))
    raw_value = (float(chaikin[index]) - lowest) / (highest - lowest) - 0.5 if highest != lowest else 0.0
    previous_state = normalized_state[index - 1] if index > 0 else 0.0
    state = 0.66 * raw_value + 0.67 * previous_state
    state = max(-0.999, min(0.999, state))
    normalized_state[index] = state
    previous_fisher = fisher[index - 1] if index > 0 else 0.0
    fisher[index] = 0.5 * np.log((1.0 + state) / (1.0 - state)) + 0.5 * previous_fisher
    if index > 0:
        trigger[index] = fisher[index - 1]

long_cross = np.zeros(n, dtype=bool)
short_cross = np.zeros(n, dtype=bool)
for index in range(1, n):
    long_cross[index] = crossed_above(fisher, trigger, index)
    short_cross[index] = crossed_below(fisher, trigger, index)

regular_bull_line = np.full(n, np.nan, dtype=float)
hidden_bull_line = np.full(n, np.nan, dtype=float)
regular_bear_line = np.full(n, np.nan, dtype=float)
hidden_bear_line = np.full(n, np.nan, dtype=float)
regular_bull = np.zeros(n, dtype=bool)
hidden_bull = np.zeros(n, dtype=bool)
regular_bear = np.zeros(n, dtype=bool)
hidden_bear = np.zeros(n, dtype=bool)

previous_low_value = np.nan
previous_low_index = -1
previous_low_price = np.nan
previous_high_value = np.nan
previous_high_index = -1
previous_high_price = np.nan

for detection_index in range(pivot_left + pivot_right, n):
    pivot_index = detection_index - pivot_right
    start = pivot_index - pivot_left
    end = pivot_index + pivot_right + 1
    window = fisher[start:end]
    if len(window) != pivot_left + pivot_right + 1 or not np.all(np.isfinite(window)):
        continue
    pivot_value = float(fisher[pivot_index])
    is_pivot_low = pivot_value <= float(np.min(window))
    is_pivot_high = pivot_value >= float(np.max(window))

    if is_pivot_low:
        price_value = float(low[pivot_index])
        within_range = previous_low_index >= 0 and pivot_index - previous_low_index <= max_pivot_bars
        if within_range:
            if price_value < previous_low_price and pivot_value > previous_low_value:
                regular_bull[pivot_index] = True
                if show_divergences:
                    draw_segment(regular_bull_line, previous_low_index, previous_low_value, pivot_index, pivot_value)
            if price_value > previous_low_price and pivot_value < previous_low_value:
                hidden_bull[pivot_index] = True
                if show_divergences:
                    draw_segment(hidden_bull_line, previous_low_index, previous_low_value, pivot_index, pivot_value)
        previous_low_value = pivot_value
        previous_low_index = pivot_index
        previous_low_price = price_value

    if is_pivot_high:
        price_value = float(high[pivot_index])
        within_range = previous_high_index >= 0 and pivot_index - previous_high_index <= max_pivot_bars
        if within_range:
            if price_value > previous_high_price and pivot_value < previous_high_value:
                regular_bear[pivot_index] = True
                if show_divergences:
                    draw_segment(regular_bear_line, previous_high_index, previous_high_value, pivot_index, pivot_value)
            if price_value < previous_high_price and pivot_value > previous_high_value:
                hidden_bear[pivot_index] = True
                if show_divergences:
                    draw_segment(hidden_bear_line, previous_high_index, previous_high_value, pivot_index, pivot_value)
        previous_high_value = pivot_value
        previous_high_index = pivot_index
        previous_high_price = price_value

early_bull = np.zeros(n, dtype=bool)
early_bear = np.zeros(n, dtype=bool)
last_bull_index = -1000000
last_bear_index = -1000000
for index in range(2, n):
    curl_up = fisher[index] > fisher[index - 1] and fisher[index - 1] <= fisher[index - 2]
    curl_down = fisher[index] < fisher[index - 1] and fisher[index - 1] >= fisher[index - 2]
    if show_early and fisher[index - 1] <= oversold and curl_up and index - last_bull_index >= cooldown_bars:
        early_bull[index] = True
        last_bull_index = index
    if show_early and fisher[index - 1] >= overbought and curl_down and index - last_bear_index >= cooldown_bars:
        early_bear[index] = True
        last_bear_index = index

fisher_points = []
for value in fisher:
    color = "#26A69A" if value >= 0.0 else "#EF5350"
    fisher_points.append({"value": float(value), "color": color} if np.isfinite(value) else None)

low_marker_level = min(float(oversold), float(np.nanmin(fisher))) - 0.15
high_marker_level = max(float(overbought), float(np.nanmax(fisher))) + 0.15

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Fisher", "data": fisher_points, "color": "#26A69A", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Trigger", "data": [float(value) if np.isfinite(value) else None for value in trigger], "color": "#787B86", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Zero", "data": [0.0 for _ in range(n)], "color": "rgba(120, 123, 134, 0.75)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Overbought", "data": [float(overbought) for _ in range(n)], "color": "rgba(239, 83, 80, 0.45)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Oversold", "data": [float(oversold) for _ in range(n)], "color": "rgba(38, 166, 154, 0.45)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Regular Bullish Divergence", "data": [float(value) if np.isfinite(value) else None for value in regular_bull_line], "color": "#26A69A", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Hidden Bullish Divergence", "data": [float(value) if np.isfinite(value) else None for value in hidden_bull_line], "color": "rgba(38, 166, 154, 0.70)", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Regular Bearish Divergence", "data": [float(value) if np.isfinite(value) else None for value in regular_bear_line], "color": "#EF5350", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Hidden Bearish Divergence", "data": [float(value) if np.isfinite(value) else None for value in hidden_bear_line], "color": "rgba(239, 83, 80, 0.70)", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Long Crossover", "data": [low_marker_level if long_cross[index] else None for index in range(n)], "color": "#26A69A", "type": "circle", "size": 3, "overlay": False},
        {"name": "Short Crossover", "data": [high_marker_level if short_cross[index] else None for index in range(n)], "color": "#EF5350", "type": "circle", "size": 3, "overlay": False},
        {"name": "Early Bullish Reversal", "data": [low_marker_level + 0.08 if early_bull[index] else None for index in range(n)], "color": "#00E5FF", "type": "circle", "size": 4, "overlay": False},
        {"name": "Early Bearish Reversal", "data": [high_marker_level - 0.08 if early_bear[index] else None for index in range(n)], "color": "#FF6D00", "type": "circle", "size": 4, "overlay": False},
        {"name": "Regular Bullish Marker", "data": [low_marker_level if show_divergences and regular_bull[index] else None for index in range(n)], "color": "#26A69A", "type": "circle", "size": 5, "overlay": False},
        {"name": "Hidden Bullish Marker", "data": [low_marker_level if show_divergences and hidden_bull[index] else None for index in range(n)], "color": "rgba(38, 166, 154, 0.70)", "type": "circle", "size": 5, "overlay": False},
        {"name": "Regular Bearish Marker", "data": [high_marker_level if show_divergences and regular_bear[index] else None for index in range(n)], "color": "#EF5350", "type": "circle", "size": 5, "overlay": False},
        {"name": "Hidden Bearish Marker", "data": [high_marker_level if show_divergences and hidden_bear[index] else None for index in range(n)], "color": "rgba(239, 83, 80, 0.70)", "type": "circle", "size": 5, "overlay": False},
    ],
    "signals": [],
    "layers": [],
}
