# ============================================================
# QuantDinger OBV MACD
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger OBV MACD"
my_indicator_description = "Maps OBV displacement into price units, applies selectable low-lag averages and tracks an adaptive MACD channel."

# @param obv_ema_length int 1 OBV EMA length range=1:500:1
# @param ma_type str DEMA Average type values=TDEMA,TTEMA,TEMA,DEMA,EMA,AVG,THMA,ZLEMA,ZLDEMA,ZLTEMA,DZLEMA,TZLEMA,LLEMA,NMA
# @param ma_length int 9 Average length range=1:500:1
# @param slow_length int 26 MACD slow length range=1:500:1
# @param regression_length int 2 Regression length range=2:200:1
# @param show_signals bool false Show channel turn signals
# @param hide_pivots bool true Hide pivots
# @param pivot_period int 50 Pivot period range=1:500:1

obv_ema_length = min(500, max(1, int(params.get("obv_ema_length", 1))))
ma_type = str(params.get("ma_type", "DEMA")).upper()
valid_types = ("TDEMA", "TTEMA", "TEMA", "DEMA", "EMA", "AVG", "THMA", "ZLEMA", "ZLDEMA", "ZLTEMA", "DZLEMA", "TZLEMA", "LLEMA", "NMA")
if ma_type not in valid_types:
    ma_type = "DEMA"
ma_length = min(500, max(1, int(params.get("ma_length", 9))))
slow_length = min(500, max(1, int(params.get("slow_length", 26))))
regression_length = min(200, max(2, int(params.get("regression_length", 2))))
show_signals = bool(params.get("show_signals", False))
hide_pivots = bool(params.get("hide_pivots", True))
pivot_period = min(500, max(1, int(params.get("pivot_period", 50))))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _sma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
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


def _stdev(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.std(window, ddof=0))
    return result


def _dema(values, period):
    first = _ema(values, period)
    second = _ema(first, period)
    return 2.0 * first - second


def _tema(values, period):
    first = _ema(values, period)
    second = _ema(first, period)
    third = _ema(second, period)
    return 3.0 * (first - second) + third


def _tdema(values, period):
    first = _dema(values, period)
    second = _dema(first, period)
    third = _dema(second, period)
    return 3.0 * (first - second) + third


def _ttema(values, period):
    first = _tema(values, period)
    second = _tema(first, period)
    third = _tema(second, period)
    return 3.0 * (first - second) + third


def _nma(values, length_one, length_two):
    ratio = float(length_one) / float(length_two)
    denominator = length_one - ratio
    alpha = ratio * (length_one - 1.0) / denominator if abs(denominator) > 1e-12 else 0.0
    first = _ema(values, length_one)
    second = _ema(first, length_two)
    return (1.0 + alpha) * first - alpha * second


def _hma(values, period):
    half = max(1, int(period / 2))
    root = max(1, int(math.floor(math.sqrt(period) + 0.5)))
    return _wma(2.0 * _wma(values, half) - _wma(values, period), root)


def _thma(values, period):
    first = _hma(values, period)
    second = _hma(first, period)
    third = _hma(second, period)
    return 3.0 * (first - second) + third


def _zero_lag_source(values, period):
    lag = max(0, int(math.floor((period - 1.0) / 2.0 + 0.5)))
    result = np.full(len(values), np.nan, dtype=float)
    if lag == 0:
        return values.copy()
    for index in range(lag, len(values)):
        if np.all(np.isfinite([values[index], values[index - lag]])):
            result[index] = values[index] + (values[index] - values[index - lag])
    return result


def _zlema(values, period):
    return _ema(_zero_lag_source(values, period), period)


def _zldema(values, period):
    return _dema(_zero_lag_source(values, period), period)


def _zltema(values, period):
    return _tema(_zero_lag_source(values, period), period)


def _dzlema(values, period):
    first = _zlema(values, period)
    second = _zlema(first, period)
    return 2.0 * first - second


def _tzlema(values, period):
    first = _zlema(values, period)
    second = _zlema(first, period)
    third = _zlema(second, period)
    return 3.0 * (first - second) + third


def _llema(values, period):
    source = np.full(len(values), np.nan, dtype=float)
    for index in range(2, len(values)):
        if np.all(np.isfinite(values[index - 2:index + 1])):
            source[index] = 0.25 * values[index] + 0.5 * values[index - 1] + 0.25 * values[index - 2]
    return _ema(source, period)


def _average(values, period):
    if ma_type == "EMA":
        return _ema(values, period)
    if ma_type == "DEMA":
        return _dema(values, period)
    if ma_type == "TEMA":
        return _tema(values, period)
    if ma_type == "TDEMA":
        return _tdema(values, period)
    if ma_type == "TTEMA":
        return _ttema(values, period)
    if ma_type == "THMA":
        return _thma(values, period)
    if ma_type == "ZLEMA":
        return _zlema(values, period)
    if ma_type == "ZLDEMA":
        return _zldema(values, period)
    if ma_type == "ZLTEMA":
        return _zltema(values, period)
    if ma_type == "DZLEMA":
        return _dzlema(values, period)
    if ma_type == "TZLEMA":
        return _tzlema(values, period)
    if ma_type == "LLEMA":
        return _llema(values, period)
    if ma_type == "NMA":
        return _nma(values, period, 26)
    return (_ttema(values, period) + _tdema(values, period)) / 2.0


df = df.copy().reset_index(drop=True)
if "time" not in df.columns:
    df["time"] = np.arange(len(df), dtype=float) * 60.0
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].fillna(0.0).to_numpy(dtype=float)
n = len(df)

price_spread = _stdev(high - low, 28)
obv = np.zeros(n, dtype=float)
for index in range(1, n):
    direction = 1.0 if close[index] > close[index - 1] else -1.0 if close[index] < close[index - 1] else 0.0
    obv[index] = obv[index - 1] + direction * volume[index]
obv_smooth = _sma(obv, 14)
obv_spread = _stdev(obv - obv_smooth, 28)
shadow = np.full(n, np.nan, dtype=float)
valid_spread = np.isfinite(obv_spread) & (np.abs(obv_spread) > 1e-12) & np.isfinite(price_spread)
shadow[valid_spread] = (obv[valid_spread] - obv_smooth[valid_spread]) / obv_spread[valid_spread] * price_spread[valid_spread]
mapped_obv = np.where(shadow > 0.0, high + shadow, low + shadow)
mapped_obv[~np.isfinite(shadow)] = np.nan
obv_ema = _ema(mapped_obv, obv_ema_length)
selected_average = _average(obv_ema, ma_length)
slow_average = _ema(close, slow_length)
macd = selected_average - slow_average

regression = np.full(n, np.nan, dtype=float)
regression_slope = np.full(n, np.nan, dtype=float)
regression_intercept = np.full(n, np.nan, dtype=float)
x_values = np.arange(2.0, regression_length + 2.0, dtype=float)
sum_x = float(np.sum(x_values))
sum_x_square = float(np.sum(x_values * x_values))
denominator = regression_length * sum_x_square - sum_x * sum_x
for index in range(regression_length - 1, n):
    window = macd[index - regression_length + 1:index + 1]
    if not np.all(np.isfinite(window)):
        continue
    sum_y = float(np.sum(window))
    sum_xy = float(np.sum(window * x_values))
    slope_value = (regression_length * sum_xy - sum_x * sum_y) / denominator
    average_value = sum_y / regression_length
    intercept_value = average_value - slope_value * sum_x / regression_length + slope_value
    regression_slope[index], regression_intercept[index] = slope_value, intercept_value
    regression[index] = intercept_value + slope_value * regression_length

adaptive_range = np.full(n, np.nan, dtype=float)
channel = np.full(n, np.nan, dtype=float)
deviation = np.full(n, np.nan, dtype=float)
direction = np.zeros(n, dtype=int)
cumulative_difference = 0.0
for index in range(n):
    previous_channel = channel[index - 1] if index >= 1 and np.isfinite(channel[index - 1]) else regression[index]
    if np.all(np.isfinite([regression[index], previous_channel])):
        cumulative_difference += abs(regression[index] - previous_channel)
        adaptive_range[index] = cumulative_difference / index if index > 0 else np.nan
        if np.isfinite(adaptive_range[index]) and regression[index] > previous_channel + adaptive_range[index]:
            channel[index] = regression[index]
        elif np.isfinite(adaptive_range[index]) and regression[index] < previous_channel - adaptive_range[index]:
            channel[index] = regression[index]
        else:
            channel[index] = previous_channel
    if index >= 1 and np.all(np.isfinite([channel[index], channel[index - 1]])) and channel[index] != channel[index - 1]:
        deviation[index] = adaptive_range[index]
    elif index >= 1 and np.isfinite(deviation[index - 1]):
        deviation[index] = deviation[index - 1]
    elif np.isfinite(adaptive_range[index]):
        deviation[index] = adaptive_range[index]
    previous_direction = direction[index - 1] if index >= 1 else 0
    if index >= 1 and np.all(np.isfinite([channel[index], channel[index - 1]])):
        direction[index] = 1 if channel[index] > channel[index - 1] else -1 if channel[index] < channel[index - 1] else previous_direction
    else:
        direction[index] = previous_direction

up_turn = np.zeros(n, dtype=bool)
down_turn = np.zeros(n, dtype=bool)
for index in range(1, n):
    up_turn[index] = direction[index] - direction[index - 1] > 0
    down_turn[index] = direction[index] - direction[index - 1] < 0

rolling_high_offset = np.zeros(n, dtype=int)
rolling_low_offset = np.zeros(n, dtype=int)
max_close = np.full(n, np.nan, dtype=float)
max_upper = np.full(n, np.nan, dtype=float)
min_close = np.full(n, np.nan, dtype=float)
min_lower = np.full(n, np.nan, dtype=float)
pivot_high = np.zeros(n, dtype=bool)
pivot_low = np.zeros(n, dtype=bool)
for index in range(n):
    start = max(0, index - pivot_period + 1)
    upper_window = regression[start:index + 1]
    finite_upper = np.flatnonzero(np.isfinite(upper_window))
    if len(finite_upper):
        local_high = int(finite_upper[np.argmax(upper_window[finite_upper])])
        local_low = int(finite_upper[np.argmin(upper_window[finite_upper])])
        rolling_high_offset[index] = index - (start + local_high)
        rolling_low_offset[index] = index - (start + local_low)
    previous_max = max_close[index - 1] if index >= 1 else np.nan
    previous_max_upper = max_upper[index - 1] if index >= 1 else np.nan
    previous_min = min_close[index - 1] if index >= 1 else np.nan
    previous_min_lower = min_lower[index - 1] if index >= 1 else np.nan
    max_close[index] = close[index] if rolling_high_offset[index] == 0 or not np.isfinite(previous_max) else previous_max
    max_upper[index] = regression[index] if rolling_high_offset[index] == 0 or not np.isfinite(previous_max_upper) else previous_max_upper
    min_close[index] = close[index] if rolling_low_offset[index] == 0 or not np.isfinite(previous_min) else previous_min
    min_lower[index] = regression[index] if rolling_low_offset[index] == 0 or not np.isfinite(previous_min_lower) else previous_min_lower
    if np.isfinite(max_close[index]) and close[index] > max_close[index]:
        max_close[index] = close[index]
    if np.isfinite(max_upper[index]) and np.isfinite(regression[index]) and regression[index] > max_upper[index]:
        max_upper[index] = regression[index]
    if np.isfinite(min_lower[index]) and close[index] < min_lower[index]:
        min_lower[index] = regression[index]
    if np.isfinite(min_lower[index]) and np.isfinite(regression[index]) and regression[index] < min_lower[index]:
        min_lower[index] = regression[index]
    if index >= 3 and np.all(np.isfinite([max_upper[index], max_upper[index - 2], max_upper[index - 3]])):
        pivot_high[index] = max_upper[index] == max_upper[index - 2] and max_upper[index - 2] != max_upper[index - 3]
    if index >= 3 and np.all(np.isfinite([min_lower[index], min_lower[index - 2], min_lower[index - 3]])):
        pivot_low[index] = min_lower[index] == min_lower[index - 2] and min_lower[index - 2] != min_lower[index - 3]

bull_channel = [float(channel[index]) if direction[index] == 1 and np.isfinite(channel[index]) else None for index in range(n)]
bear_channel = [float(channel[index]) if direction[index] == -1 and np.isfinite(channel[index]) else None for index in range(n)]
neutral_channel = [float(channel[index]) if direction[index] == 0 and np.isfinite(channel[index]) else None for index in range(n)]
up_points = [None] * n
down_points = [None] * n
if show_signals:
    for index in range(1, n):
        target = index - 1
        if up_turn[index] and np.isfinite(regression[index]):
            up_points[target] = {"value": float(regression[index]), "color": "#22D3EE", "size": 6}
        if down_turn[index] and np.isfinite(regression[index]):
            down_points[target] = {"value": float(regression[index]), "color": "#F43F5E", "size": 6}
pivot_high_points = [{"value": float(max_upper[index] + 2.0), "color": "#F43F5E", "size": 5} if not hide_pivots and pivot_high[index] and np.isfinite(max_upper[index]) else None for index in range(n)]
pivot_low_points = [{"value": float(min_lower[index] - 2.0), "color": "#22D3EE", "size": 5} if not hide_pivots and pivot_low[index] and np.isfinite(min_lower[index]) else None for index in range(n)]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Zero", "data": [0.0] * n, "color": "#9CA3AF", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Adaptive Channel", "data": _finite(channel), "color": "#F59E0B", "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "Up Turn", "data": up_points, "color": "#22D3EE", "type": "circle", "size": 6, "overlay": False},
        {"name": "Down Turn", "data": down_points, "color": "#F43F5E", "type": "circle", "size": 6, "overlay": False},
        {"name": "High Pivot", "data": pivot_high_points, "color": "#F43F5E", "type": "circle", "size": 5, "overlay": False},
        {"name": "Low Pivot", "data": pivot_low_points, "color": "#22D3EE", "type": "circle", "size": 5, "overlay": False},
    ],
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "priceSpread": _finite(price_spread), "obv": _finite(obv), "obvSmooth": _finite(obv_smooth), "obvSpread": _finite(obv_spread),
        "shadow": _finite(shadow), "mappedObv": _finite(mapped_obv), "obvEma": _finite(obv_ema), "selectedAverage": _finite(selected_average),
        "slowAverage": _finite(slow_average), "macd": _finite(macd), "regressionSlope": _finite(regression_slope), "regressionIntercept": _finite(regression_intercept), "regression": _finite(regression),
        "adaptiveRange": _finite(adaptive_range), "channel": _finite(channel), "deviation": _finite(deviation), "direction": direction.tolist(),
        "upTurn": up_turn.tolist(), "downTurn": down_turn.tolist(), "pivotHigh": pivot_high.tolist(), "pivotLow": pivot_low.tolist(),
        "maxUpper": _finite(max_upper), "minLower": _finite(min_lower), "maType": ma_type,
    },
}
