# ============================================================
# QuantDinger Apex Algo Engine
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Apex Algo Engine"
my_indicator_description = (
    "Combines a regression equilibrium cloud with an ATR trend tracker, clean trend-cycle "
    "entries, optional contrarian tools, support/resistance zones, and persistent risk levels."
)

# @param signal_mode str Strong Strong-label mode values=Strong,Confirmed,All
# @param filter_signals_by_trend bool true Require tracker alignment
# @param trend_multiplier float 2.2 Trend sensitivity multiplier range=0.5:10.0:0.1
# @param trend_atr_period int 14 Trend ATR period range=1:300:1
# @param show_bar_colors bool true Color price bars by trend
# @param solid_trend_colors bool true Use solid trend candle colors
# @param show_trend_background bool true Show trend background tint
# @param show_cloud bool true Show equilibrium cloud
# @param equilibrium_length int 55 Equilibrium regression length range=10:500:1
# @param smoothing_length int 8 Cloud smoothing range=1:30:1
# @param cloud_atr_period int 21 Weighted volatility length range=5:300:1
# @param cloud_inner_deviation float 1.8 Cloud inner deviation range=0.1:10.0:0.1
# @param cloud_outer_deviation float 3.3 Cloud outer deviation range=0.2:15.0:0.1
# @param show_sniper_signals bool false Show contrarian sniper labels
# @param show_exhaustion_dots bool false Show exhaustion points
# @param show_sr_zones bool false Show support and resistance zones
# @param sr_pivot_length int 20 Support resistance pivot length range=5:50:1
# @param sr_zone_atr_multiplier float 0.4 Zone thickness ATR multiplier range=0.05:5.0:0.05
# @param show_dashboard bool true Show risk dashboard
# @param dashboard_position str top-right Dashboard position values=top-left,top-right,bottom-left,bottom-right
# @param show_risk_lines bool true Show active stop and targets
# @param reward_ratio_1 float 1.5 First target reward ratio range=0.5:10.0:0.1
# @param reward_ratio_2 float 2.5 Second target reward ratio range=1.0:15.0:0.1
# @param reward_ratio_3 float 3.5 Third target reward ratio range=1.5:20.0:0.1

signal_mode = str(params.get("signal_mode", "Strong"))
if signal_mode not in ("Strong", "Confirmed", "All"):
    signal_mode = "Strong"
filter_signals_by_trend = bool(params.get("filter_signals_by_trend", True))
trend_multiplier = min(10.0, max(0.5, float(params.get("trend_multiplier", 2.2))))
trend_atr_period = min(300, max(1, int(params.get("trend_atr_period", 14))))
show_bar_colors = bool(params.get("show_bar_colors", True))
solid_trend_colors = bool(params.get("solid_trend_colors", True))
show_trend_background = bool(params.get("show_trend_background", True))
show_cloud = bool(params.get("show_cloud", True))
equilibrium_length = min(500, max(10, int(params.get("equilibrium_length", 55))))
smoothing_length = min(30, max(1, int(params.get("smoothing_length", 8))))
cloud_atr_period = min(300, max(5, int(params.get("cloud_atr_period", 21))))
cloud_inner_deviation = min(10.0, max(0.1, float(params.get("cloud_inner_deviation", 1.8))))
cloud_outer_deviation = min(15.0, max(cloud_inner_deviation, float(params.get("cloud_outer_deviation", 3.3))))
show_sniper_signals = bool(params.get("show_sniper_signals", False))
show_exhaustion_dots = bool(params.get("show_exhaustion_dots", False))
show_sr_zones = bool(params.get("show_sr_zones", False))
sr_pivot_length = min(50, max(5, int(params.get("sr_pivot_length", 20))))
sr_zone_atr_multiplier = min(5.0, max(0.05, float(params.get("sr_zone_atr_multiplier", 0.4))))
show_dashboard = bool(params.get("show_dashboard", True))
dashboard_position = str(params.get("dashboard_position", "top-right"))
if dashboard_position not in ("top-left", "top-right", "bottom-left", "bottom-right"):
    dashboard_position = "top-right"
show_risk_lines = bool(params.get("show_risk_lines", True))
reward_ratio_1 = min(10.0, max(0.5, float(params.get("reward_ratio_1", 1.5))))
reward_ratio_2 = min(15.0, max(1.0, float(params.get("reward_ratio_2", 2.5))))
reward_ratio_3 = min(20.0, max(1.5, float(params.get("reward_ratio_3", 3.5))))

bullish_bright = "#00E676"
bullish_dark = "#00A859"
bearish_bright = "#FF1744"
bearish_dark = "#B71C1C"
sniper_buy_color = "#2962FF"
sniper_sell_color = "#FFD600"
exhaustion_color = "#FF9100"

df = df.copy()
open_values = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


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


def rolling_highest(values, length, offset=0):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(len(source)):
        end = index - offset + 1
        start = end - length
        if start >= 0 and end > start:
            window = source[start:end]
            if np.all(np.isfinite(window)):
                result[index] = float(np.max(window))
    return result


def rolling_lowest(values, length, offset=0):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(len(source)):
        end = index - offset + 1
        start = end - length
        if start >= 0 and end > start:
            window = source[start:end]
            if np.all(np.isfinite(window)):
                result[index] = float(np.min(window))
    return result


def linear_regression(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    x_values = np.arange(length, dtype=float)
    x_mean = float(np.mean(x_values))
    denominator = float(np.sum((x_values - x_mean) ** 2))
    for index in range(length - 1, len(source)):
        window = source[index - length + 1:index + 1]
        if not np.all(np.isfinite(window)):
            continue
        y_mean = float(np.mean(window))
        slope = float(np.sum((x_values - x_mean) * (window - y_mean)) / denominator)
        intercept = y_mean - slope * x_mean
        result[index] = intercept + slope * (length - 1)
    return result


def finite_line(values, visible=True):
    if not visible:
        return [None for _ in range(len(values))]
    return [float(value) if np.isfinite(value) else None for value in values]


def cloud_between(upper_values, lower_values, color, visible=True):
    points = []
    for upper, lower in zip(upper_values, lower_values):
        if visible and np.isfinite(upper) and np.isfinite(lower):
            points.append({"upper": float(max(upper, lower)), "lower": float(min(upper, lower)), "color": color})
        else:
            points.append(None)
    return points


def event_values(mask, anchors):
    data = []
    for index, active in enumerate(mask):
        data.append(float(anchors[index]) if bool(active) and np.isfinite(anchors[index]) else None)
    return data


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

raw_regression = linear_regression(close, equilibrium_length)
equilibrium = weighted_average(weighted_average(raw_regression, smoothing_length), smoothing_length)
weighted_range = weighted_average(true_range, cloud_atr_period)
absolute_change = np.abs(np.diff(close, prepend=np.nan))
weighted_change = weighted_average(absolute_change, cloud_atr_period)
adaptive_volatility = weighted_range * 0.65 + weighted_change * 0.35
middle_deviation = (cloud_inner_deviation + cloud_outer_deviation) * 0.5

upper_inner = equilibrium + adaptive_volatility * cloud_inner_deviation
upper_middle = equilibrium + adaptive_volatility * middle_deviation
upper_outer = equilibrium + adaptive_volatility * cloud_outer_deviation
lower_inner = equilibrium - adaptive_volatility * cloud_inner_deviation
lower_middle = equilibrium - adaptive_volatility * middle_deviation
lower_outer = equilibrium - adaptive_volatility * cloud_outer_deviation

trend_atr = weighted_average(true_range, trend_atr_period)
upper_bound = high + trend_atr * trend_multiplier
lower_bound = low - trend_atr * trend_multiplier
trend_tracker = np.full(n, np.nan, dtype=float)
trend_direction = np.ones(n, dtype=int)
previous_tracker = np.nan
previous_direction = 1
for index in range(n):
    if not np.isfinite(trend_atr[index]):
        trend_direction[index] = previous_direction
        continue
    if not np.isfinite(previous_tracker):
        current_tracker = float(lower_bound[index])
        current_direction = 1
    elif previous_direction == 1:
        current_tracker = max(previous_tracker, float(lower_bound[index]))
        current_direction = 1
        if close[index] < current_tracker:
            current_direction = -1
            current_tracker = float(upper_bound[index])
    else:
        current_tracker = min(previous_tracker, float(upper_bound[index]))
        current_direction = -1
        if close[index] > current_tracker:
            current_direction = 1
            current_tracker = float(lower_bound[index])
    trend_tracker[index] = current_tracker
    trend_direction[index] = current_direction
    previous_tracker = current_tracker
    previous_direction = current_direction

trend_flipped_bull = np.zeros(n, dtype=bool)
trend_flipped_bear = np.zeros(n, dtype=bool)
for index in range(1, n):
    if not np.isfinite(trend_tracker[index]) or not np.isfinite(trend_tracker[index - 1]):
        continue
    trend_flipped_bull[index] = close[index] > trend_tracker[index] and close[index - 1] <= trend_tracker[index - 1]
    trend_flipped_bear[index] = close[index] < trend_tracker[index] and close[index - 1] >= trend_tracker[index - 1]

volatility_average = rolling_mean(adaptive_volatility, 20)
expanding_volatility = adaptive_volatility > volatility_average * 1.05
previous_five_high = rolling_highest(high, 5, offset=1)
previous_five_low = rolling_lowest(low, 5, offset=1)
breakout_bull = close > previous_five_high
breakout_bear = close < previous_five_low

trigger_buy = np.zeros(n, dtype=bool)
trigger_sell = np.zeros(n, dtype=bool)
active_position_history = np.zeros(n, dtype=int)
entry_history = np.full(n, np.nan, dtype=float)
stop_history = np.full(n, np.nan, dtype=float)
tp1_history = np.full(n, np.nan, dtype=float)
tp2_history = np.full(n, np.nan, dtype=float)
tp3_history = np.full(n, np.nan, dtype=float)
active_position = 0
entry_price = np.nan
stop_loss = np.nan
target_1 = np.nan
target_2 = np.nan
target_3 = np.nan
last_signal_index = -1
for index in range(n):
    long_plan_finished = (
        active_position == 1
        and ((np.isfinite(stop_loss) and low[index] <= stop_loss) or (np.isfinite(target_3) and high[index] >= target_3))
    )
    short_plan_finished = (
        active_position == -1
        and ((np.isfinite(stop_loss) and high[index] >= stop_loss) or (np.isfinite(target_3) and low[index] <= target_3))
    )
    if long_plan_finished or short_plan_finished:
        active_position = 0
        entry_price = np.nan
        stop_loss = np.nan
        target_1 = np.nan
        target_2 = np.nan
        target_3 = np.nan

    raw_bull = bool(trend_flipped_bull[index]) or (
        trend_direction[index] == 1 and active_position != 1 and bool(breakout_bull[index]) and bool(expanding_volatility[index])
    )
    raw_bear = bool(trend_flipped_bear[index]) or (
        trend_direction[index] == -1 and active_position != -1 and bool(breakout_bear[index]) and bool(expanding_volatility[index])
    )
    if filter_signals_by_trend:
        raw_bull = raw_bull and trend_direction[index] == 1
        raw_bear = raw_bear and trend_direction[index] == -1
    if raw_bull and active_position != 1:
        trigger_buy[index] = True
        active_position = 1
        last_signal_index = index
        entry_price = close[index]
        stop_loss = trend_tracker[index] if np.isfinite(trend_tracker[index]) and trend_tracker[index] < close[index] else close[index] - trend_atr[index] * 1.5
        risk_distance = max(entry_price - stop_loss, max(abs(entry_price) * 1e-6, 1e-8))
        target_1 = entry_price + risk_distance * reward_ratio_1
        target_2 = entry_price + risk_distance * reward_ratio_2
        target_3 = entry_price + risk_distance * reward_ratio_3
    elif raw_bear and active_position != -1:
        trigger_sell[index] = True
        active_position = -1
        last_signal_index = index
        entry_price = close[index]
        stop_loss = trend_tracker[index] if np.isfinite(trend_tracker[index]) and trend_tracker[index] > close[index] else close[index] + trend_atr[index] * 1.5
        risk_distance = max(stop_loss - entry_price, max(abs(entry_price) * 1e-6, 1e-8))
        target_1 = entry_price - risk_distance * reward_ratio_1
        target_2 = entry_price - risk_distance * reward_ratio_2
        target_3 = entry_price - risk_distance * reward_ratio_3

    active_position_history[index] = active_position
    entry_history[index] = entry_price
    stop_history[index] = stop_loss
    tp1_history[index] = target_1
    tp2_history[index] = target_2
    tp3_history[index] = target_3

equilibrium_std = rolling_std(close, equilibrium_length)
z_score = np.divide(close - equilibrium, equilibrium_std, out=np.zeros(n, dtype=float), where=np.isfinite(equilibrium_std) & (equilibrium_std > 1e-12))
ten_high = rolling_highest(high, 10)
ten_low = rolling_lowest(low, 10)
exhaustion_high = show_exhaustion_dots & ((high >= upper_inner) | (z_score >= 2.2)) & np.isfinite(ten_high) & (high >= ten_high)
exhaustion_low = show_exhaustion_dots & ((low <= lower_inner) | (z_score <= -2.2)) & np.isfinite(ten_low) & (low <= ten_low)

sniper_buy = np.zeros(n, dtype=bool)
sniper_sell = np.zeros(n, dtype=bool)
last_sniper_index = -1000
if show_sniper_signals:
    for index in range(1, n):
        raw_buy = (low[index] <= lower_inner[index] or z_score[index] <= -2.2) and close[index] > open_values[index] and close[index] > low[index - 1]
        raw_sell = (high[index] >= upper_inner[index] or z_score[index] >= 2.2) and close[index] < open_values[index] and close[index] < high[index - 1]
        if index - last_sniper_index > 20 and raw_buy:
            sniper_buy[index] = True
            last_sniper_index = index
        elif index - last_sniper_index > 20 and raw_sell:
            sniper_sell[index] = True
            last_sniper_index = index

resistance_zones = []
support_zones = []
if show_sr_zones:
    pivot_span = sr_pivot_length
    for confirmation_index in range(pivot_span * 2, n):
        pivot_index = confirmation_index - pivot_span
        high_window = high[pivot_index - pivot_span:pivot_index + pivot_span + 1]
        low_window = low[pivot_index - pivot_span:pivot_index + pivot_span + 1]
        if len(high_window) == pivot_span * 2 + 1 and high[pivot_index] >= np.max(high_window):
            thickness = adaptive_volatility[confirmation_index] * sr_zone_atr_multiplier
            if np.isfinite(thickness):
                resistance_zones.append((pivot_index, float(high[pivot_index]), float(high[pivot_index] - thickness)))
                resistance_zones = resistance_zones[-3:]
        if len(low_window) == pivot_span * 2 + 1 and low[pivot_index] <= np.min(low_window):
            thickness = adaptive_volatility[confirmation_index] * sr_zone_atr_multiplier
            if np.isfinite(thickness):
                support_zones.append((pivot_index, float(low[pivot_index] + thickness), float(low[pivot_index])))
                support_zones = support_zones[-3:]

zone_plots = []
for zone_index, (start, top, bottom) in enumerate(resistance_zones):
    data = [None for _ in range(n)]
    for index in range(start, n):
        data[index] = {"upper": top, "lower": bottom, "color": bearish_dark}
    zone_plots.append({"name": f"Resistance Zone {zone_index + 1}", "title": False, "data": data, "color": bearish_dark, "type": "cloud", "opacity": 0.24, "overlay": True})
for zone_index, (start, top, bottom) in enumerate(support_zones):
    data = [None for _ in range(n)]
    for index in range(start, n):
        data[index] = {"upper": top, "lower": bottom, "color": bullish_dark}
    zone_plots.append({"name": f"Support Zone {zone_index + 1}", "title": False, "data": data, "color": bullish_dark, "type": "cloud", "opacity": 0.24, "overlay": True})

bullish_tracker = [float(trend_tracker[index]) if np.isfinite(trend_tracker[index]) and trend_direction[index] == 1 else None for index in range(n)]
bearish_tracker = [float(trend_tracker[index]) if np.isfinite(trend_tracker[index]) and trend_direction[index] == -1 else None for index in range(n)]
bar_colors = []
background_colors = []
for index in range(n):
    if show_bar_colors:
        if trend_direction[index] == 1:
            bar_colors.append(bullish_bright if solid_trend_colors or close[index] >= open_values[index] else bullish_dark)
        else:
            bar_colors.append(bearish_bright if solid_trend_colors or close[index] <= open_values[index] else bearish_dark)
    else:
        bar_colors.append(None)
    if show_trend_background:
        background_colors.append("rgba(0,230,118,0.055)" if trend_direction[index] == 1 else "rgba(255,23,68,0.055)")
    else:
        background_colors.append(None)

risk_start = last_signal_index if show_risk_lines and active_position != 0 else n
stop_plot = [float(stop_history[index]) if index >= risk_start and np.isfinite(stop_history[index]) else None for index in range(n)]
tp1_plot = [float(tp1_history[index]) if index >= risk_start and np.isfinite(tp1_history[index]) else None for index in range(n)]
tp2_plot = [float(tp2_history[index]) if index >= risk_start and np.isfinite(tp2_history[index]) else None for index in range(n)]
tp3_plot = [float(tp3_history[index]) if index >= risk_start and np.isfinite(tp3_history[index]) else None for index in range(n)]

signal_text = "Strong" if signal_mode == "Strong" else "Buy"
sell_text = "Strong" if signal_mode == "Strong" else "Sell"
signals = [
    {"type": "buy", "name": "Apex Buy", "text": signal_text, "color": bullish_bright, "renderMode": "events", "data": event_values(trigger_buy, low - np.nan_to_num(trend_atr, nan=0.0) * 0.5)},
    {"type": "sell", "name": "Apex Sell", "text": sell_text, "color": bearish_bright, "renderMode": "events", "data": event_values(trigger_sell, high + np.nan_to_num(trend_atr, nan=0.0) * 0.5)},
]
if show_sniper_signals:
    signals.extend([
        {"type": "buy", "name": "Sniper Buy", "text": "SNIPER", "color": sniper_buy_color, "renderMode": "events", "data": event_values(sniper_buy, low)},
        {"type": "sell", "name": "Sniper Sell", "text": "SNIPER", "color": sniper_sell_color, "renderMode": "events", "data": event_values(sniper_sell, high)},
    ])
if show_exhaustion_dots:
    signals.extend([
        {"type": "sell", "name": "Exhaustion High", "text": "•", "color": exhaustion_color, "renderMode": "points", "data": event_values(exhaustion_high, high)},
        {"type": "buy", "name": "Exhaustion Low", "text": "•", "color": exhaustion_color, "renderMode": "points", "data": event_values(exhaustion_low, low)},
    ])

tables = []
if show_dashboard:
    final_direction = int(trend_direction[-1]) if n else 1
    final_position = int(active_position_history[-1]) if n else 0
    tables.append({
        "title": "APEX RISK HUD",
        "position": dashboard_position,
        "compact": True,
        "borderColor": bullish_bright if final_direction == 1 else bearish_bright,
        "accentColor": bullish_bright if final_direction == 1 else bearish_bright,
        "rows": [
            ["Trend", "BULLISH ▲" if final_direction == 1 else "BEARISH ▼"],
            ["Position", ("LONG" if final_position == 1 else "SHORT" if final_position == -1 else "WAITING") + (f" @ {entry_price:.2f}" if np.isfinite(entry_price) else "")],
            ["SL", f"{stop_loss:.2f}" if np.isfinite(stop_loss) else "—"],
            ["TP1 / TP2", (f"{target_1:.2f}" if np.isfinite(target_1) else "—") + " | " + (f"{target_2:.2f}" if np.isfinite(target_2) else "—")],
            ["TP3 Runner", f"{target_3:.2f}" if np.isfinite(target_3) else "—"],
        ],
    })

plots = [
    {"name": "Cloud Upper Outer", "data": finite_line(upper_outer, show_cloud), "color": "rgba(0,230,118,0.40)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Cloud Upper Mid", "data": finite_line(upper_middle, show_cloud), "color": "rgba(0,230,118,0.25)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Cloud Upper Inner", "data": finite_line(upper_inner, show_cloud), "color": "rgba(0,168,89,0.30)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Upper Outer Fill", "title": False, "data": cloud_between(upper_outer, upper_middle, bullish_bright, show_cloud), "color": bullish_bright, "type": "cloud", "opacity": 0.16, "overlay": True},
    {"name": "Upper Inner Fill", "title": False, "data": cloud_between(upper_middle, upper_inner, bullish_dark, show_cloud), "color": bullish_dark, "type": "cloud", "opacity": 0.12, "overlay": True},
    {"name": "Cloud Lower Inner", "data": finite_line(lower_inner, show_cloud), "color": "rgba(183,28,28,0.30)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Cloud Lower Mid", "data": finite_line(lower_middle, show_cloud), "color": "rgba(255,23,68,0.25)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Cloud Lower Outer", "data": finite_line(lower_outer, show_cloud), "color": "rgba(255,23,68,0.40)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Lower Inner Fill", "title": False, "data": cloud_between(lower_inner, lower_middle, bearish_dark, show_cloud), "color": bearish_dark, "type": "cloud", "opacity": 0.12, "overlay": True},
    {"name": "Lower Outer Fill", "title": False, "data": cloud_between(lower_middle, lower_outer, bearish_bright, show_cloud), "color": bearish_bright, "type": "cloud", "opacity": 0.16, "overlay": True},
    {"name": "Trend Tracker Bullish", "data": bullish_tracker, "color": bullish_bright, "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Trend Tracker Bearish", "data": bearish_tracker, "color": bearish_bright, "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Stop Loss", "data": stop_plot, "color": bearish_bright, "type": "line", "lineWidth": 2, "lineStyle": "dashed", "overlay": True},
    {"name": "Take Profit 1", "data": tp1_plot, "color": "rgba(0,230,118,0.60)", "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": True},
    {"name": "Take Profit 2", "data": tp2_plot, "color": "rgba(0,230,118,0.80)", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True},
    {"name": "Take Profit 3", "data": tp3_plot, "color": bullish_bright, "type": "line", "lineWidth": 2, "overlay": True},
] + zone_plots

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "tables": tables,
    "bar_colors": bar_colors,
    "background_colors": background_colors,
}
