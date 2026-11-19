# ============================================================
# QuantDinger Volume Flow
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Volume Flow"
my_indicator_description = (
    "Bullish and bearish volume averages, raw volume columns, breakout "
    "spikes, and the absolute directional volume imbalance."
)

# @param average_type int 0 Moving average type range=0:2:1
# @param length int 14 Moving average period range=1:200:1
# @param breakout_factor float 3.1 Breakout factor range=0.1:20.0:0.1

average_type = max(0, min(2, int(params.get("average_type", 0))))
length = max(1, int(params.get("length", 14)))
breakout_factor = max(0.1, float(params.get("breakout_factor", 3.1)))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)
bull_volume = np.where(close > open_price, volume, 0.0)
bear_volume = np.where(open_price > close, volume, 0.0)


def exponential_average(values, period):
    result = np.full(n, np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index in range(n):
        previous = values[index] if not np.isfinite(previous) else alpha * values[index] + (1.0 - alpha) * previous
        result[index] = previous
    return result


def moving_average(values):
    if average_type == 0:
        return pd.Series(values).rolling(length, min_periods=length).mean().to_numpy(dtype=float)
    first = exponential_average(values, length)
    if average_type == 1:
        return first
    second = exponential_average(first, length)
    return 2.0 * first - second


bull_average = moving_average(bull_volume)
bear_average = moving_average(bear_volume)
difference = bull_average - bear_average
absolute_difference = np.abs(difference) / 2.5

bull_spike = np.zeros(n, dtype=bool)
bear_spike = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(bull_average[index]) and np.isfinite(bull_average[index - 1]):
        bull_spike[index] = bull_volume[index] > bull_average[index] * breakout_factor and bull_volume[index - 1] <= bull_average[index - 1] * breakout_factor
    if np.isfinite(bear_average[index]) and np.isfinite(bear_average[index - 1]):
        bear_spike[index] = bear_volume[index] > bear_average[index] * breakout_factor and bear_volume[index - 1] <= bear_average[index - 1] * breakout_factor

volume_plot = []
difference_area = []
for index in range(n):
    candle_color = "#5AA650" if close[index] > open_price[index] else "#FF510D"
    volume_plot.append({"value": float(volume[index]), "color": candle_color})
    if np.isfinite(absolute_difference[index]):
        area_color = "#5AA650" if difference[index] > 0.0 else "#FF510D"
        difference_area.append({"upper": float(absolute_difference[index]), "lower": 0.0, "color": area_color})
    else:
        difference_area.append(None)

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Volume", "data": volume_plot, "color": "#5AA650", "type": "histogram", "baseValue": 0.0, "lineWidth": 2, "opacity": 0.20, "overlay": False},
        {"name": "Bull Average", "data": [float(value * 2.0) if np.isfinite(value) else None for value in bull_average], "color": "#5AA650", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Bear Average", "data": [float(value * 2.0) if np.isfinite(value) else None for value in bear_average], "color": "#FF510D", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Bull Volume Spike", "data": [float(volume[i]) if bull_spike[i] else None for i in range(n)], "color": "#5AA650", "type": "histogram", "baseValue": 0.0, "lineWidth": 3, "opacity": 0.50, "overlay": False},
        {"name": "Bear Volume Spike", "data": [float(volume[i]) if bear_spike[i] else None for i in range(n)], "color": "#FF510D", "type": "histogram", "baseValue": 0.0, "lineWidth": 3, "opacity": 0.50, "overlay": False},
        {"name": "Difference Value", "title": False, "data": difference_area, "color": "#5AA650", "type": "cloud", "opacity": 0.40, "overlay": False},
    ],
    "signals": [],
    "layers": [],
}
