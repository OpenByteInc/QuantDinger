# ============================================================
# QuantDinger Hull Suite
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Hull Suite"
my_indicator_description = (
    "Provides Hull, exponential Hull, and triple Hull bands with optional "
    "locally aggregated higher-timeframe calculation and trend candle coloring."
)

# @param source str close Source values=close,open,high,low,hl2,hlc3,ohlc4
# @param mode str Hma Hull variation values=Hma,Thma,Ehma
# @param length int 55 Hull length range=1:500:1
# @param length_multiplier float 1.0 Length multiplier range=0.1:20.0:0.1
# @param use_higher_timeframe bool false Use higher timeframe
# @param higher_timeframe str 4H Higher timeframe values=15m,30m,1H,2H,4H,6H,8H,12H,1D,1W
# @param switch_color bool true Color by trend
# @param color_candles bool false Color host candles
# @param show_band bool true Show Hull band
# @param line_width int 1 Line thickness range=1:5:1
# @param band_transparency int 40 Band transparency range=0:100:5
# @param up_color color #00FF00 Uptrend color
# @param down_color color #FF0000 Downtrend color
# @param neutral_color color #FF9800 Neutral color

source_name = str(params.get("source", "close"))
mode = str(params.get("mode", "Hma"))
if mode not in ("Hma", "Thma", "Ehma"):
    mode = "Hma"
length = min(500, max(1, int(params.get("length", 55))))
length_multiplier = min(20.0, max(0.1, float(params.get("length_multiplier", 1.0))))
use_higher_timeframe = bool(params.get("use_higher_timeframe", False))
higher_timeframe = str(params.get("higher_timeframe", "4H"))
supported_timeframes = ["15m", "30m", "1H", "2H", "4H", "6H", "8H", "12H", "1D", "1W"]
if higher_timeframe not in supported_timeframes:
    higher_timeframe = "4H"
switch_color = bool(params.get("switch_color", True))
color_candles = bool(params.get("color_candles", False))
show_band = bool(params.get("show_band", True))
line_width = min(5, max(1, int(params.get("line_width", 1))))
band_transparency = min(100, max(0, int(params.get("band_transparency", 40))))
up_color = str(params.get("up_color", "#00FF00"))
down_color = str(params.get("down_color", "#FF0000"))
neutral_color = str(params.get("neutral_color", "#FF9800"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
if "time" not in df.columns:
    df["time"] = np.arange(len(df), dtype=float) * 3600.0
times = df["time"].to_numpy(dtype=float)
n = len(df)
if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0

source_map = {
    "close": close, "open": open_price, "high": high, "low": low,
    "hl2": (high + low) / 2.0,
    "hlc3": (high + low + close) / 3.0,
    "ohlc4": (open_price + high + low + close) / 4.0,
}
source = source_map.get(source_name, close)
effective_length = max(1, int(length * length_multiplier))

def _wma(values, window):
    window = max(1, int(window))
    result = np.full(len(values), np.nan, dtype=float)
    denominator = window * (window + 1) / 2.0
    weights = np.arange(1, window + 1, dtype=float)
    for index in range(window - 1, len(values)):
        segment = values[index - window + 1:index + 1]
        if np.all(np.isfinite(segment)):
            result[index] = float(np.dot(segment, weights) / denominator)
    return result

def _ema(values, window):
    window = max(1, int(window))
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (window + 1.0)
    state = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        state = float(value) if not np.isfinite(state) else float(alpha * value + (1.0 - alpha) * state)
        result[index] = state
    return result

def _rounded_sqrt(value):
    return max(1, int(np.floor(np.sqrt(max(1.0, float(value))) + 0.5)))

def _hull(values, selected_mode, selected_length):
    selected_length = max(1, int(selected_length))
    if selected_mode == "Ehma":
        half = _ema(values, max(1, int(selected_length / 2)))
        full = _ema(values, selected_length)
        raw = 2.0 * half - full
        return _ema(raw, _rounded_sqrt(selected_length))
    if selected_mode == "Thma":
        triple_length = max(1, int(selected_length / 2))
        third = _wma(values, max(1, int(triple_length / 3)))
        half = _wma(values, max(1, int(triple_length / 2)))
        full = _wma(values, triple_length)
        raw = 3.0 * third - half - full
        return _wma(raw, triple_length)
    half = _wma(values, max(1, int(selected_length / 2)))
    full = _wma(values, selected_length)
    raw = 2.0 * half - full
    return _wma(raw, _rounded_sqrt(selected_length))

chart_intervals = np.diff(times)
chart_intervals = chart_intervals[np.isfinite(chart_intervals) & (chart_intervals > 0.0)]
chart_interval = float(np.median(chart_intervals)) if len(chart_intervals) else 3600000.0
timeframe_ms = {
    "15m": 900000.0, "30m": 1800000.0, "1H": 3600000.0, "2H": 7200000.0,
    "4H": 14400000.0, "6H": 21600000.0, "8H": 28800000.0, "12H": 43200000.0,
    "1D": 86400000.0, "1W": 604800000.0,
}
requested_interval = timeframe_ms[higher_timeframe]
effective_interval = max(requested_interval, chart_interval)
timeframe_clamped = bool(requested_interval < chart_interval)

group_start = np.zeros(n, dtype=float)
if n:
    if effective_interval >= 604800000.0:
        timestamps = pd.to_datetime(times, unit="ms", utc=True)
        for index, timestamp in enumerate(timestamps):
            monday = timestamp.normalize() - pd.Timedelta(days=int(timestamp.weekday()))
            group_start[index] = float(monday.value / 1000000)
    else:
        group_start = np.floor(times / effective_interval) * effective_interval

compact_values = []
compact_end_indexes = []
compact_records = []
if n:
    start = 0
    for index in range(1, n + 1):
        group_ended = index == n or group_start[index] != group_start[index - 1]
        if not group_ended:
            continue
        group_open = float(open_price[start])
        group_high = float(np.max(high[start:index]))
        group_low = float(np.min(low[start:index]))
        group_close = float(close[index - 1])
        if source_name == "open":
            value = group_open
        elif source_name == "high":
            value = group_high
        elif source_name == "low":
            value = group_low
        elif source_name == "hl2":
            value = (group_high + group_low) / 2.0
        elif source_name == "hlc3":
            value = (group_high + group_low + group_close) / 3.0
        elif source_name == "ohlc4":
            value = (group_open + group_high + group_low + group_close) / 4.0
        else:
            value = group_close
        compact_values.append(float(value))
        compact_end_indexes.append(index - 1)
        compact_records.append({
            "startIndex": int(start), "endIndex": int(index - 1),
            "open": group_open, "high": group_high, "low": group_low,
            "close": group_close, "source": float(value),
        })
        start = index

if use_higher_timeframe:
    compact_hull = _hull(np.asarray(compact_values, dtype=float), mode, effective_length)
    hull = np.full(n, np.nan, dtype=float)
    current_value = np.nan
    result_index = 0
    for index in range(n):
        if result_index < len(compact_end_indexes) and index == compact_end_indexes[result_index]:
            current_value = compact_hull[result_index]
            result_index += 1
        hull[index] = current_value
else:
    compact_hull = np.asarray([], dtype=float)
    hull = _hull(source, mode, effective_length)

shifted_hull = np.full(n, np.nan, dtype=float)
if n > 2:
    shifted_hull[2:] = hull[:-2]

trend_up = np.zeros(n, dtype=bool)
trend_down = np.zeros(n, dtype=bool)
cross_up = np.zeros(n, dtype=bool)
cross_down = np.zeros(n, dtype=bool)
for index in range(n):
    if np.isfinite(hull[index]) and np.isfinite(shifted_hull[index]):
        trend_up[index] = hull[index] > shifted_hull[index]
        trend_down[index] = not trend_up[index]
    if index > 0 and all(np.isfinite(value) for value in (hull[index], shifted_hull[index], hull[index - 1], shifted_hull[index - 1])):
        cross_up[index] = hull[index] > shifted_hull[index] and hull[index - 1] <= shifted_hull[index - 1]
        cross_down[index] = shifted_hull[index] > hull[index] and shifted_hull[index - 1] <= hull[index - 1]

def _color_with_alpha(color, alpha):
    value = str(color).strip()
    if value.startswith("#") and len(value) in (4, 7):
        if len(value) == 4:
            red = int(value[1] * 2, 16)
            green = int(value[2] * 2, 16)
            blue = int(value[3] * 2, 16)
        else:
            red = int(value[1:3], 16)
            green = int(value[3:5], 16)
            blue = int(value[5:7], 16)
        return f"rgba({red},{green},{blue},{max(0.0, min(1.0, float(alpha))):.3f})"
    return value

main_line = []
shift_line = []
band = []
bar_colors = []
for index in range(n):
    color = up_color if switch_color and trend_up[index] else down_color if switch_color else neutral_color
    line_color = _color_with_alpha(color, 0.50)
    main_line.append({"value": float(hull[index]), "color": line_color} if np.isfinite(hull[index]) else None)
    shift_line.append({"value": float(shifted_hull[index]), "color": line_color} if show_band and np.isfinite(shifted_hull[index]) else None)
    if show_band and np.isfinite(hull[index]) and np.isfinite(shifted_hull[index]):
        band.append({
            "upper": float(max(hull[index], shifted_hull[index])),
            "lower": float(min(hull[index], shifted_hull[index])),
            "color": _color_with_alpha(color, 1.0 - band_transparency / 100.0),
        })
    else:
        band.append(None)
    bar_colors.append(color if color_candles and switch_color and np.isfinite(hull[index]) else None)

def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "MHULL", "data": main_line, "color": neutral_color, "type": "line", "lineWidth": line_width, "overlay": True},
        {"name": "SHULL", "data": shift_line, "color": neutral_color, "type": "line", "lineWidth": line_width, "overlay": True},
        {"name": "Hull Band", "title": False, "data": band, "type": "cloud", "opacity": max(0.02, min(0.30, (1.0 - band_transparency / 100.0) * 0.30)), "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite_or_none(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "name": "Hull Trending Up", "text": "UP", "data": [float(low[index]) if cross_up[index] else None for index in range(n)], "color": up_color, "renderMode": "hidden"},
        {"type": "sell", "name": "Hull Trending Down", "text": "DOWN", "data": [float(high[index]) if cross_down[index] else None for index in range(n)], "color": down_color, "renderMode": "hidden"},
    ],
    "layers": [],
    "tables": [],
    "bar_colors": bar_colors,
    "background_colors": [None] * n,
    "calculatedVars": {
        "hull": _finite_or_none(hull), "shiftedHull": _finite_or_none(shifted_hull),
        "trendUp": trend_up.tolist(), "trendDown": trend_down.tolist(),
        "crossUp": cross_up.tolist(), "crossDown": cross_down.tolist(),
        "effectiveLength": int(effective_length), "requestedInterval": float(requested_interval),
        "effectiveInterval": float(effective_interval), "timeframeClamped": bool(timeframe_clamped),
        "groupStart": _finite_or_none(group_start), "compactValues": [float(value) for value in compact_values],
        "compactHull": _finite_or_none(compact_hull), "compactEndIndexes": [int(value) for value in compact_end_indexes],
        "compactRecords": compact_records, "chartInterval": float(chart_interval),
    },
}
