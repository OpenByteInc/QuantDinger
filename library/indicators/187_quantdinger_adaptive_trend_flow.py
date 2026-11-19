# ============================================================
# QuantDinger Adaptive Trend Flow
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Adaptive Trend Flow"
my_indicator_description = (
    "Combines fast and slow typical-price averages with smoothed volatility bands to create "
    "a stateful adaptive trend level, directional shading, and regime-change signals."
)

# @param main_length int 10 Main trend length range=2:500:1
# @param smoothing_length int 14 Volatility smoothing length range=2:500:1
# @param sensitivity float 2.0 Volatility-band sensitivity range=0.1:10.0:0.1
# @param bullish_color str #00FFAA Bullish color
# @param bearish_color str #FF3B3B Bearish color
# @param color_bars bool true Color price bars
# @param color_background bool true Color chart background
# @param show_signals bool true Show regime-change signals

main_length = min(500, max(2, int(params.get("main_length", 10))))
smoothing_length = min(500, max(2, int(params.get("smoothing_length", 14))))
sensitivity = min(10.0, max(0.1, float(params.get("sensitivity", 2.0))))
bullish_color = str(params.get("bullish_color", "#00FFAA"))
bearish_color = str(params.get("bearish_color", "#FF3B3B"))
color_bars = bool(params.get("color_bars", True))
color_background = bool(params.get("color_background", True))
show_signals = bool(params.get("show_signals", True))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

def rgba(color, alpha):
    value = str(color).strip()
    if value.startswith("#") and len(value) == 7:
        try:
            red = int(value[1:3], 16)
            green = int(value[3:5], 16)
            blue = int(value[5:7], 16)
            return f"rgba({red},{green},{blue},{alpha:.3f})"
        except ValueError:
            return value
    return value

def ema(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) == 0:
        return result
    alpha = 2.0 / (length + 1.0)
    state = float(source[0]) if np.isfinite(source[0]) else 0.0
    result[0] = state
    for index in range(1, len(source)):
        value = float(source[index]) if np.isfinite(source[index]) else state
        state = alpha * value + (1.0 - alpha) * state
        result[index] = state
    return result

def rolling_std(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(length - 1, len(source)):
        window = source[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.std(window, ddof=0))
    return result

def point_series(values, colors):
    result = []
    for index, value in enumerate(values):
        if not np.isfinite(value):
            result.append(None)
        else:
            result.append({"value": float(value), "color": colors[index]})
    return result

typical_price = (high + low + close) / 3.0
fast_average = ema(typical_price, main_length)
slow_average = ema(typical_price, main_length * 2)
basis = (fast_average + slow_average) / 2.0
volatility = rolling_std(typical_price, main_length)
smoothed_volatility = ema(np.nan_to_num(volatility, nan=0.0), smoothing_length)
upper_band = basis + smoothed_volatility * sensitivity
lower_band = basis - smoothed_volatility * sensitivity

trend = np.zeros(n, dtype=int)
active_level = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index == 0:
        trend[index] = 1 if close[index] > basis[index] else -1
    else:
        prior_trend = trend[index - 1]
        if prior_trend == 1:
            trend[index] = -1 if close[index] < lower_band[index] else 1
        else:
            trend[index] = 1 if close[index] > upper_band[index] else -1
    active_level[index] = lower_band[index] if trend[index] == 1 else upper_band[index]

long_signal = np.r_[False, (trend[1:] == 1) & (trend[:-1] == -1)] if n else np.zeros(0, dtype=bool)
short_signal = np.r_[False, (trend[1:] == -1) & (trend[:-1] == 1)] if n else np.zeros(0, dtype=bool)
trend_colors = [bullish_color if value == 1 else bearish_color for value in trend]
level_colors = [bullish_color if close[index] > active_level[index] else bearish_color for index in range(n)]

intensity = np.zeros(n, dtype=float)
for index in range(n):
    if index == 0 or trend[index] != trend[index - 1]:
        intensity[index] = 1.0
    else:
        intensity[index] = min(20.0, intensity[index - 1] + 1.0)

cloud_data = []
for index in range(n):
    if not np.isfinite(basis[index]) or not np.isfinite(active_level[index]):
        cloud_data.append(None)
        continue
    cloud_data.append({
        "upper": float(max(basis[index], active_level[index])),
        "lower": float(min(basis[index], active_level[index])),
        "color": rgba(trend_colors[index], 0.16),
    })

plots = [
    {
        "name": "Trend Basis",
        "data": point_series(basis, trend_colors),
        "color": bullish_color,
        "type": "line",
        "lineWidth": 2,
        "overlay": True,
    },
    {
        "name": "Adaptive Level Shadow",
        "title": False,
        "data": point_series(active_level, [rgba(color, 0.28) for color in level_colors]),
        "color": rgba(bullish_color, 0.28),
        "type": "line",
        "lineWidth": 5,
        "overlay": True,
    },
    {
        "name": "Adaptive Level",
        "data": point_series(active_level, level_colors),
        "color": bullish_color,
        "type": "line",
        "lineWidth": 2,
        "overlay": True,
    },
    {"name": "Trend Flow", "title": False, "data": cloud_data, "type": "cloud", "overlay": True},
]

signals = []
if show_signals:
    signals = [
        {
            "type": "buy",
            "name": "Adaptive Trend Flow Long",
            "text": "L",
            "color": bullish_color,
            "renderMode": "events",
            "data": [
                {"value": float(active_level[index]), "text": "L", "color": bullish_color}
                if long_signal[index] and np.isfinite(active_level[index]) else None
                for index in range(n)
            ],
        },
        {
            "type": "sell",
            "name": "Adaptive Trend Flow Short",
            "text": "S",
            "color": bearish_color,
            "renderMode": "events",
            "data": [
                {"value": float(active_level[index]), "text": "S", "color": bearish_color}
                if short_signal[index] and np.isfinite(active_level[index]) else None
                for index in range(n)
            ],
        },
    ]

bar_colors = [rgba(trend_colors[index], 0.85) if color_bars else None for index in range(n)]
background_colors = []
for index in range(n):
    if not color_background:
        background_colors.append(None)
        continue
    alpha = 0.05 + 0.15 * (intensity[index] / 20.0)
    background_colors.append(rgba(trend_colors[index], alpha))

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "tables": [],
    "bar_colors": bar_colors,
    "background_colors": background_colors,
}
