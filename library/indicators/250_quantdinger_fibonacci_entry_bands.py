# ============================================================
# QuantDinger Fibonacci Entry Bands
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Fibonacci Entry Bands"
my_indicator_description = (
    "Uses a double-EMA trend basis and Fibonacci volatility distances to mark "
    "trend entries, basis bounces, configurable take-profit crosses, and bar regimes."
)

# @param tp_aggressiveness str low Take-profit aggressiveness values=low,medium,high
# @param source str hlc3 Price source values=open,high,low,close,hl2,hlc3,ohlc4
# @param length int 21 Basis length range=1:1000:1
# @param volatility_length int 14 ATR or deviation length range=1:1000:1
# @param use_atr bool true Use ATR instead of source deviation
# @param show_take_profit bool true Show take-profit crosses
# @param show_bounces bool true Show basis-bounce arrows
# @param color_bars bool true Color price bars
# @param bullish_color str #00FFBB Bullish color
# @param bearish_color str #FF1100 Bearish color
# @param basis_color str #808080 Basis color


def clamp(value, lower, upper):
    return min(upper, max(lower, value))


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def pine_ema(values, length):
    source_values = np.asarray(values, dtype=float)
    result = np.full(len(source_values), np.nan)
    alpha = 2.0 / (length + 1.0)
    previous = np.nan
    for index, value in enumerate(source_values):
        if not np.isfinite(value):
            continue
        previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def pine_rma(values, length):
    source_values = np.asarray(values, dtype=float)
    result = np.full(len(source_values), np.nan)
    seed = []
    previous = np.nan
    for index, value in enumerate(source_values):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed.append(float(value))
            if len(seed) < length:
                continue
            previous = float(np.mean(seed[-length:]))
        else:
            previous = (previous * (length - 1.0) + float(value)) / length
        result[index] = previous
    return result


def rgba(color, opacity):
    value = str(color).strip().lstrip("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    try:
        red, green, blue = int(value[:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except (ValueError, IndexError):
        red, green, blue = 128, 128, 128
    return f"rgba({red},{green},{blue},{clamp(float(opacity), 0.0, 1.0):.3f})"


def crossing(first, second, above=True):
    result = np.zeros(len(first), dtype=bool)
    for index in range(1, len(first)):
        if not all(np.isfinite(value) for value in (first[index], first[index - 1], second[index], second[index - 1])):
            continue
        result[index] = (
            first[index] > second[index] and first[index - 1] <= second[index - 1]
            if above else
            first[index] < second[index] and first[index - 1] >= second[index - 1]
        )
    return result


tp_aggressiveness = str(params.get("tp_aggressiveness", "low")).lower()
source_name = str(params.get("source", "hlc3")).lower()
length = clamp(int(params.get("length", 21)), 1, 1000)
volatility_length = clamp(int(params.get("volatility_length", 14)), 1, 1000)
use_atr = bool(params.get("use_atr", True))
show_take_profit = bool(params.get("show_take_profit", True))
show_bounces = bool(params.get("show_bounces", True))
color_bars = bool(params.get("color_bars", True))
bullish_color = str(params.get("bullish_color", "#00FFBB"))
bearish_color = str(params.get("bearish_color", "#FF1100"))
basis_color = str(params.get("basis_color", "#808080"))
if tp_aggressiveness not in ("low", "medium", "high"):
    tp_aggressiveness = "low"

df = df.copy()
open_values = pd.to_numeric(df["open"], errors="coerce").to_numpy(dtype=float)
high_values = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype=float)
low_values = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype=float)
close_values = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
n = len(df)
sources = {
    "open": open_values,
    "high": high_values,
    "low": low_values,
    "close": close_values,
    "hl2": (high_values + low_values) * 0.5,
    "hlc3": (high_values + low_values + close_values) / 3.0,
    "ohlc4": (open_values + high_values + low_values + close_values) * 0.25,
}
source = sources.get(source_name, sources["hlc3"])

ema_once = pine_ema(source, length)
basis = pine_ema(ema_once, length)
true_range = np.full(n, np.nan)
if n:
    true_range[0] = high_values[0] - low_values[0]
for index in range(1, n):
    true_range[index] = max(
        high_values[index] - low_values[index],
        abs(high_values[index] - close_values[index - 1]),
        abs(low_values[index] - close_values[index - 1]),
    )
if use_atr:
    volatility = pine_rma(true_range, volatility_length)
else:
    volatility = pd.Series(source).rolling(length, min_periods=length).std(ddof=0).to_numpy(dtype=float)

trend = np.zeros(n, dtype=int)
previous_trend = 0
for index in range(1, n):
    if basis[index] > basis[index - 1]:
        previous_trend = 1
    elif basis[index] < basis[index - 1]:
        previous_trend = -1
    trend[index] = previous_trend

multipliers = (0.618, 1.0, 1.618, 2.618)
upper_bands = [basis + volatility * multiplier for multiplier in multipliers]
lower_bands = [basis - volatility * multiplier for multiplier in multipliers]
upper_visible = [np.where(trend == -1, values, np.nan) for values in upper_bands]
lower_visible = [np.where(trend == 1, values, np.nan) for values in lower_bands]

upper_cloud = [
    {"upper": float(max(upper_visible[0][index], upper_visible[3][index])), "lower": float(min(upper_visible[0][index], upper_visible[3][index])), "color": rgba(bearish_color, 0.30)}
    if np.isfinite(upper_visible[0][index]) and np.isfinite(upper_visible[3][index]) else None
    for index in range(n)
]
lower_cloud = [
    {"upper": float(max(lower_visible[0][index], lower_visible[3][index])), "lower": float(min(lower_visible[0][index], lower_visible[3][index])), "color": rgba(bullish_color, 0.30)}
    if np.isfinite(lower_visible[0][index]) and np.isfinite(lower_visible[3][index]) else None
    for index in range(n)
]

normal_distance = np.zeros(n, dtype=float)
denominator = volatility * multipliers[3]
valid_denominator = np.isfinite(denominator) & (denominator != 0.0)
normal_distance[valid_denominator] = np.minimum(np.abs(close_values[valid_denominator] - basis[valid_denominator]) / denominator[valid_denominator], 1.0)
transparency = np.minimum(np.round(normal_distance * 100.0), 70.0)
average_open_close = (open_values + close_values) * 0.5
bar_colors = [None for _ in range(n)]
if color_bars:
    for index in range(n):
        if trend[index] == 1:
            bar_colors[index] = basis_color if average_open_close[index] < basis[index] else rgba(bullish_color, 1.0 - transparency[index] / 100.0)
        elif trend[index] == -1:
            bar_colors[index] = basis_color if average_open_close[index] > basis[index] else rgba(bearish_color, 1.0 - transparency[index] / 100.0)

previous_basis = np.full(n, np.nan)
if n > 1:
    previous_basis[1:] = basis[:-1]
long_signal = crossing(basis, previous_basis, True)
short_signal = crossing(basis, previous_basis, False)

band_index = 3 if tp_aggressiveness == "low" else 1 if tp_aggressiveness == "medium" else 0
take_profit_long_band = lower_bands[band_index]
take_profit_short_band = upper_bands[band_index]
take_profit_long_cross = crossing(close_values, take_profit_long_band, False)
take_profit_short_cross = crossing(close_values, take_profit_short_band, True)
rejection_long = show_take_profit & take_profit_long_cross & (trend == -1)
rejection_short = show_take_profit & take_profit_short_cross & (trend == 1)
long_bounce = show_bounces & (trend == 1) & (low_values < basis) & (close_values > basis) & ~long_signal
short_bounce = show_bounces & (trend == -1) & (high_values > basis) & (close_values < basis) & ~short_signal

plots = [
    {"name": "Basis", "data": finite(basis), "color": basis_color, "type": "line", "lineWidth": 1, "overlay": True},
]
for number in range(4):
    plots.append({
        "name": f"Upper {multipliers[number]:g}", "data": finite(upper_visible[number]),
        "color": rgba(bearish_color, (0.40, 0.50, 0.60, 0.70)[number]),
        "type": "line", "lineWidth": 1, "overlay": True,
    })
    plots.append({
        "name": f"Lower {multipliers[number]:g}", "data": finite(lower_visible[number]),
        "color": rgba(bullish_color, (0.40, 0.50, 0.60, 0.70)[number]),
        "type": "line", "lineWidth": 1, "overlay": True,
    })
plots.extend([
    {"name": "Downtrend Band", "title": False, "data": upper_cloud, "type": "cloud", "overlay": True},
    {"name": "Uptrend Band", "title": False, "data": lower_cloud, "type": "cloud", "overlay": True},
    {"name": "Long Entry Mark", "title": False, "data": [float(low_values[index]) if long_signal[index] else None for index in range(n)], "color": bullish_color, "type": "scatter", "size": 5, "overlay": True},
    {"name": "Short Entry Mark", "title": False, "data": [float(high_values[index]) if short_signal[index] else None for index in range(n)], "color": bearish_color, "type": "scatter", "size": 5, "overlay": True},
    {"name": "Long Rejection Mark", "title": False, "data": [float(low_values[index]) if rejection_long[index] else None for index in range(n)], "color": bearish_color, "type": "scatter", "size": 4, "overlay": True},
    {"name": "Short Rejection Mark", "title": False, "data": [float(high_values[index]) if rejection_short[index] else None for index in range(n)], "color": bullish_color, "type": "scatter", "size": 4, "overlay": True},
    {"name": "Long Bounce Mark", "title": False, "data": [float(low_values[index]) if long_bounce[index] else None for index in range(n)], "color": bullish_color, "type": "scatter", "size": 3, "overlay": True},
    {"name": "Short Bounce Mark", "title": False, "data": [float(high_values[index]) if short_bounce[index] else None for index in range(n)], "color": bearish_color, "type": "scatter", "size": 3, "overlay": True},
])

signals = [
    {"type": "buy", "name": "Long Entry", "text": "▲", "color": bullish_color, "renderMode": "hidden", "data": [float(low_values[index]) if long_signal[index] else None for index in range(n)]},
    {"type": "sell", "name": "Short Entry", "text": "▼", "color": bearish_color, "renderMode": "hidden", "data": [float(high_values[index]) if short_signal[index] else None for index in range(n)]},
    {"type": "sell", "name": "Rejection Long", "text": "×", "color": bearish_color, "renderMode": "hidden", "data": [float(low_values[index]) if rejection_long[index] else None for index in range(n)]},
    {"type": "buy", "name": "Rejection Short", "text": "×", "color": bullish_color, "renderMode": "hidden", "data": [float(high_values[index]) if rejection_short[index] else None for index in range(n)]},
    {"type": "buy", "name": "Long Bounce", "text": "▲", "color": bullish_color, "renderMode": "hidden", "data": [float(low_values[index]) if long_bounce[index] else None for index in range(n)]},
    {"type": "sell", "name": "Short Bounce", "text": "▼", "color": bearish_color, "renderMode": "hidden", "data": [float(high_values[index]) if short_bounce[index] else None for index in range(n)]},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": [],
    "tables": [],
    "bar_colors": bar_colors,
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "source": finite(source), "emaOnce": finite(ema_once), "basis": finite(basis),
        "trueRange": finite(true_range), "volatility": finite(volatility), "trend": trend.tolist(),
        "upperBands": [finite(values) for values in upper_bands], "lowerBands": [finite(values) for values in lower_bands],
        "normalDistance": finite(normal_distance), "transparency": finite(transparency),
        "longSignal": long_signal.tolist(), "shortSignal": short_signal.tolist(),
        "takeProfitBandIndex": int(band_index), "takeProfitLongBand": finite(take_profit_long_band), "takeProfitShortBand": finite(take_profit_short_band),
        "takeProfitLongCross": take_profit_long_cross.tolist(), "takeProfitShortCross": take_profit_short_cross.tolist(),
        "rejectionLong": rejection_long.tolist(), "rejectionShort": rejection_short.tolist(),
        "longBounce": long_bounce.tolist(), "shortBounce": short_bounce.tolist(),
    },
}
