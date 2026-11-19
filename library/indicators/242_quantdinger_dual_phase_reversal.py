# ============================================================
# QuantDinger Dual-Phase Reversal
# Independent Python implementation for the QuantDinger runtime.
# ============================================================

my_indicator_name = "QuantDinger Dual-Phase Reversal"
my_indicator_description = (
    "Tracks nine-step momentum exhaustion and thirteen-step terminal exhaustion, "
    "with range pivots, directional volume pressure, and volume-weighted candles."
)

# @param phase_display str Completed_Only Phase display values=Completed_Only,All_Sequence_Steps,None
# @param show_phase_levels bool true Show momentum support and resistance
# @param show_invalidation bool false Show momentum and terminal invalidation levels
# @param pivot_timeframe str Auto Pivot timeframe values=Auto,1H,4H,1D,1W,1MO,3MO,12MO
# @param show_range_pivot bool true Show current range pivot
# @param show_developing_pivot bool false Show developing range pivot
# @param show_historical_pivots bool false Show historical range pivots
# @param resistance_color str #F23645 Resistance color
# @param support_color str #089981 Support color
# @param pressure_enabled bool true Show volume pressure
# @param pressure_color str #00BCD4 Pressure color
# @param pressure_length int 13 Pressure length range=1:100:1
# @param pressure_upper float 75 Pressure upper band range=0:100:1
# @param pressure_lower float 25 Pressure lower band range=0:100:1
# @param pressure_lookback int 200 Pressure display length range=10:475:5
# @param pressure_placement str Bottom Pressure placement values=Top,Middle,Bottom
# @param pressure_height int 9 Pressure height range=1:10:1
# @param pressure_offset int 5 Pressure vertical offset range=-3:10:1
# @param volume_weighted_bars bool true Paint volume-weighted candles
# @param volume_ma_length int 89 Volume MA length range=1:500:1
# @param high_volume_multiplier float 1.618 High-volume multiplier range=1.0:10.0:0.1
# @param low_volume_multiplier float 0.618 Low-volume multiplier range=0.1:1.0:0.1
# @param marker_lookback int 140 Event marker display length range=20:500:10
# @param show_dashboard bool true Show dashboard
# @param dashboard_position str top-right Dashboard position values=top-right,top-left,bottom-left,bottom-right

phase_display = str(params.get("phase_display", "Completed_Only"))
show_phase_levels = bool(params.get("show_phase_levels", True))
show_invalidation = bool(params.get("show_invalidation", False))
pivot_timeframe = str(params.get("pivot_timeframe", "Auto")).upper()
show_range_pivot = bool(params.get("show_range_pivot", True))
show_developing_pivot = bool(params.get("show_developing_pivot", False))
show_historical_pivots = bool(params.get("show_historical_pivots", False))
resistance_color = str(params.get("resistance_color", "#F23645"))
support_color = str(params.get("support_color", "#089981"))
pressure_enabled = bool(params.get("pressure_enabled", True))
pressure_color = str(params.get("pressure_color", "#00BCD4"))
pressure_length = min(100, max(1, int(params.get("pressure_length", 13))))
pressure_upper = min(100.0, max(0.0, float(params.get("pressure_upper", 75.0))))
pressure_lower = min(100.0, max(0.0, float(params.get("pressure_lower", 25.0))))
pressure_lookback = min(475, max(10, int(params.get("pressure_lookback", 200))))
pressure_placement = str(params.get("pressure_placement", "Bottom")).title()
pressure_height = min(10, max(1, int(params.get("pressure_height", 9))))
pressure_offset = min(10, max(-3, int(params.get("pressure_offset", 5))))
volume_weighted_bars = bool(params.get("volume_weighted_bars", True))
volume_ma_length = min(500, max(1, int(params.get("volume_ma_length", 89))))
high_volume_multiplier = min(10.0, max(1.0, float(params.get("high_volume_multiplier", 1.618))))
low_volume_multiplier = min(1.0, max(0.1, float(params.get("low_volume_multiplier", 0.618))))
marker_lookback = min(500, max(20, int(params.get("marker_lookback", 140))))
show_dashboard = bool(params.get("show_dashboard", True))
dashboard_position = str(params.get("dashboard_position", "top-right")).lower()

df = df.copy()
frame = df.reset_index(drop=True)
if "time" not in frame.columns:
    frame["time"] = np.arange(len(frame), dtype=float) * 3600.0
open_values = pd.to_numeric(frame["open"], errors="coerce").to_numpy(dtype=float)
high_values = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype=float)
low_values = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype=float)
close_values = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype=float)
volume_values = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
time_values = pd.to_numeric(frame["time"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
n = len(frame)
time_seconds = time_values.copy()
if n and np.nanmax(np.abs(time_seconds)) > 10000000000:
    time_seconds = time_seconds / 1000.0


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def rgba(color, alpha):
    value = str(color).strip().lstrip("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    try:
        red, green, blue = int(value[:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except (ValueError, IndexError):
        red, green, blue = 128, 128, 128
    return f"rgba({red},{green},{blue},{min(1.0, max(0.0, float(alpha))):.3f})"


def rolling_sma(values, length):
    return pd.Series(values, dtype=float).rolling(length, min_periods=length).mean().to_numpy(dtype=float)


buy_count = np.zeros(n, dtype=int)
sell_count = np.zeros(n, dtype=int)
buy_qualified = np.zeros(n, dtype=bool)
sell_qualified = np.zeros(n, dtype=bool)
buy_phase_resistance = np.full(n, np.nan, dtype=float)
sell_phase_support = np.full(n, np.nan, dtype=float)
buy_phase_stop = np.full(n, np.nan, dtype=float)
sell_phase_stop = np.full(n, np.nan, dtype=float)

buy_terminal_count = np.zeros(n, dtype=int)
sell_terminal_count = np.zeros(n, dtype=int)
buy_terminal_active = np.zeros(n, dtype=bool)
sell_terminal_active = np.zeros(n, dtype=bool)
buy_terminal_stop = np.full(n, np.nan, dtype=float)
sell_terminal_stop = np.full(n, np.nan, dtype=float)
buy_terminal_target = np.full(n, np.nan, dtype=float)
sell_terminal_target = np.full(n, np.nan, dtype=float)

buy_state = 0
sell_state = 0
active_resistance = np.nan
active_support = np.nan
active_buy_phase_stop = np.nan
active_sell_phase_stop = np.nan
buy_phase_lowest = np.nan
buy_phase_high_at_low = np.nan
sell_phase_highest = np.nan
sell_phase_low_at_high = np.nan

buy_terminal_state = 0
sell_terminal_state = 0
buy_terminal_running = False
sell_terminal_running = False
buy_reference_close_8 = np.nan
sell_reference_close_8 = np.nan
buy_terminal_lowest = np.nan
buy_terminal_highest = np.nan
buy_terminal_high_at_low = np.nan
buy_terminal_low_at_high = np.nan
sell_terminal_lowest = np.nan
sell_terminal_highest = np.nan
sell_terminal_high_at_low = np.nan
sell_terminal_low_at_high = np.nan
active_buy_terminal_stop = np.nan
active_buy_terminal_target = np.nan
active_sell_terminal_stop = np.nan
active_sell_terminal_target = np.nan

for index in range(n):
    buy_condition = index >= 4 and close_values[index] < close_values[index - 4]
    sell_condition = index >= 4 and close_values[index] > close_values[index - 4]
    buy_shift = index >= 5 and buy_condition and close_values[index - 1] > close_values[index - 5]
    sell_shift = index >= 5 and sell_condition and close_values[index - 1] < close_values[index - 5]

    if buy_condition:
        buy_state = (1 if buy_shift else 0) if buy_state in (0, 9) else buy_state + 1
        sell_state = 0
    elif sell_condition:
        sell_state = (1 if sell_shift else 0) if sell_state in (0, 9) else sell_state + 1
        buy_state = 0
    else:
        buy_state = 0
        sell_state = 0
    buy_count[index] = buy_state
    sell_count[index] = sell_state

    if index >= 3:
        buy_qualified[index] = (
            (low_values[index] <= low_values[index - 3] and low_values[index] <= low_values[index - 2])
            or (low_values[index - 1] <= low_values[index - 3] and low_values[index - 1] <= low_values[index - 2])
        )
        sell_qualified[index] = (
            (high_values[index] >= high_values[index - 3] and high_values[index] >= high_values[index - 2])
            or (high_values[index - 1] >= high_values[index - 3] and high_values[index - 1] >= high_values[index - 2])
        )

    if buy_state == 1:
        buy_phase_lowest = float(low_values[index])
        buy_phase_high_at_low = float(high_values[index])
    if buy_state > 0:
        if not np.isfinite(buy_phase_lowest) or low_values[index] <= buy_phase_lowest:
            buy_phase_lowest = float(low_values[index])
            buy_phase_high_at_low = float(high_values[index])
    if buy_state == 9:
        active_resistance = float(np.max(high_values[max(0, index - 8):index + 1]))
        active_buy_phase_stop = 2.0 * buy_phase_lowest - buy_phase_high_at_low
    else:
        if np.isfinite(active_resistance) and close_values[index] > active_resistance:
            active_resistance = np.nan
        if np.isfinite(active_buy_phase_stop) and close_values[index] < active_buy_phase_stop:
            active_buy_phase_stop = np.nan

    if sell_state == 1:
        sell_phase_highest = float(high_values[index])
        sell_phase_low_at_high = float(low_values[index])
    if sell_state > 0:
        if not np.isfinite(sell_phase_highest) or high_values[index] >= sell_phase_highest:
            sell_phase_highest = float(high_values[index])
            sell_phase_low_at_high = float(low_values[index])
    if sell_state == 9:
        active_support = float(np.min(low_values[max(0, index - 8):index + 1]))
        active_sell_phase_stop = 2.0 * sell_phase_highest - sell_phase_low_at_high
    else:
        if np.isfinite(active_support) and close_values[index] < active_support:
            active_support = np.nan
        if np.isfinite(active_sell_phase_stop) and close_values[index] > active_sell_phase_stop:
            active_sell_phase_stop = np.nan

    buy_phase_resistance[index] = active_resistance
    sell_phase_support[index] = active_support
    buy_phase_stop[index] = active_buy_phase_stop
    sell_phase_stop[index] = active_sell_phase_stop

    prior_buy_terminal = buy_terminal_state
    prior_sell_terminal = sell_terminal_state
    prior_buy_running = buy_terminal_running
    prior_sell_running = sell_terminal_running

    if buy_state == 9 and prior_buy_terminal == 0:
        buy_terminal_running = True
    elif sell_state == 9 or prior_buy_terminal == 13 or (
        np.isfinite(active_resistance) and close_values[index] > active_resistance
    ):
        buy_terminal_running = False
    else:
        buy_terminal_running = prior_buy_running

    buy_terminal_condition = index >= 2 and close_values[index] <= low_values[index - 2]
    if buy_terminal_running:
        if buy_state == 9:
            buy_terminal_state = 1 if buy_terminal_condition else 0
        elif buy_terminal_condition:
            buy_terminal_state = prior_buy_terminal + 1
        else:
            buy_terminal_state = prior_buy_terminal
    else:
        buy_terminal_state = 0
    buy_terminal_fail = (
        buy_terminal_state == 13 and buy_terminal_condition
        and np.isfinite(buy_reference_close_8) and low_values[index] >= buy_reference_close_8
    )
    if buy_terminal_fail:
        buy_terminal_state = 12
    if buy_terminal_state == 8 and buy_terminal_state != prior_buy_terminal:
        buy_reference_close_8 = float(close_values[index])

    if sell_state == 9 and prior_sell_terminal == 0:
        sell_terminal_running = True
    elif buy_state == 9 or prior_sell_terminal == 13 or (
        np.isfinite(active_support) and close_values[index] < active_support
    ):
        sell_terminal_running = False
    else:
        sell_terminal_running = prior_sell_running

    sell_terminal_condition = index >= 2 and close_values[index] >= high_values[index - 2]
    if sell_terminal_running:
        if sell_state == 9:
            sell_terminal_state = 1 if sell_terminal_condition else 0
        elif sell_terminal_condition:
            sell_terminal_state = prior_sell_terminal + 1
        else:
            sell_terminal_state = prior_sell_terminal
    else:
        sell_terminal_state = 0
    sell_terminal_fail = (
        sell_terminal_state == 13 and sell_terminal_condition
        and np.isfinite(sell_reference_close_8) and high_values[index] <= sell_reference_close_8
    )
    if sell_terminal_fail:
        sell_terminal_state = 12
    if sell_terminal_state == 8 and sell_terminal_state != prior_sell_terminal:
        sell_reference_close_8 = float(close_values[index])

    if buy_terminal_state == 1:
        buy_terminal_lowest = float(low_values[index])
        buy_terminal_highest = float(high_values[index])
        buy_terminal_high_at_low = float(high_values[index])
        buy_terminal_low_at_high = float(low_values[index])
    if buy_terminal_running:
        if not np.isfinite(buy_terminal_highest) or high_values[index] >= buy_terminal_highest:
            buy_terminal_highest = float(high_values[index])
            buy_terminal_low_at_high = float(low_values[index])
        if not np.isfinite(buy_terminal_lowest) or low_values[index] <= buy_terminal_lowest:
            buy_terminal_lowest = float(low_values[index])
            buy_terminal_high_at_low = float(high_values[index])
    if buy_terminal_state == 13:
        active_buy_terminal_target = 2.0 * buy_terminal_highest - buy_terminal_low_at_high
        active_buy_terminal_stop = 2.0 * buy_terminal_lowest - buy_terminal_high_at_low
    else:
        if np.isfinite(active_buy_terminal_target) and (
            close_values[index] > active_buy_terminal_target
            or (not np.isfinite(active_buy_terminal_stop) and sell_terminal_state == 13)
        ):
            active_buy_terminal_target = np.nan
        if np.isfinite(active_buy_terminal_stop) and (
            close_values[index] < active_buy_terminal_stop
            or (not np.isfinite(active_buy_terminal_target) and sell_terminal_state == 13)
        ):
            active_buy_terminal_stop = np.nan

    if sell_terminal_state == 1:
        sell_terminal_lowest = float(low_values[index])
        sell_terminal_highest = float(high_values[index])
        sell_terminal_high_at_low = float(high_values[index])
        sell_terminal_low_at_high = float(low_values[index])
    if sell_terminal_running:
        if not np.isfinite(sell_terminal_highest) or high_values[index] >= sell_terminal_highest:
            sell_terminal_highest = float(high_values[index])
            sell_terminal_low_at_high = float(low_values[index])
        if not np.isfinite(sell_terminal_lowest) or low_values[index] <= sell_terminal_lowest:
            sell_terminal_lowest = float(low_values[index])
            sell_terminal_high_at_low = float(high_values[index])
    if sell_terminal_state == 13:
        active_sell_terminal_stop = 2.0 * sell_terminal_highest - sell_terminal_low_at_high
        active_sell_terminal_target = 2.0 * sell_terminal_lowest - sell_terminal_high_at_low
    else:
        if np.isfinite(active_sell_terminal_stop) and (
            close_values[index] > active_sell_terminal_stop
            or (not np.isfinite(active_sell_terminal_target) and buy_terminal_state == 13)
        ):
            active_sell_terminal_stop = np.nan
        if np.isfinite(active_sell_terminal_target) and (
            close_values[index] < active_sell_terminal_target
            or (not np.isfinite(active_sell_terminal_stop) and buy_terminal_state == 13)
        ):
            active_sell_terminal_target = np.nan

    buy_terminal_count[index] = buy_terminal_state
    sell_terminal_count[index] = sell_terminal_state
    buy_terminal_active[index] = buy_terminal_running
    sell_terminal_active[index] = sell_terminal_running
    buy_terminal_stop[index] = active_buy_terminal_stop
    sell_terminal_stop[index] = active_sell_terminal_stop
    buy_terminal_target[index] = active_buy_terminal_target
    sell_terminal_target[index] = active_sell_terminal_target


runtime_context = globals().get("ctx")
chart_value = str(getattr(runtime_context, "timeframe", "1H") or "1H").strip().upper()
auto_pivot_map = {
    "1M": "1H", "3M": "1H", "5M": "4H", "15M": "4H", "30M": "1D",
    "45M": "1D", "1H": "1D", "2H": "1W", "3H": "1W", "4H": "1W",
    "1D": "1MO", "1W": "3MO",
}
resolved_pivot_timeframe = auto_pivot_map.get(chart_value, "12MO") if pivot_timeframe == "AUTO" else pivot_timeframe
duration_seconds = {"1M": 60, "3M": 180, "5M": 300, "15M": 900, "30M": 1800, "45M": 2700,
                    "1H": 3600, "2H": 7200, "3H": 10800, "4H": 14400, "1D": 86400, "1W": 604800,
                    "1MO": 2592000, "3MO": 7776000, "12MO": 31536000}
if duration_seconds.get(resolved_pivot_timeframe, 86400) <= duration_seconds.get(chart_value, 3600):
    resolved_pivot_timeframe = chart_value


def range_key(timestamp, timeframe_name):
    if timeframe_name in ("1H", "2H", "3H", "4H"):
        return (timeframe_name, int(timestamp // duration_seconds[timeframe_name]))
    stamp = pd.Timestamp(timestamp, unit="s", tz="UTC")
    if timeframe_name == "1D":
        return ("D", int(stamp.year), int(stamp.dayofyear))
    if timeframe_name == "1W":
        iso = stamp.isocalendar()
        return ("W", int(iso.year), int(iso.week))
    if timeframe_name == "3MO":
        return ("Q", int(stamp.year), int((stamp.month - 1) // 3))
    if timeframe_name == "12MO":
        return ("Y", int(stamp.year))
    if timeframe_name == "1MO":
        return ("MO", int(stamp.year), int(stamp.month))
    seconds = duration_seconds.get(timeframe_name, duration_seconds.get(chart_value, 3600))
    return (timeframe_name, int(timestamp // seconds))


def pivot_levels(open_price, high_price, low_price, close_price):
    if close_price < open_price:
        value = high_price + 2.0 * low_price + close_price
    elif close_price > open_price:
        value = 2.0 * high_price + low_price + close_price
    else:
        value = high_price + low_price + 2.0 * close_price
    pivot = value / 4.0
    return value / 2.0 - low_price, pivot, value / 2.0 - high_price


range_resistance = np.full(n, np.nan, dtype=float)
range_pivot = np.full(n, np.nan, dtype=float)
range_support = np.full(n, np.nan, dtype=float)
developing_resistance = np.full(n, np.nan, dtype=float)
developing_pivot = np.full(n, np.nan, dtype=float)
developing_support = np.full(n, np.nan, dtype=float)
range_group = np.zeros(n, dtype=int)
group_starts = []
previous_ohlc = None
current_ohlc = None
current_key = None
group_number = -1

for index in range(n):
    key = range_key(time_seconds[index], resolved_pivot_timeframe)
    if key != current_key:
        if current_ohlc is not None:
            previous_ohlc = current_ohlc
        current_key = key
        current_ohlc = [float(open_values[index]), float(high_values[index]), float(low_values[index]), float(close_values[index])]
        group_number += 1
        group_starts.append(index)
    else:
        current_ohlc[1] = max(current_ohlc[1], float(high_values[index]))
        current_ohlc[2] = min(current_ohlc[2], float(low_values[index]))
        current_ohlc[3] = float(close_values[index])
    range_group[index] = group_number
    if previous_ohlc is not None:
        range_resistance[index], range_pivot[index], range_support[index] = pivot_levels(*previous_ohlc)
    developing_resistance[index], developing_pivot[index], developing_support[index] = pivot_levels(*current_ohlc)

last_group_start = group_starts[-1] if group_starts else 0
current_range_resistance = np.full(n, np.nan, dtype=float)
current_range_pivot = np.full(n, np.nan, dtype=float)
current_range_support = np.full(n, np.nan, dtype=float)
if n:
    current_range_resistance[last_group_start:] = range_resistance[last_group_start:]
    current_range_pivot[last_group_start:] = range_pivot[last_group_start:]
    current_range_support[last_group_start:] = range_support[last_group_start:]

price_range = high_values - low_values
directional_weight = np.divide(
    close_values - open_values,
    price_range,
    out=np.zeros(n, dtype=float),
    where=price_range > 0.0,
) * volume_values
buy_pressure_flow = np.maximum(directional_weight, 0.0)
sell_pressure_flow = np.maximum(-directional_weight, 0.0)
rolling_buy_pressure = pd.Series(buy_pressure_flow).rolling(pressure_length, min_periods=1).sum().to_numpy(dtype=float)
rolling_sell_pressure = pd.Series(sell_pressure_flow).rolling(pressure_length, min_periods=1).sum().to_numpy(dtype=float)
pressure_total = rolling_buy_pressure + rolling_sell_pressure
pressure_oscillator = np.divide(
    100.0 * rolling_buy_pressure,
    pressure_total,
    out=np.full(n, 50.0, dtype=float),
    where=pressure_total != 0.0,
)
pressure_signal = rolling_sma(pressure_oscillator, 9)
pressure_histogram = pressure_oscillator - pressure_signal

volume_average = rolling_sma(volume_values, volume_ma_length)
bar_colors = [None for _ in range(n)]
high_volume = np.zeros(n, dtype=bool)
for index in range(n):
    if not volume_weighted_bars or not np.isfinite(volume_average[index]) or volume_values[index] == 0.0:
        continue
    rising = open_values[index] < close_values[index]
    if volume_values[index] > volume_average[index] * high_volume_multiplier:
        high_volume[index] = True
        bar_colors[index] = "#006400" if rising else "#910000"
    elif volume_values[index] < volume_average[index] * low_volume_multiplier:
        bar_colors[index] = "#7FFFD4" if rising else "#FF9800"

plots = [{
    "name": "Price Anchor", "title": False, "data": finite(close_values),
    "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True,
}]

if show_phase_levels:
    plots.extend([
        {"name": "Momentum Resistance", "data": finite(buy_phase_resistance), "color": resistance_color, "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True},
        {"name": "Momentum Support", "data": finite(sell_phase_support), "color": support_color, "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True},
    ])
if show_invalidation:
    plots.extend([
        {"name": "Buy Momentum Invalidation", "title": False, "data": finite(buy_phase_stop), "color": "#2962FF", "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": True},
        {"name": "Sell Momentum Invalidation", "title": False, "data": finite(sell_phase_stop), "color": "#2962FF", "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": True},
        {"name": "Buy Terminal Invalidation", "title": False, "data": finite(buy_terminal_stop), "color": "#2962FF", "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": True},
        {"name": "Sell Terminal Invalidation", "title": False, "data": finite(sell_terminal_stop), "color": "#2962FF", "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": True},
    ])

if show_historical_pivots:
    plots.extend([
        {"name": "Historical R", "title": False, "data": finite(range_resistance), "color": resistance_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Historical P", "title": False, "data": finite(range_pivot), "color": "#2962FF", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Historical S", "title": False, "data": finite(range_support), "color": support_color, "type": "line", "lineWidth": 1, "overlay": True},
    ])
if show_range_pivot:
    plots.extend([
        {"name": "Range R", "title": False, "data": finite(current_range_resistance), "color": resistance_color, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Range P", "title": False, "data": finite(current_range_pivot), "color": "#2962FF", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Range S", "title": False, "data": finite(current_range_support), "color": support_color, "type": "line", "lineWidth": 2, "overlay": True},
    ])
if show_developing_pivot:
    development_mask = np.arange(n) >= last_group_start
    plots.extend([
        {"name": "Developing R", "title": False, "data": finite(np.where(development_mask, developing_resistance, np.nan)), "color": rgba(resistance_color, 0.65), "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True},
        {"name": "Developing P", "title": False, "data": finite(np.where(development_mask, developing_pivot, np.nan)), "color": rgba("#2962FF", 0.65), "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True},
        {"name": "Developing S", "title": False, "data": finite(np.where(development_mask, developing_support, np.nan)), "color": rgba(support_color, 0.65), "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True},
    ])

pressure_mapped = np.full(n, np.nan, dtype=float)
pressure_upper_level = np.full(n, np.nan, dtype=float)
pressure_lower_level = np.full(n, np.nan, dtype=float)
pressure_middle_level = np.full(n, np.nan, dtype=float)
pressure_cloud = [None for _ in range(n)]
if pressure_enabled and n:
    start = max(0, n - min(n, pressure_lookback))
    highest_price = float(np.max(high_values[start:]))
    lowest_price = float(np.min(low_values[start:]))
    price_change_rate = (highest_price - lowest_price) / highest_price if highest_price != 0.0 else 0.0
    offset = pressure_offset / 10.0
    adjusted_low = lowest_price if pressure_placement == "Middle" else lowest_price * (1.0 - price_change_rate * offset)
    adjusted_high = highest_price if pressure_placement == "Middle" else highest_price * (1.0 + price_change_rate * offset)
    oscillator_high = float(np.max(pressure_oscillator[start:]))
    oscillator_low = float(np.min(pressure_oscillator[start:]))
    if oscillator_high == oscillator_low:
        oscillator_low -= 1.0
    height_scale = price_change_rate / float(max(1, 11 - pressure_height))

    def map_pressure(value):
        if pressure_placement == "Middle":
            return adjusted_low + (value - oscillator_low) * (adjusted_high - adjusted_low) / (oscillator_high - oscillator_low)
        anchor = adjusted_high if pressure_placement == "Top" else adjusted_low
        return anchor * (1.0 + value / max(oscillator_high, 1e-12) * height_scale)

    base = adjusted_high if pressure_placement == "Top" else adjusted_low
    for index in range(start, n):
        pressure_mapped[index] = map_pressure(float(pressure_oscillator[index]))
        pressure_upper_level[index] = map_pressure(pressure_upper)
        pressure_lower_level[index] = map_pressure(pressure_lower)
        pressure_middle_level[index] = map_pressure(50.0)
        if np.isfinite(pressure_histogram[index]):
            mapped_histogram = base * (1.0 + pressure_histogram[index] / max(oscillator_high, 1e-12) * height_scale)
            histogram_color = (
                "#26A69A" if pressure_histogram[index] >= 0.0 and (index == 0 or pressure_histogram[index] > pressure_histogram[index - 1])
                else "#B2DFDB" if pressure_histogram[index] >= 0.0
                else "#FFCDD2" if index > 0 and pressure_histogram[index] > pressure_histogram[index - 1]
                else "#FF5252"
            )
            pressure_cloud[index] = {
                "upper": float(max(base, mapped_histogram)),
                "lower": float(min(base, mapped_histogram)),
                "color": rgba(histogram_color, 0.45),
            }
    plots.extend([
        {"name": "Pressure Histogram", "title": False, "data": pressure_cloud, "type": "cloud", "overlay": True},
        {"name": "Volume Pressure", "data": finite(pressure_mapped), "color": pressure_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Pressure Upper", "title": False, "data": finite(pressure_upper_level), "color": "rgba(148,163,184,0.55)", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True},
        {"name": "Pressure Mid", "title": False, "data": finite(pressure_middle_level), "color": "rgba(148,163,184,0.35)", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True},
        {"name": "Pressure Lower", "title": False, "data": finite(pressure_lower_level), "color": "rgba(148,163,184,0.55)", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True},
    ])

layers = []
if phase_display != "None":
    completed_only = phase_display == "Completed_Only"
    for index in range(max(0, n - marker_lookback), n):
        if buy_count[index] > 0 and (not completed_only or buy_count[index] == 9):
            layers.append({
                "type": "label", "index": index,
                "price": float(low_values[index] if buy_count[index] == 9 else high_values[index]),
                "text": f"M{buy_count[index]}" + ("Q" if buy_count[index] >= 8 and buy_qualified[index] else ""),
                "color": rgba(support_color if buy_count[index] == 9 else resistance_color, 0.78),
                "textColor": "#FFFFFF", "side": "below" if buy_count[index] == 9 else "above",
            })
        if sell_count[index] > 0 and (not completed_only or sell_count[index] == 9):
            layers.append({
                "type": "label", "index": index, "price": float(high_values[index]),
                "text": ("Q" if sell_count[index] >= 8 and sell_qualified[index] else "") + f"M{sell_count[index]}",
                "color": rgba(resistance_color if sell_count[index] == 9 else support_color, 0.78),
                "textColor": "#FFFFFF", "side": "above",
            })
        prior_buy_terminal = buy_terminal_count[index - 1] if index else 0
        prior_sell_terminal = sell_terminal_count[index - 1] if index else 0
        if buy_terminal_count[index] > 0 and buy_terminal_count[index] != prior_buy_terminal and (
            not completed_only or buy_terminal_count[index] == 13
        ):
            layers.append({
                "type": "label", "index": index, "price": float(low_values[index]),
                "text": f"T{buy_terminal_count[index]}", "color": rgba("#006400", 0.78),
                "textColor": "#FFFFFF", "side": "below",
            })
        if sell_terminal_count[index] > 0 and sell_terminal_count[index] != prior_sell_terminal and (
            not completed_only or sell_terminal_count[index] == 13
        ):
            layers.append({
                "type": "label", "index": index, "price": float(high_values[index]),
                "text": f"T{sell_terminal_count[index]}", "color": rgba("#910000", 0.78),
                "textColor": "#FFFFFF", "side": "above",
            })
    layers = layers[-500:]

momentum_text = "—"
momentum_color = "#94A3B8"
terminal_text = "—"
terminal_color = "#94A3B8"
if n and buy_count[-1] > 0:
    momentum_text, momentum_color = f"Buy-side {buy_count[-1]}/9", resistance_color
elif n and sell_count[-1] > 0:
    momentum_text, momentum_color = f"Sell-side {sell_count[-1]}/9", support_color
if n and buy_terminal_count[-1] > 0:
    terminal_text, terminal_color = f"Buy-side {buy_terminal_count[-1]}/13", resistance_color
elif n and sell_terminal_count[-1] > 0:
    terminal_text, terminal_color = f"Sell-side {sell_terminal_count[-1]}/13", support_color

pivot_text = "—"
pivot_color = "#94A3B8"
if n and show_range_pivot and all(np.isfinite(value) for value in (range_resistance[-1], range_pivot[-1], range_support[-1])):
    names = ("R", "P", "S")
    values = (range_resistance[-1], range_pivot[-1], range_support[-1])
    nearest_index = int(np.argmin([abs(close_values[-1] - value) for value in values]))
    percentage = (values[nearest_index] - close_values[-1]) / close_values[-1] * 100.0 if close_values[-1] != 0.0 else 0.0
    pivot_text = f"{names[nearest_index]} {percentage:+.2f}%"
    pivot_color = support_color if close_values[-1] > range_resistance[-1] else resistance_color if close_values[-1] < range_support[-1] else "#94A3B8"

pressure_text = "Off"
pressure_table_color = "#94A3B8"
if pressure_enabled and n:
    pressure_text = f"{round(pressure_oscillator[-1]):.0f}% Bull" if pressure_oscillator[-1] >= 50.0 else f"{round(100.0 - pressure_oscillator[-1]):.0f}% Bear"
    pressure_table_color = support_color if pressure_oscillator[-1] >= 50.0 else resistance_color

tables = []
if show_dashboard:
    tables.append({
        "title": "Dual-Phase Reversal", "position": dashboard_position, "compact": True,
        "rows": [
            [{"text": "Momentum"}, {"text": momentum_text, "color": momentum_color, "align": "right"}],
            [{"text": "Terminal"}, {"text": terminal_text, "color": terminal_color, "align": "right"}],
            [{"text": f"Pivot {resolved_pivot_timeframe}"}, {"text": pivot_text, "color": pivot_color, "align": "right"}],
            [{"text": "Pressure"}, {"text": pressure_text, "color": pressure_table_color, "align": "right"}],
        ],
    })

buy_momentum_complete = buy_count == 9
sell_momentum_complete = sell_count == 9
buy_terminal_complete = buy_terminal_count == 13
sell_terminal_complete = sell_terminal_count == 13
pressure_overbought = np.r_[False, (pressure_oscillator[1:] > pressure_upper) & (pressure_oscillator[:-1] < pressure_upper)] if n else np.array([], dtype=bool)
pressure_oversold = np.r_[False, (pressure_oscillator[1:] < pressure_lower) & (pressure_oscillator[:-1] > pressure_lower)] if n else np.array([], dtype=bool)

signals = [
    {"type": "buy", "name": "Buy Momentum Complete", "text": "M9", "color": support_color, "renderMode": "hidden", "data": [float(low_values[index]) if buy_momentum_complete[index] else None for index in range(n)]},
    {"type": "sell", "name": "Sell Momentum Complete", "text": "M9", "color": resistance_color, "renderMode": "hidden", "data": [float(high_values[index]) if sell_momentum_complete[index] else None for index in range(n)]},
    {"type": "buy", "name": "Buy Terminal Complete", "text": "T13", "color": "#006400", "renderMode": "hidden", "data": [float(low_values[index]) if buy_terminal_complete[index] else None for index in range(n)]},
    {"type": "sell", "name": "Sell Terminal Complete", "text": "T13", "color": "#910000", "renderMode": "hidden", "data": [float(high_values[index]) if sell_terminal_complete[index] else None for index in range(n)]},
    {"type": "sell", "name": "Pressure Overbought", "text": "OB", "color": resistance_color, "renderMode": "hidden", "data": [float(close_values[index]) if pressure_enabled and pressure_overbought[index] else None for index in range(n)]},
    {"type": "buy", "name": "Pressure Oversold", "text": "OS", "color": support_color, "renderMode": "hidden", "data": [float(close_values[index]) if pressure_enabled and pressure_oversold[index] else None for index in range(n)]},
    {"type": "buy", "name": "High Volume", "text": "HV", "color": "#F59E0B", "renderMode": "hidden", "data": [float(close_values[index]) if high_volume[index] else None for index in range(n)]},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": layers,
    "tables": tables,
    "bar_colors": bar_colors,
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "buyCount": buy_count.tolist(), "sellCount": sell_count.tolist(),
        "buyQualified": buy_qualified.tolist(), "sellQualified": sell_qualified.tolist(),
        "buyPhaseResistance": finite(buy_phase_resistance), "sellPhaseSupport": finite(sell_phase_support),
        "buyPhaseStop": finite(buy_phase_stop), "sellPhaseStop": finite(sell_phase_stop),
        "buyTerminalCount": buy_terminal_count.tolist(), "sellTerminalCount": sell_terminal_count.tolist(),
        "buyTerminalActive": buy_terminal_active.tolist(), "sellTerminalActive": sell_terminal_active.tolist(),
        "buyTerminalStop": finite(buy_terminal_stop), "sellTerminalStop": finite(sell_terminal_stop),
        "buyTerminalTarget": finite(buy_terminal_target), "sellTerminalTarget": finite(sell_terminal_target),
        "rangeResistance": finite(range_resistance), "rangePivot": finite(range_pivot), "rangeSupport": finite(range_support),
        "developingResistance": finite(developing_resistance), "developingPivot": finite(developing_pivot), "developingSupport": finite(developing_support),
        "rangeGroup": range_group.tolist(), "resolvedPivotTimeframe": resolved_pivot_timeframe,
        "buyPressureFlow": finite(buy_pressure_flow), "sellPressureFlow": finite(sell_pressure_flow),
        "pressureOscillator": finite(pressure_oscillator), "pressureSignal": finite(pressure_signal),
        "pressureHistogram": finite(pressure_histogram), "pressureMapped": finite(pressure_mapped),
        "volumeAverage": finite(volume_average), "highVolume": high_volume.tolist(),
        "buyMomentumComplete": buy_momentum_complete.tolist(), "sellMomentumComplete": sell_momentum_complete.tolist(),
        "buyTerminalComplete": buy_terminal_complete.tolist(), "sellTerminalComplete": sell_terminal_complete.tolist(),
        "pressureOverbought": pressure_overbought.tolist(), "pressureOversold": pressure_oversold.tolist(),
    },
}
