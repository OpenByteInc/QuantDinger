# ============================================================
# QuantDinger SuperTrend Channels
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger SuperTrend Channels"
my_indicator_description = (
    "A recursive SuperTrend regime with expanding upper and lower extrema, "
    "a midpoint, and two directional channel areas."
)

# @param atr_length int 14 ATR period range=1:100:1
# @param atr_multiplier float 2.0 ATR multiplier range=0.1:10.0:0.1

atr_length = max(1, int(params.get("atr_length", 14)))
atr_multiplier = max(0.1, float(params.get("atr_multiplier", 2.0)))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

previous_close = np.concatenate(([close[0]], close[:-1]))
true_range = np.maximum(high - low, np.maximum(np.abs(high - previous_close), np.abs(low - previous_close)))
atr = np.full(n, np.nan, dtype=float)
if n >= atr_length:
    seed = 0.0
    for index in range(atr_length):
        seed += true_range[index]
    atr[atr_length - 1] = seed / atr_length
    for index in range(atr_length, n):
        atr[index] = (atr[index - 1] * (atr_length - 1) + true_range[index]) / atr_length

midpoint = (high + low) / 2.0
upper_rail = np.full(n, np.nan, dtype=float)
lower_rail = np.full(n, np.nan, dtype=float)
regime = np.zeros(n, dtype=int)
supertrend = np.full(n, np.nan, dtype=float)
channel_high = np.full(n, np.nan, dtype=float)
channel_low = np.full(n, np.nan, dtype=float)
channel_mid = np.full(n, np.nan, dtype=float)

for index in range(n):
    if not np.isfinite(atr[index]):
        regime[index] = regime[index - 1] if index > 0 else 0
        continue
    raw_upper = midpoint[index] + atr[index] * atr_multiplier
    raw_lower = midpoint[index] - atr[index] * atr_multiplier
    if index == 0 or not np.isfinite(upper_rail[index - 1]):
        upper_rail[index] = raw_upper
        lower_rail[index] = raw_lower
        regime[index] = 0
    else:
        upper_rail[index] = min(raw_upper, upper_rail[index - 1]) if close[index - 1] < upper_rail[index - 1] else raw_upper
        lower_rail[index] = max(raw_lower, lower_rail[index - 1]) if close[index - 1] > lower_rail[index - 1] else raw_lower
        regime[index] = 1 if close[index] > upper_rail[index] else 0 if close[index] < lower_rail[index] else regime[index - 1]
    supertrend[index] = lower_rail[index] if regime[index] == 1 else upper_rail[index]
    if index == 0 or not np.isfinite(channel_high[index - 1]):
        channel_high[index] = close[index]
        channel_low[index] = close[index]
    else:
        previous_difference = close[index - 1] - supertrend[index - 1]
        current_difference = close[index] - supertrend[index]
        crossed = np.isfinite(previous_difference) and previous_difference * current_difference <= 0.0 and previous_difference != current_difference
        if crossed:
            channel_high[index] = max(channel_high[index - 1], close[index])
            channel_low[index] = min(channel_low[index - 1], close[index])
        elif regime[index] == 1:
            channel_high[index] = max(close[index], channel_high[index - 1])
            channel_low[index] = max(supertrend[index], channel_low[index - 1])
        else:
            channel_high[index] = min(supertrend[index], channel_high[index - 1])
            channel_low[index] = min(close[index], channel_low[index - 1])
    channel_mid[index] = (channel_high[index] + channel_low[index]) / 2.0

upper_plot = []
lower_plot = []
upper_area = []
lower_area = []
for index in range(n):
    if not np.isfinite(channel_mid[index]):
        upper_plot.append(None)
        lower_plot.append(None)
        upper_area.append(None)
        lower_area.append(None)
        continue
    upper_changed = index > 0 and np.isfinite(channel_high[index - 1]) and channel_high[index] != channel_high[index - 1]
    lower_changed = index > 0 and np.isfinite(channel_low[index - 1]) and channel_low[index] != channel_low[index - 1]
    upper_plot.append(None if upper_changed and regime[index] == 1 else float(channel_high[index]))
    lower_plot.append(None if lower_changed and regime[index] == 0 else float(channel_low[index]))
    upper_area.append({"upper": float(max(channel_high[index], channel_mid[index])), "lower": float(min(channel_high[index], channel_mid[index])), "color": "#0CB51A"})
    lower_area.append({"upper": float(max(channel_mid[index], channel_low[index])), "lower": float(min(channel_mid[index], channel_low[index])), "color": "#FF1100"})

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Upper Channel", "data": upper_plot, "color": "#0CB51A", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Channel Average", "data": [float(value) if np.isfinite(value) else None for value in channel_mid], "color": "#FF5D00", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Lower Channel", "data": lower_plot, "color": "#FF1100", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Upper Channel Area", "title": False, "data": upper_area, "color": "#0CB51A", "type": "cloud", "opacity": 0.20, "overlay": True},
        {"name": "Lower Channel Area", "title": False, "data": lower_area, "color": "#FF1100", "type": "cloud", "opacity": 0.20, "overlay": True},
    ],
    "signals": [],
    "layers": [],
}
