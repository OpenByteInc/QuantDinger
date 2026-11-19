# ============================================================
# QuantDinger Signal Moving Average
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Signal Moving Average"
my_indicator_description = (
    "A correlation-gated adaptive average that follows its target directly in "
    "strong regimes and advances by target movement in weaker regimes."
)

# @param length int 50 Signal length range=2:500:1
# @param source_mode int 0 Price source range=0:3:1
# @param show_fill bool true Fill price-to-average state

length = max(2, int(params.get("length", 50)))
source_mode = max(0, min(3, int(params.get("source_mode", 0))))
show_fill = bool(params.get("show_fill", True))

df = df.copy()
open_price = df["open"].astype(float)
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)
if source_mode == 1:
    source = (high + low) / 2.0
elif source_mode == 2:
    source = (high + low + close) / 3.0
elif source_mode == 3:
    source = (open_price + high + low + close) / 4.0
else:
    source = close.copy()
n = len(df)

target = source.rolling(window=length, min_periods=length).mean()
bar_index = pd.Series(np.arange(n, dtype=float), index=source.index)
correlation = source.rolling(window=length, min_periods=length).corr(bar_index)
r_squared = correlation.pow(2).to_numpy(dtype=float)
target_values = target.to_numpy(dtype=float)
source_values = source.to_numpy(dtype=float)
average = np.full(n, np.nan, dtype=float)
state = np.zeros(n, dtype=int)
for index in range(n):
    previous_average = average[index - 1] if index > 0 else np.nan
    previous_state = state[index - 1] if index > 0 else 0
    if not np.isfinite(target_values[index]):
        state[index] = previous_state
        continue
    previous_target = target_values[index - 1] if index > 0 and np.isfinite(target_values[index - 1]) else target_values[index]
    target_change = abs(target_values[index] - previous_target)
    if np.isfinite(r_squared[index]) and r_squared[index] > 0.5:
        previous_source = source_values[index - 1] if index > 0 else source_values[index]
        state[index] = 1 if previous_source > previous_target else -1 if previous_source < previous_target else 0
        seed = previous_average if np.isfinite(previous_average) else target_values[index]
        average[index] = r_squared[index] * target_values[index] + (1.0 - r_squared[index]) * seed
    else:
        state[index] = previous_state
        seed = previous_average if np.isfinite(previous_average) else target_values[index]
        average[index] = seed - target_change * state[index]

average_plot = []
fill_plot = []
for index in range(n):
    if not np.isfinite(average[index]):
        average_plot.append(None)
        fill_plot.append(None)
        continue
    color = "#0CB51A" if state[index] == 1 else "#FF1100"
    average_plot.append({"value": float(average[index]), "color": color})
    if show_fill:
        fill_plot.append(
            {
                "upper": float(max(source_values[index], average[index])),
                "lower": float(min(source_values[index], average[index])),
                "color": "#0CB51A" if source_values[index] > average[index] else "#FF1100",
            }
        )
    else:
        fill_plot.append(None)

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Signal Average", "data": average_plot, "color": "#0CB51A", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Price State Fill", "title": False, "data": fill_plot, "color": "#0CB51A", "type": "cloud", "opacity": 0.20, "overlay": True},
    ],
    "signals": [],
    "layers": [],
}
