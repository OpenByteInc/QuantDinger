# ============================================================
# QuantDinger Boom Hunter Pro
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: analytical oscillator only; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Boom Hunter Pro"
my_indicator_description = (
    "Combines three normalized Ehlers-style oscillator waves, a volume-aware "
    "WaveTrend pressure model, pivot breaks, and governed long/short events."
)

# @param show_fibonacci bool false Show Fibonacci guide levels
# @param show_entries bool true Show warning and entry points
# @param square_line bool true Square the yellow wave transfer function
# @param show_downward_boom bool true Show downward boom line
# @param show_longs bool true Show long events
# @param show_shorts bool true Show short events
# @param lp_period_1 int 6 Main oscillator low-pass period range=2:100:1
# @param k1 float 0.0 Main oscillator quotient constant range=-0.99:0.99:0.01
# @param trigger_length int 2 Trigger smoothing range=1:50:1
# @param oscillator_color str #2962FF Main oscillator color
# @param trigger_color str #F8FAFC Trigger color
# @param lp_period_2 int 27 Red wave low-pass period range=2:200:1
# @param k1_red float 0.8 Red wave first quotient constant range=-0.99:0.99:0.01
# @param k2_red float 0.3 Red wave second quotient constant range=-0.99:0.99:0.01
# @param red_wave_color str #F23645 Red wave line color
# @param red_fill_color str #F23645 Red wave fill color
# @param lp_period_3 int 11 Yellow wave low-pass period range=2:200:1
# @param k1_yellow float 0.99 Yellow wave quotient constant range=-0.99:0.99:0.01
# @param yellow_wave_color str #FACC15 Yellow wave color
# @param wave_master int 9 WaveTrend master length range=2:100:1
# @param wave_time int 6 WaveTrend time length range=1:100:1
# @param money_flow_length int 3 Money-flow length range=1:100:1
# @param regression_length int 21 Wave regression length range=2:200:1
# @param regression_offset int 0 Wave regression offset range=0:100:1
# @param long_regression_length int 200 Long LSMA length range=20:500:1
# @param show_break_lines bool false Show oscillator pivot levels
# @param resistance_left int 1 Resistance pivot left bars range=1:30:1
# @param resistance_right int 1 Resistance pivot right bars range=1:30:1
# @param support_long_left int 5 Long support pivot left bars range=1:30:1
# @param support_long_right int 5 Long support pivot right bars range=1:30:1
# @param support_short_left int 1 Short support pivot left bars range=1:30:1
# @param support_short_right int 1 Short support pivot right bars range=1:30:1
# @param drag_threshold int 3 Drag state threshold range=1:30:1
# @param color_price_bars bool false Color price bars by oscillator state
# @param pump_bar_colors bool false Use pump direction colors

show_fibonacci = bool(params.get("show_fibonacci", False))
show_entries = bool(params.get("show_entries", True))
square_line = bool(params.get("square_line", True))
show_downward_boom = bool(params.get("show_downward_boom", True))
show_longs = bool(params.get("show_longs", True))
show_shorts = bool(params.get("show_shorts", True))
lp_period_1 = min(100, max(2, int(params.get("lp_period_1", 6))))
k1 = min(0.99, max(-0.99, float(params.get("k1", 0.0))))
trigger_length = min(50, max(1, int(params.get("trigger_length", 2))))
oscillator_color = str(params.get("oscillator_color", "#2962FF"))
trigger_color = str(params.get("trigger_color", "#F8FAFC"))
lp_period_2 = min(200, max(2, int(params.get("lp_period_2", 27))))
k1_red = min(0.99, max(-0.99, float(params.get("k1_red", 0.8))))
k2_red = min(0.99, max(-0.99, float(params.get("k2_red", 0.3))))
red_wave_color = str(params.get("red_wave_color", "#F23645"))
red_fill_color = str(params.get("red_fill_color", "#F23645"))
lp_period_3 = min(200, max(2, int(params.get("lp_period_3", 11))))
k1_yellow = min(0.99, max(-0.99, float(params.get("k1_yellow", 0.99))))
yellow_wave_color = str(params.get("yellow_wave_color", "#FACC15"))
wave_master = min(100, max(2, int(params.get("wave_master", 9))))
wave_time = min(100, max(1, int(params.get("wave_time", 6))))
money_flow_length = min(100, max(1, int(params.get("money_flow_length", 3))))
regression_length = min(200, max(2, int(params.get("regression_length", 21))))
regression_offset = min(100, max(0, int(params.get("regression_offset", 0))))
long_regression_length = min(500, max(20, int(params.get("long_regression_length", 200))))
show_break_lines = bool(params.get("show_break_lines", False))
resistance_left = min(30, max(1, int(params.get("resistance_left", 1))))
resistance_right = min(30, max(1, int(params.get("resistance_right", 1))))
support_long_left = min(30, max(1, int(params.get("support_long_left", 5))))
support_long_right = min(30, max(1, int(params.get("support_long_right", 5))))
support_short_left = min(30, max(1, int(params.get("support_short_left", 1))))
support_short_right = min(30, max(1, int(params.get("support_short_right", 1))))
drag_threshold = min(30, max(1, int(params.get("drag_threshold", 3))))
color_price_bars = bool(params.get("color_price_bars", False))
pump_bar_colors = bool(params.get("pump_bar_colors", False))

df = df.copy()
open_values = df["open"].astype(float).to_numpy(dtype=float)
high_values = df["high"].astype(float).to_numpy(dtype=float)
low_values = df["low"].astype(float).to_numpy(dtype=float)
close_values = df["close"].astype(float).to_numpy(dtype=float)
volume_values = df["volume"].astype(float).to_numpy(dtype=float)
hlc3_values = (high_values + low_values + close_values) / 3.0
n = len(df)


def finite_list(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def simple_average(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def exponential_average(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def wilder_rsi(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) <= period:
        return result
    changes = np.diff(values)
    gains = np.maximum(changes, 0.0)
    losses = np.maximum(-changes, 0.0)
    average_gain = float(np.mean(gains[:period]))
    average_loss = float(np.mean(losses[:period]))
    for index in range(period, len(values)):
        if index > period:
            average_gain = (average_gain * (period - 1) + gains[index - 1]) / period
            average_loss = (average_loss * (period - 1) + losses[index - 1]) / period
        if average_loss <= 1e-14:
            result[index] = 100.0 if average_gain > 1e-14 else 0.0
        else:
            result[index] = 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return result


def rolling_sum(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.sum(window))
    return result


def linear_regression(values, period, offset_value=0):
    result = np.full(len(values), np.nan, dtype=float)
    x_values = np.arange(period, dtype=float)
    x_mean = float(np.mean(x_values))
    denominator = float(np.sum((x_values - x_mean) ** 2))
    evaluation_x = float(period - 1 - min(period - 1, max(0, offset_value)))
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if not np.all(np.isfinite(window)):
            continue
        y_mean = float(np.mean(window))
        slope = float(np.sum((x_values - x_mean) * (window - y_mean)) / denominator)
        intercept = y_mean - slope * x_mean
        result[index] = intercept + slope * evaluation_x
    return result


def transfer(value, constant):
    denominator = constant * value + 1.0
    if abs(denominator) <= 1e-12:
        return 1.0 if value + constant >= 0.0 else -1.0
    return (value + constant) / denominator


def eot_wave(values, low_pass_period, first_constant, second_constant, third_variant=False):
    hp = np.zeros(len(values), dtype=float)
    filtered = np.zeros(len(values), dtype=float)
    peak = np.zeros(len(values), dtype=float)
    normalized = np.zeros(len(values), dtype=float)
    first = np.zeros(len(values), dtype=float)
    second = np.zeros(len(values), dtype=float)
    pi_value = 2.0 * np.arcsin(1.0)
    angle = 0.707 * 2.0 * pi_value / 100.0
    alpha = (np.cos(angle) + np.sin(angle) - 1.0) / np.cos(angle)
    hp_factor = (1.0 - alpha / 3.0) * (1.0 - alpha / 2.0) if third_variant else (1.0 - alpha / 2.0) ** 2
    decay = 1.0 - alpha
    a_value = np.exp(-1.414 * pi_value / low_pass_period)
    b_value = 2.0 * a_value * np.cos(1.414 * pi_value / low_pass_period)
    c2 = b_value
    c3 = -(a_value * a_value)
    c1 = 1.0 - c2 - c3
    for index in range(len(values)):
        price_1 = values[index - 1] if index >= 1 else 0.0
        price_2 = values[index - 2] if index >= 2 else 0.0
        hp_1 = hp[index - 1] if index >= 1 else 0.0
        hp_2 = hp[index - 2] if index >= 2 else 0.0
        filter_1 = filtered[index - 1] if index >= 1 else 0.0
        filter_2 = filtered[index - 2] if index >= 2 else 0.0
        peak_1 = peak[index - 1] if index >= 1 else 0.0
        hp[index] = hp_factor * (values[index] - 2.0 * price_1 + price_2) + 2.0 * decay * hp_1 - decay * decay * hp_2
        filtered[index] = c1 * (hp[index] + hp_1) / 2.0 + c2 * filter_1 + c3 * filter_2
        peak[index] = max(0.991 * peak_1, abs(filtered[index]))
        normalized[index] = filtered[index] / peak[index] if peak[index] > 1e-14 else 0.0
        first[index] = transfer(normalized[index], first_constant)
        second[index] = transfer(normalized[index], second_constant)
    return first, second, normalized, filtered, peak


def cross_over(left, right):
    result = np.zeros(len(left), dtype=bool)
    if np.isscalar(right):
        right_values = np.full(len(left), float(right), dtype=float)
    else:
        right_values = np.asarray(right, dtype=float)
    for index in range(1, len(left)):
        if all(np.isfinite(value) for value in (left[index], left[index - 1], right_values[index], right_values[index - 1])):
            result[index] = left[index] > right_values[index] and left[index - 1] <= right_values[index - 1]
    return result


def cross_under(left, right):
    result = np.zeros(len(left), dtype=bool)
    if np.isscalar(right):
        right_values = np.full(len(left), float(right), dtype=float)
    else:
        right_values = np.asarray(right, dtype=float)
    for index in range(1, len(left)):
        if all(np.isfinite(value) for value in (left[index], left[index - 1], right_values[index], right_values[index - 1])):
            result[index] = left[index] < right_values[index] and left[index - 1] >= right_values[index - 1]
    return result


def bars_since(mask):
    result = np.full(len(mask), np.inf, dtype=float)
    last_index = -1
    for index, active in enumerate(mask):
        if bool(active):
            last_index = index
        if last_index >= 0:
            result[index] = float(index - last_index)
    return result


def confirmed_pivot_levels(values, left_bars, right_bars, high_pivot):
    result = np.full(len(values), np.nan, dtype=float)
    current_level = np.nan
    start = left_bars + right_bars
    for confirmed_at in range(len(values)):
        if confirmed_at >= start:
            pivot_index = confirmed_at - right_bars
            window = values[pivot_index - left_bars:pivot_index + right_bars + 1]
            if len(window) == left_bars + right_bars + 1 and np.all(np.isfinite(window)):
                pivot_value = values[pivot_index]
                if high_pivot and pivot_value >= float(np.max(window)):
                    current_level = float(pivot_value)
                if not high_pivot and pivot_value <= float(np.min(window)):
                    current_level = float(pivot_value)
        if np.isfinite(current_level):
            result[confirmed_at] = current_level
    return result


def shifted(values, bars):
    result = np.full(len(values), np.nan, dtype=float)
    if bars >= 0:
        if bars < len(values):
            result[bars:] = values[:len(values) - bars] if bars else values
    elif -bars < len(values):
        result[:bars] = values[-bars:]
    return result


def segmented_level(values):
    result = values.copy()
    for index in range(1, len(result)):
        if np.isfinite(result[index]) and np.isfinite(result[index - 1]) and abs(result[index] - result[index - 1]) > 1e-12:
            result[index] = np.nan
    return result


def marker_points(mask, values, color, size=5.0):
    points = []
    for index, active in enumerate(mask):
        if bool(active) and np.isfinite(values[index]):
            points.append({"value": float(values[index]), "color": color, "size": float(size)})
        else:
            points.append(None)
    return points


def colored_line(values, colors):
    points = []
    for index, value in enumerate(values):
        points.append({"value": float(value), "color": colors[index]} if np.isfinite(value) else None)
    return points


def cloud_between(upper_values, lower_values, enabled=True):
    points = []
    for index in range(n):
        if enabled and np.isfinite(upper_values[index]) and np.isfinite(lower_values[index]):
            points.append({"upper": float(max(upper_values[index], lower_values[index])), "lower": float(min(upper_values[index], lower_values[index]))})
        else:
            points.append(None)
    return points


quotient_1, quotient_2, normalized_1, filtered_1, peak_1 = eot_wave(
    close_values, lp_period_1, k1, 0.3, False
)
quotient_3, quotient_4, normalized_2, filtered_2, peak_2 = eot_wave(
    close_values, lp_period_2, k1_red, k2_red, False
)
yellow_constant = 0.9999 if square_line else k1_yellow
quotient_5, quotient_6, normalized_3, filtered_3, peak_3 = eot_wave(
    close_values, lp_period_3, yellow_constant, -yellow_constant, True
)

q1 = quotient_1 * 60.0 + 50.0
q3 = quotient_3 * 60.0 + 50.0
q4 = quotient_4 * 60.0 + 50.0
q5 = quotient_5 * 60.0 + 50.0
q6 = quotient_6 * 60.0 + 50.0
trigger = simple_average(q1, trigger_length)

ema_source = exponential_average(hlc3_values, wave_master)
deviation_source = np.abs(hlc3_values - ema_source)
ema_deviation = exponential_average(deviation_source, wave_master)
tci_input = np.full(n, np.nan, dtype=float)
for index in range(n):
    denominator = 0.025 * ema_deviation[index]
    if np.isfinite(ema_source[index]) and np.isfinite(denominator) and abs(denominator) > 1e-14:
        tci_input[index] = (hlc3_values[index] - ema_source[index]) / denominator
tci_values = exponential_average(tci_input, wave_time) + 50.0

change_hlc3 = np.zeros(n, dtype=float)
if n > 1:
    change_hlc3[1:] = np.diff(hlc3_values)
positive_flow = np.where(change_hlc3 > 0.0, volume_values * hlc3_values, 0.0)
negative_flow = np.where(change_hlc3 < 0.0, volume_values * hlc3_values, 0.0)
positive_sum = rolling_sum(positive_flow, money_flow_length)
negative_sum = rolling_sum(negative_flow, money_flow_length)
money_flow = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not np.isfinite(positive_sum[index]) or not np.isfinite(negative_sum[index]):
        continue
    if negative_sum[index] <= 1e-14:
        money_flow[index] = 100.0 if positive_sum[index] > 1e-14 else 50.0
    else:
        money_flow[index] = 100.0 - 100.0 / (1.0 + positive_sum[index] / negative_sum[index])

rsi_values = wilder_rsi(hlc3_values, money_flow_length)
wave_tradition = np.full(n, np.nan, dtype=float)
for index in range(n):
    values = (tci_values[index], money_flow[index], rsi_values[index])
    if all(np.isfinite(value) for value in values):
        wave_tradition[index] = float(sum(values) / 3.0)
wave_average = simple_average(wave_tradition, 6)
wave_regression = linear_regression(wave_tradition, regression_length, regression_offset)
long_lsma = linear_regression(wave_regression, long_regression_length, 0)

pressure = np.full(n, np.nan, dtype=float)
for index in range(n):
    if np.isfinite(trigger[index]) and np.isfinite(wave_average[index]):
        if wave_average[index] < 20.0:
            pressure[index] = trigger[index] + 9.0
        elif wave_average[index] > 80.0:
            pressure[index] = trigger[index] - 9.0

resistance = confirmed_pivot_levels(q1, resistance_left, resistance_right, True)
support_long = confirmed_pivot_levels(q1, support_long_left, support_long_right, False)
support_short = confirmed_pivot_levels(q1, support_short_left, support_short_right, False)
resistance_short = confirmed_pivot_levels(q1, support_short_left, support_short_right, True)

q1_over_trigger = cross_over(q1, trigger)
q1_under_trigger = cross_under(q1, trigger)
entry_zone = cross_under(quotient_2, -0.9)
break_support = cross_under(q1, support_long)
break_short_support = cross_under(q1, support_short)
break_short_resistance = cross_under(q1, resistance_short)
break_resistance_raw = cross_over(q1, resistance)

cross_state = np.zeros(n, dtype=int)
drag_count = np.zeros(n, dtype=int)
dbreak_count = np.zeros(n, dtype=int)
continuation = np.zeros(n, dtype=bool)
state_cross = 0
state_drag = 0
state_dbreak = 0
state_ubreak = 0
state_dbreak2 = 0
state_ubreak2 = 0
state_cont = 0
state_cont2 = 0
for index in range(n):
    if q1_under_trigger[index]:
        state_cross = 0
    if q1_over_trigger[index]:
        state_cross = 1
    if break_support[index]:
        state_dbreak += 1
    if entry_zone[index]:
        state_dbreak = 0
        state_dbreak2 = 0
        state_ubreak = 0
        state_ubreak2 = 0
        state_cont = 0
        state_cont2 = 0
    if quotient_1[index] <= -1.0:
        state_drag += 1
    if index > 0 and quotient_1[index] > -0.9 and quotient_1[index - 1] <= -0.9:
        state_drag = 0
    if break_resistance_raw[index] and state_dbreak >= 1:
        state_ubreak += 1
        if state_ubreak <= 1:
            state_cont = 1
            continuation[index] = True
        if state_dbreak >= 2 and state_ubreak >= 2 and state_cont <= 2:
            state_cont2 = 1
    if break_short_support[index]:
        state_dbreak2 += 1
        if state_dbreak2 == 1:
            state_ubreak2 = 0
    if break_short_resistance[index]:
        state_ubreak2 += 1
    cross_state[index] = state_cross
    drag_count[index] = state_drag
    dbreak_count[index] = state_dbreak

warn_red = cross_over(quotient_3, -0.9)
warn_main = cross_over(quotient_1, -0.9)
warn_high = cross_under(quotient_1, 0.9)
q1_over_20 = cross_over(q1, 20.0)
q1_over_80 = cross_over(q1, 80.0)
recent_warn_main = bars_since(warn_main)
recent_warn_high = bars_since(warn_high)
recent_q1_over_20 = bars_since(q1_over_20)
recent_q1_over_80 = bars_since(q1_over_80)
recent_low_cross = bars_since(np.logical_and(q1 <= 0.0, q1_under_trigger))
recent_soft_cross = bars_since(np.logical_and(q1 <= 20.0, q1_under_trigger))

long_lime = np.logical_and.reduce((quotient_3 <= -0.9, q1_over_trigger, recent_warn_main <= 7.0, q1 <= 20.0, recent_q1_over_20 <= 21.0))
long_blue = np.logical_and(recent_low_cross <= 5.0, q1_over_trigger)
long_gray = np.logical_and.reduce((recent_soft_cross <= 11.0, q1_over_trigger, q1 <= 60.0))
long_yellow = np.logical_and(quotient_3 <= -0.9, q1_over_trigger)
short_red = np.logical_and.reduce((quotient_3 >= -0.9, q1_under_trigger, recent_warn_high <= 7.0, q1 >= 99.0, recent_q1_over_80 <= 21.0))

overbought_orange = np.logical_and(np.logical_or(cross_over(quotient_5, quotient_6), cross_under(quotient_5, quotient_6)), quotient_5 > 0.5)
overbought_red = np.logical_and(np.logical_or(cross_over(quotient_3, quotient_4), cross_under(quotient_3, quotient_4)), quotient_3 > 0.0)
wave_over_80 = cross_over(wave_average, 80.0)
wave_under_80 = cross_under(wave_average, 80.0)
wave_under_20 = cross_under(wave_average, 20.0)
wave_over_20 = cross_over(wave_average, 20.0)
bounce_down = np.logical_and(bars_since(wave_over_80) <= 1.0, wave_under_80)
bounce_up = np.logical_and(bars_since(wave_under_20) <= 1.0, wave_over_20)

main_colors = []
for index in range(n):
    if cross_state[index] == 1:
        main_colors.append("#00FFAA")
    elif drag_count[index] >= drag_threshold:
        main_colors.append("#7E22CE")
    elif quotient_1[index] <= -0.8:
        main_colors.append("#FACC15")
    elif quotient_3[index] <= -0.9:
        main_colors.append("#F8FAFC")
    elif cross_state[index] == 0:
        main_colors.append("#F23645")
    else:
        main_colors.append("#94A3B8")

bar_colors = []
for index in range(n):
    if not color_price_bars:
        bar_colors.append(None)
    elif pump_bar_colors:
        if np.isfinite(trigger[index]):
            bar_colors.append("#00FFAA" if q1[index] > trigger[index] else "#F23645" if q1[index] < trigger[index] else "#94A3B8")
        else:
            bar_colors.append(None)
    elif drag_count[index] >= drag_threshold:
        bar_colors.append("#7E22CE")
    elif quotient_3[index] <= -0.9:
        bar_colors.append("#F8FAFC")
    elif quotient_1[index] <= -0.9:
        bar_colors.append("#FACC15")
    else:
        bar_colors.append(None)

plots = [
    {"name": "Main Boom", "data": colored_line(q1, main_colors), "color": oscillator_color, "type": "line", "lineWidth": 3, "overlay": False},
    {"name": "Main Reference", "title": False, "data": finite_list(q1), "color": oscillator_color, "type": "line", "lineWidth": 1, "opacity": 0.35, "overlay": False},
    {"name": "Trigger", "data": finite_list(trigger), "color": trigger_color, "type": "line", "lineWidth": 2, "overlay": False},
    {"name": "Red Wave Fast", "data": finite_list(q3), "color": red_wave_color, "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Red Wave Slow", "data": finite_list(q4), "color": red_wave_color, "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Red Wave Fill", "title": False, "data": cloud_between(q3, q4), "color": red_fill_color, "type": "cloud", "opacity": 0.10, "overlay": False},
    {"name": "Yellow Boom", "data": finite_list(q5), "color": yellow_wave_color, "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Downward Boom", "data": finite_list(q6) if show_downward_boom else [None] * n, "color": "#2563EB", "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Pressure", "title": False, "data": marker_points(np.isfinite(pressure), pressure, "#00FFAA", 4.0), "color": "#00FFAA", "type": "scatter", "overlay": False},
]

for level in (84.0, 64.0, 50.0, 36.0, 18.0):
    plots.append({
        "name": f"Fibonacci {int(level)}",
        "title": False,
        "data": [level] * n if show_fibonacci else [None] * n,
        "color": "rgba(41,98,255,0.50)",
        "type": "line",
        "lineWidth": 1,
        "overlay": False,
    })

if show_break_lines:
    plots.extend([
        {"name": "Resistance", "data": finite_list(shifted(segmented_level(resistance), -(resistance_right + 1))), "color": "#F23645", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Long Support", "data": finite_list(shifted(segmented_level(support_long), -(support_long_right + 1))), "color": "#2962FF", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Short Support", "data": finite_list(shifted(segmented_level(support_short), -(support_short_right + 1))), "color": "#94A3B8", "type": "line", "lineWidth": 1, "overlay": False},
    ])

if show_entries:
    plots.extend([
        {"name": "Orange Exit Warning", "title": False, "data": marker_points(overbought_orange, np.full(n, 6.0), "#F97316", 4.0), "color": "#F97316", "type": "scatter", "overlay": False},
        {"name": "Red Exit Warning", "title": False, "data": marker_points(overbought_red, np.full(n, 10.0), "#F23645", 4.0), "color": "#F23645", "type": "scatter", "overlay": False},
        {"name": "Bounce Down", "title": False, "data": marker_points(bounce_down, wave_average + 9.0, "#FACC15", 4.0), "color": "#FACC15", "type": "scatter", "overlay": False},
        {"name": "Bounce Up", "title": False, "data": marker_points(bounce_up, wave_average - 9.0, "#FACC15", 4.0), "color": "#FACC15", "type": "scatter", "overlay": False},
    ])

if show_longs:
    plots.extend([
        {"name": "Long Gray", "title": False, "data": marker_points(long_gray, np.full(n, 104.0), "#CBD5E1", 5.0), "color": "#CBD5E1", "type": "scatter", "overlay": False},
        {"name": "Long Yellow", "title": False, "data": marker_points(long_yellow, np.full(n, 100.0), "#FACC15", 5.0), "color": "#FACC15", "type": "scatter", "overlay": False},
        {"name": "Long Blue", "title": False, "data": marker_points(long_blue, np.full(n, 96.0), "#2563EB", 5.0), "color": "#2563EB", "type": "scatter", "overlay": False},
        {"name": "Long Lime", "title": False, "data": marker_points(long_lime, np.full(n, 92.0), "#84CC16", 5.0), "color": "#84CC16", "type": "scatter", "overlay": False},
        {"name": "Continuation", "title": False, "data": marker_points(continuation, np.full(n, 4.0), "#00FFAA", 5.0), "color": "#00FFAA", "type": "scatter", "overlay": False},
    ])

if show_shorts:
    plots.append({"name": "Short", "title": False, "data": marker_points(short_red, np.full(n, 108.0), "#F23645", 6.0), "color": "#F23645", "type": "scatter", "overlay": False})

long_event = np.logical_or.reduce((long_gray, long_yellow, long_blue, long_lime)) if show_longs else np.zeros(n, dtype=bool)
short_event = short_red if show_shorts else np.zeros(n, dtype=bool)
last_index = n - 1

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [
        {"type": "buy", "text": "", "name": "Boom Long", "data": [float(low_values[index]) if long_event[index] else None for index in range(n)], "color": "#00FFAA", "renderMode": "events"},
        {"type": "sell", "text": "", "name": "Boom Short", "data": [float(high_values[index]) if short_event[index] else None for index in range(n)], "color": "#F23645", "renderMode": "events"},
    ],
    "tables": [],
    "bar_colors": bar_colors,
    "background_colors": [None] * n,
    "layers": [],
    "calculatedVars": {
        "mainBoom": float(q1[last_index]) if n else None,
        "trigger": float(trigger[last_index]) if n and np.isfinite(trigger[last_index]) else None,
        "redWave": float(q3[last_index]) if n else None,
        "yellowWave": float(q5[last_index]) if n else None,
        "wavePressure": float(wave_average[last_index]) if n and np.isfinite(wave_average[last_index]) else None,
        "longEvents": int(np.count_nonzero(long_event)),
        "shortEvents": int(np.count_nonzero(short_event)),
        "continuations": int(np.count_nonzero(continuation)),
        "crossState": int(cross_state[last_index]) if n else 0,
        "dragCount": int(drag_count[last_index]) if n else 0,
    },
}
