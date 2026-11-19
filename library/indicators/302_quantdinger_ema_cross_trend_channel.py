# ============================================================
# QuantDinger EMA Cross Trend Channel
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger EMA Cross Trend Channel"
my_indicator_description = "Combines dual EMAs, a non-repainting trend average, an ATR channel and a visual trade-state stop engine."
indicator_timeframes = ["1H", "4H", "1D", "1W"]

# @param source str Close Moving-average source values=Close,HL2,HLC3,OHLC4,Open,High,Low
# @param fast_length int 26 Fast EMA length range=1:500:1
# @param medium_length int 50 Medium EMA length range=1:500:1
# @param trend_length int 200 Trend-average length range=2:1000:1
# @param trend_ma_type str SMA Trend-average type values=SMA,EMA
# @param trend_timeframe str Current Trend timeframe values=Current,1H,4H,1D,1W
# @param channel_multiplier float 0.618 ATR channel multiplier range=0:10:0.001
# @param stop_loss_enabled bool false Enable visual stop loss
# @param stop_percent float 2.618 Stop-loss percent range=0.1:50:0.1
# @param period_enabled bool false Limit signals to a time period
# @param start_time int 1577847600000 Start time in milliseconds
# @param end_time int 4102369200000 End time in milliseconds
# @param position_type str Both Position type values=Both,Long,Short
# @param strategy_type str Strategy2 Strategy logic values=Strategy1,Strategy2
# @param color_bars bool true Color candles
# @param show_alerts bool true Show state-change markers
# @param show_channel bool false Show ATR channel
# @param show_golden_cross bool false Show medium/trend crosses

source_name = str(params.get("source", "Close")).upper()
if source_name not in ("CLOSE", "HL2", "HLC3", "OHLC4", "OPEN", "HIGH", "LOW"):
    source_name = "CLOSE"
fast_length = min(500, max(1, int(params.get("fast_length", 26))))
medium_length = min(500, max(1, int(params.get("medium_length", 50))))
trend_length = min(1000, max(2, int(params.get("trend_length", 200))))
trend_ma_type = str(params.get("trend_ma_type", "SMA")).upper()
if trend_ma_type not in ("SMA", "EMA"):
    trend_ma_type = "SMA"
trend_timeframe = str(params.get("trend_timeframe", "Current"))
if trend_timeframe not in ("Current", "1H", "4H", "1D", "1W"):
    trend_timeframe = "Current"
channel_multiplier = max(0.0, float(params.get("channel_multiplier", 0.618)))
stop_loss_enabled = bool(params.get("stop_loss_enabled", False))
stop_percent = min(50.0, max(0.1, float(params.get("stop_percent", 2.618))))
period_enabled = bool(params.get("period_enabled", False))
start_time = int(params.get("start_time", 1577847600000))
end_time = int(params.get("end_time", 4102369200000))
position_type = str(params.get("position_type", "Both"))
position_type = {"Only Long": "Long", "Only Short": "Short"}.get(position_type, position_type)
if position_type not in ("Both", "Long", "Short"):
    position_type = "Both"
strategy_type = str(params.get("strategy_type", "Strategy2")).replace(" ", "")
if strategy_type not in ("Strategy1", "Strategy2"):
    strategy_type = "Strategy2"
color_bars = bool(params.get("color_bars", True))
show_alerts = bool(params.get("show_alerts", True))
show_channel = bool(params.get("show_channel", False))
show_golden_cross = bool(params.get("show_golden_cross", False))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _normalize(source_frame):
    selected = df.copy() if source_frame is None or len(source_frame) == 0 else source_frame.copy()
    result = selected.reset_index(drop=True)
    for column in ("time", "open", "high", "low", "close", "volume"):
        if column not in result.columns:
            result[column] = 0.0
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)


def _seconds(values):
    result = np.asarray(values, dtype=float).copy()
    if len(result) and max(abs(float(value)) for value in result) > 10_000_000_000:
        result = result / 1000.0
    return result


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _sma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    running = 0.0
    for index, value in enumerate(values):
        running += float(value)
        if index >= period:
            running -= float(values[index - period])
        if index >= period - 1:
            result[index] = running / period
    return result


def _atr(high_values, low_values, close_values, period):
    true_range = np.full(len(close_values), np.nan, dtype=float)
    for index in range(len(close_values)):
        true_range[index] = high_values[index] - low_values[index] if index == 0 else max(high_values[index] - low_values[index], abs(high_values[index] - close_values[index - 1]), abs(low_values[index] - close_values[index - 1]))
    result = np.full(len(close_values), np.nan, dtype=float)
    if len(close_values) >= period:
        seed = sum(float(true_range[index]) for index in range(period)) / period
        result[period - 1] = seed
        for index in range(period, len(close_values)):
            result[index] = (result[index - 1] * (period - 1) + true_range[index]) / period
    return result


df = df.copy()
frame = _normalize(df)
times = frame["time"].to_numpy(dtype=float)
time_seconds = _seconds(times)
time_milliseconds = time_seconds * 1000.0
opened = frame["open"].to_numpy(dtype=float)
high = frame["high"].to_numpy(dtype=float)
low = frame["low"].to_numpy(dtype=float)
close = frame["close"].to_numpy(dtype=float)
n = len(frame)
if source_name == "HL2":
    ma_source = (high + low) / 2.0
elif source_name == "HLC3":
    ma_source = (high + low + close) / 3.0
elif source_name == "OHLC4":
    ma_source = (opened + high + low + close) / 4.0
elif source_name == "OPEN":
    ma_source = opened.copy()
elif source_name == "HIGH":
    ma_source = high.copy()
elif source_name == "LOW":
    ma_source = low.copy()
else:
    ma_source = close.copy()

fast_ema = _ema(ma_source, fast_length)
medium_ema = _ema(ma_source, medium_length)
ema_direction = np.full(n, -1, dtype=int)
for index in range(n):
    if fast_ema[index] > medium_ema[index]:
        ema_direction[index] = 1


def _trend_frame():
    if trend_timeframe == "Current":
        return frame.copy(), True, True
    raw = ctx.raw_series(trend_timeframe)
    if raw is None or len(raw) == 0:
        return frame.copy(), True, False
    return _normalize(raw), False, True


trend_frame, trend_direct, trend_available = _trend_frame()
trend_close = trend_frame["close"].to_numpy(dtype=float)
trend_raw = _sma(trend_close, trend_length) if trend_ma_type == "SMA" else _ema(trend_close, trend_length)
trend_shifted = np.full(len(trend_raw), np.nan, dtype=float)
if len(trend_raw) > 1:
    trend_shifted[1:] = trend_raw[:-1]
if trend_direct and len(trend_shifted) == n:
    trend = trend_shifted.copy()
else:
    trend = np.full(n, np.nan, dtype=float)
    source_time = _seconds(trend_frame["time"].to_numpy(dtype=float))
    for index, current_time in enumerate(time_seconds):
        source_index = int(np.searchsorted(source_time, current_time, side="right") - 1)
        if 0 <= source_index < len(trend_shifted) and np.isfinite(trend_shifted[source_index]):
            trend[index] = float(trend_shifted[source_index])

trend_direction = np.full(n, -1, dtype=int)
for index in range(n):
    if np.isfinite(trend[index]) and ma_source[index] >= trend[index]:
        trend_direction[index] = 1
atr = _atr(high, low, close, trend_length)
range_atr = atr * channel_multiplier
range_top = trend + range_atr
range_bottom = trend - range_atr
range_channel = np.zeros(n, dtype=bool)
trend_state = np.zeros(n, dtype=int)
for index in range(n):
    if not np.all(np.isfinite([range_top[index], range_bottom[index], trend[index], medium_ema[index]])):
        continue
    range_channel[index] = (opened[index] <= range_top[index] or close[index] <= range_top[index]) and (opened[index] >= range_bottom[index] or close[index] >= range_bottom[index])
    if range_channel[index]:
        trend_state[index] = 0
    elif strategy_type == "Strategy1":
        if trend_direction[index] == 1 and ema_direction[index] == 1 and trend[index] > medium_ema[index]:
            trend_state[index] = 0
        elif trend_direction[index] == -1 and ema_direction[index] == -1 and trend[index] < medium_ema[index]:
            trend_state[index] = 0
        elif trend_direction[index] == 1 and ema_direction[index] == 1:
            trend_state[index] = 1
        elif trend_direction[index] == -1 and ema_direction[index] == -1:
            trend_state[index] = -1
    else:
        if trend_direction[index] == 1 and ema_direction[index] == 1:
            trend_state[index] = 1
        elif trend_direction[index] == -1 and ema_direction[index] == -1:
            trend_state[index] = -1

golden_cross = np.zeros(n, dtype=bool)
death_cross = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.all(np.isfinite([medium_ema[index], medium_ema[index - 1], trend[index], trend[index - 1]])):
        golden_cross[index] = medium_ema[index] > trend[index] and medium_ema[index - 1] <= trend[index - 1]
        death_cross[index] = medium_ema[index] < trend[index] and medium_ema[index - 1] >= trend[index - 1]

period_active = np.ones(n, dtype=bool)
if period_enabled:
    period_active = (time_milliseconds >= start_time) & (time_milliseconds <= end_time)
entry_long = (position_type != "Short") & (trend_state == 1) & period_active
entry_short = (position_type != "Long") & (trend_state == -1) & period_active
condition = np.zeros(n, dtype=int)
stop_top_buffer = np.full(n, np.nan, dtype=float)
stop_bottom_buffer = np.full(n, np.nan, dtype=float)
stop_line = np.full(n, np.nan, dtype=float)
exit_long = np.zeros(n, dtype=bool)
exit_short = np.zeros(n, dtype=bool)
stop_long = np.zeros(n, dtype=bool)
stop_short = np.zeros(n, dtype=bool)
long_entry = np.zeros(n, dtype=bool)
short_entry = np.zeros(n, dtype=bool)
long_exit = np.zeros(n, dtype=bool)
short_exit = np.zeros(n, dtype=bool)
long_stop = np.zeros(n, dtype=bool)
short_stop = np.zeros(n, dtype=bool)
last_long_stop = np.nan
last_short_stop = np.nan
for index in range(n):
    previous_condition = condition[index - 1] if index >= 1 else 0
    exit_long[index] = position_type != "Short" and (trend_state[index] != 1 or ema_direction[index] == -1) and previous_condition == 1 and period_active[index]
    exit_short[index] = position_type != "Long" and (trend_state[index] != -1 or ema_direction[index] == 1) and previous_condition == -1 and period_active[index]
    stop_distance = close[index] * stop_percent / 100.0 if stop_loss_enabled else np.nan
    stop_top = close[index] + stop_distance if np.isfinite(stop_distance) else np.nan
    stop_bottom = close[index] - stop_distance if np.isfinite(stop_distance) else np.nan
    if previous_condition != 1 and entry_long[index] and np.isfinite(stop_bottom):
        last_long_stop = stop_bottom
    if previous_condition != -1 and entry_short[index] and np.isfinite(stop_top):
        last_short_stop = stop_top
    stop_top_buffer[index] = last_long_stop
    stop_bottom_buffer[index] = last_short_stop
    if previous_condition == -1 and entry_long[index]:
        stop_line[index] = last_long_stop
    elif previous_condition == 1 and entry_short[index]:
        stop_line[index] = last_short_stop
    elif previous_condition == 1 or entry_long[index]:
        stop_line[index] = last_long_stop
    elif previous_condition == -1 or entry_short[index]:
        stop_line[index] = last_short_stop
    cross_under = index >= 1 and np.all(np.isfinite([close[index], close[index - 1], stop_line[index], stop_line[index - 1]])) and close[index] < stop_line[index] and close[index - 1] >= stop_line[index - 1]
    cross_over = index >= 1 and np.all(np.isfinite([close[index], close[index - 1], stop_line[index], stop_line[index - 1]])) and close[index] > stop_line[index] and close[index - 1] <= stop_line[index - 1]
    through_stop = np.isfinite(stop_line[index]) and high[index] > stop_line[index] and low[index] < stop_line[index]
    stop_long[index] = (previous_condition == 1 and cross_under) or through_stop
    stop_short[index] = (previous_condition == -1 and cross_over) or through_stop
    if previous_condition != 1 and entry_long[index]:
        condition[index] = 1
    elif previous_condition != -1 and entry_short[index]:
        condition[index] = -1
    elif previous_condition == 1 and stop_long[index]:
        condition[index] = 0
    elif previous_condition == -1 and stop_short[index]:
        condition[index] = 0
    elif previous_condition != 0 and exit_long[index]:
        condition[index] = 0
    elif previous_condition != 0 and exit_short[index]:
        condition[index] = 0
    else:
        condition[index] = previous_condition
    long_entry[index] = previous_condition != 1 and condition[index] == 1
    short_entry[index] = previous_condition != -1 and condition[index] == -1
    long_exit[index] = previous_condition == 1 and exit_long[index] and not stop_long[index]
    short_exit[index] = previous_condition == -1 and exit_short[index] and not stop_short[index]
    long_stop[index] = previous_condition == 1 and stop_long[index]
    short_stop[index] = previous_condition == -1 and stop_short[index]

ema_colors = []
ema_fill = []
trend_colors = []
channel_colors = []
bar_colors = []
stop_plot = []
for index in range(n):
    if long_exit[index] or short_exit[index] or trend_state[index] == 0:
        base_color = "#FFA500"
    elif trend_state[index] == 1 and ema_direction[index] == 1:
        base_color = "#006400"
    elif trend_state[index] == -1 and ema_direction[index] == -1:
        base_color = "#8B0000"
    else:
        base_color = "#FFA500"
    ema_colors.append("rgba(0,100,0,0.01)" if base_color == "#006400" else "rgba(139,0,0,0.01)" if base_color == "#8B0000" else "rgba(255,165,0,0.01)")
    ema_fill.append("rgba(0,100,0,0.30)" if base_color == "#006400" else "rgba(139,0,0,0.30)" if base_color == "#8B0000" else "rgba(255,165,0,0.30)")
    previous_trend = trend[index - 1] if index >= 1 else np.nan
    if trend_state[index] == 0:
        trend_color = "#FFA500"
    elif trend_state[index] == 1:
        trend_color = "#006400" if np.isfinite(previous_trend) and previous_trend < trend[index] else "#388E3C"
    else:
        trend_color = "#B71C1C" if np.isfinite(previous_trend) and previous_trend < trend[index] else "#8B0000"
    trend_colors.append(trend_color)
    channel_colors.append("rgba(255,165,0,0.25)" if trend_state[index] == 0 else "rgba(0,100,0,0.25)" if trend_state[index] == 1 else "rgba(139,0,0,0.25)")
    if trend_state[index] == 0:
        bar_color = "#FFA500"
    elif trend_state[index] == 1:
        bar_color = "#006400" if opened[index] <= close[index] else "#388E3C"
    else:
        bar_color = "#8B0000" if opened[index] >= close[index] else "#B71C1C"
    bar_colors.append(bar_color if color_bars else None)
    horizontal = index == 0 or not np.all(np.isfinite([stop_line[index], stop_line[index - 1]])) or abs(stop_line[index] - stop_line[index - 1]) <= 1e-12
    stop_plot.append(float(stop_line[index]) if stop_loss_enabled and condition[index] != 0 and horizontal and np.isfinite(stop_line[index]) else None)

ema_cloud = []
channel_cloud = []
for index in range(n):
    ema_cloud.append({"upper": float(max(fast_ema[index], medium_ema[index])), "lower": float(min(fast_ema[index], medium_ema[index])), "color": ema_fill[index]} if np.all(np.isfinite([fast_ema[index], medium_ema[index]])) else None)
    channel_cloud.append({"upper": float(range_top[index]), "lower": float(range_bottom[index]), "color": channel_colors[index]} if show_channel and np.all(np.isfinite([range_top[index], range_bottom[index]])) else None)

signals = []
if show_alerts:
    signals = [
        {"type": "buy", "name": "Long", "text": "Long", "data": [float(low[index]) if long_entry[index] else None for index in range(n)], "color": "#006400", "renderMode": "events"},
        {"type": "sell", "name": "Short", "text": "Short", "data": [float(high[index]) if short_entry[index] else None for index in range(n)], "color": "#8B0000", "renderMode": "events"},
        {"type": "sell", "name": "Close", "text": "×", "data": [float(close[index]) if long_exit[index] or short_exit[index] else None for index in range(n)], "color": "#FFFF00", "renderMode": "events"},
        {"type": "sell", "name": "Stop Loss", "text": "SL", "data": [float(stop_line[index]) if (long_stop[index] or short_stop[index]) and np.isfinite(stop_line[index]) else None for index in range(n)], "color": "#FFFF00", "renderMode": "events"},
        {"type": "buy", "name": "Golden Cross", "text": "Golden Cross", "data": [float(trend[index]) if show_golden_cross and golden_cross[index] and np.isfinite(trend[index]) else None for index in range(n)], "color": "#FFA500", "renderMode": "events"},
        {"type": "sell", "name": "Death Cross", "text": "Death Cross", "data": [float(trend[index]) if show_golden_cross and death_cross[index] and np.isfinite(trend[index]) else None for index in range(n)], "color": "#FFA500", "renderMode": "events"},
    ]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Fast EMA", "data": _finite(fast_ema), "color": ema_colors, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Medium EMA", "data": _finite(medium_ema), "color": ema_colors, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "EMA Fill", "title": False, "data": ema_cloud, "color": "#006400", "type": "cloud", "overlay": True},
        {"name": "Trend Average", "data": _finite(trend), "color": trend_colors, "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "Top Channel", "data": _finite(range_top) if show_channel else [None] * n, "color": trend_colors, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Bottom Channel", "data": _finite(range_bottom) if show_channel else [None] * n, "color": trend_colors, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Trend Channel", "title": False, "data": channel_cloud, "color": "#006400", "type": "cloud", "overlay": True},
        {"name": "Stop Loss Line", "data": stop_plot, "color": "#8B0000", "type": "line", "style": "linebr", "lineWidth": 1, "overlay": True},
    ],
    "signals": signals,
    "layers": [],
    "tables": [],
    "bar_colors": bar_colors,
    "background_colors": ["rgba(128,128,128,0.10)" if period_enabled and not period_active[index] else None for index in range(n)],
    "calculatedVars": {
        "fastEMA": _finite(fast_ema), "mediumEMA": _finite(medium_ema), "emaDirection": ema_direction.tolist(),
        "trendAverage": _finite(trend), "trendDirection": trend_direction.tolist(), "atr": _finite(atr),
        "rangeTop": _finite(range_top), "rangeBottom": _finite(range_bottom), "rangeChannel": range_channel.tolist(),
        "trendState": trend_state.tolist(), "goldenCross": golden_cross.tolist(), "deathCross": death_cross.tolist(),
        "periodActive": period_active.tolist(), "entryLong": entry_long.tolist(), "entryShort": entry_short.tolist(),
        "exitLong": exit_long.tolist(), "exitShort": exit_short.tolist(), "stopLine": _finite(stop_line),
        "stopLong": stop_long.tolist(), "stopShort": stop_short.tolist(), "condition": condition.tolist(),
        "longEntry": long_entry.tolist(), "shortEntry": short_entry.tolist(), "longExit": long_exit.tolist(), "shortExit": short_exit.tolist(),
        "longStop": long_stop.tolist(), "shortStop": short_stop.tolist(), "trendTimeframeAvailable": bool(trend_available),
        "trendTimeframe": trend_timeframe, "strategyType": strategy_type, "positionType": position_type,
    },
}
