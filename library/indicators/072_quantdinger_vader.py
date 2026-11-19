# ============================================================
# QuantDinger VADER
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Volume-Accelerated Energy Ratio"
my_indicator_description = (
    "Demand and supply energy normalized by absolute or relative volume, with "
    "a smoothed net directional signal and optional long-term sentiment."
)

# @param length int 10 Energy period range=1:200:1
# @param energy_average int 5 Energy average range=1:100:1
# @param average_type int 0 Base average type range=0:2:1
# @param signal_smoothing int 3 Signal smoothing range=1:100:1
# @param show_sentiment bool false Show sentiment histogram
# @param sentiment_length int 20 Sentiment period range=1:200:1
# @param volume_mode int 0 Volume calculation mode range=0:2:1
# @param volume_lookback int 20 Relative volume lookback range=1:200:1

length = max(1, int(params.get("length", 10)))
energy_average = max(1, int(params.get("energy_average", 5)))
average_type = max(0, min(2, int(params.get("average_type", 0))))
signal_smoothing = max(1, int(params.get("signal_smoothing", 3)))
show_sentiment = bool(params.get("show_sentiment", False))
sentiment_length = max(1, int(params.get("sentiment_length", 20)))
volume_mode = max(0, min(2, int(params.get("volume_mode", 0))))
volume_lookback = max(1, int(params.get("volume_lookback", 20)))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)


def exponential_average(values, period):
    result = np.full(n, np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index in range(n):
        value = values[index]
        if np.isfinite(value):
            previous = value if not np.isfinite(previous) else alpha * value + (1.0 - alpha) * previous
            result[index] = previous
    return result


def weighted_average(values, period):
    result = np.full(n, np.nan, dtype=float)
    denominator = float(period * (period + 1)) / 2.0
    for index in range(period - 1, n):
        total = 0.0
        valid = True
        for offset in range(period):
            value = values[index - period + 1 + offset]
            if not np.isfinite(value):
                valid = False
                break
            total += value * float(offset + 1)
        if valid:
            result[index] = total / denominator
    return result


def base_average(values, period):
    if average_type == 1:
        return exponential_average(values, period)
    if average_type == 2:
        return pd.Series(values).rolling(period, min_periods=period).mean().to_numpy(dtype=float)
    return weighted_average(values, period)


relative_volume = np.full(n, np.nan, dtype=float)
rolling_volume_high = pd.Series(volume).rolling(volume_lookback, min_periods=volume_lookback).max().to_numpy(dtype=float)
rolling_volume_low = pd.Series(volume).rolling(volume_lookback, min_periods=volume_lookback).min().to_numpy(dtype=float)
for index in range(n):
    difference = rolling_volume_high[index] - rolling_volume_low[index]
    if np.isfinite(difference):
        relative_volume[index] = (volume[index] - rolling_volume_low[index]) / difference if difference != 0.0 else 0.0
volume_acceleration = np.ones(n, dtype=float) if volume_mode == 2 else volume if volume_mode == 1 else relative_volume

price_range = np.full(n, np.nan, dtype=float)
for index in range(1, n):
    price_range[index] = (max(high[index], high[index - 1]) - min(low[index], low[index - 1])) / 2.0
energy = np.zeros(n, dtype=float)
for index in range(1, n):
    if np.isfinite(price_range[index]) and price_range[index] != 0.0 and np.isfinite(volume_acceleration[index]):
        ratio = max(-1.0, min(1.0, (close[index] - close[index - 1]) / price_range[index]))
        energy[index] = ratio * volume_acceleration[index]
demand = np.maximum(energy, 0.0)
supply = np.maximum(-energy, 0.0)

average_volume = base_average(volume_acceleration, length)
demand_average = base_average(demand, length)
supply_average = base_average(supply, length)
demand_ratio = np.divide(demand_average, average_volume, out=np.full(n, np.nan, dtype=float), where=np.isfinite(average_volume) & (average_volume != 0.0))
supply_ratio = np.divide(supply_average, average_volume, out=np.full(n, np.nan, dtype=float), where=np.isfinite(average_volume) & (average_volume != 0.0))
demand_energy = weighted_average(demand_ratio, energy_average) * 100.0
supply_energy = weighted_average(supply_ratio, energy_average) * 100.0
net_energy = demand_energy - supply_energy
signal = weighted_average(net_energy, signal_smoothing)
sentiment_demand = weighted_average(demand_ratio, sentiment_length) * 100.0
sentiment_supply = weighted_average(supply_ratio, sentiment_length) * 100.0
sentiment = weighted_average(sentiment_demand - sentiment_supply, signal_smoothing)

sentiment_plot = []
signal_plot = []
energy_cloud = []
for index in range(n):
    if show_sentiment and np.isfinite(sentiment[index]):
        previous = sentiment[index - 1] if index > 0 and np.isfinite(sentiment[index - 1]) else sentiment[index]
        growing = abs(sentiment[index]) >= abs(previous)
        color = "#1B5E2080" if sentiment[index] >= 0.0 and growing else "#66BB6A80" if sentiment[index] >= 0.0 else "#DC4C4A80" if growing else "#EF8E9880"
        sentiment_plot.append({"value": float(sentiment[index]), "color": color})
    else:
        sentiment_plot.append(None)
    signal_plot.append({"value": float(signal[index]), "color": "#359BFC" if signal[index] >= 0.0 else "#F57F17"} if np.isfinite(signal[index]) else None)
    energy_cloud.append({"upper": float(max(demand_energy[index], supply_energy[index])), "lower": float(min(demand_energy[index], supply_energy[index])), "color": "#16A34A" if demand_energy[index] > supply_energy[index] else "#EF4444"} if np.isfinite(demand_energy[index]) and np.isfinite(supply_energy[index]) else None)

cross_up = np.zeros(n, dtype=bool)
cross_down = np.zeros(n, dtype=bool)
speed_up = np.zeros(n, dtype=bool)
slow_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(signal[index]) and np.isfinite(signal[index - 1]):
        cross_up[index] = signal[index] > 0.0 and signal[index - 1] <= 0.0
        cross_down[index] = signal[index] < 0.0 and signal[index - 1] >= 0.0
    if np.isfinite(signal[index]) and np.isfinite(signal[index - 1]) and np.isfinite(sentiment[index]) and np.isfinite(sentiment[index - 1]):
        speed_up[index] = signal[index] > sentiment[index] and signal[index - 1] <= sentiment[index - 1]
        slow_down[index] = signal[index] < sentiment[index] and signal[index - 1] >= sentiment[index - 1]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Supply Energy", "data": [float(value) if np.isfinite(value) else None for value in supply_energy], "color": "#FB923CB3", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Demand Energy", "data": [float(value) if np.isfinite(value) else None for value in demand_energy], "color": "#22D3EEB3", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Energy Balance", "title": False, "data": energy_cloud, "color": "#16A34A", "type": "cloud", "opacity": 0.20, "overlay": False},
        {"name": "Signal", "data": signal_plot, "color": "#359BFC", "type": "line", "lineWidth": 4, "overlay": False},
        {"name": "Sentiment", "data": sentiment_plot, "color": "#1B5E20", "type": "histogram", "baseValue": 0.0, "lineWidth": 2, "overlay": False},
        {"name": "Zero", "data": [0.0 for _ in range(n)], "color": "#FFEE004D", "type": "line", "lineWidth": 1, "overlay": False},
    ],
    "signals": [],
    "layers": [],
}
