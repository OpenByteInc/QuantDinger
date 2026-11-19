# ============================================================
# QuantDinger QQE MOD
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger QQE MOD"
my_indicator_description = "Combines two QQE state machines with a Bollinger envelope on the primary QQE trail and secondary-RSI histogram signals."

# @param primary_rsi_length int 6 Primary RSI length range=1:500:1
# @param primary_smoothing int 5 Primary RSI smoothing range=1:500:1
# @param primary_qqe_factor float 3.0 Primary QQE factor range=-100:100:0.01
# @param primary_threshold float 3.0 Reserved primary threshold range=-100:100:0.1
# @param primary_source str Close Primary RSI source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param secondary_rsi_length int 6 Secondary RSI length range=1:500:1
# @param secondary_smoothing int 5 Secondary RSI smoothing range=1:500:1
# @param secondary_qqe_factor float 1.61 Secondary QQE factor range=-100:100:0.01
# @param secondary_threshold float 3.0 Secondary threshold range=-100:100:0.1
# @param secondary_source str Close Secondary RSI source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param bollinger_length int 50 Bollinger length range=1:1000:1
# @param bollinger_multiplier float 0.35 Bollinger multiplier range=0.001:5:0.001

primary_rsi_length = min(500, max(1, int(params.get("primary_rsi_length", 6))))
primary_smoothing = min(500, max(1, int(params.get("primary_smoothing", 5))))
primary_qqe_factor = min(100.0, max(-100.0, float(params.get("primary_qqe_factor", 3.0))))
primary_threshold = min(100.0, max(-100.0, float(params.get("primary_threshold", 3.0))))
primary_source_name = str(params.get("primary_source", "Close")).upper()
secondary_rsi_length = min(500, max(1, int(params.get("secondary_rsi_length", 6))))
secondary_smoothing = min(500, max(1, int(params.get("secondary_smoothing", 5))))
secondary_qqe_factor = min(100.0, max(-100.0, float(params.get("secondary_qqe_factor", 1.61))))
secondary_threshold = min(100.0, max(-100.0, float(params.get("secondary_threshold", 3.0))))
secondary_source_name = str(params.get("secondary_source", "Close")).upper()
bollinger_length = min(1000, max(1, int(params.get("bollinger_length", 50))))
bollinger_multiplier = min(5.0, max(0.001, float(params.get("bollinger_multiplier", 0.35))))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    seed = []
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed.append(float(value))
            if len(seed) == length:
                previous = sum(seed) / length
                result[index] = previous
        else:
            previous = (previous * (length - 1.0) + float(value)) / length
            result[index] = previous
    return result


def _rsi(values, length):
    change = np.full(len(values), np.nan, dtype=float)
    if len(values) > 1:
        change[1:] = values[1:] - values[:-1]
    gain = np.where(np.isfinite(change), np.maximum(change, 0.0), np.nan)
    loss = np.where(np.isfinite(change), np.maximum(-change, 0.0), np.nan)
    average_gain = _rma(gain, length)
    average_loss = _rma(loss, length)
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(len(values)):
        if np.isfinite(average_gain[index]) and np.isfinite(average_loss[index]):
            result[index] = 100.0 if average_loss[index] == 0.0 and average_gain[index] > 0.0 else 50.0 if average_loss[index] == 0.0 else 100.0 - 100.0 / (1.0 + average_gain[index] / average_loss[index])
    return result


def _sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def _stdev(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.std(window, ddof=0))
    return result


def _cross(first, second):
    result = np.zeros(len(first), dtype=bool)
    for index in range(1, len(first)):
        if np.all(np.isfinite([first[index], first[index - 1], second[index], second[index - 1]])):
            result[index] = (first[index] > second[index] and first[index - 1] <= second[index - 1]) or (first[index] < second[index] and first[index - 1] >= second[index - 1])
    return result


def _qqe(source, rsi_length, smoothing, factor):
    rsi = _rsi(source, rsi_length)
    smoothed = _ema(rsi, smoothing)
    atr_rsi = np.full(len(source), np.nan, dtype=float)
    if len(source) > 1:
        atr_rsi[1:] = np.abs(smoothed[:-1] - smoothed[1:])
    dynamic = _ema(atr_rsi, rsi_length * 2 - 1) * factor
    long_band = np.full(len(source), np.nan, dtype=float)
    short_band = np.full(len(source), np.nan, dtype=float)
    direction = np.full(len(source), np.nan, dtype=float)
    trend_line = np.full(len(source), np.nan, dtype=float)
    for index in range(len(source)):
        if not np.all(np.isfinite([smoothed[index], dynamic[index]])):
            continue
        new_short = smoothed[index] + dynamic[index]
        new_long = smoothed[index] - dynamic[index]
        previous_long = long_band[index - 1] if index else np.nan
        previous_short = short_band[index - 1] if index else np.nan
        previous_rsi = smoothed[index - 1] if index else np.nan
        long_band[index] = max(previous_long, new_long) if np.all(np.isfinite([previous_rsi, previous_long])) and previous_rsi > previous_long and smoothed[index] > previous_long else new_long
        short_band[index] = min(previous_short, new_short) if np.all(np.isfinite([previous_rsi, previous_short])) and previous_rsi < previous_short and smoothed[index] < previous_short else new_short
        short_cross = False
        long_cross = False
        if index >= 2 and np.all(np.isfinite([smoothed[index], smoothed[index - 1], short_band[index - 1], short_band[index - 2]])):
            short_cross = (smoothed[index] > short_band[index - 1] and smoothed[index - 1] <= short_band[index - 2]) or (smoothed[index] < short_band[index - 1] and smoothed[index - 1] >= short_band[index - 2])
        if index >= 2 and np.all(np.isfinite([long_band[index - 1], long_band[index - 2], smoothed[index], smoothed[index - 1]])):
            long_cross = (long_band[index - 1] > smoothed[index] and long_band[index - 2] <= smoothed[index - 1]) or (long_band[index - 1] < smoothed[index] and long_band[index - 2] >= smoothed[index - 1])
        if short_cross:
            direction[index] = 1.0
        elif long_cross:
            direction[index] = -1.0
        elif index:
            direction[index] = direction[index - 1]
        trend_line[index] = long_band[index] if direction[index] == 1.0 else short_band[index]
    return {"rsi": rsi, "smoothed": smoothed, "atrRsi": atr_rsi, "dynamic": dynamic, "longBand": long_band, "shortBand": short_band, "direction": direction, "trendLine": trend_line}


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
n = len(df)
sources = {"OPEN": opened, "HIGH": high, "LOW": low, "HL2": (high + low) / 2.0, "HLC3": (high + low + close) / 3.0, "OHLC4": (opened + high + low + close) / 4.0, "CLOSE": close}
primary_source = sources.get(primary_source_name, close)
secondary_source = sources.get(secondary_source_name, close)
primary = _qqe(primary_source, primary_rsi_length, primary_smoothing, primary_qqe_factor)
secondary = _qqe(secondary_source, secondary_rsi_length, secondary_smoothing, secondary_qqe_factor)

primary_centered_trend = primary["trendLine"] - 50.0
bollinger_basis = _sma(primary_centered_trend, bollinger_length)
bollinger_deviation = bollinger_multiplier * _stdev(primary_centered_trend, bollinger_length)
bollinger_upper = bollinger_basis + bollinger_deviation
bollinger_lower = bollinger_basis - bollinger_deviation
secondary_centered_rsi = secondary["smoothed"] - 50.0
secondary_centered_trend = secondary["trendLine"] - 50.0
up_signal = (secondary_centered_rsi > secondary_threshold) & (primary["smoothed"] - 50.0 > bollinger_upper)
down_signal = (secondary_centered_rsi < -secondary_threshold) & (primary["smoothed"] - 50.0 < bollinger_lower)
secondary_histogram = np.where((secondary_centered_rsi > secondary_threshold) | (secondary_centered_rsi < -secondary_threshold), secondary_centered_rsi, np.nan)
primary_cross_up = np.zeros(n, dtype=bool)
primary_cross_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.all(np.isfinite([primary["smoothed"][index], primary["smoothed"][index - 1]])):
        primary_cross_up[index] = primary["smoothed"][index] > 50.0 and primary["smoothed"][index - 1] <= 50.0
        primary_cross_down[index] = primary["smoothed"][index] < 50.0 and primary["smoothed"][index - 1] >= 50.0


def _columns(values, mask, color):
    return [{"value": float(values[index]), "color": color} if mask[index] and np.isfinite(values[index]) else None for index in range(n)]


output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "Secondary QQE Trend Line", "data": _finite(secondary_centered_trend), "color": "#FFFFFF", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Secondary RSI Histogram", "data": _finite(secondary_histogram), "color": ["rgba(112,112,112,0.80)" if np.isfinite(value) else "rgba(0,0,0,0)" for value in secondary_histogram], "type": "histogram", "lineWidth": 1, "overlay": False},
        {"name": "QQE Up Signal", "data": _columns(secondary_centered_rsi, up_signal, "#00C3FF"), "color": "#00C3FF", "type": "histogram", "lineWidth": 1, "overlay": False},
        {"name": "QQE Down Signal", "data": _columns(secondary_centered_rsi, down_signal, "#FF0062"), "color": "#FF0062", "type": "histogram", "lineWidth": 1, "overlay": False},
        {"name": "Zero Line", "data": [0.0] * n, "color": "#FFFFFF", "type": "line", "lineWidth": 1, "dotted": True, "overlay": False},
    ],
    "signals": [
        {"type": "buy", "name": "Primary RSI Cross Above Zero", "data": [{"active": True, "price": 0.0, "text": "↑", "color": "#00C3FF"} if primary_cross_up[index] else None for index in range(n)], "color": "#00C3FF", "renderMode": "events", "overlay": False},
        {"type": "sell", "name": "Primary RSI Cross Below Zero", "data": [{"active": True, "price": 0.0, "text": "↓", "color": "#FF0062"} if primary_cross_down[index] else None for index in range(n)], "color": "#FF0062", "renderMode": "events", "overlay": False},
    ],
    "layers": [], "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "primaryRsi": _finite(primary["rsi"]), "primarySmoothedRsi": _finite(primary["smoothed"]), "primaryAtrRsi": _finite(primary["atrRsi"]), "primaryDynamicAtrRsi": _finite(primary["dynamic"]), "primaryLongBand": _finite(primary["longBand"]), "primaryShortBand": _finite(primary["shortBand"]), "primaryDirection": _finite(primary["direction"]), "primaryTrendLine": _finite(primary["trendLine"]),
        "secondaryRsi": _finite(secondary["rsi"]), "secondarySmoothedRsi": _finite(secondary["smoothed"]), "secondaryAtrRsi": _finite(secondary["atrRsi"]), "secondaryDynamicAtrRsi": _finite(secondary["dynamic"]), "secondaryLongBand": _finite(secondary["longBand"]), "secondaryShortBand": _finite(secondary["shortBand"]), "secondaryDirection": _finite(secondary["direction"]), "secondaryTrendLine": _finite(secondary["trendLine"]),
        "bollingerBasis": _finite(bollinger_basis), "bollingerDeviation": _finite(bollinger_deviation), "bollingerUpper": _finite(bollinger_upper), "bollingerLower": _finite(bollinger_lower),
        "upSignal": up_signal.tolist(), "downSignal": down_signal.tolist(), "primaryCrossUp": primary_cross_up.tolist(), "primaryCrossDown": primary_cross_down.tolist(),
        "primaryThresholdReserved": primary_threshold, "primarySource": primary_source_name, "secondarySource": secondary_source_name,
    },
}
