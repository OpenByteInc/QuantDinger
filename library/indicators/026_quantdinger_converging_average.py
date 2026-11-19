# ============================================================
# QuantDinger Converging Average
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Converging Average"
my_indicator_description = (
    "An adaptive moving average whose response accelerates as price extends to new rolling extremes."
)

# @param period int 100 Convergence period range=2:500:1
# @param increment float 10.0 Alpha increment divisor range=1.0:100.0:1.0
# @param fast_period int 10 Fast envelope response range=1:100:1

period = max(2, int(params.get("period", 100)))
increment = max(1.0, float(params.get("increment", 10.0)))
fast_period = max(1, int(params.get("fast_period", 10)))

df = df.copy()
close = df["close"].astype(float)
n = len(df)
values = close.to_numpy(dtype=float)
upper = close.rolling(window=period, min_periods=period).max().to_numpy(dtype=float)
lower = close.rolling(window=period, min_periods=period).min().to_numpy(dtype=float)
initial = close.rolling(window=period, min_periods=period).mean().to_numpy(dtype=float)
converging = np.full(n, np.nan, dtype=float)
fast_line = np.full(n, np.nan, dtype=float)
alpha_values = np.zeros(n, dtype=float)
step = 1.0 / increment

for index in range(n):
    if index == 0:
        fast_line[index] = values[index]
        continue
    previous_ma = converging[index - 1]
    previous_fast = fast_line[index - 1] if np.isfinite(fast_line[index - 1]) else values[index - 1]
    previous_alpha = alpha_values[index - 1]
    crossed = False
    if np.isfinite(previous_ma):
        crossed = (values[index] > previous_ma and values[index - 1] <= previous_ma) or (values[index] < previous_ma and values[index - 1] >= previous_ma)
    current_alpha = previous_alpha
    if crossed:
        current_alpha = 2.0 / (period + 1.0)
    elif np.isfinite(previous_ma) and values[index] > previous_ma and np.isfinite(upper[index]) and np.isfinite(upper[index - 1]) and upper[index] > upper[index - 1]:
        current_alpha += step
    elif np.isfinite(previous_ma) and values[index] < previous_ma and np.isfinite(lower[index]) and np.isfinite(lower[index - 1]) and lower[index] < lower[index - 1]:
        current_alpha += step
    alpha_values[index] = current_alpha
    if np.isfinite(previous_ma):
        converging[index] = previous_ma + previous_alpha * (values[index] - previous_ma)
    elif np.isfinite(initial[index]):
        converging[index] = initial[index]

    current_ma = converging[index]
    if crossed:
        fast_line[index] = (values[index] + previous_fast) / 2.0
    elif np.isfinite(current_ma) and values[index] > current_ma:
        fast_line[index] = max(values[index], previous_fast) + (values[index] - previous_fast) / fast_period
    elif np.isfinite(current_ma):
        fast_line[index] = min(values[index], previous_fast) + (values[index] - previous_fast) / fast_period
    else:
        fast_line[index] = values[index]

ma_plot = []
cloud_plot = []
for index in range(n):
    if not np.isfinite(converging[index]):
        ma_plot.append(None)
        cloud_plot.append(None)
        continue
    rising = fast_line[index] > converging[index]
    color = "#008080" if rising else "#FF0000"
    ma_plot.append({"value": float(converging[index]), "color": color})
    cloud_plot.append({"upper": float(max(fast_line[index], converging[index])), "lower": float(min(fast_line[index], converging[index])), "color": color})

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Converging Average", "data": ma_plot, "color": "#008080", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Convergence Cloud", "title": False, "data": cloud_plot, "color": "#008080", "type": "cloud", "opacity": 0.20, "overlay": True},
    ],
    "signals": [],
    "layers": [],
}
