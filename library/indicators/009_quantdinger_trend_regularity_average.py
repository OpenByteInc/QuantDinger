# ============================================================
# QuantDinger Trend Regularity Average
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Trend Regularity Average"
my_indicator_description = (
    "An adaptive moving average whose response accelerates when fresh rolling "
    "highs or lows appear consistently."
)

# @param period int 99 Regularity period range=2:300:1

period = max(2, int(params.get("period", 99)))

df = df.copy()
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)
n = len(df)

rolling_high = high.rolling(window=period, min_periods=1).max()
rolling_low = low.rolling(window=period, min_periods=1).min()
fresh_high = rolling_high.diff() > 0.0
fresh_low = rolling_low.diff() < 0.0
regularity_event = (fresh_high | fresh_low).astype(float)
if n:
    regularity_event.iloc[0] = 0.0

adaptation = (
    regularity_event.rolling(window=period, min_periods=period).mean() ** 2
).to_numpy(dtype=float)
close_values = close.to_numpy(dtype=float)
average_values = np.full(n, np.nan, dtype=float)

for index in range(n):
    current_close = close_values[index]
    coefficient = adaptation[index]
    if index == 0 or not np.isfinite(average_values[index - 1]):
        average_values[index] = current_close
    elif np.isfinite(coefficient):
        average_values[index] = average_values[index - 1] + coefficient * (
            current_close - average_values[index - 1]
        )
    else:
        average_values[index] = current_close

average_plot = [
    float(value) if np.isfinite(value) else None for value in average_values
]

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Trend Regularity Average",
            "data": average_plot,
            "color": "#E91E63",
            "type": "line",
            "lineWidth": 2,
            "overlay": True,
        }
    ],
    "signals": [],
    "layers": [],
}
