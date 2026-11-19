# ============================================================
# QuantDinger Multi-Length RSI
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Multi-Length RSI"
my_indicator_description = (
    "An ensemble of Wilder-style momentum ratios across a configurable length "
    "range, with adaptive channels and overbought/oversold participation areas."
)

# @param minimum_length int 10 Minimum RSI length range=2:100:1
# @param maximum_length int 20 Maximum RSI length range=2:200:1
# @param overbought float 70.0 Overbought threshold range=50.0:100.0:1.0
# @param oversold float 30.0 Oversold threshold range=0.0:50.0:1.0
# @param source_mode int 0 Price source range=0:3:1

minimum_length = max(2, int(params.get("minimum_length", 10)))
maximum_length = max(2, int(params.get("maximum_length", 20)))
if minimum_length > maximum_length:
    minimum_length, maximum_length = maximum_length, minimum_length
overbought = max(50.0, min(100.0, float(params.get("overbought", 70.0))))
oversold = max(0.0, min(50.0, float(params.get("oversold", 30.0))))
source_mode = max(0, min(3, int(params.get("source_mode", 0))))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

if source_mode == 1:
    source = (high + low) / 2.0
elif source_mode == 2:
    source = (high + low + close) / 3.0
elif source_mode == 3:
    source = (open_price + high + low + close) / 4.0
else:
    source = close.copy()

lengths = list(range(minimum_length, maximum_length + 1))
ensemble_size = len(lengths)
numerators = np.zeros(ensemble_size, dtype=float)
denominators = np.zeros(ensemble_size, dtype=float)
average_rsi = np.full(n, np.nan, dtype=float)
overbought_share = np.zeros(n, dtype=float)
oversold_share = np.zeros(n, dtype=float)

for bar in range(n):
    difference = source[bar] - source[bar - 1] if bar > 0 else 0.0
    absolute_difference = abs(difference)
    values = []
    over_count = 0
    under_count = 0
    for index, period in enumerate(lengths):
        alpha = 1.0 / period
        numerators[index] = alpha * difference + (1.0 - alpha) * numerators[index]
        denominators[index] = alpha * absolute_difference + (1.0 - alpha) * denominators[index]
        if denominators[index] == 0.0:
            oscillator = 50.0
        else:
            oscillator = 50.0 * numerators[index] / denominators[index] + 50.0
        values.append(oscillator)
        if oscillator > overbought:
            over_count += 1
        if oscillator < oversold:
            under_count += 1
    average_rsi[bar] = float(np.mean(values))
    overbought_share[bar] = over_count / ensemble_size
    oversold_share[bar] = under_count / ensemble_size

upper_channel = np.full(n, np.nan, dtype=float)
lower_channel = np.full(n, np.nan, dtype=float)
for index in range(n):
    previous_upper = upper_channel[index - 1] if index > 0 else np.nan
    previous_lower = lower_channel[index - 1] if index > 0 else np.nan
    upper_seed = average_rsi[index] if not np.isfinite(previous_upper) else previous_upper
    lower_seed = average_rsi[index] if not np.isfinite(previous_lower) else previous_lower
    upper_channel[index] = upper_seed + overbought_share[index] * (average_rsi[index] - upper_seed)
    lower_channel[index] = lower_seed + oversold_share[index] * (average_rsi[index] - lower_seed)


def interpolate_color(value, lower_bound, upper_bound):
    if not np.isfinite(value) or not np.isfinite(lower_bound) or not np.isfinite(upper_bound):
        return "#808080"
    if upper_bound == lower_bound:
        ratio = 0.5
    else:
        ratio = max(0.0, min(1.0, (value - lower_bound) / (upper_bound - lower_bound)))
    red = int(round(255 + (33 - 255) * ratio))
    green = int(round(93 + (87 - 93) * ratio))
    blue = int(round(0 + (243 - 0) * ratio))
    return "#%02X%02X%02X" % (red, green, blue)


average_plot = []
channel_cloud = []
overbought_cloud = []
oversold_cloud = []
for index in range(n):
    color = interpolate_color(average_rsi[index], lower_channel[index], upper_channel[index])
    average_plot.append({"value": float(average_rsi[index]), "color": color})
    channel_cloud.append(
        {
            "upper": float(max(upper_channel[index], lower_channel[index])),
            "lower": float(min(upper_channel[index], lower_channel[index])),
            "color": color,
        }
    )
    over_value = float(overbought_share[index] * 100.0)
    under_value = float(100.0 - oversold_share[index] * 100.0)
    overbought_cloud.append({"upper": over_value, "lower": 0.0, "color": "#0CB51A"})
    oversold_cloud.append({"upper": 100.0, "lower": under_value, "color": "#FF1100"})

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Average Multi-Length RSI",
            "data": average_plot,
            "color": "#2157F3",
            "type": "line",
            "lineWidth": 2,
            "overlay": False,
        },
        {
            "name": "Upper Adaptive Channel",
            "data": [float(value) for value in upper_channel],
            "color": "#0CB51A",
            "type": "line",
            "lineWidth": 1,
            "overlay": False,
        },
        {
            "name": "Lower Adaptive Channel",
            "data": [float(value) for value in lower_channel],
            "color": "#FF1100",
            "type": "line",
            "lineWidth": 1,
            "overlay": False,
        },
        {
            "name": "Adaptive Channel Fill",
            "title": False,
            "data": channel_cloud,
            "color": "#2157F3",
            "type": "cloud",
            "opacity": 0.10,
            "overlay": False,
        },
        {
            "name": "Overbought Participation",
            "title": False,
            "data": overbought_cloud,
            "color": "#0CB51A",
            "type": "cloud",
            "opacity": 0.30,
            "overlay": False,
        },
        {
            "name": "Oversold Participation",
            "title": False,
            "data": oversold_cloud,
            "color": "#FF1100",
            "type": "cloud",
            "opacity": 0.30,
            "overlay": False,
        },
        {
            "name": "Midline",
            "data": [50.0 for _ in range(n)],
            "color": "#808080",
            "type": "line",
            "lineWidth": 1,
            "overlay": False,
        },
    ],
    "signals": [],
    "layers": [],
}
