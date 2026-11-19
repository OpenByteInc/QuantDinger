# ============================================================
# QuantDinger CCI SuperTrend
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger CCI SuperTrend"
my_indicator_description = (
    "A compact ATR trail whose direction and ratcheting behavior are driven "
    "by a close-based commodity channel index."
)

# @param cci_period int 28 CCI period range=2:300:1
# @param cci_midline float 0.0 CCI midline range=-300.0:300.0:1.0
# @param atr_factor float 3.0 ATR factor range=1.0:100.0:0.5
# @param atr_period int 3 ATR period range=1:100:1

cci_period = max(2, int(params.get("cci_period", 28)))
cci_midline = float(params.get("cci_midline", 0.0))
atr_factor = float(params.get("atr_factor", 3.0))
atr_period = max(1, int(params.get("atr_period", 3)))

df = df.copy()
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)
n = len(df)
close_values = close.to_numpy(dtype=float)
cci_values = np.full(n, np.nan, dtype=float)
for index in range(cci_period - 1, n):
    window = close_values[index - cci_period + 1 : index + 1]
    mean_value = float(np.mean(window))
    deviation = float(np.mean(np.abs(window - mean_value)))
    if deviation > 0.0:
        cci_values[index] = (close_values[index] - mean_value) / (0.015 * deviation)

previous_close = close.shift(1)
true_range = pd.concat(
    [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
).max(axis=1, skipna=True)
tr_values = true_range.to_numpy(dtype=float)
atr_values = np.full(n, np.nan, dtype=float)
if n >= atr_period:
    atr_values[atr_period - 1] = float(np.mean(tr_values[:atr_period]))
    for index in range(atr_period, n):
        atr_values[index] = (atr_values[index - 1] * (atr_period - 1) + tr_values[index]) / atr_period

hl2 = ((high + low) / 2.0).to_numpy(dtype=float)
up = hl2 - atr_factor * atr_values
down = hl2 + atr_factor * atr_values
trend_up = np.full(n, np.nan, dtype=float)
trend_down = np.full(n, np.nan, dtype=float)
trend = np.ones(n, dtype=int)
trail = np.full(n, np.nan, dtype=float)
for index in range(n):
    previous_up = trend_up[index - 1] if index > 0 and np.isfinite(trend_up[index - 1]) else 0.0
    previous_down = trend_down[index - 1] if index > 0 and np.isfinite(trend_down[index - 1]) else 0.0
    previous_cci = cci_values[index - 1] if index > 0 else np.nan
    if np.isfinite(up[index]):
        trend_up[index] = max(up[index], previous_up) if np.isfinite(previous_cci) and previous_cci > cci_midline else up[index]
    if np.isfinite(down[index]):
        trend_down[index] = min(down[index], previous_down) if np.isfinite(previous_cci) and previous_cci < cci_midline else down[index]
    if np.isfinite(cci_values[index]):
        if cci_values[index] > cci_midline:
            trend[index] = 1
        elif cci_values[index] < cci_midline:
            trend[index] = -1
        elif index > 0:
            trend[index] = trend[index - 1]
    elif index > 0:
        trend[index] = trend[index - 1]
    selected = trend_up[index] if trend[index] == 1 else trend_down[index]
    if np.isfinite(selected):
        trail[index] = selected

trail_plot = []
for index, value in enumerate(trail):
    trail_plot.append({"value": float(value), "color": "#008000" if close_values[index] >= value else "#FF0000"} if np.isfinite(value) else None)

output = {
    "name": my_indicator_name,
    "plots": [{"name": "CCI SuperTrend", "data": trail_plot, "color": "#008000", "type": "line", "lineWidth": 4, "overlay": True}],
    "signals": [],
    "layers": [],
}
