# ============================================================
# QuantDinger ATR Percentile Bands
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger ATR Percentile Bands"
my_indicator_description = (
    "Builds volatility-adaptive inner and outer extremes from smoothed ATR and the rolling "
    "95th percentile of price deviation, with three-bar confirmed reversal signals and a decision table."
)

# @param moving_average_type str EMA Moving average type values=EMA,SMA,RMA,WMA,HMA
# @param moving_average_period int 50 Moving average period range=2:500:1
# @param atr_period int 14 ATR period range=2:500:1
# @param atr_multiplier float 2.8 ATR multiplier range=0.5:10.0:0.1
# @param smooth_period int 5 Smoothing period range=1:100:1
# @param signal_gap int 10 Minimum bars between signals range=0:500:1
# @param show_moving_average bool true Show moving average
# @param show_signals bool true Show confirmed signals
# @param show_table bool true Show decision table
# @param table_size str Small Table size values=Tiny,Small,Normal,Large
# @param table_position str Top_Right Table position values=Top_Left,Top_Center,Top_Right,Middle_Left,Middle_Center,Middle_Right,Bottom_Left,Bottom_Center,Bottom_Right
# @param alert_enabled bool true Enable signal events
# @param message_frequency str Once_Per_Bar_Close Message frequency values=All,Once_Per_Bar,Once_Per_Bar_Close

moving_average_type = str(params.get("moving_average_type", "EMA"))
if moving_average_type not in ("EMA", "SMA", "RMA", "WMA", "HMA"):
    moving_average_type = "EMA"
moving_average_period = min(500, max(2, int(params.get("moving_average_period", 50))))
atr_period = min(500, max(2, int(params.get("atr_period", 14))))
atr_multiplier = min(10.0, max(0.5, float(params.get("atr_multiplier", 2.8))))
smooth_period = min(100, max(1, int(params.get("smooth_period", 5))))
signal_gap = min(500, max(0, int(params.get("signal_gap", 10))))
show_moving_average = bool(params.get("show_moving_average", True))
show_signals = bool(params.get("show_signals", True))
show_table = bool(params.get("show_table", True))
table_size = str(params.get("table_size", "Small"))
if table_size not in ("Tiny", "Small", "Normal", "Large"):
    table_size = "Small"
table_position = str(params.get("table_position", "Top_Right"))
alert_enabled = bool(params.get("alert_enabled", True))
message_frequency = str(params.get("message_frequency", "Once_Per_Bar_Close"))
if message_frequency not in ("All", "Once_Per_Bar", "Once_Per_Bar_Close"):
    message_frequency = "Once_Per_Bar_Close"

upper_color = "#E91E63"
lower_color = "#00C6A2"
average_color = "#A8B0BE"
long_signal_color = "#00C600"
short_signal_color = "#E91E1E"
positive_state_color = "#26D6AD"
negative_state_color = "#F16B82"
neutral_state_color = "#B5C3D8"
info_state_color = "#56CFE0"
sideways_state_color = "#FFBF69"

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


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
    finite_indexes = np.flatnonzero(np.isfinite(source))
    if len(finite_indexes) < length:
        return result
    seed_end = int(finite_indexes[length - 1])
    seed_values = source[finite_indexes[:length]]
    result[seed_end] = float(np.mean(seed_values))
    alpha = 2.0 / (length + 1.0)
    for index in range(seed_end + 1, len(source)):
        if np.isfinite(source[index]) and np.isfinite(result[index - 1]):
            result[index] = alpha * source[index] + (1.0 - alpha) * result[index - 1]
        elif np.isfinite(result[index - 1]):
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
    if method == "RMA":
        return wilder_average(values, length)
    if method == "WMA":
        return weighted_average(values, length)
    if method == "HMA":
        half_length = max(1, int(round(length / 2.0)))
        root_length = max(1, int(round(np.sqrt(length))))
        return weighted_average(2.0 * weighted_average(values, half_length) - weighted_average(values, length), root_length)
    return exponential_average(values, length)


def rolling_percentile(values, length, percentile):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(length - 1, len(source)):
        window = source[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.percentile(window, percentile, method="linear"))
    return result


def rolling_percent_rank(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(length - 1, len(source)):
        window = source[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.sum(window <= source[index]) * 100.0 / length)
    return result


def finite_line(values, visible=True):
    if not visible:
        return [None for _ in range(len(values))]
    return [float(value) if np.isfinite(value) else None for value in values]


def cloud_points(upper_values, lower_values, color):
    return [
        {"upper": float(max(upper, lower)), "lower": float(min(upper, lower)), "color": color}
        if np.isfinite(upper) and np.isfinite(lower) else None
        for upper, lower in zip(upper_values, lower_values)
    ]


true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(
            high[index] - low[index],
            abs(high[index] - close[index - 1]),
            abs(low[index] - close[index - 1]),
        )

average = moving_average(close, moving_average_period, moving_average_type)
raw_atr = wilder_average(true_range, atr_period)
atr = exponential_average(raw_atr, smooth_period)
atr_distance = atr * atr_multiplier
price_deviation = np.abs(close - average)
percentile_distance = rolling_percentile(price_deviation, 200, 95.0)
raw_outer_distance = np.where(np.isfinite(percentile_distance), np.maximum(atr_distance, percentile_distance), atr_distance)
outer_distance = exponential_average(raw_outer_distance, smooth_period)
inner_distance = outer_distance * 0.62
volatility_rank = rolling_percent_rank(atr, 200)

upper_inner = average + inner_distance
upper_outer = average + outer_distance
lower_inner = average - inner_distance
lower_outer = average - outer_distance
upper_stop_1 = upper_inner + (upper_outer - upper_inner) * 0.25
upper_stop_2 = upper_inner + (upper_outer - upper_inner) * 0.50
upper_stop_3 = upper_inner + (upper_outer - upper_inner) * 0.75
lower_stop_1 = lower_inner + (lower_outer - lower_inner) * 0.25
lower_stop_2 = lower_inner + (lower_outer - lower_inner) * 0.50
lower_stop_3 = lower_inner + (lower_outer - lower_inner) * 0.75

long_setup = np.isfinite(lower_inner) & (low <= lower_inner) & (close > lower_inner) & (close > open_price)
short_setup = np.isfinite(upper_inner) & (high >= upper_inner) & (close < upper_inner) & (close < open_price)
long_signal = np.zeros(n, dtype=bool)
short_signal = np.zeros(n, dtype=bool)
pending_history = np.zeros(n, dtype=int)
confirmation_history = np.zeros(n, dtype=int)
pending_direction = 0
confirmation_count = 0
last_published_signal = None
for index in range(n):
    gap_qualified = last_published_signal is None or index - last_published_signal >= signal_gap
    if pending_direction == 0:
        if gap_qualified and long_setup[index]:
            pending_direction = 1
            confirmation_count = 1
        elif gap_qualified and short_setup[index]:
            pending_direction = -1
            confirmation_count = 1
    elif pending_direction == 1:
        if short_setup[index]:
            pending_direction = -1
            confirmation_count = 1
        elif np.isfinite(lower_inner[index]) and close[index] > lower_inner[index] and close[index] > open_price[index]:
            confirmation_count += 1
        else:
            pending_direction = 0
            confirmation_count = 0
    else:
        if long_setup[index]:
            pending_direction = 1
            confirmation_count = 1
        elif np.isfinite(upper_inner[index]) and close[index] < upper_inner[index] and close[index] < open_price[index]:
            confirmation_count += 1
        else:
            pending_direction = 0
            confirmation_count = 0

    if pending_direction == 1 and confirmation_count >= 3:
        long_signal[index] = True
        last_published_signal = index
        pending_direction = 0
        confirmation_count = 0
    elif pending_direction == -1 and confirmation_count >= 3:
        short_signal[index] = True
        last_published_signal = index
        pending_direction = 0
        confirmation_count = 0

    pending_history[index] = pending_direction
    confirmation_history[index] = confirmation_count

plots = [
    {"name": "Upper Band Inner", "data": finite_line(upper_inner), "color": rgba(upper_color, 0.18), "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Upper Band Center", "data": finite_line(upper_stop_2), "color": rgba(upper_color, 0.88), "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Upper Band Outer", "data": finite_line(upper_outer), "color": rgba(upper_color, 0.96), "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Lower Band Inner", "data": finite_line(lower_inner), "color": rgba(lower_color, 0.18), "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Lower Band Center", "data": finite_line(lower_stop_2), "color": rgba(lower_color, 0.88), "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Lower Band Outer", "data": finite_line(lower_outer), "color": rgba(lower_color, 0.96), "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Moving Average", "data": finite_line(average, show_moving_average), "color": rgba(average_color, 0.45), "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Upper Layer 1", "title": False, "data": cloud_points(upper_inner, upper_stop_1, rgba(upper_color, 0.06)), "type": "cloud", "opacity": 0.06, "overlay": True},
    {"name": "Upper Layer 2", "title": False, "data": cloud_points(upper_stop_1, upper_stop_2, rgba(upper_color, 0.14)), "type": "cloud", "opacity": 0.14, "overlay": True},
    {"name": "Upper Layer 3", "title": False, "data": cloud_points(upper_stop_2, upper_stop_3, rgba(upper_color, 0.27)), "type": "cloud", "opacity": 0.27, "overlay": True},
    {"name": "Upper Layer 4", "title": False, "data": cloud_points(upper_stop_3, upper_outer, rgba(upper_color, 0.30)), "type": "cloud", "opacity": 0.30, "overlay": True},
    {"name": "Lower Layer 1", "title": False, "data": cloud_points(lower_inner, lower_stop_1, rgba(lower_color, 0.06)), "type": "cloud", "opacity": 0.06, "overlay": True},
    {"name": "Lower Layer 2", "title": False, "data": cloud_points(lower_stop_1, lower_stop_2, rgba(lower_color, 0.14)), "type": "cloud", "opacity": 0.14, "overlay": True},
    {"name": "Lower Layer 3", "title": False, "data": cloud_points(lower_stop_2, lower_stop_3, rgba(lower_color, 0.27)), "type": "cloud", "opacity": 0.27, "overlay": True},
    {"name": "Lower Layer 4", "title": False, "data": cloud_points(lower_stop_3, lower_outer, rgba(lower_color, 0.30)), "type": "cloud", "opacity": 0.30, "overlay": True},
]

long_values = [float(low[index]) if show_signals and long_signal[index] else None for index in range(n)]
short_values = [float(high[index]) if show_signals and short_signal[index] else None for index in range(n)]
signals = [
    {"type": "buy", "name": "Long Signal", "text": "Long Signal", "color": long_signal_color, "renderMode": "events", "enabled": alert_enabled, "data": long_values},
    {"type": "sell", "name": "Short Signal", "text": "Short Signal", "color": short_signal_color, "renderMode": "events", "enabled": alert_enabled, "data": short_values},
]

position_map = {
    "Top_Left": "top-left", "Top_Center": "top-right", "Top_Right": "top-right",
    "Middle_Left": "top-left", "Middle_Center": "top-right", "Middle_Right": "top-right",
    "Bottom_Left": "bottom-left", "Bottom_Center": "bottom-right", "Bottom_Right": "bottom-right",
}
tables = []
if show_table and n:
    index = n - 1
    pending = int(pending_history[index])
    market_state = "Buy Signal" if long_signal[index] else "Sell Signal" if short_signal[index] else "Buy Setup" if pending == 1 else "Sell Setup" if pending == -1 else "No Signal"
    market_color = positive_state_color if long_signal[index] or pending == 1 else negative_state_color if short_signal[index] or pending == -1 else neutral_state_color
    if np.isfinite(upper_outer[index]) and close[index] > upper_outer[index]:
        extreme_zone = "Above Upper Band"
    elif np.isfinite(upper_inner[index]) and close[index] >= upper_inner[index]:
        extreme_zone = "Upper Band"
    elif np.isfinite(lower_outer[index]) and close[index] < lower_outer[index]:
        extreme_zone = "Below Lower Band"
    elif np.isfinite(lower_inner[index]) and close[index] <= lower_inner[index]:
        extreme_zone = "Lower Band"
    elif np.isfinite(average[index]) and close[index] >= average[index]:
        extreme_zone = "Above Average"
    else:
        extreme_zone = "Below Average"
    zone_color = negative_state_color if np.isfinite(upper_inner[index]) and close[index] >= upper_inner[index] else positive_state_color if np.isfinite(lower_inner[index]) and close[index] <= lower_inner[index] else info_state_color
    bullish_trend = index > 0 and np.isfinite(average[index]) and np.isfinite(average[index - 1]) and close[index] > average[index] and average[index] > average[index - 1]
    bearish_trend = index > 0 and np.isfinite(average[index]) and np.isfinite(average[index - 1]) and close[index] < average[index] and average[index] < average[index - 1]
    trend_state = "Bullish" if bullish_trend else "Bearish" if bearish_trend else "Sideways"
    trend_color = positive_state_color if bullish_trend else negative_state_color if bearish_trend else sideways_state_color
    bands_ready = np.isfinite(upper_inner[index]) and np.isfinite(lower_inner[index])
    if not bands_ready or close[index] == 0.0:
        nearest_band = "Loading"
        nearest_color = neutral_state_color
    else:
        upper_distance = abs(upper_inner[index] - close[index]) / abs(close[index]) * 100.0
        lower_distance = abs(close[index] - lower_inner[index]) / abs(close[index]) * 100.0
        if close[index] >= upper_inner[index]:
            nearest_band, nearest_color = "At Upper Band", negative_state_color
        elif close[index] <= lower_inner[index]:
            nearest_band, nearest_color = "At Lower Band", positive_state_color
        elif upper_distance <= lower_distance:
            nearest_band, nearest_color = f"{upper_distance:.2f}% to Upper", negative_state_color
        else:
            nearest_band, nearest_color = f"{lower_distance:.2f}% to Lower", positive_state_color
    if not np.isfinite(volatility_rank[index]):
        volatility_state, volatility_color = "Loading", neutral_state_color
    elif volatility_rank[index] >= 80.0:
        volatility_state, volatility_color = "High", negative_state_color
    elif volatility_rank[index] <= 20.0:
        volatility_state, volatility_color = "Low", info_state_color
    else:
        volatility_state, volatility_color = "Normal", positive_state_color
    next_step = "Buy Confirmed" if long_signal[index] else "Sell Confirmed" if short_signal[index] else "Wait for Buy" if pending == 1 else "Wait for Sell" if pending == -1 else "Look for Sell" if bands_ready and close[index] >= upper_inner[index] else "Look for Buy" if bands_ready and close[index] <= lower_inner[index] else "Wait"
    next_color = positive_state_color if long_signal[index] or pending == 1 or (bands_ready and close[index] <= lower_inner[index]) else negative_state_color if short_signal[index] or pending == -1 or (bands_ready and close[index] >= upper_inner[index]) else neutral_state_color
    rows = [
        ["Signal", {"text": market_state, "color": market_color}],
        ["Price Zone", {"text": extreme_zone, "color": zone_color}],
        ["Trend", {"text": trend_state, "color": trend_color}],
        ["Nearest Band", {"text": nearest_band, "color": nearest_color}],
        ["Volatility", {"text": volatility_state, "color": volatility_color}],
        ["Next Action", {"text": next_step, "color": next_color}],
    ]
    tables.append({
        "title": "DYNAMIC EXTREMES",
        "position": position_map.get(table_position, "top-right"),
        "compact": table_size in ("Tiny", "Small"),
        "borderColor": "#1C306D",
        "accentColor": info_state_color,
        "rows": rows,
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "tables": tables,
}
