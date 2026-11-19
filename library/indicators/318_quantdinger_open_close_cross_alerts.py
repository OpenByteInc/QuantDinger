# ============================================================
# QuantDinger Open-Close Cross Alerts
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Open-Close Cross Alerts"
my_indicator_description = "Compares selectable open/close averages with a non-repainting period-multiplier simulation and optional oscillator divergences."

# @param use_alternate bool true Use alternate-period simulation
# @param resolution_multiplier int 6 Alternate-period multiplier range=1:50:1
# @param average_type str ZEMA Average type values=SMA,EMA,DEMA,TEMA,WMA,VWMA,SMMA,HullMA,ZEMA,SSMA,TMA
# @param average_length int 8 Average period range=1:500:1
# @param show_divergence_channel bool false Show divergence channel
# @param divergence_width float 0.5 Divergence width factor range=0:3:0.1
# @param show_hidden_divergence bool false Show hidden divergence
# @param show_regular_divergence bool false Show regular divergence

use_alternate = bool(params.get("use_alternate", True))
resolution_multiplier = min(50, max(1, int(params.get("resolution_multiplier", 6))))
average_type = str(params.get("average_type", "ZEMA"))
valid_types = ("SMA", "EMA", "DEMA", "TEMA", "WMA", "VWMA", "SMMA", "HullMA", "ZEMA", "SSMA", "TMA")
if average_type not in valid_types:
    average_type = "SMA"
average_length = min(500, max(1, int(params.get("average_length", 8))))
show_divergence_channel = bool(params.get("show_divergence_channel", False))
divergence_width = min(3.0, max(0.0, float(params.get("divergence_width", 0.5))))
show_hidden_divergence = bool(params.get("show_hidden_divergence", False))
show_regular_divergence = bool(params.get("show_regular_divergence", False))
show_divergence_channel = show_divergence_channel and (show_hidden_divergence or show_regular_divergence)


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _wma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, period + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.sum(window * weights) / denominator)
    return result


def _vwma(values, volumes, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        source_window = values[index - period + 1:index + 1]
        volume_window = volumes[index - period + 1:index + 1]
        denominator = float(np.sum(volume_window))
        if np.all(np.isfinite(source_window)) and np.all(np.isfinite(volume_window)) and abs(denominator) > 1e-12:
            result[index] = float(np.sum(source_window * volume_window) / denominator)
    return result


def _smma(values, period):
    seed = _sma(values, period)
    result = np.full(len(values), np.nan, dtype=float)
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(previous):
            result[index] = seed[index]
        elif np.isfinite(value):
            result[index] = (previous * (period - 1) + value) / period
        previous = result[index]
    return result


def _dema(values, period):
    first = _ema(values, period)
    return 2.0 * first - _ema(first, period)


def _tema(values, period):
    first = _ema(values, period)
    second = _ema(first, period)
    return 3.0 * (first - second) + _ema(second, period)


def _hma(values, period):
    half = max(1, int(period / 2))
    root = max(1, int(math.floor(math.sqrt(period) + 0.5)))
    return _wma(2.0 * _wma(values, half) - _wma(values, period), root)


def _zema(values, period):
    lag = max(0, int((period - 1) / 2))
    adjusted = np.full(len(values), np.nan, dtype=float)
    if lag == 0:
        adjusted = values.copy()
    else:
        for index in range(lag, len(values)):
            if np.all(np.isfinite([values[index], values[index - lag]])):
                adjusted[index] = values[index] + values[index] - values[index - lag]
    return _ema(adjusted, period)


def _supersmoother(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    a1 = math.exp(-1.414 * math.pi / period)
    b1 = 2.0 * a1 * math.cos(1.414 * math.pi / period)
    c2 = b1
    c3 = -(a1 * a1)
    c1 = 1.0 - c2 - c3
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        previous_source = values[index - 1] if index >= 1 and np.isfinite(values[index - 1]) else 0.0
        previous_one = result[index - 1] if index >= 1 and np.isfinite(result[index - 1]) else 0.0
        previous_two = result[index - 2] if index >= 2 and np.isfinite(result[index - 2]) else 0.0
        result[index] = c1 * (value + previous_source) / 2.0 + c2 * previous_one + c3 * previous_two
    return result


def _variant(values, volumes, period):
    if average_type == "EMA":
        return _ema(values, period)
    if average_type == "WMA":
        return _wma(values, period)
    if average_type == "VWMA":
        return _vwma(values, volumes, period)
    if average_type == "SMMA":
        return _smma(values, period)
    if average_type == "DEMA":
        return _dema(values, period)
    if average_type == "TEMA":
        return _tema(values, period)
    if average_type == "HullMA":
        return _hma(values, period)
    if average_type == "SSMA":
        return _supersmoother(values, period)
    if average_type == "ZEMA":
        return _zema(values, period)
    if average_type == "TMA":
        return _sma(_sma(values, period), period)
    return _sma(values, period)


def _stdev(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.std(window, ddof=0))
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].fillna(0.0).to_numpy(dtype=float)
n = len(df)

close_series = _variant(close, volume, average_length)
open_series = _variant(opened, volume, average_length)
close_open_average = (close_series + open_series) / 2.0
effective_multiplier = resolution_multiplier if use_alternate else 1
alternate_length = min(5000, average_length * effective_multiplier)
close_alternate = _variant(close, volume, alternate_length)
open_alternate = _variant(opened, volume, alternate_length)

long_cross = np.zeros(n, dtype=bool)
short_cross = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.all(np.isfinite([close_alternate[index], open_alternate[index], close_alternate[index - 1], open_alternate[index - 1]])):
        long_cross[index] = close_alternate[index] > open_alternate[index] and close_alternate[index - 1] <= open_alternate[index - 1]
        short_cross[index] = close_alternate[index] < open_alternate[index] and close_alternate[index - 1] >= open_alternate[index - 1]
average_denominator = close_open_average.copy()
difference_factor = np.full(n, np.nan, dtype=float)
valid_average = np.isfinite(close_alternate) & np.isfinite(open_alternate) & np.isfinite(average_denominator) & (np.abs(average_denominator) > 1e-12)
difference_factor[valid_average] = 50000.0 * (close_alternate[valid_average] - open_alternate[valid_average]) / average_denominator[valid_average]
trend_colors = ["#008000" if np.isfinite(close_alternate[index]) and np.isfinite(open_alternate[index]) and close_alternate[index] > open_alternate[index] else "#FF0000" for index in range(n)]

deviation_offset = _stdev(difference_factor, 20) * divergence_width
fractal_top = np.full(n, np.nan, dtype=float)
fractal_bottom = np.full(n, np.nan, dtype=float)
regular_bearish = np.zeros(n, dtype=bool)
hidden_bearish = np.zeros(n, dtype=bool)
regular_bullish = np.zeros(n, dtype=bool)
hidden_bullish = np.zeros(n, dtype=bool)
previous_top_value = np.nan
previous_top_price = np.nan
previous_bottom_value = np.nan
previous_bottom_price = np.nan
for confirmation in range(4, n):
    center = confirmation - 2
    window = difference_factor[center - 2:center + 3]
    if not np.all(np.isfinite(window)):
        continue
    top_found = window[2] > window[0] and window[2] > window[1] and window[2] > window[3] and window[2] > window[4]
    bottom_found = window[2] < window[0] and window[2] < window[1] and window[2] < window[3] and window[2] < window[4]
    if top_found:
        fractal_top[confirmation] = difference_factor[center]
        if np.all(np.isfinite([previous_top_value, previous_top_price])):
            regular_bearish[confirmation] = high[center] > previous_top_price and difference_factor[center] < previous_top_value and difference_factor[confirmation] > 0.0
            hidden_bearish[confirmation] = high[center] < previous_top_price and difference_factor[center] > previous_top_value and difference_factor[confirmation] > 0.0
        previous_top_value, previous_top_price = float(difference_factor[center]), float(high[center])
    if bottom_found:
        fractal_bottom[confirmation] = difference_factor[center]
        if np.all(np.isfinite([previous_bottom_value, previous_bottom_price])):
            regular_bullish[confirmation] = low[center] < previous_bottom_price and difference_factor[center] > previous_bottom_value and difference_factor[confirmation] < 0.0
            hidden_bullish[confirmation] = low[center] > previous_bottom_price and difference_factor[center] < previous_bottom_value and difference_factor[confirmation] < 0.0
        previous_bottom_value, previous_bottom_price = float(difference_factor[center]), float(low[center])


def _shifted_points(values, top_side, mode):
    points = [None] * n
    if not show_divergence_channel:
        return points
    for confirmation, value in enumerate(values):
        target = confirmation - 2
        if target < 0 or not np.isfinite(value) or not np.isfinite(deviation_offset[confirmation]):
            continue
        plotted = float(value + deviation_offset[confirmation] if top_side else value - deviation_offset[confirmation])
        if mode == "line":
            points[target] = plotted
        else:
            highlighted = (regular_bearish[confirmation] and show_regular_divergence) or (hidden_bearish[confirmation] and show_hidden_divergence) if top_side else (regular_bullish[confirmation] and show_regular_divergence) or (hidden_bullish[confirmation] and show_hidden_divergence)
            points[target] = {"value": plotted, "color": "#800000" if top_side and highlighted else "#008000" if not top_side and highlighted else "#808080", "size": 5}
    return points


area_fill = []
for index, value in enumerate(difference_factor):
    if np.isfinite(value):
        area_fill.append({"upper": float(max(value, 0.0)), "lower": float(min(value, 0.0)), "color": "rgba(0,128,0,0.20)" if value >= 0.0 else "rgba(255,0,0,0.20)"})
    else:
        area_fill.append(None)
alert_points = [{"value": float(difference_factor[index]), "color": trend_colors[index], "size": 5} if (long_cross[index] or short_cross[index]) and np.isfinite(difference_factor[index]) else None for index in range(n)]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Zero", "data": [0.0] * n, "color": "#808080", "type": "line", "style": "dashed", "lineWidth": 1, "overlay": False},
        {"name": "OCC Difference Area", "title": False, "data": area_fill, "color": "#008000", "type": "cloud", "overlay": False},
        {"name": "OCC Difference Factor", "data": _finite(difference_factor), "color": trend_colors, "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "OCC Alerts", "data": alert_points, "color": "#008000", "type": "circle", "size": 5, "overlay": False},
        {"name": "Divergence High", "data": _shifted_points(fractal_top, True, "line"), "color": "#FF0000", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Divergence Low", "data": _shifted_points(fractal_bottom, False, "line"), "color": "#008000", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Divergence High Points", "data": _shifted_points(fractal_top, True, "points"), "color": "#808080", "type": "circle", "size": 5, "overlay": False},
        {"name": "Divergence Low Points", "data": _shifted_points(fractal_bottom, False, "points"), "color": "#808080", "type": "circle", "size": 5, "overlay": False},
    ],
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "closeSeries": _finite(close_series), "openSeries": _finite(open_series), "closeOpenAverage": _finite(close_open_average),
        "closeSeriesAlternate": _finite(close_alternate), "openSeriesAlternate": _finite(open_alternate), "differenceFactor": _finite(difference_factor),
        "longCross": long_cross.tolist(), "shortCross": short_cross.tolist(), "deviationOffset": _finite(deviation_offset),
        "fractalTop": _finite(fractal_top), "fractalBottom": _finite(fractal_bottom),
        "regularBearish": regular_bearish.tolist(), "hiddenBearish": hidden_bearish.tolist(), "regularBullish": regular_bullish.tolist(), "hiddenBullish": hidden_bullish.tolist(),
        "averageType": average_type, "alternateLength": alternate_length, "divergenceVisible": show_divergence_channel,
    },
}
