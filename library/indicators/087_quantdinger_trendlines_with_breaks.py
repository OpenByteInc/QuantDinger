# ============================================================
# QuantDinger Trendlines with Breaks
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Trendlines with Breaks"
my_indicator_description = (
    "Builds descending and ascending swing trendlines with selectable slope "
    "estimation, optional historical backpainting, extensions, and break events."
)

# @param swing_length int 14 Swing detection lookback range=2:100:1
# @param slope_multiplier float 1.0 Slope multiplier range=0.0:10.0:0.1
# @param slope_method int 0 Slope method: 0 ATR, 1 standard deviation, 2 regression range=0:2:1
# @param backpaint bool true Shift confirmed swing lines back to pivot bars
# @param show_extended bool true Show latest extended trendlines

swing_length = max(2, int(params.get("swing_length", 14)))
slope_multiplier = max(0.0, float(params.get("slope_multiplier", 1.0)))
slope_method = min(2, max(0, int(params.get("slope_method", 0))))
backpaint = bool(params.get("backpaint", True))
show_extended = bool(params.get("show_extended", True))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def pine_rma(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    seed_values = []
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            if np.isfinite(previous):
                result[index] = previous
            continue
        if not np.isfinite(previous):
            seed_values.append(float(value))
            if len(seed_values) < period:
                continue
            previous = float(np.mean(seed_values[-period:]))
        else:
            previous = (previous * (period - 1) + float(value)) / period
        result[index] = previous
    return result


previous_close = np.concatenate(([close[0]], close[:-1])) if n else np.array([], dtype=float)
true_range = np.maximum(high - low, np.maximum(np.abs(high - previous_close), np.abs(low - previous_close)))
atr = pine_rma(true_range, swing_length)

slope = np.full(n, np.nan, dtype=float)
bar_index = np.arange(n, dtype=float)
for index in range(swing_length - 1, n):
    start = index - swing_length + 1
    price_window = close[start : index + 1]
    index_window = bar_index[start : index + 1]
    if slope_method == 0:
        if np.isfinite(atr[index]):
            slope[index] = atr[index] / swing_length * slope_multiplier
    elif slope_method == 1:
        slope[index] = float(np.std(price_window, ddof=0) / swing_length * slope_multiplier)
    else:
        covariance = float(np.mean(price_window * index_window) - np.mean(price_window) * np.mean(index_window))
        variance = float(np.var(index_window, ddof=0))
        slope[index] = abs(covariance) / variance / 2.0 * slope_multiplier if variance != 0.0 else 0.0

pivot_high = np.full(n, np.nan, dtype=float)
pivot_low = np.full(n, np.nan, dtype=float)
for confirmation in range(swing_length * 2, n):
    pivot_index = confirmation - swing_length
    start = pivot_index - swing_length
    end = pivot_index + swing_length + 1
    high_window = high[start:end]
    low_window = low[start:end]
    if high[pivot_index] >= float(np.max(high_window)):
        pivot_high[confirmation] = float(high[pivot_index])
    if low[pivot_index] <= float(np.min(low_window)):
        pivot_low[confirmation] = float(low[pivot_index])

offset = swing_length if backpaint else 0
upper_plot = [None for _ in range(n)]
lower_plot = [None for _ in range(n)]
upper_state = 0.0
lower_state = 0.0
upper_slope = 0.0
lower_slope = 0.0
upper_break_state = 0
lower_break_state = 0
upper_break = np.zeros(n, dtype=bool)
lower_break = np.zeros(n, dtype=bool)
last_upper_anchor = None
last_lower_anchor = None

for index in range(n):
    previous_upper_break = upper_break_state
    previous_lower_break = lower_break_state

    if np.isfinite(pivot_high[index]):
        if np.isfinite(slope[index]):
            upper_slope = float(slope[index])
        upper_state = float(pivot_high[index])
        upper_break_state = 0
        start_index = index - offset
        start_value = upper_state if backpaint else upper_state - upper_slope * swing_length
        last_upper_anchor = (start_index, start_value, upper_slope)
    else:
        upper_state -= upper_slope
        if close[index] > upper_state - upper_slope * swing_length:
            upper_break_state = 1

    if np.isfinite(pivot_low[index]):
        if np.isfinite(slope[index]):
            lower_slope = float(slope[index])
        lower_state = float(pivot_low[index])
        lower_break_state = 0
        start_index = index - offset
        start_value = lower_state if backpaint else lower_state + lower_slope * swing_length
        last_lower_anchor = (start_index, start_value, lower_slope)
    else:
        lower_state += lower_slope
        if close[index] < lower_state + lower_slope * swing_length:
            lower_break_state = 1

    upper_break[index] = upper_break_state > previous_upper_break
    lower_break[index] = lower_break_state > previous_lower_break

    target = index - offset
    if target >= 0:
        if not np.isfinite(pivot_high[index]):
            upper_value = upper_state if backpaint else upper_state - upper_slope * swing_length
            upper_plot[target] = float(upper_value)
        if not np.isfinite(pivot_low[index]):
            lower_value = lower_state if backpaint else lower_state + lower_slope * swing_length
            lower_plot[target] = float(lower_value)

extended_upper = [None for _ in range(n)]
extended_lower = [None for _ in range(n)]
if show_extended and last_upper_anchor is not None:
    start_index, start_value, line_slope = last_upper_anchor
    for index in range(max(0, start_index), n):
        extended_upper[index] = float(start_value - line_slope * (index - start_index))
if show_extended and last_lower_anchor is not None:
    start_index, start_value, line_slope = last_lower_anchor
    for index in range(max(0, start_index), n):
        extended_lower[index] = float(start_value + line_slope * (index - start_index))

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Upper Trendline", "data": upper_plot, "color": "#008080", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Lower Trendline", "data": lower_plot, "color": "#FF0000", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Upper Extension", "data": extended_upper, "color": "rgba(0, 128, 128, 0.55)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Lower Extension", "data": extended_lower, "color": "rgba(255, 0, 0, 0.55)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {
            "type": "buy",
            "text": "B",
            "name": "Upper Break",
            "data": [float(low[index]) if upper_break[index] else None for index in range(n)],
            "color": "#008080",
            "renderMode": "events",
        },
        {
            "type": "sell",
            "text": "B",
            "name": "Lower Break",
            "data": [float(high[index]) if lower_break[index] else None for index in range(n)],
            "color": "#FF0000",
            "renderMode": "events",
        },
    ],
    "layers": [],
}
