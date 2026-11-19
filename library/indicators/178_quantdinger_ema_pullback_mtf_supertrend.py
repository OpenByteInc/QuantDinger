# ============================================================
# QuantDinger EMA Pullback with MTF SuperTrend
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger EMA Pullback with MTF SuperTrend"
my_indicator_description = (
    "Arms a setup on a fast/slow EMA cross, waits for price to establish beyond and retest the fast EMA, "
    "then confirms the first pullback with an independently calculated multi-timeframe SuperTrend."
)

indicator_timeframes = ["15m", "1H", "4H", "1D"]

# @param ema_timeframe str Current EMA timeframe values=Current,15m,1H,4H,1D
# @param fast_length int 20 Fast EMA length range=1:500:1
# @param slow_length int 50 Slow EMA length range=1:500:1
# @param setup_expiry int 30 Setup expiry in chart candles range=1:500:1
# @param show_setup_labels bool true Show armed labels
# @param show_expiry bool true Show expiry labels
# @param show_emas bool true Show fast and slow EMAs
# @param supertrend_timeframe str 15m SuperTrend timeframe values=15m,1H,4H,1D
# @param supertrend_factor float 3.0 SuperTrend factor range=0.1:20.0:0.1
# @param supertrend_atr_length int 10 SuperTrend ATR length range=1:500:1
# @param show_supertrend bool true Show SuperTrend
# @param show_shading bool true Show SuperTrend shading
# @param shading_transparency int 90 Shading transparency range=0:100:1
# @param fast_color str #FF9800 Fast EMA color
# @param slow_color str #2196F3 Slow EMA color
# @param bullish_color str #00C853 Bullish SuperTrend color
# @param bearish_color str #F44336 Bearish SuperTrend color

ema_timeframe = str(params.get("ema_timeframe", "Current"))
if ema_timeframe not in ("Current", "15m", "1H", "4H", "1D"):
    ema_timeframe = "Current"
fast_length = min(500, max(1, int(params.get("fast_length", 20))))
slow_length = min(500, max(1, int(params.get("slow_length", 50))))
setup_expiry = min(500, max(1, int(params.get("setup_expiry", 30))))
show_setup_labels = bool(params.get("show_setup_labels", True))
show_expiry = bool(params.get("show_expiry", True))
show_emas = bool(params.get("show_emas", True))
supertrend_timeframe = str(params.get("supertrend_timeframe", "15m"))
if supertrend_timeframe not in ("15m", "1H", "4H", "1D"):
    supertrend_timeframe = "15m"
supertrend_factor = min(20.0, max(0.1, float(params.get("supertrend_factor", 3.0))))
supertrend_atr_length = min(500, max(1, int(params.get("supertrend_atr_length", 10))))
show_supertrend = bool(params.get("show_supertrend", True))
show_shading = bool(params.get("show_shading", True))
shading_transparency = min(100, max(0, int(params.get("shading_transparency", 90))))
fast_color = str(params.get("fast_color", "#FF9800"))
slow_color = str(params.get("slow_color", "#2196F3"))
bullish_color = str(params.get("bullish_color", "#00C853"))
bearish_color = str(params.get("bearish_color", "#F44336"))

timeframe_seconds = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1H": 3600, "2H": 7200, "4H": 14400, "6H": 21600,
    "8H": 28800, "12H": 43200, "1D": 86400, "1W": 604800,
}

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
raw_time = df["time"].astype(float).to_numpy(dtype=float) if "time" in df.columns else np.arange(len(df), dtype=float)
base_time = raw_time / 1000.0 if len(raw_time) and np.nanmax(np.abs(raw_time)) > 10_000_000_000 else raw_time
n = len(df)
chart_timeframe = str(ctx.timeframe or "1H")
if chart_timeframe not in timeframe_seconds:
    chart_timeframe = "1H"
chart_seconds = timeframe_seconds[chart_timeframe]

def normalize_frame(frame):
    if frame is None or len(frame) == 0:
        frame = df.copy()
    result = frame.copy().reset_index(drop=True)
    for column in ("time", "open", "high", "low", "close", "volume"):
        if column not in result.columns:
            result[column] = 0.0
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
    times = result["time"].to_numpy(dtype=float)
    if len(times) and np.nanmax(np.abs(times)) > 10_000_000_000:
        result["time"] = times / 1000.0
    return result

def source_frame(timeframe):
    if timeframe == "Current" or timeframe == chart_timeframe:
        return normalize_frame(df), chart_timeframe
    requested = ctx.raw_series(timeframe)
    if requested is None or len(requested) == 0:
        return normalize_frame(df), chart_timeframe
    return normalize_frame(requested), timeframe

def ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    result[length - 1] = float(np.mean(values[:length]))
    alpha = 2.0 / (length + 1.0)
    for index in range(length, len(values)):
        result[index] = alpha * values[index] + (1.0 - alpha) * result[index - 1]
    return result

def rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    result[length - 1] = float(np.mean(values[:length]))
    for index in range(length, len(values)):
        result[index] = (result[index - 1] * (length - 1) + values[index]) / length
    return result

def align_completed(source_time, source_values, source_seconds):
    result = np.full(n, np.nan, dtype=float)
    if len(source_time) == n and source_seconds == chart_seconds and np.allclose(source_time, base_time):
        result[:] = source_values
        return result
    source_close_time = source_time + source_seconds
    base_close_time = base_time + chart_seconds
    for index in range(n):
        source_index = int(np.searchsorted(source_close_time, base_close_time[index], side="right") - 1)
        if 0 <= source_index < len(source_values) and np.isfinite(source_values[source_index]):
            result[index] = float(source_values[source_index])
    return result

def supertrend(frame, factor, atr_length):
    source_high = frame["high"].to_numpy(dtype=float)
    source_low = frame["low"].to_numpy(dtype=float)
    source_close = frame["close"].to_numpy(dtype=float)
    count = len(frame)
    true_range = np.zeros(count, dtype=float)
    for index in range(count):
        if index == 0:
            true_range[index] = source_high[index] - source_low[index]
        else:
            true_range[index] = max(
                source_high[index] - source_low[index],
                abs(source_high[index] - source_close[index - 1]),
                abs(source_low[index] - source_close[index - 1]),
            )
    atr = rma(true_range, atr_length)
    midpoint = (source_high + source_low) / 2.0
    raw_upper = midpoint + factor * atr
    raw_lower = midpoint - factor * atr
    upper = np.full(count, np.nan, dtype=float)
    lower_band = np.full(count, np.nan, dtype=float)
    trend = np.full(count, np.nan, dtype=float)
    direction = np.ones(count, dtype=float)
    for index in range(count):
        if not (np.isfinite(raw_upper[index]) and np.isfinite(raw_lower[index])):
            continue
        previous_lower = lower_band[index - 1] if index > 0 and np.isfinite(lower_band[index - 1]) else raw_lower[index]
        previous_upper = upper[index - 1] if index > 0 and np.isfinite(upper[index - 1]) else raw_upper[index]
        previous_close = source_close[index - 1] if index > 0 else source_close[index]
        lower_band[index] = raw_lower[index] if raw_lower[index] > previous_lower or previous_close < previous_lower else previous_lower
        upper[index] = raw_upper[index] if raw_upper[index] < previous_upper or previous_close > previous_upper else previous_upper
        if index == 0 or not np.isfinite(trend[index - 1]):
            direction[index] = 1.0
        elif abs(trend[index - 1] - previous_upper) <= 1e-9:
            direction[index] = -1.0 if source_close[index] > upper[index] else 1.0
        else:
            direction[index] = 1.0 if source_close[index] < lower_band[index] else -1.0
        trend[index] = lower_band[index] if direction[index] < 0 else upper[index]
    return trend, direction

ema_frame, resolved_ema_timeframe = source_frame(ema_timeframe)
ema_source_close = ema_frame["close"].to_numpy(dtype=float)
ema_source_time = ema_frame["time"].to_numpy(dtype=float)
ema_source_seconds = timeframe_seconds[resolved_ema_timeframe]
fast_ema_source = ema(ema_source_close, fast_length)
slow_ema_source = ema(ema_source_close, slow_length)
fast_ema = align_completed(ema_source_time, fast_ema_source, ema_source_seconds)
slow_ema = align_completed(ema_source_time, slow_ema_source, ema_source_seconds)

supertrend_frame, resolved_supertrend_timeframe = source_frame(supertrend_timeframe)
supertrend_source_time = supertrend_frame["time"].to_numpy(dtype=float)
supertrend_source_seconds = timeframe_seconds[resolved_supertrend_timeframe]
supertrend_source, direction_source = supertrend(supertrend_frame, supertrend_factor, supertrend_atr_length)
supertrend_values = align_completed(supertrend_source_time, supertrend_source, supertrend_source_seconds)
direction = align_completed(supertrend_source_time, direction_source, supertrend_source_seconds)
supertrend_bullish = direction < 0
supertrend_bearish = direction > 0

bull_cross = np.zeros(n, dtype=bool)
bear_cross = np.zeros(n, dtype=bool)
for index in range(1, n):
    if not (
        np.isfinite(fast_ema[index]) and np.isfinite(slow_ema[index])
        and np.isfinite(fast_ema[index - 1]) and np.isfinite(slow_ema[index - 1])
    ):
        continue
    bull_cross[index] = fast_ema[index] > slow_ema[index] and fast_ema[index - 1] <= slow_ema[index - 1]
    bear_cross[index] = fast_ema[index] < slow_ema[index] and fast_ema[index - 1] >= slow_ema[index - 1]

long_signal = np.zeros(n, dtype=bool)
short_signal = np.zeros(n, dtype=bool)
bull_expired = np.zeros(n, dtype=bool)
bear_expired = np.zeros(n, dtype=bool)
setup_state = 0
setup_bar = -1
established_above = False
established_below = False
for index in range(n):
    if bull_cross[index]:
        setup_state = 1
        setup_bar = index
        established_above = False
        established_below = False
    if bear_cross[index]:
        setup_state = -1
        setup_bar = index
        established_above = False
        established_below = False

    if setup_state == 1 and index > setup_bar and np.isfinite(fast_ema[index]) and close[index] > fast_ema[index]:
        established_above = True
    if setup_state == -1 and index > setup_bar and np.isfinite(fast_ema[index]) and close[index] < fast_ema[index]:
        established_below = True

    setup_age = index - setup_bar if setup_state != 0 and setup_bar >= 0 else -1
    if setup_state == 1 and setup_age >= setup_expiry:
        bull_expired[index] = True
        setup_state = 0
        setup_bar = -1
        established_above = False
    if setup_state == -1 and setup_age >= setup_expiry:
        bear_expired[index] = True
        setup_state = 0
        setup_bar = -1
        established_below = False

    long_tap = setup_state == 1 and established_above and index > setup_bar and np.isfinite(fast_ema[index]) and low[index] <= fast_ema[index]
    short_tap = setup_state == -1 and established_below and index > setup_bar and np.isfinite(fast_ema[index]) and high[index] >= fast_ema[index]
    long_signal[index] = long_tap and supertrend_bullish[index]
    short_signal[index] = short_tap and supertrend_bearish[index]
    if long_signal[index]:
        setup_state = 0
        setup_bar = -1
        established_above = False
    if short_signal[index]:
        setup_state = 0
        setup_bar = -1
        established_below = False

plots = []
plots.append({"name": "Fast EMA", "title": show_emas, "data": [float(value) if np.isfinite(value) else None for value in fast_ema], "color": fast_color if show_emas else "rgba(255,152,0,0.001)", "type": "line", "lineWidth": 2, "overlay": True})
plots.append({"name": "Slow EMA", "title": show_emas, "data": [float(value) if np.isfinite(value) else None for value in slow_ema], "color": slow_color if show_emas else "rgba(33,150,243,0.001)", "type": "line", "lineWidth": 2, "overlay": True})
if show_supertrend:
    plots.append({
        "name": "Bullish SuperTrend",
        "data": [float(supertrend_values[index]) if supertrend_bullish[index] and np.isfinite(supertrend_values[index]) else None for index in range(n)],
        "color": bullish_color,
        "type": "line",
        "lineWidth": 2,
        "overlay": True,
    })
    plots.append({
        "name": "Bearish SuperTrend",
        "data": [float(supertrend_values[index]) if supertrend_bearish[index] and np.isfinite(supertrend_values[index]) else None for index in range(n)],
        "color": bearish_color,
        "type": "line",
        "lineWidth": 2,
        "overlay": True,
    })
if show_shading:
    opacity = max(0.02, (100 - shading_transparency) / 100.0)
    plots.append({
        "name": "Bullish SuperTrend Shade",
        "data": [
            {"upper": float(max(low[index], supertrend_values[index])), "lower": float(min(low[index], supertrend_values[index]))}
            if supertrend_bullish[index] and np.isfinite(supertrend_values[index]) else None
            for index in range(n)
        ],
        "color": f"rgba(0,200,83,{opacity:.3f})",
        "type": "cloud",
        "overlay": True,
    })
    plots.append({
        "name": "Bearish SuperTrend Shade",
        "data": [
            {"upper": float(max(high[index], supertrend_values[index])), "lower": float(min(high[index], supertrend_values[index]))}
            if supertrend_bearish[index] and np.isfinite(supertrend_values[index]) else None
            for index in range(n)
        ],
        "color": f"rgba(244,67,54,{opacity:.3f})",
        "type": "cloud",
        "overlay": True,
    })

def event_points(mask, values, text, color):
    return [
        {"value": float(values[index]), "text": text, "color": color} if mask[index] else None
        for index in range(n)
    ]

signals = [
    {"type": "buy", "name": "Bullish EMA Cross", "text": "20/50 ARMED", "color": bullish_color, "data": event_points(bull_cross & show_setup_labels, low, "20/50 ARMED", bullish_color), "renderMode": "events"},
    {"type": "sell", "name": "Bearish EMA Cross", "text": "20/50 ARMED", "color": bearish_color, "data": event_points(bear_cross & show_setup_labels, high, "20/50 ARMED", bearish_color), "renderMode": "events"},
    {"type": "buy", "name": "EMA Pullback Buy", "text": "BUY", "color": "#76FF03", "data": event_points(long_signal, low, "BUY", "#76FF03"), "renderMode": "events"},
    {"type": "sell", "name": "EMA Pullback Sell", "text": "SELL", "color": bearish_color, "data": event_points(short_signal, high, "SELL", bearish_color), "renderMode": "events"},
    {"type": "sell", "name": "Bullish Setup Expired", "text": "EXPIRED", "color": "#9CA3AF", "data": event_points(bull_expired & show_expiry, high, "EXPIRED", "#9CA3AF"), "renderMode": "events"},
    {"type": "buy", "name": "Bearish Setup Expired", "text": "EXPIRED", "color": "#9CA3AF", "data": event_points(bear_expired & show_expiry, low, "EXPIRED", "#9CA3AF"), "renderMode": "events"},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
}
