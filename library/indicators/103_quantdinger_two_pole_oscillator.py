# ============================================================
# QuantDinger Two-Pole Oscillator
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Two-Pole Oscillator"
my_indicator_description = (
    "Normalizes price displacement, applies a two-stage low-pass filter, and "
    "projects active reversal levels onto the price chart until invalidation."
)

# @param filter_length int 15 Filter length range=1:300:1
# @param display_levels bool true Show projected price levels

filter_length = min(300, max(1, int(params.get("filter_length", 15))))
display_levels = bool(params.get("display_levels", True))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def rolling_mean(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    running_sum = 0.0
    finite_count = 0
    for index, value in enumerate(source):
        if np.isfinite(value):
            running_sum += float(value)
            finite_count += 1
        if index >= period:
            old_value = source[index - period]
            if np.isfinite(old_value):
                running_sum -= float(old_value)
                finite_count -= 1
        if index >= period - 1 and finite_count == period:
            result[index] = running_sum / period
    return result


def rolling_std(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(period - 1, len(source)):
        window = source[index - period + 1 : index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.std(window, ddof=0))
    return result


baseline = rolling_mean(close, 25)
displacement = close - baseline
centered = displacement - rolling_mean(displacement, 25)
deviation = rolling_std(displacement, 25)
normalized = np.full(n, np.nan, dtype=float)
valid = np.isfinite(centered) & np.isfinite(deviation) & (deviation != 0.0)
normalized[valid] = centered[valid] / deviation[valid]

first_pole = np.full(n, np.nan, dtype=float)
second_pole = np.full(n, np.nan, dtype=float)
alpha = 2.0 / (filter_length + 1.0)
for index, value in enumerate(normalized):
    if not np.isfinite(value):
        continue
    previous_first = first_pole[index - 1] if index > 0 else np.nan
    first_pole[index] = float(value) if not np.isfinite(previous_first) else (1.0 - alpha) * previous_first + alpha * float(value)
    previous_second = second_pole[index - 1] if index > 0 else np.nan
    second_pole[index] = first_pole[index] if not np.isfinite(previous_second) else (1.0 - alpha) * previous_second + alpha * first_pole[index]

lagged = np.full(n, np.nan, dtype=float)
if n > 4:
    lagged[4:] = second_pole[:-4]

buy = np.zeros(n, dtype=bool)
sell = np.zeros(n, dtype=bool)
for index in range(1, n):
    if not all(np.isfinite(value) for value in (second_pole[index], lagged[index], second_pole[index - 1], lagged[index - 1])):
        continue
    buy[index] = second_pole[index] > lagged[index] and second_pole[index - 1] <= lagged[index - 1] and second_pole[index] < 0.0
    sell[index] = second_pole[index] < lagged[index] and second_pole[index - 1] >= lagged[index - 1] and second_pole[index] > 0.0

average_range = rolling_mean(high - low, 100)
buy_level_plot = [None for _ in range(n)]
sell_level_plot = [None for _ in range(n)]
buy_event = [None for _ in range(n)]
sell_event = [None for _ in range(n)]
buy_invalidation = [None for _ in range(n)]
sell_invalidation = [None for _ in range(n)]
active_buy_level = np.nan
active_sell_level = np.nan

for index in range(n):
    if buy[index]:
        active_sell_level = np.nan
        if display_levels and np.isfinite(average_range[index]):
            active_buy_level = float(low[index] - average_range[index])
            buy_event[index] = active_buy_level
    if sell[index]:
        active_buy_level = np.nan
        if display_levels and np.isfinite(average_range[index]):
            active_sell_level = float(high[index] + average_range[index])
            sell_event[index] = active_sell_level

    if np.isfinite(active_buy_level):
        buy_level_plot[index] = float(active_buy_level)
        previous_low = low[index - 1] if index > 0 else low[index]
        if low[index] < active_buy_level and previous_low >= active_buy_level:
            buy_invalidation[index] = float(active_buy_level)
            active_buy_level = np.nan
    if np.isfinite(active_sell_level):
        sell_level_plot[index] = float(active_sell_level)
        previous_high = high[index - 1] if index > 0 else high[index]
        if high[index] > active_sell_level and previous_high <= active_sell_level:
            sell_invalidation[index] = float(active_sell_level)
            active_sell_level = np.nan

oscillator_points = []
oscillator_cloud = []
buy_dots = []
sell_dots = []
for index in range(n):
    value = second_pole[index]
    comparison = lagged[index]
    if not np.isfinite(value):
        oscillator_points.append(None)
        oscillator_cloud.append(None)
        buy_dots.append(None)
        sell_dots.append(None)
        continue
    rising = np.isfinite(comparison) and value > comparison
    intensity = min(1.0, max(0.0, (float(value) + 1.0) / 2.0))
    if rising:
        color = f"rgba(85, 255, 218, {0.50 + 0.50 * intensity:.3f})"
    else:
        color = f"rgba(140, 91, 255, {1.00 - 0.50 * intensity:.3f})"
    oscillator_points.append({"value": float(value), "color": color})
    if np.isfinite(comparison):
        oscillator_cloud.append({"upper": float(max(value, comparison)), "lower": float(min(value, comparison)), "color": color})
    else:
        oscillator_cloud.append(None)
    buy_dots.append(float(value) if buy[index] else None)
    sell_dots.append(float(value) if sell[index] else None)

upper_zone = [{"upper": 1.0, "lower": 0.0, "color": "rgba(148, 163, 184, 0.08)"} for _ in range(n)]
lower_zone = [{"upper": 0.0, "lower": -1.0, "color": "rgba(148, 163, 184, 0.08)"} for _ in range(n)]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Two-Pole Oscillator", "data": oscillator_points, "color": "#55FFDA", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Filter Separation", "title": False, "data": oscillator_cloud, "color": "#55FFDA", "type": "cloud", "opacity": 0.28, "overlay": False},
        {"name": "Buy Dot", "data": buy_dots, "color": "#55FFDA", "type": "circle", "size": 5, "overlay": False},
        {"name": "Sell Dot", "data": sell_dots, "color": "#8C5BFF", "type": "circle", "size": 5, "overlay": False},
        {"name": "Upper Guide", "data": [1.0 for _ in range(n)], "color": "rgba(148, 163, 184, 0.28)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Upper Mid Guide", "data": [0.5 for _ in range(n)], "color": "rgba(148, 163, 184, 0.50)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Zero Guide", "data": [0.0 for _ in range(n)], "color": "rgba(148, 163, 184, 0.70)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Lower Mid Guide", "data": [-0.5 for _ in range(n)], "color": "rgba(148, 163, 184, 0.50)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Lower Guide", "data": [-1.0 for _ in range(n)], "color": "rgba(148, 163, 184, 0.28)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Upper Zone", "title": False, "data": upper_zone, "color": "#94A3B8", "type": "cloud", "opacity": 0.08, "overlay": False},
        {"name": "Lower Zone", "title": False, "data": lower_zone, "color": "#94A3B8", "type": "cloud", "opacity": 0.08, "overlay": False},
        {"name": "Active Buy Level", "data": buy_level_plot, "color": "rgba(85, 255, 218, 0.75)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Active Sell Level", "data": sell_level_plot, "color": "rgba(140, 91, 255, 0.75)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "▲", "name": "Two-Pole Buy", "data": buy_event, "color": "#55FFDA", "renderMode": "events"},
        {"type": "sell", "text": "▼", "name": "Two-Pole Sell", "data": sell_event, "color": "#8C5BFF", "renderMode": "events"},
        {"type": "sell", "text": "×", "name": "Buy Level Invalidated", "data": buy_invalidation, "color": "#55FFDA", "renderMode": "events"},
        {"type": "buy", "text": "×", "name": "Sell Level Invalidated", "data": sell_invalidation, "color": "#8C5BFF", "renderMode": "events"},
    ],
    "layers": [],
}
