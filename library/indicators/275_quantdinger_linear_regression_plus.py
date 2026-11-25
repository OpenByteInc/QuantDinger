# ============================================================
# QuantDinger Linear Regression Plus
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Linear Regression Plus"
my_indicator_description = "Plots a smoothed current- or multi-timeframe regression, projected deviation channel, retained displacement lines, and channel-cross signals."

# @param timeframe str Current Calculation timeframe values=Current,1m,5m,15m,30m,1H,2H,4H,1D,1W
# @param source str close Price source values=close,open,high,low,hl2,hlc3,ohlc4
# @param length int 100 Regression length range=1:1000:1
# @param deviation_multiplier float 2.0 Channel deviation range=0:20:0.1
# @param offset int 0 Regression offset range=0:500:1
# @param smoothing int 1 Post-security SMA length range=1:200:1
# @param line_thickness int 4 Channel line thickness range=1:4:1
# @param hide_old_signals bool true Show only recent signals
# @param up_color color #00FF00 Lower channel and rising color
# @param down_color color #FF0000 Upper channel and falling color
# @param displacement bool false Retain displaced historical channels
# @param end_time_ms int 2531346000000 Stop creating channel objects after this timestamp
# @param projection_bars int 100 Bounded right extension range=1:500:1

selected_timeframe = str(params.get("timeframe", "Current"))
source_name = str(params.get("source", "close"))
if source_name not in ("close", "open", "high", "low", "hl2", "hlc3", "ohlc4"):
    source_name = "close"
length = min(1000, max(1, int(params.get("length", 100))))
deviation_multiplier = min(20.0, max(0.0, float(params.get("deviation_multiplier", 2.0))))
offset = min(500, max(0, int(params.get("offset", 0))))
smoothing = min(200, max(1, int(params.get("smoothing", 1))))
line_thickness = min(4, max(1, int(params.get("line_thickness", 4))))
hide_old_signals = bool(params.get("hide_old_signals", True))
up_color = str(params.get("up_color", "#00FF00"))
down_color = str(params.get("down_color", "#FF0000"))
displacement = bool(params.get("displacement", False))
end_time_ms = int(params.get("end_time_ms", 2531346000000))
projection_bars = min(500, max(1, int(params.get("projection_bars", 100))))

timeframe_seconds = {"1M": 60, "5M": 300, "15M": 900, "30M": 1800, "1H": 3600, "2H": 7200, "4H": 14400, "1D": 86400, "1W": 604800}


def _normalize_frame(raw):
    if raw is None or len(raw) == 0:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    frame = raw.copy().reset_index(drop=True)
    if "time" not in frame.columns:
        frame["time"] = np.arange(len(frame), dtype=float) * 3600000.0
    if "volume" not in frame.columns:
        frame["volume"] = 0.0
    for column in ("time", "open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["volume"] = frame["volume"].fillna(0.0)
    frame = frame.dropna(subset=["time", "open", "high", "low", "close"])
    if len(frame) and np.nanmax(np.abs(frame["time"].to_numpy(dtype=float))) < 100000000000.0:
        frame["time"] = frame["time"] * 1000.0
    return frame.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)


def _source_values(frame, name):
    o = frame["open"].to_numpy(dtype=float); h = frame["high"].to_numpy(dtype=float); l = frame["low"].to_numpy(dtype=float); c = frame["close"].to_numpy(dtype=float)
    if name == "open": return o
    if name == "high": return h
    if name == "low": return l
    if name == "hl2": return (h + l) * 0.5
    if name == "hlc3": return (h + l + c) / 3.0
    if name == "ohlc4": return (o + h + l + c) * 0.25
    return c


def _linreg(values, window, selected_offset):
    result = np.full(len(values), np.nan, dtype=float)
    if window == 1:
        result[:] = values
        return result
    x = np.arange(window, dtype=float); x_mean = (window - 1.0) * 0.5; denominator = float(np.sum((x - x_mean) ** 2))
    for index in range(window - 1, len(values)):
        sample = values[index - window + 1:index + 1]
        if np.all(np.isfinite(sample)):
            y_mean = float(np.mean(sample)); slope_value = float(np.sum((x - x_mean) * (sample - y_mean)) / denominator)
            intercept_value = y_mean - slope_value * x_mean
            result[index] = intercept_value + slope_value * (window - 1.0 - selected_offset)
    return result


def _sma(values, window):
    return pd.Series(values, dtype=float).rolling(window, min_periods=window).mean().to_numpy(dtype=float)


df = df.copy()
base = _normalize_frame(df)
base_times = base["time"].to_numpy(dtype=float)
open_price = base["open"].to_numpy(dtype=float)
high = base["high"].to_numpy(dtype=float)
low = base["low"].to_numpy(dtype=float)
close = base["close"].to_numpy(dtype=float)
source = _source_values(base, source_name)
n = len(base)
runtime_ctx = ctx
chart_key = str(runtime_ctx.timeframe or "1H").upper()
chart_seconds = timeframe_seconds.get(chart_key, 3600)
requested_key = chart_key if selected_timeframe.lower() in ("", "current") else selected_timeframe.upper()
if requested_key not in timeframe_seconds:
    requested_key = chart_key
resolved_timeframe = requested_key

if requested_key == chart_key:
    calculation = base
else:
    raw_series = runtime_ctx.raw_series(selected_timeframe) if runtime_ctx is not None and hasattr(runtime_ctx, "raw_series") else None
    calculation = _normalize_frame(raw_series)
    if not len(calculation):
        calculation = base
        resolved_timeframe = chart_key

calculation_source = _source_values(calculation, source_name)
calculation_linreg = _linreg(calculation_source, length, offset)
calculation_previous = _linreg(calculation_source, length, offset + 1)

if resolved_timeframe == chart_key:
    aligned_linreg = calculation_linreg.copy()
    aligned_previous = calculation_previous.copy()
    source_indexes = np.arange(n, dtype=int)
else:
    target_seconds = timeframe_seconds.get(resolved_timeframe, chart_seconds)
    target_times = calculation["time"].to_numpy(dtype=float)
    target_ends = target_times + target_seconds * 1000.0
    base_ends = base_times + chart_seconds * 1000.0
    source_indexes = np.searchsorted(target_ends, base_ends, side="right") - 1
    aligned_linreg = np.full(n, np.nan, dtype=float); aligned_previous = np.full(n, np.nan, dtype=float)
    valid = (source_indexes >= 0) & (source_indexes < len(calculation))
    aligned_linreg[valid] = calculation_linreg[source_indexes[valid]]
    aligned_previous[valid] = calculation_previous[source_indexes[valid]]

linreg = _sma(aligned_linreg, smoothing)
linreg_previous = _sma(aligned_previous, smoothing)
slope = linreg - linreg_previous
deviation = np.full(n, np.nan, dtype=float)
for index in range(length - 1, n):
    if not np.all(np.isfinite([linreg[index], slope[index]])):
        continue
    sample = source[index - length + 1:index + 1][::-1]
    if np.all(np.isfinite(sample)):
        fitted = linreg[index] - slope[index] * np.arange(length, dtype=float)
        deviation[index] = float(np.sqrt(np.mean((sample - fitted) ** 2)))

lower_current = linreg - deviation * deviation_multiplier
upper_current = linreg + deviation * deviation_multiplier
buy = np.zeros(n, dtype=bool); sell = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.all(np.isfinite([close[index], close[index - 1], lower_current[index], lower_current[index - 1]])):
        buy[index] = close[index] < lower_current[index] and close[index - 1] >= lower_current[index - 1]
    if np.all(np.isfinite([close[index], close[index - 1], upper_current[index], upper_current[index - 1]])):
        sell[index] = close[index] > upper_current[index] and close[index - 1] <= upper_current[index - 1]

if hide_old_signals and n > length:
    buy[:n - length] = False
    sell[:n - length] = False


def _interval():
    if n < 2: return float(chart_seconds * 1000)
    differences = np.diff(base_times); differences = differences[np.isfinite(differences) & (differences > 0.0)]
    return float(np.median(differences)) if len(differences) else float(chart_seconds * 1000)


eligible = [index for index in range(length, n) if base_times[index] <= end_time_ms and np.all(np.isfinite([linreg[index], slope[index], deviation[index]]))]
selected_indexes = eligible[-16:] if displacement else eligible[-1:]
layers = []
interval = _interval()
future_limit = float(base_times[-1] + projection_bars * interval) if n else 0.0
channel_audit = []
for index in selected_indexes:
    start_index = index - length
    end_time = max(future_limit, float(base_times[index] + projection_bars * interval))
    forward_bars = (end_time - base_times[index]) / interval
    start_value = float(linreg[index] - slope[index] * length)
    projected_value = float(linreg[index] + slope[index] * forward_bars)
    spread = float(deviation[index] * deviation_multiplier)
    channel_audit.append({"index": int(index), "startIndex": int(start_index), "start": start_value, "end": float(linreg[index]), "deviation": float(deviation[index])})
    for role, delta, color in (("Regression Base", 0.0, "#00FFFF"), ("Regression Upper", spread, down_color), ("Regression Lower", -spread, up_color)):
        layers.append({
            "type": "line", "role": role, "start": float(base_times[start_index]), "end": end_time,
            "y1": start_value + delta, "y2": projected_value + delta,
            "color": color, "lineWidth": line_thickness, "dashed": False,
        })


def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]


line_points = []
for index, value in enumerate(linreg):
    if np.isfinite(value):
        color = up_color if index > 0 and np.isfinite(linreg[index - 1]) and value > linreg[index - 1] else down_color
        line_points.append({"value": float(value), "color": color})
    else:
        line_points.append(None)

output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "Regression Line", "data": line_points, "color": down_color, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite_or_none(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "name": "Buy Lin Reg", "text": "BUY", "data": [float(low[i]) if buy[i] else None for i in range(n)], "color": up_color, "renderMode": "events"},
        {"type": "sell", "name": "Sell Lin Reg", "text": "SELL", "data": [float(high[i]) if sell[i] else None for i in range(n)], "color": down_color, "renderMode": "events"},
    ],
    "layers": layers, "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "alignedLinreg": _finite_or_none(aligned_linreg), "alignedPrevious": _finite_or_none(aligned_previous),
        "linreg": _finite_or_none(linreg), "linregPrevious": _finite_or_none(linreg_previous), "slope": _finite_or_none(slope), "deviation": _finite_or_none(deviation),
        "lowerCurrent": _finite_or_none(lower_current), "upperCurrent": _finite_or_none(upper_current), "buy": buy.tolist(), "sell": sell.tolist(),
        "sourceIndexes": [int(value) for value in source_indexes], "resolvedTimeframe": resolved_timeframe, "chartTimeframe": chart_key,
        "calculationLinreg": _finite_or_none(calculation_linreg), "calculationPrevious": _finite_or_none(calculation_previous),
        "channelAudit": channel_audit, "barInterval": interval,
    },
}
