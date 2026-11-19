# ============================================================
# QuantDinger Extreme Trend Reversal
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Extreme Trend Reversal"
my_indicator_description = (
    "Builds a cascade of moving averages, scores their pairwise ordering, "
    "colors the primary average by historical strength, and marks extreme reversals."
)

# @param source str close Source values=close,open,high,low,hl2,hlc3,ohlc4
# @param average_type str sma Average type values=sma,ema,rma,wma
# @param length int 20 Moving-average length range=2:500:1
# @param levels int 10 Cascade levels range=5:30:1
# @param range_percentile int 20 Range percentile range=5:45:5
# @param extreme_percentile int 45 Extreme range percentile range=40:48:1
# @param percentile_history int 1000 Percentile history range=100:5000:100
# @param real_time_alerts bool false Real-time alert timing

source_name = str(params.get("source", "close"))
average_type = str(params.get("average_type", "sma")).lower()
length = min(500, max(1, int(params.get("length", 20))))
levels = min(30, max(5, int(params.get("levels", 10))))
range_percentile = min(45, max(5, int(params.get("range_percentile", 20))))
extreme_percentile = min(48, max(40, int(params.get("extreme_percentile", 45))))
percentile_history = min(5000, max(1000, int(params.get("percentile_history", 1000))))
real_time_alerts = bool(params.get("real_time_alerts", False))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
time_values = df["time"].astype(float).to_numpy(dtype=float)
n = len(df)

if source_name == "open":
    source_values = open_price.copy()
elif source_name == "high":
    source_values = high.copy()
elif source_name == "low":
    source_values = low.copy()
elif source_name == "hl2":
    source_values = (high + low) / 2.0
elif source_name == "hlc3":
    source_values = (high + low + close) / 3.0
elif source_name == "ohlc4":
    source_values = (open_price + high + low + close) / 4.0
else:
    source_values = close.copy()

if average_type not in ("sma", "ema", "rma", "wma"):
    average_type = "sma"

cascade = np.full((n, levels + 1), np.nan, dtype=float)
cascade[:, 0] = source_values

if average_type in ("ema", "rma"):
    cascade[0, 1:] = float(source_values[0])
    alpha = 2.0 / (length + 1.0) if average_type == "ema" else 1.0 / length
    for index in range(1, n):
        for level_index in range(1, levels + 1):
            cascade[index, level_index] = (
                alpha * cascade[index, level_index - 1]
                + (1.0 - alpha) * cascade[index - 1, level_index]
            )
else:
    weights = np.arange(length, 0, -1, dtype=float)
    weight_sum = float(np.sum(weights))
    for index in range(n):
        for level_index in range(1, levels + 1):
            first_index = max(0, index - length + 1)
            values = cascade[first_index:index + 1, level_index - 1][::-1]
            if values.size < length or not np.all(np.isfinite(values)):
                cascade[index, level_index] = cascade[index, level_index - 1]
            elif average_type == "wma":
                cascade[index, level_index] = float(np.sum(values * weights) / weight_sum)
            else:
                cascade[index, level_index] = float(np.mean(values))

strength = np.zeros(n, dtype=float)
for index in range(n):
    current_strength = 0
    for first_level in range(levels + 1):
        for second_level in range(first_level + 1, levels + 1):
            if cascade[index, first_level] > cascade[index, second_level]:
                current_strength += 1
    strength[index] = float(current_strength)

def rolling_nearest_rank(values, window, percentile):
    result = np.full(values.size, np.nan, dtype=float)
    rank = max(1, int(np.ceil(percentile / 100.0 * window)))
    for index in range(window - 1, values.size):
        ordered = np.sort(values[index - window + 1:index + 1])
        result[index] = float(ordered[min(rank - 1, ordered.size - 1)])
    return result

minimum_range = rolling_nearest_rank(strength, percentile_history, 50 - range_percentile)
maximum_range = rolling_nearest_rank(strength, percentile_history, 50 + range_percentile)
extreme_minimum = rolling_nearest_rank(strength, percentile_history, 50 - extreme_percentile)
extreme_maximum = rolling_nearest_rank(strength, percentile_history, 50 + extreme_percentile)

primary_average = np.full(n, np.nan, dtype=float)
if average_type == "sma":
    primary_average = (
        pd.Series(source_values, dtype=float)
        .rolling(length, min_periods=length)
        .mean()
        .to_numpy(dtype=float)
    )
elif average_type == "wma":
    chronological_weights = np.arange(1, length + 1, dtype=float)
    chronological_sum = float(np.sum(chronological_weights))
    for index in range(length - 1, n):
        primary_average[index] = float(
            np.sum(source_values[index - length + 1:index + 1] * chronological_weights)
            / chronological_sum
        )
elif average_type == "ema":
    alpha = 2.0 / (length + 1.0)
    if n:
        primary_average[0] = float(source_values[0])
        for index in range(1, n):
            primary_average[index] = alpha * source_values[index] + (1.0 - alpha) * primary_average[index - 1]
else:
    if n >= length:
        primary_average[length - 1] = float(np.mean(source_values[:length]))
        alpha = 1.0 / length
        for index in range(length, n):
            primary_average[index] = alpha * source_values[index] + (1.0 - alpha) * primary_average[index - 1]

maximum_strength = float(levels * (levels + 1) / 2)
bullish_trend_reversal = np.zeros(n, dtype=bool)
bearish_trend_reversal = np.zeros(n, dtype=bool)
for index in range(1, n):
    if not np.isfinite(primary_average[index]) or not np.isfinite(primary_average[index - 1]):
        continue
    crossed_under = source_values[index] < primary_average[index] and source_values[index - 1] >= primary_average[index - 1]
    crossed_over = source_values[index] > primary_average[index] and source_values[index - 1] <= primary_average[index - 1]
    bullish_trend_reversal[index] = strength[index - 1] == maximum_strength and crossed_under
    bearish_trend_reversal[index] = strength[index - 1] == 0.0 and crossed_over

average_data = []
strength_range = np.full(n, -2.0, dtype=float)
for index in range(n):
    if not np.isfinite(primary_average[index]):
        average_data.append(None)
        continue
    color = "#B0B0B0"
    if np.isfinite(extreme_maximum[index]) and strength[index] > extreme_maximum[index]:
        color = "#008000"
        strength_range[index] = 2.0
    elif np.isfinite(maximum_range[index]) and strength[index] > maximum_range[index]:
        color = "#00D000"
        strength_range[index] = 1.0
    elif np.isfinite(extreme_minimum[index]) and strength[index] < extreme_minimum[index]:
        color = "#F23645"
        strength_range[index] = -1.0
    elif np.isfinite(minimum_range[index]) and strength[index] < minimum_range[index]:
        color = "#FF9800"
        strength_range[index] = -2.0
    elif np.isfinite(minimum_range[index]) and strength[index] > minimum_range[index]:
        strength_range[index] = 0.0
    average_data.append({"value": float(primary_average[index]), "color": color})

layers = []
level_rows = []
if n > 0:
    for level_index in range(1, levels + 1):
        fraction = 0.0 if levels <= 1 else (level_index - 1.0) / (levels - 1.0)
        red = int(round(255.0 * fraction))
        green = int(round(128.0 * (1.0 - fraction)))
        color = f"rgb({red}, {green}, 0)"
        level_value = float(cascade[-1, level_index])
        layers.append(
            {
                "type": "line",
                "startIndex": int(n - 1),
                "endIndex": int(n - 1),
                "extendBars": 20,
                "y1": level_value,
                "y2": level_value,
                "color": color,
                "lineWidth": 1,
                "dashed": False,
            }
        )
        level_rows.append([
            {"text": f"{average_type}({level_index})", "color": color},
            {"text": f"{level_value:.2f}", "color": color, "align": "right"},
        ])

tables = []
if level_rows:
    tables.append({
        "title": "MA Cascade", "position": "top-right", "compact": True,
        "columns": ["Level", "Value"], "rows": level_rows,
    })

sell_data = [None for _ in range(n)]
buy_data = [None for _ in range(n)]
for index in range(n):
    if bullish_trend_reversal[index]:
        sell_data[index] = {"value": float(high[index]), "text": "▼", "color": "#F23645"}
    if bearish_trend_reversal[index]:
        buy_data[index] = {"value": float(low[index]), "text": "▲", "color": "#089981"}

plots = [
    {"name": "Moving Average", "data": average_data, "color": "#B0B0B0", "type": "line", "lineWidth": 2, "overlay": True},
]
signals = [
    {"type": "sell", "text": "▼", "name": "Bullish Trend Reversal", "data": sell_data, "color": "#F23645", "renderMode": "events"},
    {"type": "buy", "text": "▲", "name": "Bearish Trend Reversal", "data": buy_data, "color": "#089981", "renderMode": "events"},
]

signals = [signal for signal in signals if any(item is not None for item in signal["data"])]

alert_bullish = np.zeros(n, dtype=bool)
alert_bearish = np.zeros(n, dtype=bool)
if real_time_alerts:
    alert_bullish[:] = bullish_trend_reversal
    alert_bearish[:] = bearish_trend_reversal
elif n > 1:
    alert_bullish[1:] = bullish_trend_reversal[:-1]
    alert_bearish[1:] = bearish_trend_reversal[:-1]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": layers,
    "tables": tables,
    "bar_colors": [None for _ in range(n)],
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "source": [float(value) for value in source_values],
        "averageType": average_type,
        "length": int(length),
        "levels": int(levels),
        "rangePercentile": int(range_percentile),
        "extremePercentile": int(extreme_percentile),
        "percentileHistory": int(percentile_history),
        "cascade": [[float(value) if np.isfinite(value) else None for value in row] for row in cascade],
        "strength": strength.tolist(),
        "minimumRange": [float(value) if np.isfinite(value) else None for value in minimum_range],
        "maximumRange": [float(value) if np.isfinite(value) else None for value in maximum_range],
        "extremeMinimum": [float(value) if np.isfinite(value) else None for value in extreme_minimum],
        "extremeMaximum": [float(value) if np.isfinite(value) else None for value in extreme_maximum],
        "primaryAverage": [float(value) if np.isfinite(value) else None for value in primary_average],
        "maximumStrength": float(maximum_strength),
        "strengthRange": strength_range.tolist(),
        "bullishTrendReversal": bullish_trend_reversal.tolist(),
        "bearishTrendReversal": bearish_trend_reversal.tolist(),
        "alertBullish": alert_bullish.tolist(),
        "alertBearish": alert_bearish.tolist(),
    },
}
