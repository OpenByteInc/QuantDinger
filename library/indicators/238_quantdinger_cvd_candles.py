# ============================================================
# QuantDinger CVD Candles
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger CVD Candles"
my_indicator_description = (
    "Builds cumulative volume-delta candles from classified lower-timeframe volume, "
    "with configurable resets, divergence colors, trend resets, and audit statistics."
)

indicator_lower_timeframes = ["1m", "3m", "5m", "15m", "30m", "1H"]
indicator_lower_timeframe_param = "lower_timeframe"

# @param lower_timeframe str 1m Intrabar timeframe values=1m,3m,5m,15m,30m,1H
# @param reset_mode str Stepped_HTF CVD reset values=None,Stepped_HTF,Fixed_HTF,Fixed_Time,Session,First_Bar,Trend_Change
# @param fixed_timeframe str 1D Fixed reset timeframe values=1H,4H,1D,1W,1M
# @param reset_hour int 9 Fixed reset hour range=0:23:1
# @param reset_minute int 30 Fixed reset minute range=0:59:1
# @param trend_method str Supertrend Trend reset method values=Supertrend,Aroon,Parabolic_SAR
# @param trend_period int 14 Trend period range=2:100:1
# @param trend_multiplier float 3.0 Supertrend ATR multiplier range=0.25:10:0.25
# @param delta_mode str Volume_Delta Delta calculation values=Volume_Delta,Delta_Percent
# @param show_candles bool true Show CVD candles
# @param show_total_volume bool false Show total-volume comparison candles
# @param show_line bool false Show CVD line
# @param show_average bool false Show CVD average
# @param average_length int 20 Average length when resets are disabled range=2:200:1
# @param color_divergence bool true Color candle bodies on price divergence
# @param show_divergence_background bool false Color background on divergences
# @param show_reset_background bool true Color background on resets
# @param show_zero_line bool true Show zero line
# @param show_info_table bool true Show data and reset information
# @param up_color str #00E676 Positive CVD color
# @param down_color str #FF4081 Negative CVD color
# @param up_divergence_color str #00897B Positive divergence color
# @param down_divergence_color str #800000 Negative divergence color

lower_timeframe = str(params.get("lower_timeframe", "1m"))
if lower_timeframe not in indicator_lower_timeframes:
    lower_timeframe = "1m"
reset_mode = str(params.get("reset_mode", "Stepped_HTF"))
if reset_mode not in ("None", "Stepped_HTF", "Fixed_HTF", "Fixed_Time", "Session", "First_Bar", "Trend_Change"):
    reset_mode = "Stepped_HTF"
fixed_timeframe = str(params.get("fixed_timeframe", "1D"))
reset_hour = min(23, max(0, int(params.get("reset_hour", 9))))
reset_minute = min(59, max(0, int(params.get("reset_minute", 30))))
trend_method = str(params.get("trend_method", "Supertrend"))
trend_period = min(100, max(2, int(params.get("trend_period", 14))))
trend_multiplier = min(10.0, max(0.25, float(params.get("trend_multiplier", 3.0))))
delta_mode = str(params.get("delta_mode", "Volume_Delta"))
show_candles = bool(params.get("show_candles", True))
show_total_volume = bool(params.get("show_total_volume", False))
show_line = bool(params.get("show_line", False))
show_average = bool(params.get("show_average", False))
average_length = min(200, max(2, int(params.get("average_length", 20))))
color_divergence = bool(params.get("color_divergence", True))
show_divergence_background = bool(params.get("show_divergence_background", False))
show_reset_background = bool(params.get("show_reset_background", True))
show_zero_line = bool(params.get("show_zero_line", True))
show_info_table = bool(params.get("show_info_table", True))
up_color = str(params.get("up_color", "#00E676"))
down_color = str(params.get("down_color", "#FF4081"))
up_divergence_color = str(params.get("up_divergence_color", "#00897B"))
down_divergence_color = str(params.get("down_divergence_color", "#800000"))

df = df.copy()
open_values = df["open"].astype(float).to_numpy(dtype=float)
high_values = df["high"].astype(float).to_numpy(dtype=float)
low_values = df["low"].astype(float).to_numpy(dtype=float)
close_values = df["close"].astype(float).to_numpy(dtype=float)
volume_values = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)
if "time" in df.columns:
    time_values = df["time"].astype("int64").to_numpy()
else:
    time_values = np.arange(n, dtype=np.int64) * 3_600_000
timestamps = pd.to_datetime(time_values, unit="ms", utc=True)


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def rgba(color, alpha):
    value = str(color).lstrip("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    try:
        red, green, blue = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except (ValueError, IndexError):
        red, green, blue = 128, 128, 128
    return f"rgba({red},{green},{blue},{float(alpha):.3f})"


def sma(values, length):
    return pd.Series(values, dtype=float).rolling(length, min_periods=length).mean().to_numpy(dtype=float)


def true_range():
    previous_close = np.r_[np.nan, close_values[:-1]]
    return np.maximum(
        high_values - low_values,
        np.maximum(np.abs(high_values - previous_close), np.abs(low_values - previous_close)),
    )


def rma(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) < length:
        return result
    state = float(np.nanmean(source[:length]))
    result[length - 1] = state
    for index in range(length, len(source)):
        state = (state * (length - 1) + float(source[index])) / length
        result[index] = state
    return result


def supertrend_direction(length, multiplier):
    atr = rma(true_range(), length)
    midpoint = (high_values + low_values) * 0.5
    upper = midpoint + multiplier * atr
    lower = midpoint - multiplier * atr
    final_upper = upper.copy()
    final_lower = lower.copy()
    direction = np.ones(n, dtype=int)
    for index in range(1, n):
        if np.isfinite(final_upper[index - 1]) and np.isfinite(upper[index]):
            if upper[index] >= final_upper[index - 1] and close_values[index - 1] <= final_upper[index - 1]:
                final_upper[index] = final_upper[index - 1]
        if np.isfinite(final_lower[index - 1]) and np.isfinite(lower[index]):
            if lower[index] <= final_lower[index - 1] and close_values[index - 1] >= final_lower[index - 1]:
                final_lower[index] = final_lower[index - 1]
        previous = direction[index - 1]
        if previous < 0 and close_values[index] > final_upper[index]:
            direction[index] = 1
        elif previous > 0 and close_values[index] < final_lower[index]:
            direction[index] = -1
        else:
            direction[index] = previous
    return direction


def aroon_direction(length):
    up = np.full(n, np.nan, dtype=float)
    down = np.full(n, np.nan, dtype=float)
    for index in range(length, n):
        start = index - length
        high_window = high_values[start:index + 1]
        low_window = low_values[start:index + 1]
        high_since = length - int(np.argmax(high_window))
        low_since = length - int(np.argmin(low_window))
        up[index] = 100.0 * (length - high_since) / length
        down[index] = 100.0 * (length - low_since) / length
    direction = np.ones(n, dtype=int)
    for index in range(1, n):
        direction[index] = direction[index - 1]
        if np.isfinite(up[index]) and np.isfinite(down[index]):
            if up[index] > down[index] and up[index - 1] <= down[index - 1]:
                direction[index] = 1
            elif up[index] < down[index] and up[index - 1] >= down[index - 1]:
                direction[index] = -1
    return direction


def psar_direction():
    direction = np.ones(n, dtype=int)
    if n < 2:
        return direction
    rising = close_values[1] >= close_values[0]
    sar = low_values[0] if rising else high_values[0]
    extreme = high_values[0] if rising else low_values[0]
    acceleration = 0.02
    for index in range(1, n):
        sar = sar + acceleration * (extreme - sar)
        if rising:
            sar = min(sar, low_values[index - 1], low_values[index - 2] if index > 1 else low_values[index - 1])
            if low_values[index] < sar:
                rising = False
                sar = extreme
                extreme = low_values[index]
                acceleration = 0.02
            elif high_values[index] > extreme:
                extreme = high_values[index]
                acceleration = min(0.2, acceleration + 0.02)
        else:
            sar = max(sar, high_values[index - 1], high_values[index - 2] if index > 1 else high_values[index - 1])
            if high_values[index] > sar:
                rising = True
                sar = extreme
                extreme = high_values[index]
                acceleration = 0.02
            elif low_values[index] < extreme:
                extreme = low_values[index]
                acceleration = min(0.2, acceleration + 0.02)
        direction[index] = 1 if rising else -1
    return direction


timeframe_seconds = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1H": 3600, "2H": 7200, "4H": 14400, "6H": 21600,
    "8H": 28800, "12H": 43200, "1D": 86400, "1W": 604800,
}
runtime_context = globals().get("ctx")
chart_timeframe = str(getattr(runtime_context, "timeframe", "") or "")
lower_request = globals().get("request_lower_tf")
can_request_lower = (
    chart_timeframe in timeframe_seconds
    and lower_timeframe in timeframe_seconds
    and timeframe_seconds[lower_timeframe] < timeframe_seconds[chart_timeframe]
    and callable(lower_request)
)
if can_request_lower:
    intrabars = lower_request(lower_timeframe, fields=["open", "close", "volume"])
    lower_open = intrabars.get("open", [[] for _ in range(n)])
    lower_close = intrabars.get("close", [[] for _ in range(n)])
    lower_volume = intrabars.get("volume", [[] for _ in range(n)])
else:
    lower_open = [[] for _ in range(n)]
    lower_close = [[] for _ in range(n)]
    lower_volume = [[] for _ in range(n)]

up_volume = np.zeros(n, dtype=float)
down_volume = np.zeros(n, dtype=float)
max_up_volume = np.zeros(n, dtype=float)
max_down_volume = np.zeros(n, dtype=float)
intrabar_counts = np.zeros(n, dtype=int)
for index in range(n):
    opens = lower_open[index] if index < len(lower_open) else []
    closes = lower_close[index] if index < len(lower_close) else []
    volumes = lower_volume[index] if index < len(lower_volume) else []
    count = min(len(opens), len(closes), len(volumes))
    intrabar_counts[index] = count
    previous_close = None
    last_direction = 1
    for cursor in range(count):
        current_open = float(opens[cursor])
        current_close = float(closes[cursor])
        current_volume = max(0.0, float(volumes[cursor]))
        if current_close > current_open:
            last_direction = 1
        elif current_close < current_open:
            last_direction = -1
        elif previous_close is not None and current_close > previous_close:
            last_direction = 1
        elif previous_close is not None and current_close < previous_close:
            last_direction = -1
        if last_direction > 0:
            up_volume[index] += current_volume
            max_up_volume[index] = max(max_up_volume[index], current_volume)
        else:
            down_volume[index] -= current_volume
            max_down_volume[index] = min(max_down_volume[index], -current_volume)
        previous_close = current_close

total_volume = up_volume - down_volume
delta = up_volume + down_volume
delta_percent = np.divide(delta, total_volume, out=np.zeros(n, dtype=float), where=total_volume > 1e-12)
bar_delta = delta if delta_mode == "Volume_Delta" else delta_percent


def period_keys(label):
    if label == "1H":
        return timestamps.floor("h")
    if label == "4H":
        return timestamps.floor("4h")
    if label == "1W":
        return timestamps.to_period("W").astype(str)
    if label == "1M":
        return timestamps.to_period("M").astype(str)
    if label == "12M":
        return timestamps.to_period("Y").astype(str)
    return timestamps.floor("D")


reset = np.zeros(n, dtype=bool)
trend_direction = np.zeros(n, dtype=int)
reset_description = "No resets"
if reset_mode in ("Stepped_HTF", "Fixed_HTF"):
    label = fixed_timeframe
    if reset_mode == "Stepped_HTF":
        seconds = timeframe_seconds.get(chart_timeframe, 3600)
        label = "1H" if seconds <= 60 else "1D" if seconds < 10800 else "1W" if seconds <= 43200 else "1M" if seconds < 604800 else "12M"
    keys = period_keys(label)
    reset[0] = True
    if n > 1:
        reset[1:] = np.asarray(keys[1:] != keys[:-1], dtype=bool)
    reset_description = f"Resets every {label}"
elif reset_mode == "Fixed_Time":
    minute_of_day = timestamps.hour.to_numpy() * 60 + timestamps.minute.to_numpy()
    target = reset_hour * 60 + reset_minute
    bar_minutes = max(1, int(timeframe_seconds.get(chart_timeframe, 3600) // 60))
    for index in range(n):
        reset[index] = minute_of_day[index] <= target < minute_of_day[index] + bar_minutes
    reset_description = f"Resets at {reset_hour:02d}:{reset_minute:02d}"
elif reset_mode == "Session":
    dates = timestamps.floor("D")
    reset[0] = True
    if n > 1:
        reset[1:] = np.asarray(dates[1:] != dates[:-1], dtype=bool)
    reset_description = "Resets at the beginning of the session"
elif reset_mode == "First_Bar":
    if n:
        reset[0] = True
    reset_description = "Resets at the first visible chart bar"
elif reset_mode == "Trend_Change":
    if trend_method == "Aroon":
        trend_direction = aroon_direction(trend_period)
    elif trend_method == "Parabolic_SAR":
        trend_direction = psar_direction()
    else:
        trend_direction = supertrend_direction(trend_period, trend_multiplier)
    if n > 1:
        reset[1:] = trend_direction[1:] != trend_direction[:-1]
    reset_description = f"Resets on {trend_method.replace('_', ' ')} changes"

cvd_open = np.zeros(n, dtype=float)
cvd_close = np.zeros(n, dtype=float)
cvd_high = np.zeros(n, dtype=float)
cvd_low = np.zeros(n, dtype=float)
cvd_average = np.full(n, np.nan, dtype=float)
state = 0.0
segment_sum = 0.0
segment_count = 0
for index in range(n):
    if reset[index]:
        state = 0.0
        segment_sum = 0.0
        segment_count = 0
    cvd_open[index] = state
    cvd_close[index] = state + bar_delta[index]
    if delta_mode == "Volume_Delta":
        cvd_high[index] = state + max_up_volume[index]
        cvd_low[index] = state + max_down_volume[index]
    else:
        cvd_high[index] = max(cvd_open[index], cvd_close[index])
        cvd_low[index] = min(cvd_open[index], cvd_close[index])
    state = cvd_close[index]
    segment_sum += state
    segment_count += 1
    cvd_average[index] = segment_sum / segment_count
if reset_mode == "None":
    cvd_average = sma(cvd_close, average_length)

divergence = (delta != 0.0) & (np.sign(delta) != np.sign(close_values - open_values))
candle_colors = []
candles = []
total_candles = []
for index in range(n):
    positive = delta[index] > 0.0
    color = up_divergence_color if positive and color_divergence and divergence[index] else down_divergence_color if (not positive) and color_divergence and divergence[index] else up_color if positive else down_color
    candle_colors.append(color)
    candles.append({
        "open": float(cvd_open[index]), "high": float(cvd_high[index]),
        "low": float(cvd_low[index]), "close": float(cvd_close[index]),
        "color": color, "fillColor": color, "borderColor": color, "wickColor": color,
    })
    total_level = cvd_open[index] + total_volume[index] * (1.0 if bar_delta[index] >= 0.0 else -1.0)
    total_color = up_divergence_color if positive else down_divergence_color
    total_candles.append({
        "open": float(cvd_open[index]), "high": float(max(cvd_open[index], total_level)),
        "low": float(min(cvd_open[index], total_level)), "close": float(total_level),
        "color": rgba(total_color, 0.2), "fillColor": rgba(total_color, 0.2),
        "borderColor": total_color, "wickColor": total_color,
    })

plots = []
if show_total_volume and delta_mode == "Volume_Delta":
    plots.append({"name": "Total Volume Comparison", "data": total_candles, "type": "candlestick", "overlay": False})
if show_candles:
    plots.append({"name": "CVD Candles", "data": candles, "type": "candlestick", "overlay": False})
if show_line:
    plots.append({"name": "CVD Line", "data": finite(cvd_close), "color": up_color, "type": "line", "lineWidth": 2, "overlay": False})
if show_average:
    plots.append({"name": "CVD Average", "data": finite(cvd_average), "color": up_divergence_color, "type": "line", "lineWidth": 2, "overlay": False})
if show_zero_line:
    plots.append({"name": "Zero", "data": [0.0] * n, "color": "rgba(128,128,128,0.65)", "type": "line", "lineWidth": 1, "overlay": False})
if not plots:
    plots.append({"name": "CVD Reference", "data": finite(cvd_close), "color": "rgba(128,128,128,0.15)", "type": "line", "lineWidth": 1, "overlay": False})

signals = []
if reset_mode == "Trend_Change":
    signals.append({
        "type": "buy", "name": "Up Trend Reset", "text": "▲", "color": up_color, "renderMode": "events",
        "data": [float(cvd_high[index]) if reset[index] and trend_direction[index] > 0 else None for index in range(n)],
    })
    signals.append({
        "type": "sell", "name": "Down Trend Reset", "text": "▼", "color": down_color, "renderMode": "events",
        "data": [float(cvd_low[index]) if reset[index] and trend_direction[index] < 0 else None for index in range(n)],
    })

background_colors = [None] * n
for index in range(n):
    if show_reset_background and reset[index]:
        background_colors[index] = "rgba(128,128,128,0.10)"
    elif show_divergence_background and divergence[index]:
        background_colors[index] = "rgba(255,128,0,0.10)"

covered_bars = int(np.sum(intrabar_counts > 0))
average_intrabars = float(np.mean(intrabar_counts[intrabar_counts > 0])) if covered_bars else 0.0
tables = []
if show_info_table:
    tables.append({
        "title": "CVD Candles", "position": "bottom-right", "compact": True,
        "rows": [
            ["Reset", reset_description],
            ["Lower TF", lower_timeframe if can_request_lower else "Host fallback"],
            ["Coverage", f"{covered_bars} / {n}"],
            ["Avg intrabars", f"{average_intrabars:.1f}"],
            ["Delta", f"{delta[-1]:,.2f}" if n else "0"],
        ],
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": [],
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": background_colors,
    "calculatedVars": {
        "upVolume": finite(up_volume), "downVolume": finite(down_volume),
        "maxUpVolume": finite(max_up_volume), "maxDownVolume": finite(max_down_volume),
        "totalVolume": finite(total_volume), "delta": finite(delta), "deltaPercent": finite(delta_percent),
        "barDelta": finite(bar_delta), "reset": reset.tolist(), "trendDirection": trend_direction.tolist(),
        "cvdOpen": finite(cvd_open), "cvdHigh": finite(cvd_high), "cvdLow": finite(cvd_low),
        "cvdClose": finite(cvd_close), "cvdAverage": finite(cvd_average),
        "divergence": divergence.tolist(), "intrabarCounts": intrabar_counts.tolist(),
    },
}
