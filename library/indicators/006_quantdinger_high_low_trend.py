# ============================================================
# QuantDinger High-Low Trend
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger High-Low Trend"
my_indicator_description = (
    "A stateful trend overlay that switches between moving averages of highs "
    "and lows when price confirms a directional change."
)

# @param high_period int 13 High average period range=1:300:1
# @param low_period int 21 Low average period range=1:300:1

high_period = min(1000, max(1, int(params.get("high_period", 13))))
low_period = min(1000, max(1, int(params.get("low_period", 21))))

df = df.copy()
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)
high_average = high.rolling(window=high_period, min_periods=high_period).mean()
low_average = low.rolling(window=low_period, min_periods=low_period).mean()

close_values = close.to_numpy(dtype=float)
high_values = high_average.to_numpy(dtype=float)
low_values = low_average.to_numpy(dtype=float)
state = np.nan
direction_event = np.zeros(len(df), dtype=int)
state_values = np.full(len(df), np.nan, dtype=float)
trend_values = np.full(len(df), np.nan, dtype=float)
trend_line = []

for index in range(len(df)):
    previous_high = high_values[index - 1] if index > 0 and np.isfinite(high_values[index - 1]) else 0.0
    previous_low = low_values[index - 1] if index > 0 and np.isfinite(low_values[index - 1]) else 0.0
    if close_values[index] > previous_high:
        direction_event[index] = 1
    elif close_values[index] < previous_low:
        direction_event[index] = -1

    if direction_event[index] != 0:
        state = float(direction_event[index])
    state_values[index] = state

    selected = high_values[index] if state == -1 else low_values[index]
    if np.isfinite(selected):
        trend_values[index] = selected
        trend_line.append(
            {
                "value": float(selected),
                "color": "#800000" if state == -1 else "#0000FF",
            }
        )
    else:
        trend_line.append(None)

cross_over = np.zeros(len(df), dtype=bool)
cross_under = np.zeros(len(df), dtype=bool)
for index in range(1, len(df)):
    if not all(np.isfinite(value) for value in (close_values[index], close_values[index - 1], trend_values[index], trend_values[index - 1])):
        continue
    cross_over[index] = close_values[index] > trend_values[index] and close_values[index - 1] <= trend_values[index - 1]
    cross_under[index] = close_values[index] < trend_values[index] and close_values[index - 1] >= trend_values[index - 1]
any_cross = cross_over | cross_under

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {
            "name": "High-Low Trend",
            "data": trend_line,
            "color": "#0000FF",
            "type": "line",
            "lineWidth": 2,
            "overlay": True,
        }
    ],
    "signals": [
        {
            "type": "buy", "name": "Price Cross Over", "text": "Cross Over",
            "color": "#0000FF", "renderMode": "hidden",
            "data": [float(trend_values[index]) if cross_over[index] else None for index in range(len(df))],
        },
        {
            "type": "sell", "name": "Price Cross Under", "text": "Cross Under",
            "color": "#800000", "renderMode": "hidden",
            "data": [float(trend_values[index]) if cross_under[index] else None for index in range(len(df))],
        },
    ],
    "layers": [],
    "tables": [],
    "bar_colors": [None for _ in range(len(df))],
    "background_colors": [None for _ in range(len(df))],
    "calculatedVars": {
        "highAverage": [float(value) if np.isfinite(value) else None for value in high_values],
        "lowAverage": [float(value) if np.isfinite(value) else None for value in low_values],
        "directionEvent": direction_event.tolist(),
        "state": [float(value) if np.isfinite(value) else None for value in state_values],
        "trendLine": [float(value) if np.isfinite(value) else None for value in trend_values],
        "cross": any_cross.tolist(),
        "crossOver": cross_over.tolist(),
        "crossUnder": cross_under.tolist(),
    },
}
