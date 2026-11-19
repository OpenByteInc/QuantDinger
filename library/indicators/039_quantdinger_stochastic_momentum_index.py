# ============================================================
# QuantDinger Stochastic Momentum Index
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Stochastic Momentum Index"
my_indicator_description = (
    "A smoothed stochastic momentum oscillator with an EMA signal and shaded "
    "overbought and oversold excursions."
)

# @param percent_k_length int 10 Percent K length range=1:300:1
# @param percent_d_length int 3 Percent D length range=1:100:1
# @param signal_length int 10 EMA signal length range=1:300:1
# @param smoothing_period int 5 Smoothing period range=1:100:1
# @param overbought float 40.0 Overbought level range=-100.0:100.0:1.0
# @param oversold float -40.0 Oversold level range=-100.0:100.0:1.0

percent_k_length = max(1, int(params.get("percent_k_length", 10)))
percent_d_length = max(1, int(params.get("percent_d_length", 3)))
signal_length = max(1, int(params.get("signal_length", 10)))
smoothing_period = max(1, int(params.get("smoothing_period", 5)))
overbought = max(-100.0, min(100.0, float(params.get("overbought", 40.0))))
oversold = max(-100.0, min(100.0, float(params.get("oversold", -40.0))))

df = df.copy()
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)
n = len(df)


def pine_ema(series, period):
    values = np.asarray(series, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    seed_values = []
    seeded = False
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        if not seeded:
            seed_values.append(float(value))
            if len(seed_values) == period:
                previous = float(np.mean(seed_values))
                result[index] = previous
                seeded = True
        else:
            previous = alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


lowest_low = low.rolling(window=percent_k_length, min_periods=percent_k_length).min()
highest_high = high.rolling(window=percent_k_length, min_periods=percent_k_length).max()
range_width = highest_high - lowest_low
relative_distance = close - (highest_high + lowest_low) / 2.0
average_relative = pine_ema(relative_distance.to_numpy(dtype=float), percent_d_length)
average_range = pine_ema(range_width.to_numpy(dtype=float), percent_d_length)
smi = np.zeros(n, dtype=float)
valid = np.isfinite(average_relative) & np.isfinite(average_range) & (average_range != 0.0)
smi[valid] = average_relative[valid] / (average_range[valid] / 2.0) * 100.0
smi_series = pd.Series(smi)
smi_smoothed = smi_series.rolling(window=smoothing_period, min_periods=smoothing_period).mean().to_numpy(dtype=float)
signal = pine_ema(smi_smoothed, signal_length)

overbought_cloud = []
oversold_cloud = []
for index, value in enumerate(smi_smoothed):
    if np.isfinite(value) and value > overbought:
        overbought_cloud.append({"upper": float(value), "lower": float(overbought), "color": "#FF0000"})
    else:
        overbought_cloud.append(None)
    if np.isfinite(value) and value < oversold:
        oversold_cloud.append({"upper": float(oversold), "lower": float(value), "color": "#008000"})
    else:
        oversold_cloud.append(None)

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Stochastic Momentum", "data": [float(value) if np.isfinite(value) else None for value in smi_smoothed], "color": "#D0D4DC", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "EMA Signal", "data": [float(value) if np.isfinite(value) else None for value in signal], "color": "#FF0000", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Overbought", "data": [float(overbought) for _ in range(n)], "color": "#808080", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Oversold", "data": [float(oversold) for _ in range(n)], "color": "#808080", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Overbought Excursion", "title": False, "data": overbought_cloud, "color": "#FF0000", "type": "cloud", "opacity": 0.40, "overlay": False},
        {"name": "Oversold Excursion", "title": False, "data": oversold_cloud, "color": "#008000", "type": "cloud", "opacity": 0.40, "overlay": False},
    ],
    "signals": [],
    "layers": [],
}
