# ============================================================
# QuantDinger Deviation Trend Profile
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Deviation Trend Profile"
my_indicator_description = (
    "Colors an SMA by normalized slope, marks regime changes, projects ATR-based "
    "deviation levels, and builds a horizontal distribution profile for the active trend."
)

# @param sma_length int 50 SMA length range=5:500:1
# @param deviation_1 int 1 First ATR deviation range=1:10:1
# @param deviation_2 int 2 Second ATR deviation range=1:10:1
# @param deviation_3 int 3 Third ATR deviation range=1:10:1
# @param show_profile bool true Show trend distribution profile
# @param bins int 50 Profile bins range=10:100:1
# @param offset int 30 Profile offset bars range=5:100:1

sma_length = min(500, max(5, int(params.get("sma_length", 50))))
deviation_1 = min(10, max(1, int(params.get("deviation_1", 1))))
deviation_2 = min(10, max(1, int(params.get("deviation_2", 2))))
deviation_3 = min(10, max(1, int(params.get("deviation_3", 3))))
show_profile = bool(params.get("show_profile", True))
bins = min(100, max(10, int(params.get("bins", 50))))
offset = min(100, max(5, int(params.get("offset", 30))))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
times = df["time"].to_numpy(dtype=float)
n = len(df)

if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0
interval = float(np.median(np.diff(times))) if n > 1 else 3600000.0
interval = max(interval, 1.0)

average = pd.Series(close).rolling(sma_length, min_periods=sma_length).mean().to_numpy(dtype=float)
true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))

atr_length = 200
atr = np.full(n, np.nan, dtype=float)
if n >= atr_length:
    atr[atr_length - 1] = float(np.mean(true_range[:atr_length]))
    for index in range(atr_length, n):
        atr[index] = (atr[index - 1] * (atr_length - 1) + true_range[index]) / atr_length

average_difference = np.full(n, np.nan, dtype=float)
for index in range(5, n):
    if np.isfinite(average[index]) and np.isfinite(average[index - 5]):
        average_difference[index] = average[index] - average[index - 5]
normalization_min_periods = min(500, max(20, min(n, 100)))
rolling_maximum = (
    pd.Series(np.abs(average_difference))
    .rolling(500, min_periods=normalization_min_periods)
    .max()
    .to_numpy(dtype=float)
)
normalized_slope = np.full(n, np.nan, dtype=float)
valid_denominator = np.isfinite(rolling_maximum) & (np.abs(rolling_maximum) > 1e-12)
normalized_slope[valid_denominator] = average_difference[valid_denominator] / rolling_maximum[valid_denominator]

def blended_color(value):
    if not np.isfinite(value):
        return "#787B86"
    weight = min(1.0, max(0.0, (float(value) + 0.3) / 0.6))
    down = (250, 40, 86)
    up = (18, 209, 235)
    rgb = tuple(round(down[channel] + (up[channel] - down[channel]) * weight) for channel in range(3))
    return "#%02X%02X%02X" % rgb

trend = None
trend_start = None
trend_up = np.zeros(n, dtype=bool)
trend_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    previous = normalized_slope[index - 1]
    current = normalized_slope[index]
    if not np.isfinite(previous) or not np.isfinite(current):
        continue
    if trend is None:
        trend = bool(current >= 0.0)
        trend_start = index
        trend_up[index] = trend
        trend_down[index] = not trend
    elif previous <= 0.1 and current > 0.1 and not trend:
        trend = True
        trend_start = index
        trend_up[index] = True
    elif previous >= -0.1 and current < -0.1 and trend:
        trend = False
        trend_start = index
        trend_down[index] = True

average_plot = []
for index in range(n):
    if np.isfinite(average[index]):
        average_plot.append({"value": float(average[index]), "color": blended_color(normalized_slope[index])})
    else:
        average_plot.append(None)

layers = []
latest_color = blended_color(normalized_slope[-1]) if n else "#787B86"

def time_for_index(index):
    if n == 0:
        return 0.0
    if index < n:
        return float(times[max(0, index)])
    return float(times[-1] + (index - (n - 1)) * interval)

if n and np.isfinite(average[-1]) and np.isfinite(atr[-1]):
    level_pairs = [
        (average[-1] + atr[-1] * deviation_1, "+1"),
        (average[-1] + atr[-1] * deviation_2, "+2"),
        (average[-1] + atr[-1] * deviation_3, "+3"),
        (average[-1] - atr[-1] * deviation_1, "-1"),
        (average[-1] - atr[-1] * deviation_2, "-2"),
        (average[-1] - atr[-1] * deviation_3, "-3"),
    ]
    future_time = time_for_index(n + 4)
    layers.append(
        {
            "type": "line",
            "startIndex": int(n - 1),
            "end": future_time,
            "y1": float(average[-1]),
            "y2": float(average[-1]),
            "color": latest_color,
            "lineWidth": 2,
        }
    )
    for price, text_value in level_pairs:
        layers.append(
            {
                "type": "line",
                "startIndex": int(n - 1),
                "end": future_time,
                "y1": float(price),
                "y2": float(price),
                "color": "#A0A0A0",
                "lineWidth": 2,
            }
        )
        layers.append(
            {
                "type": "label",
                "timestamp": future_time,
                "price": float(price),
                "text": text_value,
                "color": "rgba(0,0,0,0)",
                "textColor": "#A0A0A0",
                "side": "right",
            }
        )

if show_profile and trend_start is not None and n and np.isfinite(average[-1]) and np.isfinite(atr[-1]):
    profile_minimum = float(average[-1] - atr[-1] * deviation_3)
    profile_maximum = float(average[-1] + atr[-1] * deviation_3)
    step = (profile_maximum - profile_minimum) / bins
    counts = np.zeros(bins, dtype=int)
    if step > 0.0:
        for bar_index in range(trend_start, n):
            price = close[bar_index]
            for bin_index in range(bins):
                lower = profile_minimum + step * bin_index
                upper = lower + step
                if lower - step <= price <= upper + step:
                    counts[bin_index] += 1
        maximum_count = int(np.max(counts)) if len(counts) else 0
        profile_right = n - 1 + offset
        for bin_index in range(bins):
            count = int(counts[bin_index])
            lower = profile_minimum + step * bin_index
            upper = lower + step
            strength = count / maximum_count if maximum_count else 0.0
            alpha = 0.20 + strength * 0.65
            red = int(latest_color[1:3], 16)
            green = int(latest_color[3:5], 16)
            blue = int(latest_color[5:7], 16)
            fill_color = f"rgba({red},{green},{blue},{alpha:.3f})"
            layers.append(
                {
                    "type": "zone",
                    "start": time_for_index(profile_right - count),
                    "end": time_for_index(profile_right),
                    "top": float(upper),
                    "bottom": float(lower),
                    "fillColor": fill_color,
                    "borderColor": "#A0A0A0" if count == 0 else "rgba(0,0,0,0)",
                    "borderSize": 1 if count == 0 else 0,
                }
            )
        layers.append(
            {
                "type": "label",
                "timestamp": time_for_index(profile_right),
                "price": profile_maximum,
                "text": str(n - 1 - trend_start),
                "color": latest_color,
                "textColor": "#FFFFFF",
                "side": "right",
            }
        )

up_marks = [
    {"value": float(average[index]), "color": "#12D1EB", "size": 6}
    if trend_up[index] and np.isfinite(average[index])
    else None
    for index in range(n)
]
down_marks = [
    {"value": float(average[index]), "color": "#FA2856", "size": 6}
    if trend_down[index] and np.isfinite(average[index])
    else None
    for index in range(n)
]

plots = [
    {"name": "Deviation Trend Average", "data": average_plot, "color": "#787B86", "type": "line", "lineWidth": 3, "overlay": True},
    {"name": "Up Trend Change", "data": up_marks, "color": "#12D1EB", "type": "circle", "size": 6, "overlay": True},
    {"name": "Down Trend Change", "data": down_marks, "color": "#FA2856", "type": "circle", "size": 6, "overlay": True},
]
plots = [plot for plot in plots if any(item is not None for item in plot["data"])]
output = {"name": my_indicator_name, "plots": plots, "signals": [], "layers": layers}
