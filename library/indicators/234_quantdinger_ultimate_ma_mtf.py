# ============================================================
# QuantDinger Ultimate MA MTF
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Ultimate MA MTF"
my_indicator_description = (
    "Plots configurable fast and slow moving averages from independent confirmed "
    "timeframes with eight algorithms, slope or relationship coloring, and alerts."
)

indicator_timeframes = ["1H", "4H", "1D", "1W"]

# @param fast_timeframe str Chart Fast MA timeframe values=Chart,1H,4H,1D,1W
# @param fast_length int 20 Fast MA length range=1:999:1
# @param fast_source str Close Fast MA source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param fast_offset int 0 Fast MA offset range=-200:200:1
# @param fast_type str SMA Fast MA type values=SMA,EMA,WMA,Hull_MA,VWMA,RMA,TEMA,TIL_T3
# @param fast_t3_factor float 0.7 Fast Tilson T3 factor range=0.0:0.99:0.01
# @param show_slow bool false Show slow MA
# @param slow_timeframe str Chart Slow MA timeframe values=Chart,1H,4H,1D,1W
# @param slow_length int 50 Slow MA length range=1:999:1
# @param slow_source str Close Slow MA source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param slow_offset int 0 Slow MA offset range=-200:200:1
# @param slow_type str SMA Slow MA type values=SMA,EMA,WMA,Hull_MA,VWMA,RMA,TEMA,TIL_T3
# @param slow_t3_factor float 0.7 Slow Tilson T3 factor range=0.0:0.99:0.01
# @param fast_color str #2962FF Fast MA color
# @param slow_color str #FF6D00 Slow MA color
# @param fast_width int 3 Fast MA width range=0:5:1
# @param slow_width int 3 Slow MA width range=0:5:1
# @param color_fast_by_slope bool false Color fast MA by slope
# @param slope_smoothing int 2 Color smoothing range=1:20:1
# @param bullish_color str #22C55E Bullish color
# @param bearish_color str #EF4444 Bearish color
# @param color_fast_by_relation bool false Color fast MA above or below slow MA
# @param show_fill bool true Show fill
# @param fill_transparency int 50 Fill transparency range=0:100:1
# @param alert_slope_bull bool false Enable slope bull alert state
# @param alert_slope_bear bool false Enable slope bear alert state
# @param alert_cross_bull bool false Enable cross bull alert state
# @param alert_cross_bear bool false Enable cross bear alert state

fast_timeframe = str(params.get("fast_timeframe", "Chart")).upper()
fast_length = min(999, max(1, int(params.get("fast_length", 20))))
fast_source_name = str(params.get("fast_source", "Close"))
fast_offset = min(200, max(-200, int(params.get("fast_offset", 0))))
fast_type = str(params.get("fast_type", "SMA")).upper().replace(" ", "_")
fast_t3_factor = min(0.99, max(0.0, float(params.get("fast_t3_factor", 0.7))))
show_slow = bool(params.get("show_slow", False))
slow_timeframe = str(params.get("slow_timeframe", "Chart")).upper()
slow_length = min(999, max(1, int(params.get("slow_length", 50))))
slow_source_name = str(params.get("slow_source", "Close"))
slow_offset = min(200, max(-200, int(params.get("slow_offset", 0))))
slow_type = str(params.get("slow_type", "SMA")).upper().replace(" ", "_")
slow_t3_factor = min(0.99, max(0.0, float(params.get("slow_t3_factor", 0.7))))
fast_color = str(params.get("fast_color", "#2962FF"))
slow_color = str(params.get("slow_color", "#FF6D00"))
fast_width = min(5, max(0, int(params.get("fast_width", 3))))
slow_width = min(5, max(0, int(params.get("slow_width", 3))))
color_fast_by_slope = bool(params.get("color_fast_by_slope", False))
slope_smoothing = min(20, max(1, int(params.get("slope_smoothing", 2))))
bullish_color = str(params.get("bullish_color", "#22C55E"))
bearish_color = str(params.get("bearish_color", "#EF4444"))
color_fast_by_relation = bool(params.get("color_fast_by_relation", False))
show_fill = bool(params.get("show_fill", True))
fill_transparency = min(100, max(0, int(params.get("fill_transparency", 50))))
alert_slope_bull = bool(params.get("alert_slope_bull", False))
alert_slope_bear = bool(params.get("alert_slope_bear", False))
alert_cross_bull = bool(params.get("alert_cross_bull", False))
alert_cross_bear = bool(params.get("alert_cross_bear", False))

timeframe_seconds = {"1M": 60, "5M": 300, "15M": 900, "30M": 1800, "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
runtime_context = globals().get("ctx")
chart_timeframe = str(getattr(runtime_context, "timeframe", "1H") or "1H").upper()
chart_seconds = timeframe_seconds.get(chart_timeframe, 3600)


def normalize_ohlc(raw):
    if raw is None or len(raw) == 0:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    result = raw.copy().reset_index(drop=True)
    for column in ("time", "open", "high", "low", "close", "volume"):
        if column not in result.columns:
            result[column] = np.arange(len(result), dtype=float) * 3600.0 if column == "time" else np.nan
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result["volume"] = result["volume"].fillna(0.0)
    result = result.dropna(subset=["time", "open", "high", "low", "close"])
    times = result["time"].to_numpy(dtype=float)
    if len(times) and np.nanmax(np.abs(times)) > 10000000000:
        result["time"] = times / 1000.0
    return result.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)


def select_source(source_frame, source_name):
    opens = source_frame["open"].to_numpy(dtype=float)
    highs = source_frame["high"].to_numpy(dtype=float)
    lows = source_frame["low"].to_numpy(dtype=float)
    closes = source_frame["close"].to_numpy(dtype=float)
    key = source_name.replace("_", "").replace(" ", "").upper()
    choices = {
        "OPEN": opens,
        "HIGH": highs,
        "LOW": lows,
        "HL2": (highs + lows) * 0.5,
        "HLC3": (highs + lows + closes) / 3.0,
        "OHLC4": (opens + highs + lows + closes) * 0.25,
    }
    return choices.get(key, closes).astype(float)


def sma(values, length):
    return pd.Series(values, dtype=float).rolling(length, min_periods=length).mean().to_numpy(dtype=float)


def ema(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    finite_indexes = np.flatnonzero(np.isfinite(values))
    if len(finite_indexes) < length:
        return result
    seed_indexes = finite_indexes[:length]
    seed_at = seed_indexes[-1]
    previous = float(np.mean(values[seed_indexes]))
    result[seed_at] = previous
    alpha = 2.0 / (float(length) + 1.0)
    for index in range(seed_at + 1, len(values)):
        if np.isfinite(values[index]):
            previous = alpha * float(values[index]) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def rma(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    finite_indexes = np.flatnonzero(np.isfinite(values))
    if len(finite_indexes) < length:
        return result
    seed_indexes = finite_indexes[:length]
    seed_at = seed_indexes[-1]
    previous = float(np.mean(values[seed_indexes]))
    result[seed_at] = previous
    for index in range(seed_at + 1, len(values)):
        if np.isfinite(values[index]):
            previous = (previous * (length - 1) + float(values[index])) / float(length)
        result[index] = previous
    return result


def wma(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, length + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / denominator)
    return result


def vwma(values, volumes, length):
    values = np.asarray(values, dtype=float)
    volumes = np.asarray(volumes, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        left = index - length + 1
        denominator = float(np.sum(volumes[left:index + 1]))
        if denominator != 0.0:
            result[index] = float(np.sum(values[left:index + 1] * volumes[left:index + 1]) / denominator)
    return result


def moving_average(source_frame, source_name, kind, length, factor):
    values = select_source(source_frame, source_name)
    volumes = source_frame["volume"].to_numpy(dtype=float)
    if kind == "SMA":
        return sma(values, length)
    if kind == "EMA":
        return ema(values, length)
    if kind == "WMA":
        return wma(values, length)
    if kind in ("HULL_MA", "HMA"):
        half = max(1, int(round(length / 2.0)))
        root = max(1, int(round(np.sqrt(length))))
        return wma(2.0 * wma(values, half) - wma(values, length), root)
    if kind == "VWMA":
        return vwma(values, volumes, length)
    if kind == "RMA":
        return rma(values, length)
    if kind == "TEMA":
        first = ema(values, length)
        second = ema(first, length)
        third = ema(second, length)
        return 3.0 * (first - second) + third
    first = ema(values, length)
    second = ema(first, length)
    gd_one = first * (1.0 + factor) - second * factor
    third = ema(gd_one, length)
    fourth = ema(third, length)
    gd_two = third * (1.0 + factor) - fourth * factor
    fifth = ema(gd_two, length)
    sixth = ema(fifth, length)
    return fifth * (1.0 + factor) - sixth * factor


base_frame = normalize_ohlc(df)
n = len(base_frame)
base_time = base_frame["time"].to_numpy(dtype=float)


def resolved_frame(timeframe_name):
    resolved = chart_timeframe if timeframe_name in ("", "CHART") else timeframe_name
    if resolved == chart_timeframe:
        return base_frame, chart_seconds, resolved, True
    raw_source = (
        runtime_context.raw_series(resolved)
        if runtime_context is not None and hasattr(runtime_context, "raw_series")
        else None
    )
    raw = normalize_ohlc(raw_source)
    if not len(raw):
        return base_frame, chart_seconds, chart_timeframe, True
    return raw, timeframe_seconds.get(resolved, chart_seconds), resolved, False


def align_completed(source_frame, values, source_seconds, direct):
    if direct and len(values) == n:
        return np.asarray(values, dtype=float).copy(), np.arange(n, dtype=int)
    source_time = source_frame["time"].to_numpy(dtype=float)
    completed = source_time + source_seconds
    indexes = np.searchsorted(completed, base_time + chart_seconds, side="right") - 1
    result = np.full(n, np.nan, dtype=float)
    valid = (indexes >= 0) & (indexes < len(values))
    result[valid] = np.asarray(values, dtype=float)[indexes[valid]]
    return result, indexes


fast_frame, fast_seconds, resolved_fast_timeframe, fast_direct = resolved_frame(fast_timeframe)
slow_frame, slow_seconds, resolved_slow_timeframe, slow_direct = resolved_frame(slow_timeframe)
raw_fast = moving_average(fast_frame, fast_source_name, fast_type, fast_length, fast_t3_factor)
raw_slow = moving_average(slow_frame, slow_source_name, slow_type, slow_length, slow_t3_factor)
fast_ma, fast_source_index = align_completed(fast_frame, raw_fast, fast_seconds, fast_direct)
slow_ma, slow_source_index = align_completed(slow_frame, raw_slow, slow_seconds, slow_direct)

fast_up = np.zeros(n, dtype=bool)
fast_down = np.zeros(n, dtype=bool)
for index in range(slope_smoothing, n):
    if np.isfinite(fast_ma[index]) and np.isfinite(fast_ma[index - slope_smoothing]):
        fast_up[index] = fast_ma[index] >= fast_ma[index - slope_smoothing]
        fast_down[index] = fast_ma[index] < fast_ma[index - slope_smoothing]
fast_above = fast_ma >= slow_ma
fast_below = fast_ma < slow_ma

fast_colors = []
for index in range(n):
    if color_fast_by_relation and np.isfinite(slow_ma[index]):
        fast_colors.append(bullish_color if fast_above[index] else bearish_color)
    elif color_fast_by_slope:
        fast_colors.append(bullish_color if fast_up[index] else bearish_color if fast_down[index] else fast_color)
    else:
        fast_colors.append(fast_color)


def shifted(values, offset):
    result = [None] * n
    for index, value in enumerate(values):
        target = index + offset
        if 0 <= target < n and np.isfinite(value):
            result[target] = float(value)
    return result


def shifted_points(values, colors, offset):
    result = [None] * n
    for index, value in enumerate(values):
        target = index + offset
        if 0 <= target < n and np.isfinite(value):
            result[target] = {"value": float(value), "color": colors[index]}
    return result


fast_plot = shifted_points(fast_ma, fast_colors, fast_offset)
slow_plot = shifted(slow_ma, slow_offset)
cloud = [None] * n
if show_fill and show_slow:
    opacity = (100.0 - fill_transparency) / 100.0
    for index in range(n):
        fast_point = fast_plot[index]
        slow_value = slow_plot[index]
        if fast_point is not None and slow_value is not None:
            fast_value = float(fast_point["value"])
            cloud[index] = {"upper": max(fast_value, slow_value), "lower": min(fast_value, slow_value), "color": fast_point["color"]}

slope_up_alert = np.zeros(n, dtype=bool)
slope_down_alert = np.zeros(n, dtype=bool)
cross_up_alert = np.zeros(n, dtype=bool)
cross_down_alert = np.zeros(n, dtype=bool)
for index in range(slope_smoothing + 1, n):
    if np.isfinite(fast_ma[index]) and np.isfinite(fast_ma[index - 1]) and np.isfinite(fast_ma[index - slope_smoothing]) and np.isfinite(fast_ma[index - 1 - slope_smoothing]):
        slope_up_alert[index] = fast_ma[index - 1] < fast_ma[index - 1 - slope_smoothing] and fast_ma[index] >= fast_ma[index - slope_smoothing]
        slope_down_alert[index] = fast_ma[index - 1] > fast_ma[index - 1 - slope_smoothing] and fast_ma[index] < fast_ma[index - slope_smoothing]
for index in range(1, n):
    if np.isfinite(fast_ma[index]) and np.isfinite(slow_ma[index]) and np.isfinite(fast_ma[index - 1]) and np.isfinite(slow_ma[index - 1]):
        cross_up_alert[index] = fast_ma[index - 1] < slow_ma[index - 1] and fast_ma[index] >= slow_ma[index]
        cross_down_alert[index] = fast_ma[index - 1] > slow_ma[index - 1] and fast_ma[index] < slow_ma[index]

plots = [
    {"name": "Fast MA", "data": fast_plot, "color": fast_color, "type": "line", "lineWidth": max(1, fast_width), "overlay": True},
]
if show_slow:
    plots.append({"name": "Slow MA", "data": slow_plot, "color": slow_color, "type": "line", "lineWidth": max(1, slow_width), "overlay": True})
if show_fill and show_slow:
    plots.insert(0, {"name": "MA Fill", "title": False, "data": cloud, "color": bullish_color, "type": "cloud", "opacity": (100.0 - fill_transparency) / 100.0, "overlay": True})

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "resolvedFastTimeframe": resolved_fast_timeframe,
        "resolvedSlowTimeframe": resolved_slow_timeframe,
        "fastSourceIndex": [int(value) for value in fast_source_index],
        "slowSourceIndex": [int(value) for value in slow_source_index],
        "fastMA": [float(value) if np.isfinite(value) else None for value in fast_ma],
        "slowMA": [float(value) if np.isfinite(value) else None for value in slow_ma],
        "fastUp": [bool(value) for value in fast_up],
        "fastDown": [bool(value) for value in fast_down],
        "fastAbove": [bool(value) for value in fast_above],
        "fastBelow": [bool(value) for value in fast_below],
        "slopeBullEvent": [bool(value) for value in slope_up_alert],
        "slopeBearEvent": [bool(value) for value in slope_down_alert],
        "crossBullEvent": [bool(value) for value in cross_up_alert],
        "crossBearEvent": [bool(value) for value in cross_down_alert],
        "alertSlopeBull": [bool(alert_slope_bull and value) for value in slope_up_alert],
        "alertSlopeBear": [bool(alert_slope_bear and value) for value in slope_down_alert],
        "alertCrossBull": [bool(alert_cross_bull and value) for value in cross_up_alert],
        "alertCrossBear": [bool(alert_cross_bear and value) for value in cross_down_alert],
    },
}
