# ============================================================
# QuantDinger Trend Magic
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Trend Magic"
my_indicator_description = (
    "A trailing trend overlay driven by the sign of a close-based commodity "
    "channel index and a simple-average true-range envelope."
)

# @param cci_period int 20 CCI period range=2:300:1
# @param atr_multiplier float 1.0 ATR multiplier range=0.1:20.0:0.1
# @param atr_period int 5 ATR period range=1:300:1

cci_period = max(2, int(params.get("cci_period", 20)))
atr_multiplier = float(params.get("atr_multiplier", 1.0))
atr_period = max(1, int(params.get("atr_period", 5)))

df = df.copy()
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)
previous_close = close.shift(1)
true_range = pd.concat(
    [
        high - low,
        (high - previous_close).abs(),
        (low - previous_close).abs(),
    ],
    axis=1,
).max(axis=1, skipna=True)
average_range = true_range.rolling(window=atr_period, min_periods=atr_period).mean()

close_values = close.to_numpy(dtype=float)
cci_values = np.full(len(df), np.nan, dtype=float)
for index in range(cci_period - 1, len(df)):
    window = close_values[index - cci_period + 1 : index + 1]
    mean_value = float(np.mean(window))
    mean_deviation = float(np.mean(np.abs(window - mean_value)))
    if mean_deviation > 0.0:
        cci_values[index] = (close_values[index] - mean_value) / (0.015 * mean_deviation)

up_threshold = (low - average_range * atr_multiplier).to_numpy(dtype=float)
down_threshold = (high + average_range * atr_multiplier).to_numpy(dtype=float)
trend_values = np.full(len(df), np.nan, dtype=float)
previous_trend = 0.0

for index in range(len(df)):
    if not np.isfinite(cci_values[index]) or not np.isfinite(up_threshold[index]) or not np.isfinite(down_threshold[index]):
        continue
    if cci_values[index] >= 0.0:
        current = max(up_threshold[index], previous_trend)
    else:
        current = min(down_threshold[index], previous_trend) if previous_trend != 0.0 else down_threshold[index]
    trend_values[index] = current
    previous_trend = current

plot_data = []
for index in range(len(df)):
    value = trend_values[index]
    if np.isfinite(value):
        plot_data.append(
            {
                "value": float(value),
                "color": "#0022FC" if cci_values[index] >= 0.0 else "#FC0400",
            }
        )
    else:
        plot_data.append(None)

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Trend Magic",
            "data": plot_data,
            "color": "#0022FC",
            "type": "line",
            "lineWidth": 3,
            "overlay": True,
        }
    ],
    "signals": [],
    "layers": [],
}
