# ============================================================
# QuantDinger Angle Attack Follow Line
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Angle Attack Follow Line"
my_indicator_description = (
    "Transforms a Bollinger/ATR follow line into a normalized angle oscillator, with optional "
    "higher-timeframe direction filtering and staged buy, sell, add, and reduce events."
)

indicator_timeframes = ["4H", "1D"]

# @param bb_period int 21 Current follow-line period range=1:300:1
# @param bb_deviation float 1.0 Current Bollinger deviation range=0.1:10.0:0.05
# @param use_atr_filter bool true Use ATR offset on current follow line
# @param atr_period int 5 Current follow-line ATR period range=1:200:1
# @param higher_timeframe str 4H Higher timeframe values=4H,1D
# @param higher_ma_type str SMA Higher-timeframe average values=RMA,SMA,EMA,WMA,VWMA,SMMA,KMA,TMA,HullMA,DEMA,TEMA,CTI
# @param higher_period int 21 Higher-timeframe period range=2:300:1
# @param higher_deviation float 1.0 Higher-timeframe Bollinger deviation range=0.1:10.0:0.05
# @param higher_atr_period int 5 Higher-timeframe ATR period range=1:200:1
# @param filter_mode str Unfiltered Signal filter values=Unfiltered,Higher_Timeframe
# @param angle_period int 8 Angle lookback range=1:100:1
# @param angle_atr_period int 10 Angle normalization ATR period range=1:200:1
# @param show_background bool true Color background by higher-timeframe trend
# @param color_price_bars bool true Color price bars by current trend
# @param show_labels bool true Show event labels
# @param show_add_signals bool true Show add-position events
# @param add_after_5_flat_bars bool false Enable second add condition
# @param add_after_8_flat_bars bool false Enable third add condition
# @param show_reduce_signals bool true Show reduce-position events
# @param max_angle_level_1 float 40.0 First positive angle threshold range=1.0:89.0:1.0
# @param max_angle_level_2 float 65.0 Second positive angle threshold range=1.0:89.0:1.0
# @param min_angle_level_1 float -40.0 First negative angle threshold range=-89.0:-1.0:1.0
# @param min_angle_level_2 float -65.0 Second negative angle threshold range=-89.0:-1.0:1.0
# @param reduce_level_1 bool true Reduce on first threshold cross
# @param reduce_level_2 bool true Reduce on second threshold cross
# @param reduce_after_2_bars bool false Reduce after two extreme bars
# @param reduce_after_3_bars bool true Reduce after three extreme bars
# @param reduce_after_4_bars bool false Reduce after four extreme bars
# @param bullish_color str #2962FF Bullish color
# @param bearish_color str #F23645 Bearish color

bb_period = min(300, max(1, int(params.get("bb_period", 21))))
bb_deviation = min(10.0, max(0.1, float(params.get("bb_deviation", 1.0))))
use_atr_filter = bool(params.get("use_atr_filter", True))
atr_period = min(200, max(1, int(params.get("atr_period", 5))))
higher_timeframe = str(params.get("higher_timeframe", "4H"))
if higher_timeframe not in ("4H", "1D"):
    higher_timeframe = "4H"
higher_ma_type = str(params.get("higher_ma_type", "SMA"))
ma_types = ("RMA", "SMA", "EMA", "WMA", "VWMA", "SMMA", "KMA", "TMA", "HullMA", "DEMA", "TEMA", "CTI")
if higher_ma_type not in ma_types:
    higher_ma_type = "SMA"
higher_period = min(300, max(2, int(params.get("higher_period", 21))))
higher_deviation = min(10.0, max(0.1, float(params.get("higher_deviation", 1.0))))
higher_atr_period = min(200, max(1, int(params.get("higher_atr_period", 5))))
filter_mode = str(params.get("filter_mode", "Unfiltered"))
if filter_mode not in ("Unfiltered", "Higher_Timeframe"):
    filter_mode = "Unfiltered"
angle_period = min(100, max(1, int(params.get("angle_period", 8))))
angle_atr_period = min(200, max(1, int(params.get("angle_atr_period", 10))))
show_background = bool(params.get("show_background", True))
color_price_bars = bool(params.get("color_price_bars", True))
show_labels = bool(params.get("show_labels", True))
show_add_signals = bool(params.get("show_add_signals", True))
add_after_5_flat_bars = bool(params.get("add_after_5_flat_bars", False))
add_after_8_flat_bars = bool(params.get("add_after_8_flat_bars", False))
show_reduce_signals = bool(params.get("show_reduce_signals", True))
max_angle_level_1 = min(89.0, max(1.0, float(params.get("max_angle_level_1", 40.0))))
max_angle_level_2 = min(89.0, max(max_angle_level_1, float(params.get("max_angle_level_2", 65.0))))
min_angle_level_1 = max(-89.0, min(-1.0, float(params.get("min_angle_level_1", -40.0))))
min_angle_level_2 = max(-89.0, min(min_angle_level_1, float(params.get("min_angle_level_2", -65.0))))
reduce_level_1 = bool(params.get("reduce_level_1", True))
reduce_level_2 = bool(params.get("reduce_level_2", True))
reduce_after_2_bars = bool(params.get("reduce_after_2_bars", False))
reduce_after_3_bars = bool(params.get("reduce_after_3_bars", True))
reduce_after_4_bars = bool(params.get("reduce_after_4_bars", False))
bullish_color = str(params.get("bullish_color", "#2962FF"))
bearish_color = str(params.get("bearish_color", "#F23645"))

df = df.copy()

timeframe_seconds = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1H": 3600, "2H": 7200, "4H": 14400, "6H": 21600,
    "8H": 28800, "12H": 43200, "1D": 86400, "1W": 604800,
}


def normalized_frame(frame):
    if frame is None or len(frame) == 0:
        frame = df.copy()
    result = frame.copy().reset_index(drop=True)
    for column in ("time", "open", "high", "low", "close", "volume"):
        if column not in result.columns:
            result[column] = 0.0
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
    times = result["time"].to_numpy(dtype=float)
    if len(times) and float(np.nanmax(np.abs(times))) > 10_000_000_000:
        result["time"] = times / 1000.0
    return result


def rolling_mean(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(length - 1, len(source)):
        window = source[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def rolling_std(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(length - 1, len(source)):
        window = source[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.std(window, ddof=0))
    return result


def exponential_average(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) < length:
        return result
    seed_index = -1
    for index in range(length - 1, len(source)):
        if np.all(np.isfinite(source[index - length + 1:index + 1])):
            seed_index = index
            break
    if seed_index < 0:
        return result
    result[seed_index] = float(np.mean(source[seed_index - length + 1:seed_index + 1]))
    alpha = 2.0 / (length + 1.0)
    for index in range(seed_index + 1, len(source)):
        if np.isfinite(source[index]):
            result[index] = alpha * source[index] + (1.0 - alpha) * result[index - 1]
        else:
            result[index] = result[index - 1]
    return result


def wilder_average(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) < length:
        return result
    seed_index = -1
    for index in range(length - 1, len(source)):
        if np.all(np.isfinite(source[index - length + 1:index + 1])):
            seed_index = index
            break
    if seed_index < 0:
        return result
    result[seed_index] = float(np.mean(source[seed_index - length + 1:seed_index + 1]))
    for index in range(seed_index + 1, len(source)):
        if np.isfinite(source[index]):
            result[index] = (result[index - 1] * (length - 1) + source[index]) / length
        else:
            result[index] = result[index - 1]
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


def volume_weighted_average(values, volumes, length):
    source = np.asarray(values, dtype=float)
    volume_values = np.asarray(volumes, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(length - 1, len(source)):
        price_window = source[index - length + 1:index + 1]
        volume_window = volume_values[index - length + 1:index + 1]
        denominator = float(np.sum(volume_window))
        if np.all(np.isfinite(price_window)) and np.all(np.isfinite(volume_window)) and denominator > 1e-12:
            result[index] = float(np.sum(price_window * volume_window) / denominator)
    return result


def kaufman_average(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    previous = 0.0
    fast = 0.666
    slow = 0.0645
    for index in range(len(source)):
        if not np.isfinite(source[index]):
            continue
        if index < length:
            previous = previous + slow * slow * (source[index] - previous)
        else:
            signal = abs(source[index] - source[index - length])
            noise = float(np.sum(np.abs(np.diff(source[index - length:index + 1]))))
            efficiency = signal / noise if noise > 1e-12 else 0.0
            smoothing = (efficiency * (fast - slow) + slow) ** 2
            previous = previous + smoothing * (source[index] - previous)
        result[index] = previous
    return result


def zero_seed_ema(values, alpha):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    previous = 0.0
    for index in range(len(source)):
        if np.isfinite(source[index]):
            previous = alpha * source[index] + (1.0 - alpha) * previous
            result[index] = previous
    return result


def moving_average(values, volumes, length, method):
    source = np.asarray(values, dtype=float)
    if method in ("RMA", "SMMA"):
        return wilder_average(source, length)
    if method == "EMA":
        return exponential_average(source, length)
    if method == "WMA":
        return weighted_average(source, length)
    if method == "VWMA":
        return volume_weighted_average(source, volumes, length)
    if method == "KMA":
        return kaufman_average(source, length)
    if method == "TMA":
        return rolling_mean(rolling_mean(source, length), length)
    if method == "HullMA":
        half = weighted_average(source, max(1, int(round(length / 2.0))))
        whole = weighted_average(source, length)
        return weighted_average(2.0 * half - whole, max(1, int(round(np.sqrt(length)))))
    if method == "DEMA":
        first = exponential_average(source, length)
        second = exponential_average(first, length)
        return 2.0 * first - second
    if method == "TEMA":
        first = exponential_average(source, length)
        second = exponential_average(first, length)
        third = exponential_average(second, length)
        return 3.0 * first - 3.0 * second + third
    if method == "CTI":
        di = (length - 1.0) / 2.0 + 1.0
        alpha = 2.0 / (di + 1.0)
        return zero_seed_ema(zero_seed_ema(zero_seed_ema(source, alpha), alpha), alpha)
    return rolling_mean(source, length)


def true_range_average(high_values, low_values, close_values, length):
    highs = np.asarray(high_values, dtype=float)
    lows = np.asarray(low_values, dtype=float)
    closes = np.asarray(close_values, dtype=float)
    ranges = np.full(len(closes), np.nan, dtype=float)
    for index in range(len(closes)):
        if index == 0:
            ranges[index] = highs[index] - lows[index]
        else:
            ranges[index] = max(
                highs[index] - lows[index],
                abs(highs[index] - closes[index - 1]),
                abs(lows[index] - closes[index - 1]),
            )
    return wilder_average(ranges, length)


def follow_line(close_values, high_values, low_values, basis, deviation, atr_values, use_offset):
    closes = np.asarray(close_values, dtype=float)
    highs = np.asarray(high_values, dtype=float)
    lows = np.asarray(low_values, dtype=float)
    basis_values = np.asarray(basis, dtype=float)
    dispersion = rolling_std(closes, len_window)
    upper_band = basis_values + dispersion * deviation
    lower_band = basis_values - dispersion * deviation
    trail = np.full(len(closes), np.nan, dtype=float)
    trend = np.zeros(len(closes), dtype=int)
    previous_trail = 0.0
    previous_trend = 0
    for index in range(len(closes)):
        signal = 0
        if np.isfinite(upper_band[index]) and closes[index] > upper_band[index]:
            signal = 1
        elif np.isfinite(lower_band[index]) and closes[index] < lower_band[index]:
            signal = -1

        current_trail = previous_trail
        if signal == 1:
            candidate = lows[index] - atr_values[index] if use_offset and np.isfinite(atr_values[index]) else lows[index]
            current_trail = max(previous_trail, float(candidate))
        elif signal == -1:
            candidate = highs[index] + atr_values[index] if use_offset and np.isfinite(atr_values[index]) else highs[index]
            current_trail = min(previous_trail, float(candidate))

        if current_trail > previous_trail:
            previous_trend = 1
        elif current_trail < previous_trail:
            previous_trend = -1
        trail[index] = current_trail
        trend[index] = previous_trend
        previous_trail = current_trail
    return trail, trend


def line_points(values, colors):
    points = []
    for index, value in enumerate(values):
        if np.isfinite(value):
            points.append({"value": float(value), "color": colors[index]})
        else:
            points.append(None)
    return points


def marker_points(mask, values, color, size=5.0):
    data = []
    for index, active in enumerate(mask):
        if bool(active) and np.isfinite(values[index]):
            data.append({"value": float(values[index]), "color": color, "size": float(size)})
        else:
            data.append(None)
    return data


base_frame = normalized_frame(df)
base_time = base_frame["time"].to_numpy(dtype=float)
base_high = base_frame["high"].to_numpy(dtype=float)
base_low = base_frame["low"].to_numpy(dtype=float)
base_close = base_frame["close"].to_numpy(dtype=float)
base_volume = base_frame["volume"].to_numpy(dtype=float)
n = len(base_frame)

len_window = bb_period
base_basis = rolling_mean(base_close, bb_period)
base_atr = true_range_average(base_high, base_low, base_close, atr_period)
current_trail, current_trend = follow_line(
    base_close, base_high, base_low, base_basis, bb_deviation, base_atr, use_atr_filter
)

runtime_context = ctx
chart_timeframe = str(runtime_context.timeframe or "1H")
chart_seconds = timeframe_seconds.get(chart_timeframe, 3600)
if higher_timeframe == chart_timeframe:
    higher_frame = base_frame.copy()
else:
    requested_frame = runtime_context.raw_series(higher_timeframe) if runtime_context is not None and hasattr(runtime_context, "raw_series") else None
    higher_frame = normalized_frame(requested_frame if requested_frame is not None and len(requested_frame) else base_frame)
higher_seconds = timeframe_seconds.get(higher_timeframe, 14400)
higher_time = higher_frame["time"].to_numpy(dtype=float)
higher_high = higher_frame["high"].to_numpy(dtype=float)
higher_low = higher_frame["low"].to_numpy(dtype=float)
higher_close = higher_frame["close"].to_numpy(dtype=float)
higher_volume = higher_frame["volume"].to_numpy(dtype=float)

len_window = higher_period
higher_basis = moving_average(higher_close, higher_volume, higher_period, higher_ma_type)
higher_atr = true_range_average(higher_high, higher_low, higher_close, higher_atr_period)
higher_trail, higher_trend_source = follow_line(
    higher_close, higher_high, higher_low, higher_basis, higher_deviation, higher_atr, True
)

base_close_time = base_time + chart_seconds
higher_close_time = higher_time + higher_seconds
higher_trend = np.zeros(n, dtype=int)
for index in range(n):
    source_index = int(np.searchsorted(higher_close_time, base_close_time[index], side="right") - 1)
    if 0 <= source_index < len(higher_trend_source):
        higher_trend[index] = int(higher_trend_source[source_index])

angle_atr = true_range_average(base_high, base_low, base_close, angle_atr_period)
angle = np.full(n, np.nan, dtype=float)
for index in range(angle_period, n):
    denominator = angle_atr[index]
    if np.isfinite(current_trail[index]) and np.isfinite(current_trail[index - angle_period]) and np.isfinite(denominator) and denominator > 1e-12:
        angle[index] = float(np.degrees(np.arctan((current_trail[index] - current_trail[index - angle_period]) / denominator)))

filter_enabled = filter_mode == "Higher_Timeframe"
buy_event = np.zeros(n, dtype=bool)
sell_event = np.zeros(n, dtype=bool)
add_buy_event = np.zeros(n, dtype=bool)
add_sell_event = np.zeros(n, dtype=bool)
reduce_buy_event = np.zeros(n, dtype=bool)
reduce_sell_event = np.zeros(n, dtype=bool)


def near_zero(value):
    return np.isfinite(value) and abs(value) <= 1e-12


def flat_after_direction(index, bars, positive):
    if index < bars:
        return False
    directional_value = angle[index - bars]
    if not np.isfinite(directional_value):
        return False
    if positive and directional_value <= 0.0:
        return False
    if not positive and directional_value >= 0.0:
        return False
    for offset in range(bars):
        if not near_zero(angle[index - offset]):
            return False
    return True


for index in range(1, n):
    filter_long = not filter_enabled or higher_trend[index] == 1
    filter_short = not filter_enabled or higher_trend[index] == -1
    buy_event[index] = current_trend[index - 1] < 0 and current_trend[index] > 0 and filter_long
    sell_event[index] = current_trend[index - 1] > 0 and current_trend[index] < 0 and filter_short

    if show_add_signals:
        add_buy = flat_after_direction(index, 2, True)
        add_sell = flat_after_direction(index, 2, False)
        if add_after_5_flat_bars:
            add_buy = add_buy or flat_after_direction(index, 5, True)
            add_sell = add_sell or flat_after_direction(index, 5, False)
        if add_after_8_flat_bars:
            add_buy = add_buy or flat_after_direction(index, 8, True)
            add_sell = add_sell or flat_after_direction(index, 8, False)
        add_buy_event[index] = add_buy and current_trend[index] > 0 and filter_long
        add_sell_event[index] = add_sell and current_trend[index] < 0 and filter_short

    if show_reduce_signals and np.isfinite(angle[index]) and np.isfinite(angle[index - 1]):
        reduce_buy = False
        reduce_sell = False
        if reduce_level_1:
            reduce_buy = reduce_buy or (angle[index - 1] < max_angle_level_1 and angle[index] > max_angle_level_1)
            reduce_sell = reduce_sell or (angle[index - 1] > min_angle_level_1 and angle[index] < min_angle_level_1)
        if reduce_level_2:
            reduce_buy = reduce_buy or (angle[index - 1] < max_angle_level_2 and angle[index] > max_angle_level_2)
            reduce_sell = reduce_sell or (angle[index - 1] > min_angle_level_2 and angle[index] < min_angle_level_2)
        streak_settings = ((2, reduce_after_2_bars), (3, reduce_after_3_bars), (4, reduce_after_4_bars))
        for streak, enabled in streak_settings:
            if not enabled or index < streak:
                continue
            earlier = angle[index - streak]
            recent = angle[index - streak + 1:index + 1]
            if np.isfinite(earlier) and np.all(np.isfinite(recent)):
                reduce_buy = reduce_buy or (earlier < max_angle_level_2 and bool(np.all(recent > max_angle_level_2)))
                reduce_sell = reduce_sell or (earlier > min_angle_level_2 and bool(np.all(recent < min_angle_level_2)))
        reduce_buy_event[index] = reduce_buy and filter_long
        reduce_sell_event[index] = reduce_sell and filter_short

trend_colors = [bullish_color if current_trend[index] > 0 else bearish_color for index in range(n)]
histogram_data = []
for index in range(n):
    if np.isfinite(angle[index]):
        histogram_data.append({"value": float(angle[index]), "color": trend_colors[index]})
    else:
        histogram_data.append(None)

background_colors = []
for index in range(n):
    if not show_background or higher_trend[index] == 0:
        background_colors.append(None)
    elif higher_trend[index] > 0:
        background_colors.append("rgba(41,98,255,0.10)")
    else:
        background_colors.append("rgba(242,54,69,0.10)")
bar_colors = trend_colors if color_price_bars else [None for _ in range(n)]

event_plots = []
if show_labels:
    event_plots.extend([
        {"name": "BUY", "data": marker_points(buy_event, np.full(n, -5.0), bullish_color, 6.0), "color": bullish_color, "type": "scatter", "overlay": False},
        {"name": "SELL", "data": marker_points(sell_event, np.full(n, 5.0), bearish_color, 6.0), "color": bearish_color, "type": "scatter", "overlay": False},
    ])
    if show_add_signals:
        event_plots.extend([
            {"name": "ADD BUY", "data": marker_points(add_buy_event, angle - 5.0, bullish_color, 5.0), "color": bullish_color, "type": "scatter", "overlay": False},
            {"name": "ADD SELL", "data": marker_points(add_sell_event, angle + 5.0, bearish_color, 5.0), "color": bearish_color, "type": "scatter", "overlay": False},
        ])
    if show_reduce_signals:
        event_plots.extend([
            {"name": "REDUCE BUY", "data": marker_points(reduce_buy_event, angle + 5.0, bullish_color, 5.0), "color": bullish_color, "type": "scatter", "overlay": False},
            {"name": "REDUCE SELL", "data": marker_points(reduce_sell_event, angle - 5.0, bearish_color, 5.0), "color": bearish_color, "type": "scatter", "overlay": False},
        ])

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Angle Follow Line", "data": line_points(angle, trend_colors), "color": bullish_color, "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "Angle Histogram", "data": histogram_data, "color": bullish_color, "type": "histogram", "baseValue": 0.0, "lineWidth": 2, "overlay": False},
        {"name": "Max Angle Level 1", "data": [max_angle_level_1 for _ in range(n)], "color": bullish_color, "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": False},
        {"name": "Max Angle Level 2", "data": [max_angle_level_2 for _ in range(n)], "color": bullish_color, "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": False},
        {"name": "Min Angle Level 1", "data": [min_angle_level_1 for _ in range(n)], "color": bearish_color, "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": False},
        {"name": "Min Angle Level 2", "data": [min_angle_level_2 for _ in range(n)], "color": bearish_color, "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": False},
    ] + event_plots,
    "signals": [],
    "tables": [],
    "bar_colors": bar_colors,
    "background_colors": background_colors,
}
