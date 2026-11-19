# ============================================================
# QuantDinger MA Sabres
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger MA Sabres"
my_indicator_description = "Marks moving-average reversals after sustained extrema and projects curved ATR-scaled sabres."

# @param ma_type str TEMA Moving-average type values=SMA,EMA,SMMA (RMA),HullMA,WMA,VWMA,DEMA,TEMA,NONE
# @param length int 50 Moving-average length range=1:1000:1
# @param previous_trend_duration int 20 Previous trend duration range=1:1000:1
# @param bullish_color color #2962FF Bullish reversal color
# @param bearish_color color #F23645 Bearish reversal color
# @param ma_color color #787B86 Moving-average color

ma_type = str(params.get("ma_type", "TEMA"))
length = min(1000, max(1, int(params.get("length", 50))))
trend_duration = min(1000, max(1, int(params.get("previous_trend_duration", 20))))
bullish_color = str(params.get("bullish_color", "#2962FF"))
bearish_color = str(params.get("bearish_color", "#F23645"))
ma_color = str(params.get("ma_color", "#787B86"))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].fillna(0.0).astype(float).to_numpy(dtype=float)
n = len(df)
times = df["time"].to_numpy(dtype=float) if "time" in df.columns else np.arange(n, dtype=float) * 3600000.0
if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    if period == 1:
        return values.astype(float).copy()
    cumulative = np.cumsum(np.insert(values.astype(float), 0, 0.0))
    result[period - 1:] = (cumulative[period:] - cumulative[:-period]) / period
    return result


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def _rma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < period:
        return result
    previous = float(np.mean(values[:period]))
    result[period - 1] = previous
    for index in range(period, len(values)):
        previous = (previous * (period - 1.0) + values[index]) / period
        result[index] = previous
    return result


def _wma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, period + 1, dtype=float)
    divisor = float(np.sum(weights))
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / divisor)
    return result


def _vwma(values, volumes, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        price_window = values[index - period + 1:index + 1]
        volume_window = volumes[index - period + 1:index + 1]
        total = float(np.sum(volume_window))
        if total != 0.0 and np.all(np.isfinite(price_window)):
            result[index] = float(np.sum(price_window * volume_window) / total)
    return result


ema_one = _ema(close, length)
ema_two = _ema(ema_one, length)
ema_three = _ema(ema_two, length)
if ma_type == "SMA":
    moving_average = _sma(close, length)
elif ma_type == "EMA":
    moving_average = ema_one
elif ma_type == "SMMA (RMA)":
    moving_average = _rma(close, length)
elif ma_type == "HullMA":
    half_length = max(1, length // 2)
    root_length = max(1, int(round(np.sqrt(length))))
    moving_average = _wma(2.0 * _wma(close, half_length) - _wma(close, length), root_length)
elif ma_type == "WMA":
    moving_average = _wma(close, length)
elif ma_type == "VWMA":
    moving_average = _vwma(close, volume, length)
elif ma_type == "DEMA":
    moving_average = 2.0 * ema_one - ema_two
elif ma_type == "TEMA":
    moving_average = 3.0 * ema_one - 3.0 * ema_two + ema_three
else:
    moving_average = np.full(n, np.nan, dtype=float)

falling = np.zeros(n, dtype=bool)
rising = np.zeros(n, dtype=bool)
for index in range(trend_duration, n):
    current = moving_average[index]
    history = moving_average[index - trend_duration:index]
    if np.isfinite(current) and np.all(np.isfinite(history)):
        falling[index] = current < float(np.min(history))
        rising[index] = current > float(np.max(history))

up_reversal = np.zeros(n, dtype=bool)
down_reversal = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(moving_average[index]) and np.isfinite(moving_average[index - 1]):
        up_reversal[index] = falling[index - 1] and moving_average[index] > moving_average[index - 1]
        down_reversal[index] = rising[index - 1] and moving_average[index] < moving_average[index - 1]

true_range = np.full(n, np.nan, dtype=float)
if n:
    true_range[0] = high[0] - low[0]
for index in range(1, n):
    true_range[index] = max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
atr = _rma(true_range, 14)


def _interval():
    if n < 2:
        return 60000.0
    differences = np.diff(times)
    differences = differences[np.isfinite(differences) & (differences > 0.0)]
    return float(np.median(differences)) if len(differences) else 60000.0


interval = _interval()


def _time_for_index(index):
    if 0 <= index < n:
        return float(times[index])
    if not n:
        return float(index * interval)
    return float(times[-1] + (index - (n - 1)) * interval)


layers = []
polyline_audit = []
middle_offset = int(length / 2.0 - 1.0)
for index in range(n):
    if not np.isfinite(atr[index]):
        continue
    if up_reversal[index]:
        anchor = float(low[index - 1]); points = [
            {"time": _time_for_index(index - 1), "price": anchor - atr[index] / 15.0},
            {"time": _time_for_index(index + middle_offset), "price": anchor + atr[index] / 2.5},
            {"time": _time_for_index(index + length), "price": anchor + atr[index] * 2.0},
            {"time": _time_for_index(index + middle_offset), "price": anchor + atr[index] / 2.5},
            {"time": _time_for_index(index - 1), "price": anchor + atr[index] / 15.0},
        ]
        layers.append({"type": "polyline", "role": "Bullish Sabre", "points": points, "curved": True, "closed": False, "color": bullish_color, "fillColor": bullish_color, "fillOpacity": 0.50, "lineWidth": 1})
        polyline_audit.append({"index": int(index), "direction": 1, "anchor": anchor, "atr": float(atr[index]), "points": points})
    if down_reversal[index]:
        anchor = float(high[index - 1]); points = [
            {"time": _time_for_index(index - 1), "price": anchor + atr[index] / 15.0},
            {"time": _time_for_index(index + middle_offset), "price": anchor - atr[index] / 2.5},
            {"time": _time_for_index(index + length), "price": anchor - atr[index] * 2.0},
            {"time": _time_for_index(index + middle_offset), "price": anchor - atr[index] / 2.5},
            {"time": _time_for_index(index - 1), "price": anchor - atr[index] / 15.0},
        ]
        layers.append({"type": "polyline", "role": "Bearish Sabre", "points": points, "curved": True, "closed": False, "color": bearish_color, "fillColor": bearish_color, "fillOpacity": 0.50, "lineWidth": 1})
        polyline_audit.append({"index": int(index), "direction": -1, "anchor": anchor, "atr": float(atr[index]), "points": points})
if len(layers) > 100:
    layers = layers[-100:]

up_marker = [float(low[index]) if index + 1 < n and up_reversal[index + 1] else None for index in range(n)]
down_marker = [float(high[index]) if index + 1 < n and down_reversal[index + 1] else None for index in range(n)]
plots = [
    {"name": "Moving Average", "data": _finite(moving_average), "color": ma_color, "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Bullish Sabre Core", "title": False, "data": up_marker, "color": bullish_color, "type": "circle", "size": 3, "overlay": True},
    {"name": "Bullish Sabre Mid", "title": False, "data": up_marker, "color": bullish_color, "opacity": 0.50, "type": "circle", "size": 6, "overlay": True},
    {"name": "Bullish Sabre Halo", "title": False, "data": up_marker, "color": bullish_color, "opacity": 0.35, "type": "circle", "size": 9, "overlay": True},
    {"name": "Bearish Sabre Core", "title": False, "data": down_marker, "color": bearish_color, "type": "circle", "size": 3, "overlay": True},
    {"name": "Bearish Sabre Mid", "title": False, "data": down_marker, "color": bearish_color, "opacity": 0.50, "type": "circle", "size": 6, "overlay": True},
    {"name": "Bearish Sabre Halo", "title": False, "data": down_marker, "color": bearish_color, "opacity": 0.35, "type": "circle", "size": 9, "overlay": True},
]

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": [], "layers": layers, "tables": [],
    "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "emaOne": _finite(ema_one), "emaTwo": _finite(ema_two), "emaThree": _finite(ema_three), "movingAverage": _finite(moving_average),
        "falling": falling.tolist(), "rising": rising.tolist(), "upReversal": up_reversal.tolist(), "downReversal": down_reversal.tolist(),
        "trueRange": _finite(true_range), "atr": _finite(atr), "middleOffset": int(middle_offset), "barInterval": float(interval), "polylines": polyline_audit,
    },
}
