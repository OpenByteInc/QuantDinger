# ============================================================
# QuantDinger Mean Reversion Channel
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

import math

my_indicator_name = "QuantDinger Mean Reversion Channel"
my_indicator_description = "Displays an Ehlers-style mean channel, graded outer zones and optional multi-timeframe status."
indicator_timeframes = ["1m", "5m", "15m", "1H", "4H", "1D", "1W", "1M"]

# @param source str HLC3 Price source values=HLC3,Close,HL2,OHLC4,Open,High,Low
# @param filter_type str SuperSmoother Filter type values=SuperSmoother,Ehlers EMA,Gaussian,Butterworth,BandStop,SMA,EMA,RMA
# @param length int 200 Lookback period range=1:1000:1
# @param inner_multiplier float 1.0 Inner channel size range=0.1:10:0.1
# @param outer_multiplier float 2.415 Outer channel size range=0.1:10:0.005
# @param draw_channel bool true Draw channel
# @param display_zone bool true Draw graded outer zones
# @param zone_transparency int 60 Zone transparency range=0:100:1
# @param display_extension bool true Display latest-value extensions
# @param enable_mtf bool true Enable multiple-timeframe analysis
# @param mtf_display str Hover MTF display type values=Always,Hover
# @param mtf_type str Auto Multiple-timeframe type values=Auto,Custom
# @param mtf_level_1 str 1D Custom MTF level 1 values=1m,5m,15m,30m,1H,2H,4H,1D,1W,1M
# @param mtf_level_2 str 1W Custom MTF level 2 values=1m,5m,15m,30m,1H,2H,4H,1D,1W,1M

source_name = str(params.get("source", "HLC3")).upper()
if source_name not in ("HLC3", "CLOSE", "HL2", "OHLC4", "OPEN", "HIGH", "LOW"):
    source_name = "HLC3"
filter_type = str(params.get("filter_type", "SuperSmoother"))
valid_filters = ("SuperSmoother", "Ehlers EMA", "Gaussian", "Butterworth", "BandStop", "SMA", "EMA", "RMA")
if filter_type not in valid_filters:
    filter_type = "SuperSmoother"
length = min(1000, max(1, int(params.get("length", 200))))
inner_multiplier = max(0.1, float(params.get("inner_multiplier", 1.0)))
outer_multiplier = max(0.1, float(params.get("outer_multiplier", 2.415)))
draw_channel = bool(params.get("draw_channel", True))
display_zone = bool(params.get("display_zone", True))
zone_transparency = min(100, max(0, int(params.get("zone_transparency", 60))))
display_extension = bool(params.get("display_extension", True))
enable_mtf = bool(params.get("enable_mtf", True))
mtf_display = str(params.get("mtf_display", "Hover"))
if mtf_display == "Always Display":
    mtf_display = "Always"
elif mtf_display == "On Hover":
    mtf_display = "Hover"
if mtf_display not in ("Always", "Hover"):
    mtf_display = "Hover"
mtf_type = str(params.get("mtf_type", "Auto"))
if mtf_type not in ("Auto", "Custom"):
    mtf_type = "Auto"


def _tf_key(value):
    text = str(value).strip()
    aliases = {"1": "1m", "5": "5m", "15": "15m", "30": "30m", "60": "1H", "120": "2H", "240": "4H", "D": "1D", "W": "1W", "M": "1M"}
    if text in aliases:
        return aliases[text]
    upper = text.upper()
    aliases_upper = {"1M": "1m" if text.endswith("m") else "1M", "5M": "5m", "15M": "15m", "30M": "30m", "1H": "1H", "2H": "2H", "4H": "4H", "1D": "1D", "1W": "1W"}
    return aliases_upper.get(upper, "1H")


custom_level_1 = _tf_key(params.get("mtf_level_1", "1D"))
custom_level_2 = _tf_key(params.get("mtf_level_2", "1W"))
timeframe_seconds = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1H": 3600, "2H": 7200, "4H": 14400, "1D": 86400, "1W": 604800, "1M": 2629800}


def _normalize(frame):
    source_frame = df.copy() if frame is None or len(frame) == 0 else frame.copy()
    result = source_frame.reset_index(drop=True)
    if "time" not in result.columns:
        result["time"] = np.arange(len(result), dtype=float) * 3600.0
    if "volume" not in result.columns:
        result["volume"] = 0.0
    for column in ("time", "open", "high", "low", "close", "volume"):
        if column not in result.columns:
            result[column] = 0.0
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
    times = result["time"].to_numpy(dtype=float)
    if len(times) and max(abs(float(value)) for value in times) > 10_000_000_000:
        result["time"] = times / 1000.0
    return result


df = df.copy()
base_frame = _normalize(df)
base_time = base_frame["time"].to_numpy(dtype=float)
n = len(base_frame)
runtime_ctx = globals().get("ctx")
chart_timeframe = _tf_key(str(getattr(runtime_ctx, "timeframe", "1H") or "1H"))
chart_seconds = timeframe_seconds.get(chart_timeframe, 3600)


def _price(frame):
    opened = frame["open"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    if source_name == "CLOSE":
        return close
    if source_name == "HL2":
        return (high + low) / 2.0
    if source_name == "OHLC4":
        return (opened + high + low + close) / 4.0
    if source_name == "OPEN":
        return opened
    if source_name == "HIGH":
        return high
    if source_name == "LOW":
        return low
    return (high + low + close) / 3.0


def _true_range(frame):
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    result = np.full(len(frame), np.nan, dtype=float)
    for index in range(len(frame)):
        if index == 0:
            result[index] = high[index] - low[index]
        else:
            result[index] = max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
    return result


def _super_smoother(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    a1 = math.exp(-math.sqrt(2.0) * math.pi / period)
    b1 = 2.0 * a1 * math.cos(math.sqrt(2.0) * math.pi / period)
    c3 = -(a1 * a1)
    c2 = b1
    c1 = 1.0 - c2 - c3
    for index in range(len(values)):
        previous_1 = result[index - 1] if index >= 1 and np.isfinite(result[index - 1]) else values[index - 1] if index >= 1 else np.nan
        previous_2 = result[index - 2] if index >= 2 and np.isfinite(result[index - 2]) else values[index - 2] if index >= 2 else np.nan
        if np.all(np.isfinite([values[index], previous_1, previous_2])):
            result[index] = c1 * values[index] + c2 * previous_1 + c3 * previous_2
    return result


def _sak(values, period, kind):
    c0 = 1.0
    c1 = 0.0
    b0 = 1.0
    b1 = 0.0
    b2 = 0.0
    a1 = 0.0
    a2 = 0.0
    cycle = 2.0 * math.pi / period
    if kind == "Ehlers EMA":
        cosine = math.cos(cycle)
        alpha = (cosine + math.sin(cycle) - 1.0) / cosine if abs(cosine) > 1e-12 else 1.0
        b0, a1 = alpha, 1.0 - alpha
    elif kind in ("Gaussian", "Butterworth"):
        beta = 2.415 * (1.0 - math.cos(cycle))
        alpha = -beta + math.sqrt(max(0.0, beta * beta + 2.0 * beta))
        if kind == "Gaussian":
            c0, a1, a2 = alpha * alpha, 2.0 * (1.0 - alpha), -((1.0 - alpha) ** 2)
        else:
            c0, b1, b2, a1, a2 = alpha * alpha / 4.0, 2.0, 1.0, 2.0 * (1.0 - alpha), -((1.0 - alpha) ** 2)
    elif kind == "BandStop":
        beta = math.cos(cycle)
        divisor = math.cos(cycle * 0.2)
        gamma = 1.0 / divisor if abs(divisor) > 1e-12 else 1e12
        alpha = gamma - math.sqrt(max(0.0, gamma * gamma - 1.0))
        c0, b1, b2, a1, a2 = (1.0 + alpha) / 2.0, -2.0 * beta, 1.0, beta * (1.0 + alpha), -alpha
    elif kind == "SMA":
        c1, b0, a1 = 1.0 / period, 1.0 / period, 1.0
    elif kind == "EMA":
        alpha = 2.0 / (period + 1.0)
        b0, a1 = alpha, 1.0 - alpha
    elif kind == "RMA":
        alpha = 1.0 / period
        b0, a1 = alpha, 1.0 - alpha
    result = np.full(len(values), np.nan, dtype=float)
    for index, value in enumerate(values):
        input_1 = values[index - 1] if index >= 1 and np.isfinite(values[index - 1]) else 0.0
        input_2 = values[index - 2] if index >= 2 and np.isfinite(values[index - 2]) else 0.0
        input_length = values[index - period] if index >= period and np.isfinite(values[index - period]) else 0.0
        output_1 = result[index - 1] if index >= 1 and np.isfinite(result[index - 1]) else 0.0
        output_2 = result[index - 2] if index >= 2 and np.isfinite(result[index - 2]) else 0.0
        result[index] = c0 * (b0 * value + b1 * input_1 + b2 * input_2) + a1 * output_1 + a2 * output_2 - c1 * input_length
    return result


def _mrc(frame):
    price = _price(frame)
    close = frame["close"].to_numpy(dtype=float)
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    mean_range = _super_smoother(_true_range(frame), length)
    mean_line = _super_smoother(price, length) if filter_type == "SuperSmoother" else _sak(price, length, filter_type)
    inner = math.pi * inner_multiplier
    outer = math.pi * outer_multiplier
    upper_1 = mean_line + mean_range * inner
    lower_1 = mean_line - mean_range * inner
    upper_2 = mean_line + mean_range * outer
    lower_2 = mean_line - mean_range * outer
    condition = np.zeros(len(frame), dtype=int)
    for index in range(len(frame)):
        if not np.all(np.isfinite([mean_line[index], mean_range[index], upper_2[index], lower_2[index]])):
            continue
        if close[index] > mean_line[index]:
            outer_high = upper_2[index] + mean_range[index] * 2.0
            outer_low = upper_2[index] - mean_range[index] * 2.0
            if high[index] >= outer_low and high[index] < upper_2[index]:
                condition[index] = 1
            elif high[index] >= upper_2[index] and high[index] < outer_high:
                condition[index] = 2
            elif high[index] >= outer_high:
                condition[index] = 3
            elif close[index] <= mean_line[index] + mean_range[index]:
                condition[index] = 4
            else:
                condition[index] = 5
        elif close[index] < mean_line[index]:
            outer_low = lower_2[index] - mean_range[index] * 2.0
            outer_high = lower_2[index] + mean_range[index] * 2.0
            if low[index] <= outer_high and low[index] > lower_2[index]:
                condition[index] = -1
            elif low[index] <= lower_2[index] and low[index] > outer_low:
                condition[index] = -2
            elif low[index] <= outer_low:
                condition[index] = -3
            elif close[index] >= mean_line[index] + mean_range[index]:
                condition[index] = -4
            else:
                condition[index] = -5
    return {"mean": mean_line, "range": mean_range, "upper1": upper_1, "lower1": lower_1, "upper2": upper_2, "lower2": lower_2, "condition": condition}


def _auto_timeframes():
    minutes = chart_seconds / 60.0
    if minutes < 1.0:
        return "1m", "5m"
    if minutes <= 3.0:
        return "5m", "15m"
    if minutes <= 10.0:
        return "15m", "1H"
    if minutes <= 30.0:
        return "1H", "4H"
    if minutes <= 120.0:
        return "4H", "1D"
    if minutes <= 240.0:
        return "1D", "1W"
    if minutes <= 1440.0:
        return "1W", "1M"
    if minutes <= 10080.0:
        return "1M", chart_timeframe
    return chart_timeframe, chart_timeframe


level_1, level_2 = _auto_timeframes() if mtf_type == "Auto" else (custom_level_1, custom_level_2)


def _requested(timeframe):
    if timeframe == chart_timeframe:
        return base_frame.copy(), chart_seconds, True, True
    raw = runtime_ctx.raw_series(timeframe) if runtime_ctx is not None and hasattr(runtime_ctx, "raw_series") else None
    if raw is None or len(raw) == 0:
        return base_frame.copy(), chart_seconds, True, False
    return _normalize(raw), timeframe_seconds.get(timeframe, chart_seconds), False, True


def _align_completed(source_frame, values, source_seconds, direct):
    if direct and len(values) == n:
        return np.asarray(values).copy()
    result = np.full(n, np.nan, dtype=float)
    source_time = source_frame["time"].to_numpy(dtype=float)
    source_close = source_time + source_seconds
    for index in range(n):
        current_close = base_time[index] + chart_seconds
        source_index = int(np.searchsorted(source_close, current_close, side="right") - 1)
        if 0 <= source_index < len(values) and np.isfinite(values[source_index]):
            result[index] = float(values[source_index])
    return result


base = _mrc(base_frame)
mtf_results = []
for timeframe in (level_1, level_2):
    requested_frame, requested_seconds, direct, available = _requested(timeframe)
    raw_result = _mrc(requested_frame)
    aligned = {key: _align_completed(requested_frame, values, requested_seconds, direct) for key, values in raw_result.items()}
    mtf_results.append({"timeframe": timeframe, "available": available, "values": aligned})


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


visible = _finite(base["mean"]) if draw_channel else [None] * n
plots = [
    {"name": "Mean", "data": visible, "color": "#FFCD00", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "R1", "data": _finite(base["upper1"]) if draw_channel else [None] * n, "color": "rgba(0,128,0,0.50)", "type": "line", "style": "dotted", "lineWidth": 1, "overlay": True},
    {"name": "S1", "data": _finite(base["lower1"]) if draw_channel else [None] * n, "color": "rgba(0,128,0,0.50)", "type": "line", "style": "dotted", "lineWidth": 1, "overlay": True},
    {"name": "R2", "data": _finite(base["upper2"]) if draw_channel else [None] * n, "color": "rgba(255,0,0,0.50)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "S2", "data": _finite(base["lower2"]) if draw_channel else [None] * n, "color": "rgba(255,0,0,0.50)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Price Guide", "title": False, "data": _finite(base_frame["close"].to_numpy(dtype=float)), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
]

zone_colors = ["#FF0000", "#FF4200", "#FF5D00", "#FF7400", "#FF9700", "#FFAE00", "#FFC500", "#FFCD00"]
zone_factors = [2.0, 1.5, 1.0, 0.5, 0.0, -0.5, -1.0, -1.5, -2.0]
zone_opacity = 1.0 - zone_transparency / 100.0
for side, sign in (("Upper", 1.0), ("Lower", -1.0)):
    anchor = base["upper2"] if side == "Upper" else base["lower2"]
    for zone_index, color in enumerate(zone_colors):
        first = anchor + sign * base["range"] * zone_factors[zone_index]
        second = anchor + sign * base["range"] * zone_factors[zone_index + 1]
        cloud = []
        for index in range(n):
            if draw_channel and display_zone and np.all(np.isfinite([first[index], second[index]])):
                cloud.append({"upper": float(max(first[index], second[index])), "lower": float(min(first[index], second[index])), "color": color})
            else:
                cloud.append(None)
        plots.append({"name": f"{side} Zone {zone_index + 1}", "title": False, "data": cloud, "color": color, "type": "cloud", "opacity": zone_opacity, "overlay": True})

layers = []
extension_visible = display_extension and not (enable_mtf and mtf_display == "Always")
if extension_visible and n:
    time_ms = base_time * 1000.0
    latest_values = (("Mean Extension", base["mean"], "#FFCD00"), ("R1 Extension", base["upper1"], "#008000"), ("S1 Extension", base["lower1"], "#008000"), ("R2 Extension", base["upper2"], "#FF0000"), ("S2 Extension", base["lower2"], "#FF0000"))
    for role, values, color in latest_values:
        finite_indices = [index for index, value in enumerate(values) if np.isfinite(value)]
        if finite_indices:
            latest = finite_indices[-1]
            layers.append({"type": "line", "role": role, "start": float(time_ms[0]), "end": float(time_ms[-1]), "y1": float(values[latest]), "y2": float(values[latest]), "color": color, "lineWidth": 1})


def _status(condition):
    names = {1: "Overbought (Weak)", 2: "Overbought", 3: "Overbought (Strong)", 4: "Price Near Mean", 5: "Price Above Mean", -1: "Oversold (Weak)", -2: "Oversold", -3: "Oversold (Strong)", -4: "Price Near Mean", -5: "Price Below Mean", 0: "Price at Mean Line"}
    return names.get(int(condition), "Price at Mean Line")


tables = []
if enable_mtf and n:
    close = base_frame["close"].to_numpy(dtype=float)
    rows = []
    frames = [(chart_timeframe, True, base)] + [(item["timeframe"], item["available"], item["values"]) for item in mtf_results]
    for timeframe, available, values in frames:
        finite_indices = [index for index, value in enumerate(values["mean"]) if np.isfinite(value)]
        if finite_indices:
            index = finite_indices[-1]
            distance = (close[-1] - values["mean"][index]) / close[-1] * 100.0 if abs(close[-1]) > 1e-12 else 0.0
            condition_value = int(values["condition"][index])
            text = f"{_status(condition_value)} · {distance:.2f}%"
        else:
            text = "Insufficient history"
        if not available:
            text += " · chart fallback"
        rows.append([{"text": timeframe, "color": "#9CA3AF"}, {"text": text, "color": "#FFFFFF"}])
    tables = [{"title": "Mean Reversion · MTF", "position": "top-right", "compact": mtf_display == "Hover", "rows": rows}]

calculated = {
    "meanLine": _finite(base["mean"]), "meanRange": _finite(base["range"]),
    "upperBand1": _finite(base["upper1"]), "lowerBand1": _finite(base["lower1"]),
    "upperBand2": _finite(base["upper2"]), "lowerBand2": _finite(base["lower2"]),
    "condition": base["condition"].tolist(), "chartTimeframe": chart_timeframe,
    "mtfLevel1": level_1, "mtfLevel2": level_2, "extensionVisible": bool(extension_visible),
    "mtf1Available": bool(mtf_results[0]["available"]), "mtf2Available": bool(mtf_results[1]["available"]),
    "mtf1Mean": _finite(mtf_results[0]["values"]["mean"]), "mtf2Mean": _finite(mtf_results[1]["values"]["mean"]),
    "mtf1Condition": [int(value) if np.isfinite(value) else None for value in mtf_results[0]["values"]["condition"]],
    "mtf2Condition": [int(value) if np.isfinite(value) else None for value in mtf_results[1]["values"]["condition"]],
}

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": [], "layers": layers,
    "tables": tables, "bar_colors": [None] * n, "background_colors": [None] * n, "calculatedVars": calculated,
}
