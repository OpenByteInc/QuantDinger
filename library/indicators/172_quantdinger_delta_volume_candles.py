# ============================================================
# QuantDinger Delta Volume Candles
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Delta Volume Candles"
my_indicator_description = (
    "Builds price-candle coloring, volume-pressure extensions, balance signals, and divergence "
    "levels from lower-timeframe buy and sell volume."
)

indicator_lower_timeframes = ["1m", "3m", "5m", "15m", "30m", "1H"]
indicator_lower_timeframe_param = "lower_timeframe"

# @param lower_timeframe str 1m Intrabar timeframe values=1m,3m,5m,15m,30m,1H
# @param body_mode str Price Candle color source values=Off,Price,Delta,Average,Momentum,Relative,Percent,Composite
# @param body_gradient bool false Use signal-strength candle gradient
# @param hollow_on_lower_volume bool true Keep candle body unchanged when volume falls
# @param up_color str #16A34A Bull candle color
# @param down_color str #B91C1C Bear candle color
# @param wick_up_color str #22C55E Bull extension color
# @param wick_down_color str #EF4444 Bear extension color
# @param body_divergence_mode str Off Candle divergence source values=Off,Delta,Average,Momentum,Relative,Percent,Composite
# @param divergence_color str #FACC15 Divergence candle color
# @param pressure_mode str Delta_Winner Pressure extension mode values=Off,Split,Delta_Winner,Composite_Winner
# @param pressure_reference str Average_Body Extension reference values=Body,Average_Body
# @param show_pressure_average bool false Show extension averages
# @param pressure_average_length int 14 Extension average length range=1:500:1
# @param pressure_average_up_color str #15803D Bull extension average color
# @param pressure_average_down_color str #991B1B Bear extension average color
# @param pressure_cloud_mode str Delta Average cloud color source values=Delta,Average,Momentum,Relative,Percent,Composite
# @param divergence_channel_mode str Off Divergence channel trigger values=Off,Body,Pressure,Both
# @param divergence_channel_source str Body Channel level source values=Full_Range,Pressure_Range,Body,High_Low
# @param divergence_breach_source str Close Channel breach source values=Close,Body,High_Low,Pressure_Range
# @param divergence_channel_fill bool true Fill divergence channel
# @param channel_bull_color str #16A34A Bull divergence channel color
# @param channel_bear_color str #B91C1C Bear divergence channel color
# @param channel_neutral_color str #6B7280 Neutral divergence channel color
# @param background_mode str Off Background color source values=Off,Delta,Average,Momentum,Relative,Percent,Composite
# @param show_volume_discrepancy bool true Highlight intrabar/chart volume mismatch
# @param discrepancy_tolerance float 1.0 Volume mismatch tolerance percent range=0.0:100.0:0.25
# @param discrepancy_color str #EF5350 Volume mismatch color
# @param marker_direction str Both Marker direction values=Both,Longs_Only,Shorts_Only
# @param show_bump_marker bool false Show volume bump markers
# @param show_double_bump_marker bool false Show double bump markers
# @param show_divergence_confirmation bool false Show divergence confirmation markers
# @param show_balance_shift bool false Show balance zero-cross markers
# @param balance_shift_mode str Average Balance used for shifts values=Delta,Average,Momentum,Relative,Percent,Composite
# @param show_bias_shift bool false Show marker-bias shifts
# @param show_full_agreement bool false Show full balance agreement
# @param average_length int 50 Average balance length range=2:500:1
# @param momentum_length int 14 Momentum balance length range=2:500:1
# @param relative_length int 14 Relative balance length range=1:500:1
# @param percent_length int 14 Percent balance length range=1:500:1
# @param bias_length int 14 Marker bias length range=2:500:1
# @param show_delta_pane bool true Show volume delta percent pane
# @param show_info_table bool true Show intrabar statistics
# @param table_position str top-right Statistics table position values=top-left,top-right,bottom-left,bottom-right

lower_timeframe = str(params.get("lower_timeframe", "1m"))
if lower_timeframe not in ("1m", "3m", "5m", "15m", "30m", "1H"):
    lower_timeframe = "1m"
body_mode = str(params.get("body_mode", "Price"))
body_gradient = bool(params.get("body_gradient", False))
hollow_on_lower_volume = bool(params.get("hollow_on_lower_volume", True))
up_color = str(params.get("up_color", "#16A34A"))
down_color = str(params.get("down_color", "#B91C1C"))
wick_up_color = str(params.get("wick_up_color", "#22C55E"))
wick_down_color = str(params.get("wick_down_color", "#EF4444"))
body_divergence_mode = str(params.get("body_divergence_mode", "Off"))
divergence_color = str(params.get("divergence_color", "#FACC15"))
pressure_mode = str(params.get("pressure_mode", "Delta_Winner"))
pressure_reference = str(params.get("pressure_reference", "Average_Body"))
show_pressure_average = bool(params.get("show_pressure_average", False))
pressure_average_length = min(500, max(1, int(params.get("pressure_average_length", 14))))
pressure_average_up_color = str(params.get("pressure_average_up_color", "#15803D"))
pressure_average_down_color = str(params.get("pressure_average_down_color", "#991B1B"))
pressure_cloud_mode = str(params.get("pressure_cloud_mode", "Delta"))
divergence_channel_mode = str(params.get("divergence_channel_mode", "Off"))
divergence_channel_source = str(params.get("divergence_channel_source", "Body"))
divergence_breach_source = str(params.get("divergence_breach_source", "Close"))
divergence_channel_fill = bool(params.get("divergence_channel_fill", True))
channel_bull_color = str(params.get("channel_bull_color", "#16A34A"))
channel_bear_color = str(params.get("channel_bear_color", "#B91C1C"))
channel_neutral_color = str(params.get("channel_neutral_color", "#6B7280"))
background_mode = str(params.get("background_mode", "Off"))
show_volume_discrepancy = bool(params.get("show_volume_discrepancy", True))
discrepancy_tolerance = min(100.0, max(0.0, float(params.get("discrepancy_tolerance", 1.0)))) / 100.0
discrepancy_color = str(params.get("discrepancy_color", "#EF5350"))
marker_direction = str(params.get("marker_direction", "Both"))
show_bump_marker = bool(params.get("show_bump_marker", False))
show_double_bump_marker = bool(params.get("show_double_bump_marker", False))
show_divergence_confirmation = bool(params.get("show_divergence_confirmation", False))
show_balance_shift = bool(params.get("show_balance_shift", False))
balance_shift_mode = str(params.get("balance_shift_mode", "Average"))
show_bias_shift = bool(params.get("show_bias_shift", False))
show_full_agreement = bool(params.get("show_full_agreement", False))
average_length = min(500, max(2, int(params.get("average_length", 50))))
momentum_length = min(500, max(2, int(params.get("momentum_length", 14))))
relative_length = min(500, max(1, int(params.get("relative_length", 14))))
percent_length = min(500, max(1, int(params.get("percent_length", 14))))
bias_length = min(500, max(2, int(params.get("bias_length", 14))))
show_delta_pane = bool(params.get("show_delta_pane", True))
show_info_table = bool(params.get("show_info_table", True))
table_position = str(params.get("table_position", "top-right"))
if table_position not in ("top-left", "top-right", "bottom-left", "bottom-right"):
    table_position = "top-right"

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)

def sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    finite_values = np.asarray(values, dtype=float)
    for index in range(length - 1, len(finite_values)):
        window = finite_values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result

def ema(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    finite_indexes = np.flatnonzero(np.isfinite(source))
    if len(finite_indexes) == 0:
        return result
    alpha = 2.0 / (length + 1.0)
    state = float(source[finite_indexes[0]])
    result[finite_indexes[0]] = state
    for index in range(finite_indexes[0] + 1, len(source)):
        if np.isfinite(source[index]):
            state = alpha * float(source[index]) + (1.0 - alpha) * state
        result[index] = state
    return result

def alma(values, length, offset=0.85, sigma=6.0):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    center = offset * (length - 1)
    width = length / sigma
    weights = np.exp(-((np.arange(length, dtype=float) - center) ** 2) / (2.0 * width * width))
    weights = weights / np.sum(weights)
    for index in range(length - 1, len(source)):
        window = source[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights))
    return result

def rsi(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) <= length:
        return result
    differences = np.diff(source, prepend=source[0])
    gains = np.maximum(differences, 0.0)
    losses = np.maximum(-differences, 0.0)
    avg_gain = float(np.mean(gains[1:length + 1]))
    avg_loss = float(np.mean(losses[1:length + 1]))
    result[length] = 100.0 if avg_loss <= 1e-12 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    for index in range(length + 1, len(source)):
        avg_gain = (avg_gain * (length - 1) + gains[index]) / length
        avg_loss = (avg_loss * (length - 1) + losses[index]) / length
        result[index] = 100.0 if avg_loss <= 1e-12 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return result

def rolling_sum(values, length):
    source = np.asarray(values, dtype=float)
    result = np.zeros(len(source), dtype=float)
    running = 0.0
    for index in range(len(source)):
        running += source[index]
        if index >= length:
            running -= source[index - length]
        result[index] = running
    return result

def forward_fill(values, fallback):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), fallback, dtype=float)
    state = float(fallback)
    for index, value in enumerate(source):
        if np.isfinite(value):
            state = float(value)
        result[index] = state
    return result

def hex_rgb(color):
    value = str(color).strip()
    if value.startswith("#") and len(value) in (4, 7):
        if len(value) == 4:
            return tuple(int(value[position] * 2, 16) for position in range(1, 4))
        return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
    return 128, 128, 128

def mix_color(start_color, end_color, weight):
    start = hex_rgb(start_color)
    end = hex_rgb(end_color)
    ratio = min(1.0, max(0.0, float(weight)))
    channels = [int(round(start[index] + (end[index] - start[index]) * ratio)) for index in range(3)]
    return "#" + "".join(f"{channel:02X}" for channel in channels)

def with_alpha(color, alpha):
    red, green, blue = hex_rgb(color)
    return f"rgba({red},{green},{blue},{min(1.0, max(0.0, float(alpha))):.3f})"

timeframe_seconds = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1H": 3600, "2H": 7200, "4H": 14400, "6H": 21600,
    "8H": 28800, "12H": 43200, "1D": 86400, "1W": 604800,
}
runtime_context = ctx
chart_timeframe = str(runtime_context.timeframe or "")
can_request_lower = (
    callable(request_lower_tf)
    and
    chart_timeframe in timeframe_seconds
    and lower_timeframe in timeframe_seconds
    and timeframe_seconds[lower_timeframe] < timeframe_seconds[chart_timeframe]
)
if can_request_lower:
    intrabars = request_lower_tf(lower_timeframe, fields=["open", "close", "volume"])
    intrabar_open = intrabars.get("open", [[] for _ in range(n)])
    intrabar_close = intrabars.get("close", [[] for _ in range(n)])
    intrabar_volume = intrabars.get("volume", [[] for _ in range(n)])
else:
    intrabar_open = [[] for _ in range(n)]
    intrabar_close = [[] for _ in range(n)]
    intrabar_volume = [[] for _ in range(n)]

buy_volume = np.zeros(n, dtype=float)
sell_volume = np.zeros(n, dtype=float)
intrabar_counts = np.zeros(n, dtype=int)
for index in range(n):
    opens = intrabar_open[index] if index < len(intrabar_open) else []
    closes = intrabar_close[index] if index < len(intrabar_close) else []
    volumes = intrabar_volume[index] if index < len(intrabar_volume) else []
    count = min(len(opens), len(closes), len(volumes))
    intrabar_counts[index] = count
    last_direction = 1 if close[index] >= open_price[index] else -1
    previous_close = None
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
            buy_volume[index] += current_volume
        else:
            sell_volume[index] += current_volume
        previous_close = current_close

    if count == 0:
        bar_range = max(float(high[index] - low[index]), 1e-12)
        buy_share = min(1.0, max(0.0, float(close[index] - low[index]) / bar_range))
        buy_volume[index] = max(0.0, float(volume[index])) * buy_share
        sell_volume[index] = max(0.0, float(volume[index])) - buy_volume[index]

intrabar_total = buy_volume + sell_volume
delta = buy_volume - sell_volume
delta_percent = np.divide(
    100.0 * delta,
    intrabar_total,
    out=np.zeros(n, dtype=float),
    where=intrabar_total > 1e-12,
)
bar_up = close > open_price
bar_down = close < open_price
close_up = np.r_[False, close[1:] > close[:-1]]
close_down = np.r_[False, close[1:] < close[:-1]]
volume_up = np.r_[False, volume[1:] > volume[:-1]]

average_buy = ema(buy_volume, average_length)
average_sell = ema(sell_volume, average_length)
average_balance = average_buy - average_sell
slow_buy = sma(buy_volume, min(n if n else 1, average_length * 2)) if n else np.array([], dtype=float)
slow_sell = sma(sell_volume, min(n if n else 1, average_length * 2)) if n else np.array([], dtype=float)
momentum_source = (average_buy - np.nan_to_num(slow_buy, nan=average_buy)) - (average_sell - np.nan_to_num(slow_sell, nan=average_sell))
momentum_balance = rsi(momentum_source, momentum_length) - 50.0
buy_excess = np.maximum(0.0, buy_volume - average_buy)
sell_excess = np.maximum(0.0, sell_volume - average_sell)
relative_balance = alma(buy_excess - sell_excess, relative_length)
percent_balance = alma(delta_percent, percent_length)

composite_balance = (
    np.sign(np.nan_to_num(delta, nan=0.0))
    + np.sign(np.nan_to_num(average_balance, nan=0.0))
    + np.sign(np.nan_to_num(momentum_balance, nan=0.0))
    + np.sign(np.nan_to_num(relative_balance, nan=0.0))
    + np.sign(np.nan_to_num(percent_balance, nan=0.0))
)

balances = {
    "Price": close - open_price,
    "Delta": delta,
    "Average": average_balance,
    "Momentum": momentum_balance,
    "Relative": relative_balance,
    "Percent": percent_balance,
    "Composite": composite_balance,
}

def balance_values(mode):
    return np.asarray(balances.get(str(mode), delta), dtype=float)

bump_up = bar_up & close_up & (delta > 0.0) & (buy_volume > average_buy) & np.r_[False, buy_volume[1:] > buy_volume[:-1]]
bump_down = bar_down & close_down & (delta < 0.0) & (sell_volume > average_sell) & np.r_[False, sell_volume[1:] > sell_volume[:-1]]
double_bump_up = bump_up & np.r_[False, bump_up[:-1]]
double_bump_down = bump_down & np.r_[False, bump_down[:-1]]

divergence_source = balance_values(body_divergence_mode if body_divergence_mode != "Off" else "Delta")
body_divergence = np.isfinite(divergence_source) & (
    np.sign(np.nan_to_num(divergence_source, nan=0.0))
    != np.sign(np.nan_to_num(close - open_price, nan=0.0))
)
divergence_confirm_up = np.r_[False, body_divergence[:-1]] & bar_up & ~body_divergence
divergence_confirm_down = np.r_[False, body_divergence[:-1]] & bar_down & ~body_divergence
shift_source = balance_values(balance_shift_mode)
balance_shift_up = np.r_[False, (shift_source[1:] > 0.0) & (shift_source[:-1] <= 0.0)]
balance_shift_down = np.r_[False, (shift_source[1:] < 0.0) & (shift_source[:-1] >= 0.0)]

marker_up_counts = bump_up.astype(float) + double_bump_up.astype(float) + divergence_confirm_up.astype(float) + balance_shift_up.astype(float)
marker_down_counts = bump_down.astype(float) + double_bump_down.astype(float) + divergence_confirm_down.astype(float) + balance_shift_down.astype(float)
marker_bias = rolling_sum(marker_up_counts - marker_down_counts, bias_length)
bias_shift_up = np.r_[False, (marker_bias[1:] > 0.0) & (marker_bias[:-1] <= 0.0)]
bias_shift_down = np.r_[False, (marker_bias[1:] < 0.0) & (marker_bias[:-1] >= 0.0)]
full_agreement_up = composite_balance >= 5.0
full_agreement_down = composite_balance <= -5.0

body_top = np.maximum(open_price, close)
body_bottom = np.minimum(open_price, close)
body_size = np.maximum(0.0, body_top - body_bottom)
average_body = sma(body_size, min(100, max(1, n))) if n else np.array([], dtype=float)
reference_size = body_size if pressure_reference == "Body" else np.where(np.isfinite(average_body), average_body, body_size)
top_factor = np.zeros(n, dtype=float)
bottom_factor = np.zeros(n, dtype=float)
if pressure_mode == "Split":
    top_factor = np.divide(buy_volume, intrabar_total, out=np.zeros(n), where=intrabar_total > 1e-12)
    bottom_factor = np.divide(sell_volume, intrabar_total, out=np.zeros(n), where=intrabar_total > 1e-12)
elif pressure_mode == "Delta_Winner":
    top_factor = np.divide(np.maximum(delta, 0.0), intrabar_total, out=np.zeros(n), where=intrabar_total > 1e-12)
    bottom_factor = np.divide(np.maximum(-delta, 0.0), intrabar_total, out=np.zeros(n), where=intrabar_total > 1e-12)
elif pressure_mode == "Composite_Winner":
    top_factor = np.maximum(composite_balance, 0.0) / 5.0
    bottom_factor = np.maximum(-composite_balance, 0.0) / 5.0

pressure_top = body_top + reference_size * top_factor
pressure_bottom = body_bottom - reference_size * bottom_factor
pressure_divergence = (top_factor > 0.0) != bar_up

body_signal = balance_values(body_mode)
body_colors = [None for _ in range(n)]
finite_strength = np.abs(body_signal[np.isfinite(body_signal)])
strength_reference = float(np.nanpercentile(finite_strength, 75)) if len(finite_strength) else 1.0
strength_reference = max(strength_reference, 1e-12)
for index in range(n):
    if body_mode == "Off" or not np.isfinite(body_signal[index]):
        continue
    if hollow_on_lower_volume and index > 0 and not volume_up[index]:
        continue
    color = up_color if body_signal[index] > 0.0 else down_color if body_signal[index] < 0.0 else None
    if body_divergence_mode != "Off" and body_divergence[index]:
        color = divergence_color
    elif body_gradient and color is not None:
        weight = min(1.0, abs(float(body_signal[index])) / strength_reference)
        color = mix_color("#D1D5DB", color, 0.25 + 0.75 * weight)
    body_colors[index] = color

def point_series(values, color=None, colors=None):
    result = []
    for index, value in enumerate(values):
        if not np.isfinite(value):
            result.append(None)
            continue
        point = {"value": float(value)}
        if colors is not None and index < len(colors) and colors[index] is not None:
            point["color"] = colors[index]
        elif color is not None:
            point["color"] = color
        result.append(point)
    return result

plots = []
if pressure_mode != "Off":
    visible_top = np.where(top_factor > 0.0, pressure_top, np.nan)
    visible_bottom = np.where(bottom_factor > 0.0, pressure_bottom, np.nan)
    plots.append({"name": "Buy Pressure Extension", "title": False, "data": point_series(visible_top, wick_up_color), "color": wick_up_color, "type": "line", "lineWidth": 2, "overlay": True})
    plots.append({"name": "Sell Pressure Extension", "title": False, "data": point_series(visible_bottom, wick_down_color), "color": wick_down_color, "type": "line", "lineWidth": 2, "overlay": True})

if show_pressure_average and n:
    filled_top = forward_fill(np.where(top_factor > 0.0, pressure_top, np.nan), body_top[0])
    filled_bottom = forward_fill(np.where(bottom_factor > 0.0, pressure_bottom, np.nan), body_bottom[0])
    top_average = ema(filled_top, pressure_average_length)
    bottom_average = ema(filled_bottom, pressure_average_length)
    plots.append({"name": "Buy Pressure Average", "data": point_series(top_average, pressure_average_up_color), "color": pressure_average_up_color, "type": "line", "overlay": True})
    plots.append({"name": "Sell Pressure Average", "data": point_series(bottom_average, pressure_average_down_color), "color": pressure_average_down_color, "type": "line", "overlay": True})
    cloud_signal = balance_values(pressure_cloud_mode)
    cloud_points = []
    for index in range(n):
        if not np.isfinite(top_average[index]) or not np.isfinite(bottom_average[index]):
            cloud_points.append(None)
            continue
        cloud_color = with_alpha(pressure_average_up_color if cloud_signal[index] >= 0.0 else pressure_average_down_color, 0.18)
        cloud_points.append({"upper": float(top_average[index]), "lower": float(bottom_average[index]), "color": cloud_color})
    plots.append({"name": "Pressure Average Cloud", "title": False, "data": cloud_points, "type": "cloud", "overlay": True})

channel_trigger = np.zeros(n, dtype=bool)
if divergence_channel_mode in ("Body", "Both"):
    channel_trigger |= body_divergence
if divergence_channel_mode in ("Pressure", "Both"):
    channel_trigger |= pressure_divergence
channel_upper = np.full(n, np.nan, dtype=float)
channel_lower = np.full(n, np.nan, dtype=float)
channel_colors = [channel_neutral_color for _ in range(n)]
active_upper = np.nan
active_lower = np.nan
active_color = channel_neutral_color
for index in range(n):
    if divergence_channel_source == "Full_Range":
        new_upper = max(high[index], pressure_top[index])
        new_lower = min(low[index], pressure_bottom[index])
    elif divergence_channel_source == "Pressure_Range":
        new_upper = pressure_top[index]
        new_lower = pressure_bottom[index]
    elif divergence_channel_source == "High_Low":
        new_upper = high[index]
        new_lower = low[index]
    else:
        new_upper = body_top[index]
        new_lower = body_bottom[index]
    if divergence_breach_source == "Body":
        breach_high = body_top[index]
        breach_low = body_bottom[index]
    elif divergence_breach_source == "High_Low":
        breach_high = high[index]
        breach_low = low[index]
    elif divergence_breach_source == "Pressure_Range":
        breach_high = pressure_top[index]
        breach_low = pressure_bottom[index]
    else:
        breach_high = close[index]
        breach_low = close[index]
    if np.isfinite(active_upper) and (breach_high > active_upper or breach_low < active_lower):
        active_upper = np.nan
        active_lower = np.nan
    if channel_trigger[index]:
        if np.isfinite(active_upper):
            active_upper = max(active_upper, new_upper)
            active_lower = min(active_lower, new_lower)
        else:
            active_upper = float(new_upper)
            active_lower = float(new_lower)
        active_color = channel_bull_color if delta[index] > 0.0 else channel_bear_color if delta[index] < 0.0 else channel_neutral_color
    if divergence_channel_mode != "Off" and np.isfinite(active_upper):
        channel_upper[index] = active_upper
        channel_lower[index] = active_lower
        channel_colors[index] = active_color

if divergence_channel_mode != "Off":
    plots.append({"name": "Divergence Upper", "title": False, "data": point_series(channel_upper, colors=channel_colors), "color": channel_neutral_color, "type": "line", "overlay": True})
    plots.append({"name": "Divergence Lower", "title": False, "data": point_series(channel_lower, colors=channel_colors), "color": channel_neutral_color, "type": "line", "overlay": True})
    if divergence_channel_fill:
        cloud_points = [
            {"upper": float(channel_upper[index]), "lower": float(channel_lower[index]), "color": with_alpha(channel_colors[index], 0.16)}
            if np.isfinite(channel_upper[index]) and np.isfinite(channel_lower[index]) else None
            for index in range(n)
        ]
        plots.append({"name": "Divergence Channel", "title": False, "data": cloud_points, "type": "cloud", "overlay": True})

if show_delta_pane:
    delta_colors = [wick_up_color if value >= 0.0 else wick_down_color for value in delta_percent]
    plots.append({"name": "Delta %", "data": point_series(delta_percent, colors=delta_colors), "color": wick_up_color, "type": "bar", "overlay": False})

allow_longs = marker_direction in ("Both", "Longs_Only")
allow_shorts = marker_direction in ("Both", "Shorts_Only")
marker_conditions_up = [
    (show_bump_marker, bump_up, "B1"),
    (show_double_bump_marker, double_bump_up, "B2"),
    (show_divergence_confirmation, divergence_confirm_up, "D"),
    (show_balance_shift, balance_shift_up, "S"),
    (show_bias_shift, bias_shift_up, "BIAS"),
    (show_full_agreement, full_agreement_up, "ALL"),
]
marker_conditions_down = [
    (show_bump_marker, bump_down, "B1"),
    (show_double_bump_marker, double_bump_down, "B2"),
    (show_divergence_confirmation, divergence_confirm_down, "D"),
    (show_balance_shift, balance_shift_down, "S"),
    (show_bias_shift, bias_shift_down, "BIAS"),
    (show_full_agreement, full_agreement_down, "ALL"),
]

up_text = ["" for _ in range(n)]
down_text = ["" for _ in range(n)]
for enabled, condition, label in marker_conditions_up:
    if enabled and allow_longs:
        for index in np.flatnonzero(condition):
            up_text[index] = (up_text[index] + "+" + label).lstrip("+")
for enabled, condition, label in marker_conditions_down:
    if enabled and allow_shorts:
        for index in np.flatnonzero(condition):
            down_text[index] = (down_text[index] + "+" + label).lstrip("+")

signals = [
    {
        "type": "buy",
        "name": "Bull Volume Balance",
        "text": "VOL UP",
        "color": wick_up_color,
        "renderMode": "events",
        "data": [
            {"value": float(min(low[index], pressure_bottom[index])), "text": up_text[index], "color": wick_up_color}
            if up_text[index] else None
            for index in range(n)
        ],
    },
    {
        "type": "sell",
        "name": "Bear Volume Balance",
        "text": "VOL DN",
        "color": wick_down_color,
        "renderMode": "events",
        "data": [
            {"value": float(max(high[index], pressure_top[index])), "text": down_text[index], "color": wick_down_color}
            if down_text[index] else None
            for index in range(n)
        ],
    },
]

background_colors = [None for _ in range(n)]
background_signal = balance_values(background_mode) if background_mode != "Off" else np.zeros(n, dtype=float)
for index in range(n):
    mismatch = (
        intrabar_counts[index] > 0
        and volume[index] > 1e-12
        and abs(intrabar_total[index] - volume[index]) / volume[index] > discrepancy_tolerance
    )
    if show_volume_discrepancy and mismatch:
        background_colors[index] = with_alpha(discrepancy_color, 0.09)
    elif background_mode != "Off" and np.isfinite(background_signal[index]) and background_signal[index] != 0.0:
        background_colors[index] = with_alpha(up_color if background_signal[index] > 0.0 else down_color, 0.055)

covered_bars = int(np.sum(intrabar_counts > 0))
average_intrabars = float(np.mean(intrabar_counts[intrabar_counts > 0])) if covered_bars else 0.0
coverage_percent = 100.0 * covered_bars / n if n else 0.0
latest_delta = float(delta[-1]) if n else 0.0
latest_delta_percent = float(delta_percent[-1]) if n else 0.0
latest_composite = int(round(composite_balance[-1])) if n else 0
quality = "Bar estimate"
if covered_bars == n:
    quality = "Intrabar"
elif covered_bars:
    quality = "Hybrid estimate"

tables = []
if show_info_table:
    tables.append({
        "title": "Delta Volume Candles",
        "position": table_position,
        "compact": True,
        "rows": [
            ["Lower TF", lower_timeframe if can_request_lower else "Unavailable"],
            ["Coverage", f"{covered_bars} / {n} ({coverage_percent:.1f}%)"],
            ["Avg intrabars", f"{average_intrabars:.1f}"],
            ["Data quality", quality],
            ["Delta", f"{latest_delta:,.0f}"],
            ["Delta %", f"{latest_delta_percent:.1f}%"],
            ["Balance", f"{latest_composite:+d} / 5"],
        ],
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": [],
    "tables": tables,
    "bar_colors": body_colors,
    "background_colors": background_colors,
    "calculatedVars": {
        "buyVolume": [float(value) for value in buy_volume],
        "sellVolume": [float(value) for value in sell_volume],
        "intrabarTotal": [float(value) for value in intrabar_total],
        "delta": [float(value) for value in delta],
        "deltaPercent": [float(value) for value in delta_percent],
        "intrabarCounts": [int(value) for value in intrabar_counts],
        "averageBalance": [float(value) if np.isfinite(value) else None for value in average_balance],
        "momentumBalance": [float(value) if np.isfinite(value) else None for value in momentum_balance],
        "relativeBalance": [float(value) if np.isfinite(value) else None for value in relative_balance],
        "percentBalance": [float(value) if np.isfinite(value) else None for value in percent_balance],
        "compositeBalance": [float(value) if np.isfinite(value) else None for value in composite_balance],
        "coverageBars": int(covered_bars),
        "averageIntrabars": float(average_intrabars),
    },
}
