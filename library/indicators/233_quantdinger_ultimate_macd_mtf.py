# ============================================================
# QuantDinger Ultimate MACD MTF
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Ultimate MACD MTF"
my_indicator_description = (
    "Computes MACD on the chart or a confirmed alternate timeframe with selectable "
    "SMA/EMA methods, four-state histogram colors, cross dots, and trend highlights."
)

indicator_timeframes = ["1H", "4H", "1D", "1W"]

# @param indicator_timeframe str Chart Indicator timeframe values=Chart,1H,4H,1D,1W
# @param fast_length int 12 Fast length range=1:500:1
# @param slow_length int 26 Slow length range=1:500:1
# @param source str Close Source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param signal_length int 9 Signal smoothing range=1:999:1
# @param oscillator_ma str EMA Oscillator MA type values=SMA,EMA
# @param signal_ma str EMA Signal MA type values=SMA,EMA
# @param show_macd bool true Show MACD lines
# @param macd_width int 3 MACD width range=0:5:1
# @param signal_width int 2 Signal width range=0:5:1
# @param show_histogram bool true Show histogram
# @param histogram_width int 5 Histogram width range=0:5:1
# @param show_trend_color bool true Show MACD trend color
# @param highlight_price_bars bool false Highlight price bars
# @param highlight_cross_background bool false Highlight cross background
# @param show_cross_dots bool true Show cross dots
# @param cross_dot_size int 5 Cross dot size range=1:8:1
# @param macd_color str #FF6D00 MACD color
# @param signal_color str #2962FF Signal color
# @param trend_up_color str #4BAF4F Trend up color
# @param trend_down_color str #B71D1C Trend down color
# @param grow_above_color str #26A69A Above zero growing color
# @param fall_above_color str #B2DFDB Above zero falling color
# @param grow_below_color str #FF5252 Below zero growing color
# @param fall_below_color str #FFCDD2 Below zero falling color
# @param alert_cross_up bool true Enable cross-up alert state
# @param alert_cross_down bool true Enable cross-down alert state
# @param alert_cross_up_above_zero bool false Enable cross-up above zero alert state
# @param alert_cross_down_below_zero bool false Enable cross-down below zero alert state

requested_timeframe = str(params.get("indicator_timeframe", "Chart")).upper()
fast_length = min(500, max(1, int(params.get("fast_length", 12))))
slow_length = min(500, max(1, int(params.get("slow_length", 26))))
source_name = str(params.get("source", "Close"))
signal_length = min(999, max(1, int(params.get("signal_length", 9))))
oscillator_ma = str(params.get("oscillator_ma", "EMA")).upper()
signal_ma = str(params.get("signal_ma", "EMA")).upper()
show_macd = bool(params.get("show_macd", True))
macd_width = min(5, max(0, int(params.get("macd_width", 3))))
signal_width = min(5, max(0, int(params.get("signal_width", 2))))
show_histogram = bool(params.get("show_histogram", True))
histogram_width = min(5, max(0, int(params.get("histogram_width", 5))))
show_trend_color = bool(params.get("show_trend_color", True))
highlight_price_bars = bool(params.get("highlight_price_bars", False))
highlight_cross_background = bool(params.get("highlight_cross_background", False))
show_cross_dots = bool(params.get("show_cross_dots", True))
cross_dot_size = min(8, max(1, int(params.get("cross_dot_size", 5))))
macd_color = str(params.get("macd_color", "#FF6D00"))
signal_color = str(params.get("signal_color", "#2962FF"))
trend_up_color = str(params.get("trend_up_color", "#4BAF4F"))
trend_down_color = str(params.get("trend_down_color", "#B71D1C"))
grow_above_color = str(params.get("grow_above_color", "#26A69A"))
fall_above_color = str(params.get("fall_above_color", "#B2DFDB"))
grow_below_color = str(params.get("grow_below_color", "#FF5252"))
fall_below_color = str(params.get("fall_below_color", "#FFCDD2"))
alert_cross_up = bool(params.get("alert_cross_up", True))
alert_cross_down = bool(params.get("alert_cross_down", True))
alert_cross_up_above_zero = bool(params.get("alert_cross_up_above_zero", False))
alert_cross_down_below_zero = bool(params.get("alert_cross_down_below_zero", False))

timeframe_seconds = {"1M": 60, "5M": 300, "15M": 900, "30M": 1800, "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
runtime_context = ctx
chart_timeframe = str(runtime_context.timeframe or "1H").upper()
chart_seconds = timeframe_seconds.get(chart_timeframe, 3600)
resolved_timeframe = chart_timeframe if requested_timeframe in ("", "CHART") else requested_timeframe
resolved_seconds = timeframe_seconds.get(resolved_timeframe, chart_seconds)


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
    return result.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)


def select_source(source_frame):
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


def ema(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    if not len(values):
        return result
    alpha = 2.0 / (float(length) + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            if np.isfinite(previous):
                result[index] = previous
            continue
        previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def moving_average(values, length, method):
    if method == "SMA":
        return pd.Series(values, dtype=float).rolling(length, min_periods=length).mean().to_numpy(dtype=float)
    return ema(values, length)


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


base_frame = normalize_ohlc(df)
n = len(base_frame)
base_times = base_frame["time"].to_numpy(dtype=float)
base_seconds = base_times.copy()
if n and np.nanmax(np.abs(base_seconds)) > 10000000000:
    base_seconds = base_seconds / 1000.0

source_index = np.arange(n, dtype=int)
if resolved_timeframe == chart_timeframe:
    calculation_frame = base_frame
    calculation_source = select_source(calculation_frame)
    calculation_fast = moving_average(calculation_source, fast_length, oscillator_ma)
    calculation_slow = moving_average(calculation_source, slow_length, oscillator_ma)
    calculation_macd = calculation_fast - calculation_slow
    calculation_signal = moving_average(calculation_macd, signal_length, signal_ma)
    macd = calculation_macd
    signal = calculation_signal
else:
    calculation_source = (
        runtime_context.raw_series(resolved_timeframe)
        if runtime_context is not None and hasattr(runtime_context, "raw_series")
        else None
    )
    calculation_frame = normalize_ohlc(calculation_source)
    if not len(calculation_frame):
        calculation_frame = base_frame
        resolved_timeframe = chart_timeframe
        resolved_seconds = chart_seconds
    calculation_times = calculation_frame["time"].to_numpy(dtype=float)
    if len(calculation_times) and np.nanmax(np.abs(calculation_times)) > 10000000000:
        calculation_times = calculation_times / 1000.0
    calculation_source = select_source(calculation_frame)
    calculation_fast = moving_average(calculation_source, fast_length, oscillator_ma)
    calculation_slow = moving_average(calculation_source, slow_length, oscillator_ma)
    calculation_macd = calculation_fast - calculation_slow
    calculation_signal = moving_average(calculation_macd, signal_length, signal_ma)
    completed_times = calculation_times + resolved_seconds
    base_close_times = base_seconds + chart_seconds
    source_index = np.searchsorted(completed_times, base_close_times, side="right") - 1
    macd = np.full(n, np.nan, dtype=float)
    signal = np.full(n, np.nan, dtype=float)
    valid = (source_index >= 0) & (source_index < len(calculation_frame))
    macd[valid] = calculation_macd[source_index[valid]]
    signal[valid] = calculation_signal[source_index[valid]]

histogram = macd - signal
trend_up = macd > signal
trend_down = macd < signal
cross_up = np.zeros(n, dtype=bool)
cross_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(macd[index]) and np.isfinite(signal[index]) and np.isfinite(macd[index - 1]) and np.isfinite(signal[index - 1]):
        cross_up[index] = signal[index - 1] >= macd[index - 1] and signal[index] < macd[index]
        cross_down[index] = signal[index - 1] <= macd[index - 1] and signal[index] > macd[index]
cross_up_above_zero = cross_up & (macd > 0.0)
cross_down_below_zero = cross_down & (macd < 0.0)

histogram_colors = []
histogram_states = []
previous_state = "neutral"
for index, value in enumerate(histogram):
    if not np.isfinite(value):
        state = "neutral"
    elif index and np.isfinite(histogram[index - 1]) and value == histogram[index - 1]:
        state = previous_state
    elif index and value > histogram[index - 1] and value > 0.0:
        state = "grow_above"
    elif index and value < histogram[index - 1] and value > 0.0:
        state = "fall_above"
    elif index and value < histogram[index - 1] and value <= 0.0:
        state = "grow_below"
    elif index and value > histogram[index - 1] and value <= 0.0:
        state = "fall_below"
    else:
        state = "neutral"
    previous_state = state
    histogram_states.append(state)
    histogram_colors.append({
        "grow_above": grow_above_color,
        "fall_above": fall_above_color,
        "grow_below": grow_below_color,
        "fall_below": fall_below_color,
    }.get(state, "#94A3B8"))

histogram_points = [
    {"value": float(value), "color": histogram_colors[index]} if show_histogram and np.isfinite(value) and value != 0.0 else None
    for index, value in enumerate(histogram)
]
macd_points = [
    {
        "value": float(value),
        "color": trend_up[index] and show_trend_color and trend_up_color or trend_down[index] and show_trend_color and trend_down_color or macd_color,
    }
    if show_macd and np.isfinite(value) and value != 0.0
    else None
    for index, value in enumerate(macd)
]
signal_points = [float(value) if show_macd and np.isfinite(value) and value != 0.0 else None for value in signal]


def cross_points(mask):
    return [
        {"value": float(macd[index]), "color": trend_up_color if trend_up[index] else trend_down_color, "size": float(cross_dot_size)}
        if show_cross_dots and mask[index] and np.isfinite(macd[index])
        else None
        for index in range(n)
    ]


plots = [
    {"name": "Histogram", "data": histogram_points, "color": grow_above_color, "type": "histogram", "baseValue": 0.0, "lineWidth": max(1, histogram_width), "overlay": False},
    {"name": "Signal", "data": signal_points, "color": signal_color, "type": "line", "lineWidth": max(1, signal_width), "overlay": False},
    {"name": "MACD", "data": macd_points, "color": macd_color, "type": "line", "lineWidth": max(1, macd_width), "overlay": False},
    {"name": "Zero", "data": [0.0] * n, "color": "#64748B", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": False},
    {"name": "Cross Up", "title": False, "data": cross_points(cross_up), "color": trend_up_color, "type": "scatter", "overlay": False},
    {"name": "Cross Down", "title": False, "data": cross_points(cross_down), "color": trend_down_color, "type": "scatter", "overlay": False},
]

bar_colors = [
    trend_up_color if highlight_price_bars and trend_up[index] else trend_down_color if highlight_price_bars and trend_down[index] else None
    for index in range(n)
]
background_colors = [
    "rgba(75,175,79,0.14)" if highlight_cross_background and cross_up[index] else "rgba(183,29,28,0.14)" if highlight_cross_background and cross_down[index] else None
    for index in range(n)
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": bar_colors,
    "background_colors": background_colors,
    "calculatedVars": {
        "resolvedTimeframe": resolved_timeframe,
        "sourceIndex": [int(value) for value in source_index],
        "rawCount": int(len(calculation_frame)),
        "macd": finite(macd),
        "signal": finite(signal),
        "histogram": finite(histogram),
        "trendUp": [bool(value) for value in trend_up],
        "trendDown": [bool(value) for value in trend_down],
        "crossUp": [bool(value) for value in cross_up],
        "crossDown": [bool(value) for value in cross_down],
        "crossUpAboveZero": [bool(value) for value in cross_up_above_zero],
        "crossDownBelowZero": [bool(value) for value in cross_down_below_zero],
        "histogramState": histogram_states,
        "alertLong": [bool(alert_cross_up and value) for value in cross_up],
        "alertShort": [bool(alert_cross_down and value) for value in cross_down],
        "alertLongAboveZero": [bool(alert_cross_up_above_zero and value) for value in cross_up_above_zero],
        "alertShortBelowZero": [bool(alert_cross_down_below_zero and value) for value in cross_down_below_zero],
    },
}
