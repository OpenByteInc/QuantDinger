# ============================================================
# QuantDinger Momentum Ghost Machine
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

import math

my_indicator_name = "QuantDinger Momentum Ghost Machine"
my_indicator_description = "Blackman-windowed sinc momentum, configurable post filters, convergence histogram and two-bar ghost projection."

# @param source str Close Price source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param momentum_length int 50 Momentum length range=1:500:1
# @param momentum_smoothing float 50.0 Sinc smoothing range=1:500:1
# @param post_smoothing int 4 Post smoothing range=1:500:1
# @param post_smoothing_style str WMA Post smoothing style values=EMA,DEMA,TEMA,WMA,SMA
# @param average_length int 24 Signal average length range=2:500:1
# @param average_style str EMA Signal average style values=EMA,DEMA,TEMA,WMA,SMA
# @param enable_projection bool true Ghost projection
# @param difference_alpha int 25 Histogram transparency range=0:100:1
# @param candle_color_style str Both Candle colors values=MA,Direct,Both,None
# @param momentum_color str #FFFFFF Momentum color
# @param average_color str #F7A000 Average color
# @param bullish_up_color str #2AC075 Bullish rising color
# @param bullish_down_color str #D5FCE9 Bullish falling color
# @param bearish_down_color str #F82934 Bearish falling color
# @param bearish_up_color str #FFC8CB Bearish rising color
# @param glow bool true Glow layers

source_name = str(params.get("source", "Close")).upper()
if source_name not in ("CLOSE", "OPEN", "HIGH", "LOW", "HL2", "HLC3", "OHLC4"):
    source_name = "CLOSE"
momentum_length = min(500, max(1, int(params.get("momentum_length", 50))))
momentum_smoothing = min(500.0, max(1.0, float(params.get("momentum_smoothing", 50.0))))
post_smoothing = min(500, max(1, int(params.get("post_smoothing", 4))))
post_smoothing_style = str(params.get("post_smoothing_style", "WMA")).upper()
if post_smoothing_style not in ("EMA", "DEMA", "TEMA", "WMA", "SMA"):
    post_smoothing_style = "WMA"
average_length = min(500, max(2, int(params.get("average_length", 24))))
average_style = str(params.get("average_style", "EMA")).upper()
if average_style not in ("EMA", "DEMA", "TEMA", "WMA", "SMA"):
    average_style = "EMA"
enable_projection = bool(params.get("enable_projection", True))
difference_alpha = min(100, max(0, int(params.get("difference_alpha", 25))))
candle_color_style = str(params.get("candle_color_style", "Both"))
if candle_color_style not in ("MA", "Direct", "Both", "None"):
    candle_color_style = "Both"
momentum_color = str(params.get("momentum_color", "#FFFFFF"))
average_color = str(params.get("average_color", "#F7A000"))
bullish_up_color = str(params.get("bullish_up_color", "#2AC075"))
bullish_down_color = str(params.get("bullish_down_color", "#D5FCE9"))
bearish_down_color = str(params.get("bearish_down_color", "#F82934"))
bearish_up_color = str(params.get("bearish_up_color", "#FFC8CB"))
glow = bool(params.get("glow", True))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (float(length) + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _wma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, length + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.sum(window * weights) / denominator)
    return result


def _sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def _filter(values, length, style):
    if length <= 1:
        return np.asarray(values, dtype=float).copy()
    if style == "WMA":
        return _wma(values, int(length))
    if style == "SMA":
        return _sma(values, int(length))
    first = _ema(values, length)
    if style == "DEMA":
        return 2.0 * first - _ema(first, length)
    if style == "TEMA":
        second = _ema(first, length)
        third = _ema(second, length)
        return (first - second) * 3.0 + third
    return first


def _sinc(value):
    return 1.0 if value == 0.0 else math.sin(math.pi * value) / (math.pi * value)


def _coefficients(length, frequency):
    midpoint = int((length - 1) / 2)
    cutoff = 1.0 / frequency
    result = np.zeros(length, dtype=float)
    for index in range(length):
        distance = index - midpoint
        window_index = index + 0.5 if length % 2 == 0 else index
        blackman = 0.42 - 0.5 * math.cos((2.0 * math.pi * window_index) / (length - 1)) + 0.08 * math.cos((4.0 * math.pi * window_index) / (length - 1))
        result[index] = _sinc(2.0 * cutoff * distance) * blackman
    return result


def _with_alpha(color, transparency):
    alpha = max(0.0, min(1.0, 1.0 - transparency / 100.0))
    text = str(color).strip()
    if text.startswith("#") and len(text) >= 7:
        try:
            red, green, blue = int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)
            return f"rgba({red},{green},{blue},{alpha:.2f})"
        except ValueError:
            return text
    return text


def _change_color(color, lighter):
    text = str(color).strip()
    if not (text.startswith("#") and len(text) >= 7):
        return text
    try:
        channels = [int(text[index:index + 2], 16) for index in (1, 3, 5)]
    except ValueError:
        return text
    if lighter:
        channels = [int((255 - value) / 3.0 + value) for value in channels]
    else:
        channels = [int(value / 1.5) for value in channels]
    return "#" + "".join(f"{value:02X}" for value in channels)


df = df.copy().reset_index(drop=True)
if "time" not in df.columns:
    df["time"] = np.arange(len(df), dtype=float) * 3600000.0
if "volume" not in df.columns:
    df["volume"] = 0.0
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
times = df["time"].to_numpy(dtype=float)
if len(times) and max(abs(float(value)) for value in times) < 10_000_000_000:
    times = times * 1000.0
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)
source = {"OPEN": opened, "HIGH": high, "LOW": low, "HL2": (high + low) / 2.0, "HLC3": (high + low + close) / 3.0, "OHLC4": (opened + high + low + close) / 4.0}.get(source_name, close)

filter_length = momentum_length * 2
coefficients = _coefficients(filter_length, momentum_smoothing)
normalizer = float(np.sum(coefficients))
sinc_line = np.full(n, np.nan, dtype=float)
if abs(normalizer) > 1e-15:
    for index in range(filter_length + 1, n):
        data = source[index - filter_length + 1:index + 1][::-1]
        sinc_line[index] = float(np.dot(coefficients, data) / normalizer)

offset_value = int((filter_length - 1) / 2)
safe_offset = max(1, offset_value)
raw_delta = (source - sinc_line) / safe_offset
delta = _filter(raw_delta, post_smoothing, post_smoothing_style)
inner_average = _filter(delta, 2, average_style)
average = _filter(inner_average, average_length, average_style)
momo = delta - average
projection = np.full(n, np.nan, dtype=float)
average_projection = np.full(n, np.nan, dtype=float)
for index in range(2, n):
    if np.all(np.isfinite([delta[index], delta[index - 1], delta[index - 2]])):
        projection[index] = ((delta[index] - delta[index - 1]) + (delta[index] - delta[index - 2]) / 2.0) / 2.0
    if np.all(np.isfinite([average[index], average[index - 1], average[index - 2]])):
        average_projection[index] = ((average[index] - average[index - 1]) + (average[index] - average[index - 2]) / 2.0) / 2.0

projected_momentum_1 = delta + projection if enable_projection else np.full(n, np.nan)
projected_momentum_2 = delta + projection * 2.0 if enable_projection else np.full(n, np.nan)
projected_average_1 = average + average_projection if enable_projection else np.full(n, np.nan)
projected_average_2 = average + average_projection * 2.0 if enable_projection else np.full(n, np.nan)
projected_difference_1 = projected_momentum_1 - projected_average_1
projected_difference_2 = projected_momentum_2 - projected_average_2

momo_base_colors = []
momo_colors = []
direct_colors = []
projection_colors_1 = []
projection_colors_2 = []
for index in range(n):
    value = momo[index]
    previous_momo = momo[index - 1] if index >= 1 and np.isfinite(momo[index - 1]) else np.nan
    if not np.isfinite(value):
        base_color = None
    elif value > 0.0:
        base_color = bullish_up_color if np.isfinite(previous_momo) and value > previous_momo else bullish_down_color
    else:
        base_color = bearish_down_color if np.isfinite(previous_momo) and value < previous_momo else bearish_up_color
    momo_base_colors.append(base_color)
    momo_colors.append(_with_alpha(base_color, difference_alpha) if base_color else None)
    previous_delta = delta[index - 1] if index >= 1 and np.isfinite(delta[index - 1]) else np.nan
    if candle_color_style == "Both" and np.isfinite(delta[index]) and base_color:
        if delta[index] > 0.0:
            direct = _change_color(base_color, True) if value < 0.0 else base_color
        else:
            direct = _change_color(base_color, False) if value > 0.0 else base_color
    elif np.isfinite(delta[index]):
        if delta[index] > 0.0:
            direct = bullish_up_color if np.isfinite(previous_delta) and delta[index] > previous_delta else bullish_down_color
        else:
            direct = bearish_down_color if np.isfinite(previous_delta) and delta[index] < previous_delta else bearish_up_color
    else:
        direct = None
    direct_colors.append(direct)
    d1 = projected_difference_1[index]
    d2 = projected_difference_2[index]
    if np.isfinite(d1) and np.isfinite(value):
        color_1 = bullish_up_color if d1 > 0.0 and d1 > value else bullish_down_color if d1 > 0.0 else bearish_down_color if d1 < value else bearish_up_color
    else:
        color_1 = None
    if np.isfinite(d2) and np.isfinite(d1):
        color_2 = bullish_up_color if d2 > 0.0 and d2 > d1 else bullish_down_color if d2 > 0.0 else bearish_down_color if d2 < d1 else bearish_up_color
    else:
        color_2 = None
    projection_colors_1.append(_with_alpha(color_1, 60) if color_1 else None)
    projection_colors_2.append(_with_alpha(color_2, 60) if color_2 else None)

if candle_color_style == "MA":
    bar_colors = momo_base_colors
elif candle_color_style in ("Both", "Direct"):
    bar_colors = direct_colors
else:
    bar_colors = [None] * n

cloud = []
for index in range(n):
    if glow and np.all(np.isfinite([delta[index], average[index]])):
        cloud.append({"upper": float(max(delta[index], average[index])), "lower": float(min(delta[index], average[index])), "color": "rgba(255,255,255,0.10)" if delta[index] > average[index] else "rgba(247,160,0,0.10)"})
    else:
        cloud.append(None)

forecast_1 = [None] * n
forecast_2 = [None] * n
if enable_projection and n:
    if np.isfinite(projected_difference_1[-1]):
        forecast_1[-1] = float(projected_difference_1[-1])
    if np.isfinite(projected_difference_2[-1]):
        forecast_2[-1] = float(projected_difference_2[-1])

layers = []
if enable_projection and n and np.all(np.isfinite([delta[-1], average[-1], projected_momentum_2[-1], projected_average_2[-1]])):
    step = float(np.median(np.diff(times[-min(n, 20):]))) if n >= 2 else 3_600_000.0
    future_time = float(times[-1] + 2.0 * step)
    layers = [
        {"type": "line", "role": "Momentum Projection", "start": float(times[-1]), "end": future_time, "y1": float(delta[-1]), "y2": float(projected_momentum_2[-1]), "color": momentum_color, "style": "dotted", "lineWidth": 1},
        {"type": "line", "role": "Average Projection", "start": float(times[-1]), "end": future_time, "y1": float(average[-1]), "y2": float(projected_average_2[-1]), "color": average_color, "style": "dotted", "lineWidth": 1},
    ]

plots = [
    {"name": "Convergence Divergence", "data": _finite(momo), "color": momo_colors, "type": "histogram" if glow else "column", "baseValue": 0.0, "lineWidth": 5, "overlay": False},
    {"name": "Convergence Glow", "data": _finite(momo) if glow else [None] * n, "color": [_with_alpha(color, difference_alpha + (100 - difference_alpha) / 2.0) if color else None for color in momo_colors], "type": "column", "baseValue": 0.0, "lineWidth": 1, "overlay": False},
    {"name": "Signal Average", "data": _finite(average), "color": average_color, "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Signal Average Glow", "data": _finite(average) if glow else [None] * n, "color": _with_alpha(average_color, 80), "type": "line", "lineWidth": 4, "overlay": False},
    {"name": "Momentum", "data": _finite(delta), "color": momentum_color, "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Momentum Glow", "data": _finite(delta) if glow else [None] * n, "color": _with_alpha(momentum_color, 80), "type": "line", "lineWidth": 4, "overlay": False},
    {"name": "Momentum Fill", "title": False, "data": cloud, "color": momentum_color, "type": "cloud", "overlay": False},
    {"name": "Forecast Difference One", "data": forecast_1, "color": projection_colors_1[-1] if n else bullish_up_color, "type": "column", "baseValue": 0.0, "lineWidth": 1, "overlay": False},
    {"name": "Forecast Difference Two", "data": forecast_2, "color": projection_colors_2[-1] if n else bullish_up_color, "type": "column", "baseValue": 0.0, "lineWidth": 1, "overlay": False},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": layers,
    "tables": [],
    "bar_colors": bar_colors,
    "background_colors": [None] * n,
    "calculatedVars": {
        "sincCoefficients": _finite(coefficients), "sincNormalizer": float(normalizer), "sincLine": _finite(sinc_line), "offset": int(offset_value), "effectiveOffset": int(safe_offset),
        "rawDelta": _finite(raw_delta), "momentum": _finite(delta), "innerAverage": _finite(inner_average), "average": _finite(average), "difference": _finite(momo),
        "momentumProjection": _finite(projection), "averageProjection": _finite(average_projection),
        "projectedMomentum1": _finite(projected_momentum_1), "projectedMomentum2": _finite(projected_momentum_2),
        "projectedAverage1": _finite(projected_average_1), "projectedAverage2": _finite(projected_average_2),
        "projectedDifference1": _finite(projected_difference_1), "projectedDifference2": _finite(projected_difference_2),
        "differenceColors": momo_base_colors, "directColors": direct_colors, "projectionColors1": projection_colors_1, "projectionColors2": projection_colors_2,
        "source": source_name, "postSmoothingStyle": post_smoothing_style, "averageStyle": average_style,
        "projectionEnabled": bool(enable_projection), "glowEnabled": bool(glow), "candleColorStyle": candle_color_style,
    },
}
