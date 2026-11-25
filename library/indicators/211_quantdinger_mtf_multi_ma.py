# ============================================================
# QuantDinger MTF Multi-MA
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; cross events do not place orders.
# ============================================================

my_indicator_name = "QuantDinger MTF Multi-MA"
my_indicator_description = (
    "Plots up to three independently typed and timed moving averages, colors "
    "them by slope or RSI momentum, and optionally colors host candles and emits crosses."
)

indicator_timeframes = ["5m", "15m", "30m", "1H", "4H", "1D", "1W"]

# @param smooth bool true Smooth higher-timeframe lines
# @param color_bars bool true Color host candles
# @param bar_color_basis str MA_1 Bar color basis values=MA_1,MA_2,MA_3,RSI_1,RSI_2,RSI_3
# @param ma1_type str EMA MA 1 type values=EMA,HEMA,SMA,HMA,WMA,DEMA,VWMA,VWAP,T3,IT
# @param ma1_length int 50 MA 1 length range=1:500:1
# @param ma1_timeframe str Current MA 1 timeframe values=Current,5m,15m,30m,1H,4H,1D,1W
# @param ma1_color_mode str MA MA 1 color mode values=MA,RSI
# @param ma1_bull_color str #64B5F6 MA 1 bullish color
# @param ma1_bear_color str #EF5350 MA 1 bearish color
# @param ma1_width int 2 MA 1 width range=1:6:1
# @param ma2_type str EMA MA 2 type values=EMA,HEMA,SMA,HMA,WMA,DEMA,VWMA,VWAP,T3,IT
# @param ma2_length int 0 MA 2 length (0 = disabled) range=0:500:1
# @param ma2_timeframe str Current MA 2 timeframe values=Current,5m,15m,30m,1H,4H,1D,1W
# @param ma2_color_mode str MA MA 2 color mode values=MA,RSI
# @param ma2_bull_color str #42A5F5 MA 2 bullish color
# @param ma2_bear_color str #FF7043 MA 2 bearish color
# @param ma2_width int 2 MA 2 width range=1:6:1
# @param ma3_type str EMA MA 3 type values=EMA,HEMA,SMA,HMA,WMA,DEMA,VWMA,VWAP,T3,IT
# @param ma3_length int 0 MA 3 length (0 = disabled) range=0:500:1
# @param ma3_timeframe str Current MA 3 timeframe values=Current,5m,15m,30m,1H,4H,1D,1W
# @param ma3_color_mode str MA MA 3 color mode values=MA,RSI
# @param ma3_bull_color str #26C6DA MA 3 bullish color
# @param ma3_bear_color str #AB47BC MA 3 bearish color
# @param ma3_width int 2 MA 3 width range=1:6:1
# @param rsi_length int 14 RSI length range=1:200:1
# @param rsi_bull_color str #0000FF RSI bullish color
# @param rsi_bear_color str #FF0000 RSI bearish color
# @param it_source str HL2 IT source values=Close,HL2,HLC3,OHLC4
# @param it_alpha float 0.07 IT alpha range=0.01:1.0:0.01
# @param show_cross_signals bool false Show close and MA cross signals

smooth = bool(params.get("smooth", True))
color_bars = bool(params.get("color_bars", True))
bar_color_basis = str(params.get("bar_color_basis", "MA_1"))
if bar_color_basis not in ("MA_1", "MA_2", "MA_3", "RSI_1", "RSI_2", "RSI_3"):
    bar_color_basis = "MA_1"

valid_ma_types = ("EMA", "HEMA", "SMA", "HMA", "WMA", "DEMA", "VWMA", "VWAP", "T3", "IT")
valid_timeframes = ("Current", "5m", "15m", "30m", "1H", "4H", "1D", "1W")


def ma_type_param(name, default):
    value = str(params.get(name, default)).upper()
    return value if value in valid_ma_types else default


def timeframe_param(name):
    value = str(params.get(name, "Current"))
    return value if value in valid_timeframes else "Current"


def color_mode_param(name):
    value = str(params.get(name, "MA")).upper()
    return value if value in ("MA", "RSI") else "MA"


ma_types = [
    ma_type_param("ma1_type", "EMA"),
    ma_type_param("ma2_type", "EMA"),
    ma_type_param("ma3_type", "EMA"),
]
ma_lengths = [
    min(500, max(1, int(params.get("ma1_length", 50)))),
    min(500, max(0, int(params.get("ma2_length", 0)))),
    min(500, max(0, int(params.get("ma3_length", 0)))),
]
ma_timeframes = [
    timeframe_param("ma1_timeframe"),
    timeframe_param("ma2_timeframe"),
    timeframe_param("ma3_timeframe"),
]
ma_color_modes = [
    color_mode_param("ma1_color_mode"),
    color_mode_param("ma2_color_mode"),
    color_mode_param("ma3_color_mode"),
]
ma_bull_colors = [
    str(params.get("ma1_bull_color", "#64B5F6")),
    str(params.get("ma2_bull_color", "#42A5F5")),
    str(params.get("ma3_bull_color", "#26C6DA")),
]
ma_bear_colors = [
    str(params.get("ma1_bear_color", "#EF5350")),
    str(params.get("ma2_bear_color", "#FF7043")),
    str(params.get("ma3_bear_color", "#AB47BC")),
]
ma_widths = [
    min(6, max(1, int(params.get("ma1_width", 2)))),
    min(6, max(1, int(params.get("ma2_width", 2)))),
    min(6, max(1, int(params.get("ma3_width", 2)))),
]
rsi_length = min(200, max(1, int(params.get("rsi_length", 14))))
rsi_bull_color = str(params.get("rsi_bull_color", "#0000FF"))
rsi_bear_color = str(params.get("rsi_bear_color", "#FF0000"))
it_source_name = str(params.get("it_source", "HL2")).upper()
if it_source_name not in ("CLOSE", "HL2", "HLC3", "OHLC4"):
    it_source_name = "HL2"
it_alpha = min(1.0, max(0.01, float(params.get("it_alpha", 0.07))))
show_cross_signals = bool(params.get("show_cross_signals", False))

df = df.copy()
n = len(df)

timeframe_seconds = {
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1H": 3600,
    "4H": 14400,
    "1D": 86400,
    "1W": 604800,
}


def normalized_frame(frame):
    source = df.copy() if frame is None or len(frame) == 0 else frame.copy()
    result = source.reset_index(drop=True)
    for column in ("time", "open", "high", "low", "close", "volume"):
        if column not in result.columns:
            result[column] = np.arange(len(result), dtype=float) * 3600.0 if column == "time" else 0.0
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
    times = result["time"].to_numpy(dtype=float)
    if len(times) and max(abs(float(value)) for value in times) > 10_000_000_000:
        result["time"] = times / 1000.0
    return result


base_frame = normalized_frame(df)
base_time = base_frame["time"].to_numpy(dtype=float)
base_open = base_frame["open"].to_numpy(dtype=float)
base_high = base_frame["high"].to_numpy(dtype=float)
base_low = base_frame["low"].to_numpy(dtype=float)
base_close = base_frame["close"].to_numpy(dtype=float)
base_volume = base_frame["volume"].to_numpy(dtype=float)

runtime_context = ctx
chart_timeframe = str(runtime_context.timeframe or "")
if chart_timeframe in timeframe_seconds:
    chart_seconds = timeframe_seconds[chart_timeframe]
elif len(base_time) > 1:
    positive_deltas = sorted(
        float(base_time[index] - base_time[index - 1])
        for index in range(1, len(base_time))
        if base_time[index] > base_time[index - 1]
    )
    chart_seconds = int(positive_deltas[len(positive_deltas) // 2]) if positive_deltas else 3600
else:
    chart_seconds = 3600


def average(values):
    return sum(float(value) for value in values) / len(values) if len(values) else np.nan


def sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if all(np.isfinite(float(value)) for value in window):
            result[index] = average(window)
    return result


def ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    seed = values[:length]
    if not all(np.isfinite(float(value)) for value in seed):
        return result
    result[length - 1] = average(seed)
    weight = 2.0 / (length + 1.0)
    for index in range(length, len(values)):
        if np.isfinite(values[index]) and np.isfinite(result[index - 1]):
            result[index] = weight * values[index] + (1.0 - weight) * result[index - 1]
    return result


def wma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    denominator = length * (length + 1) / 2.0
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if all(np.isfinite(float(value)) for value in window):
            result[index] = sum(
                float(value) * (offset + 1) for offset, value in enumerate(window)
            ) / denominator
    return result


def vwma(values, volume_values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        left = index - length + 1
        denominator = sum(float(value) for value in volume_values[left:index + 1])
        if denominator > 1e-12:
            result[index] = sum(
                float(values[cursor]) * float(volume_values[cursor])
                for cursor in range(left, index + 1)
            ) / denominator
    return result


def heikin_open(open_values, high_values, low_values, close_values):
    count = len(close_values)
    ha_close = (
        open_values + high_values + low_values + close_values
    ) / 4.0
    result = np.full(count, np.nan, dtype=float)
    for index in range(count):
        if index == 0:
            result[index] = (open_values[index] + close_values[index]) / 2.0
        else:
            result[index] = (result[index - 1] + ha_close[index - 1]) / 2.0
    return result


def session_vwap(time_values, high_values, low_values, close_values, volume_values):
    result = np.full(len(close_values), np.nan, dtype=float)
    current_day = None
    cumulative_value = 0.0
    cumulative_volume = 0.0
    for index in range(len(close_values)):
        day = int(time_values[index] // 86400)
        if current_day is None or day != current_day:
            current_day = day
            cumulative_value = 0.0
            cumulative_volume = 0.0
        typical = (high_values[index] + low_values[index] + close_values[index]) / 3.0
        cumulative_value += typical * volume_values[index]
        cumulative_volume += volume_values[index]
        if cumulative_volume > 1e-12:
            result[index] = cumulative_value / cumulative_volume
    return result


def instantaneous_trend(source, alpha):
    result = np.full(len(source), np.nan, dtype=float)
    alpha_squared = alpha * alpha
    for index in range(len(source)):
        source_1 = float(source[index - 1]) if index >= 1 else 0.0
        source_2 = float(source[index - 2]) if index >= 2 else 0.0
        if index < 7:
            result[index] = (float(source[index]) + 2.0 * source_1 + source_2) / 4.0
        else:
            result[index] = (
                (alpha - alpha_squared / 4.0) * source[index]
                + 0.5 * alpha_squared * source_1
                - (alpha - 0.75 * alpha_squared) * source_2
                + 2.0 * (1.0 - alpha) * result[index - 1]
                - (1.0 - alpha) * (1.0 - alpha) * result[index - 2]
            )
    return result


def moving_average(frame, kind, length):
    open_values = frame["open"].to_numpy(dtype=float)
    high_values = frame["high"].to_numpy(dtype=float)
    low_values = frame["low"].to_numpy(dtype=float)
    close_values = frame["close"].to_numpy(dtype=float)
    volume_values = frame["volume"].to_numpy(dtype=float)
    time_values = frame["time"].to_numpy(dtype=float)
    safe_length = max(1, int(length))
    if kind == "HEMA":
        return ema(heikin_open(open_values, high_values, low_values, close_values), safe_length)
    if kind == "SMA":
        return sma(close_values, safe_length)
    if kind == "HMA":
        half = max(1, safe_length // 2)
        root = max(1, int(round(safe_length ** 0.5)))
        full_wma = wma(close_values, safe_length)
        half_wma = wma(close_values, half)
        raw = 2.0 * half_wma - full_wma
        return wma(raw, root)
    if kind == "WMA":
        return wma(close_values, safe_length)
    if kind == "DEMA":
        first = ema(close_values, safe_length)
        second = ema(first[np.isfinite(first)], safe_length)
        expanded_second = np.full(len(close_values), np.nan, dtype=float)
        first_indices = [index for index, value in enumerate(first) if np.isfinite(value)]
        for offset, index in enumerate(first_indices):
            if offset < len(second) and np.isfinite(second[offset]):
                expanded_second[index] = second[offset]
        return 2.0 * first - expanded_second
    if kind == "VWMA":
        return vwma(close_values, volume_values, safe_length)
    if kind == "VWAP":
        return session_vwap(time_values, high_values, low_values, close_values, volume_values)
    if kind == "T3":
        stages = []
        current = close_values.copy()
        for _ in range(6):
            current = ema(current[np.isfinite(current)], safe_length)
            expanded = np.full(len(close_values), np.nan, dtype=float)
            valid_indices = [index for index, value in enumerate(stages[-1] if stages else close_values) if np.isfinite(value)]
            for offset, index in enumerate(valid_indices):
                if offset < len(current) and np.isfinite(current[offset]):
                    expanded[index] = current[offset]
            stages.append(expanded)
            current = expanded
        factor = 0.7
        c1 = -(factor ** 3)
        c2 = 3.0 * factor * factor + 3.0 * factor ** 3
        c3 = -6.0 * factor * factor - 3.0 * factor - 3.0 * factor ** 3
        c4 = 1.0 + 3.0 * factor + factor ** 3 + 3.0 * factor * factor
        return c1 * stages[5] + c2 * stages[4] + c3 * stages[3] + c4 * stages[2]
    if kind == "IT":
        if it_source_name == "CLOSE":
            source = close_values
        elif it_source_name == "HLC3":
            source = (high_values + low_values + close_values) / 3.0
        elif it_source_name == "OHLC4":
            source = (open_values + high_values + low_values + close_values) / 4.0
        else:
            source = (high_values + low_values) / 2.0
        return instantaneous_trend(source, it_alpha)
    return ema(close_values, safe_length)


def rsi(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) <= length:
        return result
    gains = np.zeros(len(values), dtype=float)
    losses = np.zeros(len(values), dtype=float)
    for index in range(1, len(values)):
        change = float(values[index] - values[index - 1])
        gains[index] = max(change, 0.0)
        losses[index] = max(-change, 0.0)
    average_gain = average(gains[1:length + 1])
    average_loss = average(losses[1:length + 1])
    result[length] = 100.0 if average_loss <= 1e-12 else 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    for index in range(length + 1, len(values)):
        average_gain = (average_gain * (length - 1) + gains[index]) / length
        average_loss = (average_loss * (length - 1) + losses[index]) / length
        result[index] = 100.0 if average_loss <= 1e-12 else 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return result


def requested_frame(timeframe):
    if timeframe == "Current" or timeframe == chart_timeframe:
        return base_frame.copy(), chart_seconds, True
    source = (
        runtime_context.raw_series(timeframe)
        if runtime_context is not None and hasattr(runtime_context, "raw_series")
        else None
    )
    if source is None or len(source) == 0:
        return base_frame.copy(), chart_seconds, True
    return normalized_frame(source), timeframe_seconds[timeframe], False


def align_completed(source_frame, values, source_seconds, direct):
    if direct and len(values) == n:
        return np.asarray(values, dtype=float).copy()
    result = np.full(n, np.nan, dtype=float)
    source_time = source_frame["time"].to_numpy(dtype=float)
    source_close_time = source_time + source_seconds
    for index in range(n):
        base_close_time = base_time[index] + chart_seconds
        source_index = int(np.searchsorted(source_close_time, base_close_time, side="right") - 1)
        if 0 <= source_index < len(values) and np.isfinite(values[source_index]):
            result[index] = float(values[source_index])
    return result


def smooth_completed(step_values):
    if not smooth:
        return step_values.copy()
    result = step_values.copy()
    change_points = []
    prior = np.nan
    for index, value in enumerate(step_values):
        if not np.isfinite(value):
            continue
        if not np.isfinite(prior) or abs(float(value) - float(prior)) > 1e-12:
            change_points.append(index)
            prior = value
    for cursor in range(1, len(change_points)):
        left = change_points[cursor - 1]
        right = change_points[cursor]
        left_value = float(step_values[left])
        right_value = float(step_values[right])
        distance = right - left
        if distance <= 0:
            continue
        for index in range(left, right + 1):
            weight = (index - left) / distance
            result[index] = left_value + (right_value - left_value) * weight
    return result


def hex_rgb(value):
    color = str(value).strip()
    if color.startswith("#") and len(color) == 7:
        return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    if color.startswith("#") and len(color) == 4:
        return tuple(int(color[index] * 2, 16) for index in range(1, 4))
    return 128, 128, 128


def gradient_color(value, lower_color, upper_color):
    if not np.isfinite(value):
        return lower_color
    weight = min(1.0, max(0.0, (float(value) - 30.0) / 40.0))
    lower = hex_rgb(lower_color)
    upper = hex_rgb(upper_color)
    channels = [
        int(round(lower[index] + (upper[index] - lower[index]) * weight))
        for index in range(3)
    ]
    return "#" + "".join(f"{channel:02X}" for channel in channels)


ma_lines = []
ma_steps = []
ma_rsi = []
ma_directions = []
enabled = []
for slot in range(3):
    source_frame, source_seconds, direct = requested_frame(ma_timeframes[slot])
    source_close = source_frame["close"].to_numpy(dtype=float)
    source_ma = moving_average(source_frame, ma_types[slot], max(1, ma_lengths[slot]))
    source_rsi = rsi(source_close, rsi_length)
    source_direction = np.zeros(len(source_ma), dtype=float)
    for index in range(1, len(source_ma)):
        if np.isfinite(source_ma[index]) and np.isfinite(source_ma[index - 1]):
            source_direction[index] = 1.0 if source_ma[index] >= source_ma[index - 1] else -1.0
    step_ma = align_completed(source_frame, source_ma, source_seconds, direct)
    aligned_rsi = align_completed(source_frame, source_rsi, source_seconds, direct)
    aligned_direction = align_completed(source_frame, source_direction, source_seconds, direct)
    ma_steps.append(step_ma)
    ma_lines.append(smooth_completed(step_ma) if ma_timeframes[slot] != "Current" else step_ma.copy())
    ma_rsi.append(aligned_rsi)
    ma_directions.append(aligned_direction)
    enabled.append(slot == 0 or ma_lengths[slot] > 0 or ma_types[slot] == "IT")


def plain_line(values, visible=True):
    return [
        float(value) if visible and np.isfinite(value) else None
        for value in values
    ]


def direction_segment(slot, bullish):
    result = [None] * n
    for index, value in enumerate(ma_lines[slot]):
        if not (enabled[slot] and np.isfinite(value)):
            continue
        current_bullish = ma_directions[slot][index] >= 0.0
        if current_bullish != bullish:
            continue
        result[index] = float(value)
        if index > 0 and np.isfinite(ma_lines[slot][index - 1]):
            prior_bullish = ma_directions[slot][index - 1] >= 0.0
            if prior_bullish != current_bullish:
                result[index - 1] = float(ma_lines[slot][index - 1])
    return result


def rsi_bucket_index(value, bucket_count):
    if not np.isfinite(value):
        return -1
    weight = min(1.0, max(0.0, (float(value) - 30.0) / 40.0))
    return int(round(weight * (bucket_count - 1)))


def rsi_segment(slot, bucket, bucket_count):
    result = [None] * n
    for index, value in enumerate(ma_lines[slot]):
        if not (enabled[slot] and np.isfinite(value)):
            continue
        current_bucket = rsi_bucket_index(ma_rsi[slot][index], bucket_count)
        if current_bucket != bucket:
            continue
        result[index] = float(value)
        if index > 0 and np.isfinite(ma_lines[slot][index - 1]):
            prior_bucket = rsi_bucket_index(ma_rsi[slot][index - 1], bucket_count)
            if prior_bucket != current_bucket:
                result[index - 1] = float(ma_lines[slot][index - 1])
    return result


bar_colors = [None] * n
if color_bars:
    basis_slot = 0
    if bar_color_basis.endswith("2"):
        basis_slot = 1
    elif bar_color_basis.endswith("3"):
        basis_slot = 2
    if not enabled[basis_slot]:
        basis_slot = 0
    for index in range(n):
        if bar_color_basis.startswith("RSI"):
            bar_colors[index] = gradient_color(
                ma_rsi[basis_slot][index], rsi_bear_color, rsi_bull_color
            ) if np.isfinite(ma_rsi[basis_slot][index]) else None
        elif np.isfinite(ma_steps[basis_slot][index]):
            bar_colors[index] = (
                ma_bull_colors[basis_slot]
                if base_close[index] >= ma_steps[basis_slot][index]
                else ma_bear_colors[basis_slot]
            )


def crossover(left, right, upward):
    result = np.zeros(n, dtype=bool)
    for index in range(1, n):
        if not (
            np.isfinite(left[index])
            and np.isfinite(right[index])
            and np.isfinite(left[index - 1])
            and np.isfinite(right[index - 1])
        ):
            continue
        if upward:
            result[index] = left[index] > right[index] and left[index - 1] <= right[index - 1]
        else:
            result[index] = left[index] < right[index] and left[index - 1] >= right[index - 1]
    return result


signals = []
if show_cross_signals:
    for slot in range(3):
        if not enabled[slot]:
            continue
        cross_up = crossover(base_close, ma_steps[slot], True)
        cross_down = crossover(base_close, ma_steps[slot], False)
        signals.append({
            "type": "buy",
            "name": f"Close Above MA {slot + 1}",
            "text": f"MA{slot + 1} UP",
            "color": ma_bull_colors[slot],
            "renderMode": "events",
            "data": [float(base_low[index]) if cross_up[index] else None for index in range(n)],
        })
        signals.append({
            "type": "sell",
            "name": f"Close Below MA {slot + 1}",
            "text": f"MA{slot + 1} DOWN",
            "color": ma_bear_colors[slot],
            "renderMode": "events",
            "data": [float(base_high[index]) if cross_down[index] else None for index in range(n)],
        })
    for left_slot, right_slot in ((0, 1), (0, 2), (1, 2)):
        if not (enabled[left_slot] and enabled[right_slot]):
            continue
        pair_up = crossover(ma_steps[left_slot], ma_steps[right_slot], True)
        pair_down = crossover(ma_steps[left_slot], ma_steps[right_slot], False)
        signals.append({
            "type": "buy",
            "name": f"MA {left_slot + 1} Above MA {right_slot + 1}",
            "text": f"MA{left_slot + 1}>{right_slot + 1}",
            "color": ma_bull_colors[left_slot],
            "renderMode": "events",
            "data": [float(base_low[index]) if pair_up[index] else None for index in range(n)],
        })
        signals.append({
            "type": "sell",
            "name": f"MA {left_slot + 1} Below MA {right_slot + 1}",
            "text": f"MA{left_slot + 1}<{right_slot + 1}",
            "color": ma_bear_colors[left_slot],
            "renderMode": "events",
            "data": [float(base_high[index]) if pair_down[index] else None for index in range(n)],
        })

plots = []
for slot in range(3):
    if enabled[slot]:
        plots.append({
            "name": f"Moving Average {slot + 1}",
            "data": plain_line(ma_lines[slot]),
            "color": "rgba(0,0,0,0)",
            "opacity": 0.0,
            "type": "line",
            "lineWidth": 1,
            "overlay": True,
        })
        if ma_color_modes[slot] == "MA":
            plots.append({
                "name": f"MA {slot + 1} Bullish",
                "title": False,
                "data": direction_segment(slot, True),
                "color": ma_bull_colors[slot],
                "type": "line",
                "lineWidth": ma_widths[slot],
                "overlay": True,
            })
            plots.append({
                "name": f"MA {slot + 1} Bearish",
                "title": False,
                "data": direction_segment(slot, False),
                "color": ma_bear_colors[slot],
                "type": "line",
                "lineWidth": ma_widths[slot],
                "overlay": True,
            })
        else:
            bucket_count = 7
            for bucket in range(bucket_count):
                palette_value = 30.0 + 40.0 * bucket / (bucket_count - 1)
                plots.append({
                    "name": f"MA {slot + 1} RSI Color {bucket + 1}",
                    "title": False,
                    "data": rsi_segment(slot, bucket, bucket_count),
                    "color": gradient_color(palette_value, rsi_bear_color, rsi_bull_color),
                    "type": "line",
                    "lineWidth": ma_widths[slot],
                    "overlay": True,
                })

last_values = []
for slot in range(3):
    current = None
    for value in ma_lines[slot][::-1]:
        if np.isfinite(value):
            current = float(value)
            break
    last_values.append(current)

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "bar_colors": bar_colors,
    "layers": [],
    "calculatedVars": {
        "chartTimeframe": chart_timeframe or "inferred",
        "enabledAverages": int(sum(1 for value in enabled if value)),
        "ma1": last_values[0],
        "ma2": last_values[1] if enabled[1] else None,
        "ma3": last_values[2] if enabled[2] else None,
    },
}
