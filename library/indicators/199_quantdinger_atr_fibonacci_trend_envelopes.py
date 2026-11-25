# ============================================================
# QuantDinger ATR Fibonacci Trend Envelopes
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger ATR Fibonacci Trend Envelopes"
my_indicator_description = (
    "Combines a selectable moving-average baseline with ATR trend envelopes, Fibonacci "
    "retracement levels, a dynamic golden pocket, slope projections, and a same-symbol MTF dashboard."
)

indicator_timeframes = ["5m", "15m", "1H", "4H"]

# @param ma_type str RMA Baseline moving average values=SMA,EMA,WMA,HMA,RMA
# @param ma_length int 100 Baseline length range=1:500:1
# @param atr_length int 100 ATR length range=1:500:1
# @param atr_multiplier float 3.0 ATR envelope multiplier range=0.1:20.0:0.1
# @param show_projections bool true Show projected levels
# @param projection_length int 50 Projection bars range=1:500:1
# @param bullish_color str #24C0D4 Bullish color
# @param bearish_color str #EB3333 Bearish color
# @param midpoint_color str #808080 Fibonacci midpoint color
# @param pocket_color str #FF7B00 Golden pocket color
# @param show_dashboard bool true Show multi-timeframe dashboard
# @param dashboard_position str Bottom_Right Dashboard position values=Top_Right,Bottom_Right,Top_Left,Bottom_Left,Middle_Right
# @param dashboard_size str Small Dashboard size values=Tiny,Small,Normal,Large
# @param timeframe_1 str 5m Dashboard timeframe 1 values=5m,15m,1H,4H
# @param timeframe_2 str 15m Dashboard timeframe 2 values=5m,15m,1H,4H
# @param timeframe_3 str 1H Dashboard timeframe 3 values=5m,15m,1H,4H
# @param timeframe_4 str 4H Dashboard timeframe 4 values=5m,15m,1H,4H

ma_type = str(params.get("ma_type", "RMA"))
if ma_type not in ("SMA", "EMA", "WMA", "HMA", "RMA"):
    ma_type = "RMA"
ma_length = min(500, max(1, int(params.get("ma_length", 100))))
atr_length = min(500, max(1, int(params.get("atr_length", 100))))
atr_multiplier = min(20.0, max(0.1, float(params.get("atr_multiplier", 3.0))))
show_projections = bool(params.get("show_projections", True))
projection_length = min(500, max(1, int(params.get("projection_length", 50))))
bullish_color = str(params.get("bullish_color", "#24C0D4"))
bearish_color = str(params.get("bearish_color", "#EB3333"))
midpoint_color = str(params.get("midpoint_color", "#808080"))
pocket_color = str(params.get("pocket_color", "#FF7B00"))
show_dashboard = bool(params.get("show_dashboard", True))
dashboard_position = str(params.get("dashboard_position", "Bottom_Right"))
dashboard_size = str(params.get("dashboard_size", "Small"))
if dashboard_size not in ("Tiny", "Small", "Normal", "Large"):
    dashboard_size = "Small"
allowed_timeframes = ("5m", "15m", "1H", "4H")
dashboard_timeframes = [
    str(params.get("timeframe_1", "5m")),
    str(params.get("timeframe_2", "15m")),
    str(params.get("timeframe_3", "1H")),
    str(params.get("timeframe_4", "4H")),
]
dashboard_timeframes = [timeframe if timeframe in allowed_timeframes else allowed_timeframes[index] for index, timeframe in enumerate(dashboard_timeframes)]

df = df.copy()
n = len(df)
runtime_context = ctx


def rgba(color, alpha):
    value = str(color).strip()
    if len(value) == 7 and value.startswith("#"):
        try:
            red = int(value[1:3], 16)
            green = int(value[3:5], 16)
            blue = int(value[5:7], 16)
            return f"rgba({red},{green},{blue},{min(1.0, max(0.0, float(alpha))):.3f})"
        except ValueError:
            pass
    return value


def normalize_frame(frame):
    if frame is None or len(frame) == 0:
        frame = df.copy()
    result = frame.copy().reset_index(drop=True)
    for column in ("time", "open", "high", "low", "close", "volume"):
        if column not in result.columns:
            result[column] = 0.0
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.dropna(subset=["high", "low", "close"]).reset_index(drop=True)


def simple_average(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(length - 1, len(source)):
        window = source[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def exponential_average(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) < length:
        return result
    seed = source[:length]
    if np.all(np.isfinite(seed)):
        result[length - 1] = float(np.mean(seed))
    alpha = 2.0 / (length + 1.0)
    for index in range(length, len(source)):
        if np.isfinite(result[index - 1]) and np.isfinite(source[index]):
            result[index] = alpha * source[index] + (1.0 - alpha) * result[index - 1]
    return result


def weighted_average(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    weights = np.arange(1.0, length + 1.0)
    divisor = float(np.sum(weights))
    for index in range(length - 1, len(source)):
        window = source[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / divisor)
    return result


def wilder_average(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) < length:
        return result
    seed = source[:length]
    if np.all(np.isfinite(seed)):
        result[length - 1] = float(np.mean(seed))
    for index in range(length, len(source)):
        if np.isfinite(result[index - 1]) and np.isfinite(source[index]):
            result[index] = (result[index - 1] * (length - 1) + source[index]) / length
    return result


def moving_average(values, length, method):
    if method == "SMA":
        return simple_average(values, length)
    if method == "EMA":
        return exponential_average(values, length)
    if method == "WMA":
        return weighted_average(values, length)
    if method == "HMA":
        half_length = max(1, int(round(length / 2.0)))
        root_length = max(1, int(round(np.sqrt(length))))
        full = weighted_average(values, length)
        half = weighted_average(values, half_length)
        return weighted_average(2.0 * half - full, root_length)
    return wilder_average(values, length)


def calculate(frame):
    source = normalize_frame(frame)
    source_high = source["high"].to_numpy(dtype=float)
    source_low = source["low"].to_numpy(dtype=float)
    source_close = source["close"].to_numpy(dtype=float)
    count = len(source)
    true_range = np.full(count, np.nan, dtype=float)
    for index in range(count):
        if index == 0:
            true_range[index] = source_high[index] - source_low[index]
        else:
            true_range[index] = max(
                source_high[index] - source_low[index],
                abs(source_high[index] - source_close[index - 1]),
                abs(source_low[index] - source_close[index - 1]),
            )

    basis = moving_average(source_close, ma_length, ma_type)
    atr = wilder_average(true_range, atr_length)
    upper = basis + atr * atr_multiplier
    lower = basis - atr * atr_multiplier
    trend = np.zeros(count, dtype=int)
    current_trend = 0
    for index in range(count):
        if index > 0 and np.isfinite(upper[index]) and np.isfinite(upper[index - 1]):
            if source_close[index] > upper[index] and source_close[index - 1] <= upper[index - 1]:
                current_trend = 1
            elif source_close[index] < lower[index] and source_close[index - 1] >= lower[index - 1]:
                current_trend = -1
        trend[index] = current_trend

    fib50 = np.where(trend == 1, lower + (upper - lower) * 0.5, upper - (upper - lower) * 0.5)
    fib618 = np.where(trend == 1, lower + (upper - lower) * (1.0 - 0.618), upper - (upper - lower) * (1.0 - 0.618))
    fib786 = np.where(trend == 1, lower + (upper - lower) * (1.0 - 0.786), upper - (upper - lower) * (1.0 - 0.786))
    level100 = np.where(trend == 1, lower, upper)
    pocket = (source_close <= np.maximum(fib618, fib786)) & (source_close >= np.minimum(fib618, fib786))
    return {
        "close": source_close,
        "trend": trend,
        "pocket": pocket,
        "upper": upper,
        "lower": lower,
        "level100": level100,
        "fib50": fib50,
        "fib618": fib618,
        "fib786": fib786,
    }


def line_points(values, colors, visible=None):
    points = []
    for index, value in enumerate(values):
        allowed = True if visible is None else bool(visible[index])
        if allowed and np.isfinite(value):
            points.append({"value": float(value), "color": colors[index]})
        else:
            points.append(None)
    return points


main = calculate(df)
trend = main["trend"]
trend_change = np.zeros(n, dtype=bool)
for index in range(1, n):
    trend_change[index] = trend[index] != trend[index - 1]

trend_colors = [bullish_color if direction == 1 else bearish_color for direction in trend]
pocket_midpoint = (main["fib618"] + main["fib786"]) * 0.5
max_distance = (main["upper"] - main["lower"]) * 0.5
dynamic_colors = []
for index in range(n):
    if not (np.isfinite(pocket_midpoint[index]) and np.isfinite(max_distance[index]) and max_distance[index] > 0.0):
        dynamic_colors.append(rgba(pocket_color, 0.10))
        continue
    distance = abs(main["close"][index] - pocket_midpoint[index])
    transparency = min(90.0, max(0.0, 65.0 + distance / max_distance[index] * 30.0 * 0.6))
    dynamic_colors.append(rgba(pocket_color, (100.0 - transparency) / 100.0))

trend_visible = ~trend_change
fib_visible = (trend != 0) & (~trend_change)
midpoint_visible = trend != 0
cloud_data = []
for index in range(n):
    if fib_visible[index] and np.isfinite(main["fib618"][index]) and np.isfinite(main["fib786"][index]):
        cloud_data.append({
            "upper": float(max(main["fib618"][index], main["fib786"][index])),
            "lower": float(min(main["fib618"][index], main["fib786"][index])),
            "color": dynamic_colors[index],
        })
    else:
        cloud_data.append(None)

plots = [
    {"name": "Trend MA", "data": line_points(main["level100"], trend_colors, trend_visible), "color": bearish_color, "type": "line", "lineWidth": 3, "overlay": True},
    {"name": "Fib 0.618", "data": line_points(main["fib618"], dynamic_colors, fib_visible), "color": pocket_color, "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Fib 0.786", "data": line_points(main["fib786"], dynamic_colors, fib_visible), "color": pocket_color, "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Fib 0.5", "data": line_points(main["fib50"], [midpoint_color for _ in range(n)], midpoint_visible), "color": midpoint_color, "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Dynamic Golden Pocket", "title": False, "data": cloud_data, "color": pocket_color, "type": "cloud", "opacity": 0.22, "overlay": True},
]

bull_flip_values = [float(main["level100"][index]) if trend_change[index] and trend[index] == 1 and np.isfinite(main["level100"][index]) else None for index in range(n)]
bear_flip_values = [float(main["level100"][index]) if trend_change[index] and trend[index] == -1 and np.isfinite(main["level100"][index]) else None for index in range(n)]
bull_text = [f"{main['close'][index]:.2f}" if bull_flip_values[index] is not None else "" for index in range(n)]
bear_text = [f"{main['close'][index]:.2f}" if bear_flip_values[index] is not None else "" for index in range(n)]
signals = [
    {"type": "buy", "name": "Bullish Trend", "text": "Bull", "textData": bull_text, "color": bullish_color, "renderMode": "events", "data": bull_flip_values},
    {"type": "sell", "name": "Bearish Trend", "text": "Bear", "textData": bear_text, "color": bearish_color, "renderMode": "events", "data": bear_flip_values},
]

layers = []
if show_projections and n >= 5 and trend[-1] != 0:
    projection_specs = [
        ("1.0", main["level100"], trend_colors[-1], 3, False),
        ("0.5", main["fib50"], midpoint_color, 1, True),
        ("0.618", main["fib618"], pocket_color, 1, True),
        ("0.786", main["fib786"], pocket_color, 1, True),
    ]
    for label, values, color, width, dashed in projection_specs:
        if np.isfinite(values[-1]) and np.isfinite(values[-5]):
            layers.append({
                "type": "line",
                "startIndex": n - 5,
                "endIndex": n - 1,
                "startPrice": float(values[-5]),
                "endPrice": float(values[-1]),
                "extendBars": projection_length,
                "text": label,
                "textPosition": "end",
                "color": color,
                "lineWidth": width,
                "dashed": dashed,
            })

position_map = {
    "Top_Right": "top-right",
    "Bottom_Right": "bottom-right",
    "Top_Left": "top-left",
    "Bottom_Left": "bottom-left",
    "Middle_Right": "top-right",
}
font_size_map = {"Tiny": 9, "Small": 10, "Normal": 11, "Large": 12}
tables = []
if show_dashboard:
    rows = []
    for timeframe in dashboard_timeframes:
        raw_frame = runtime_context.raw_series(timeframe) if runtime_context is not None and hasattr(runtime_context, "raw_series") else None
        resolved = calculate(raw_frame if raw_frame is not None and len(raw_frame) else df)
        finite = np.flatnonzero(np.isfinite(resolved["level100"]))
        if len(finite):
            final_index = int(finite[-1])
            direction = int(resolved["trend"][final_index])
            inside = bool(resolved["pocket"][final_index])
            trend_text = "BULL" if direction == 1 else "BEAR" if direction == -1 else "WAIT"
            trend_color = bullish_color if direction == 1 else bearish_color if direction == -1 else midpoint_color
            rows.append([
                {"text": timeframe, "color": "#FFFFFF", "align": "center"},
                {"text": trend_text, "color": "#FFFFFF", "backgroundColor": rgba(trend_color, 0.70), "align": "center"},
                {"text": "INSIDE" if inside else "OUT", "color": "#FFFFFF", "backgroundColor": rgba(pocket_color if inside else midpoint_color, 0.70 if inside else 0.20), "align": "center"},
            ])
        else:
            rows.append([timeframe, "N/A", "N/A"])
    tables.append({
        "title": "MTF TREND & POCKET",
        "position": position_map.get(dashboard_position, "bottom-right"),
        "compact": dashboard_size in ("Tiny", "Small"),
        "fontSize": font_size_map[dashboard_size],
        "borderColor": midpoint_color,
        "accentColor": trend_colors[-1] if n else midpoint_color,
        "columns": ["TF", "Trend", "Pocket"],
        "rows": rows,
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": layers,
    "tables": tables,
}
