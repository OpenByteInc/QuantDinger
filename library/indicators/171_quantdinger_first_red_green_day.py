# ============================================================
# QuantDinger First Red / Green Day
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger First Red / Green Day"
my_indicator_description = (
    "Finds the first bearish day after a rising run and the first bullish day after a falling run, "
    "then confirms the setup with an independently calculated multi-timeframe moving average."
)

indicator_timeframes = ["1D", "4H"]

# @param signal_timeframe str 1D Signal timeframe values=1D,4H
# @param candle_source str Real_Candles Signal candle source values=Real_Candles,Heikin_Ashi
# @param color_definition str Prior_Close Red and green definition values=Prior_Close,Candle_Color,Both
# @param min_run int 3 Minimum directional closes range=1:20:1
# @param grace_bars int 2 Allowed pauses in run range=0:10:1
# @param extreme_lookback int 10 Extreme lookback range=2:500:1
# @param require_ma bool true Require MA confirmation
# @param ma_timeframe str 4H MA timeframe values=4H,1D
# @param ma_length int 47 MA length range=1:500:1
# @param ma_type str SMA MA type values=SMA,EMA
# @param arm_max int 15 Maximum bars from extreme range=2:60:1
# @param ma_gap_bars int 3 Maximum signal-to-cross gap range=0:20:1
# @param use_slope_filter bool true Filter strong MA slopes
# @param slope_length int 10 MA slope lookback range=2:100:1
# @param slope_limit float 1.0 Maximum slope in ATRs range=0.1:10.0:0.1
# @param show_armed bool true Show armed signal days
# @param show_ma bool false Show confirmation MA
# @param show_zones bool true Show setup zones
# @param zone_mode str Auto Zone construction values=Auto,Signal_Day,MA_Retest
# @param zone_max_atr float 1.5 Maximum signal-zone distance range=0.1:10.0:0.1
# @param zone_half_atr float 0.5 MA zone half-width range=0.1:5.0:0.1
# @param show_trigger bool true Show trigger line
# @param trigger_entry bool true Enable trigger-line entries
# @param trigger_requires_untouched bool true Trigger only before zone touch
# @param zone_length int 3 Zone length in signal bars range=1:20:1
# @param show_bos bool true Show break of structure
# @param pivot_length int 3 Pivot strength range=1:20:1
# @param bearish_color str #FF1744 Bearish color
# @param bullish_color str #00E676 Bullish color
# @param accent_color str #00BCD4 Accent color
# @param panel_background str #0A1628 Dashboard background
# @param show_dashboard bool true Show dashboard
# @param dashboard_position str bottom-left Dashboard position values=top-left,top-right,bottom-left,bottom-right

signal_timeframe = str(params.get("signal_timeframe", "1D"))
if signal_timeframe not in ("1D", "4H"):
    signal_timeframe = "1D"
candle_source = str(params.get("candle_source", "Real_Candles"))
color_definition = str(params.get("color_definition", "Prior_Close"))
min_run = min(20, max(1, int(params.get("min_run", 3))))
grace_bars = min(10, max(0, int(params.get("grace_bars", 2))))
extreme_lookback = min(500, max(2, int(params.get("extreme_lookback", 10))))
require_ma = bool(params.get("require_ma", True))
ma_timeframe = str(params.get("ma_timeframe", "4H"))
if ma_timeframe not in ("4H", "1D"):
    ma_timeframe = "4H"
ma_length = min(500, max(1, int(params.get("ma_length", 47))))
ma_type = str(params.get("ma_type", "SMA"))
arm_max = min(60, max(2, int(params.get("arm_max", 15))))
ma_gap_bars = min(20, max(0, int(params.get("ma_gap_bars", 3))))
use_slope_filter = bool(params.get("use_slope_filter", True))
slope_length = min(100, max(2, int(params.get("slope_length", 10))))
slope_limit = min(10.0, max(0.1, float(params.get("slope_limit", 1.0))))
show_armed = bool(params.get("show_armed", True))
show_ma = bool(params.get("show_ma", False))
show_zones = bool(params.get("show_zones", True))
zone_mode = str(params.get("zone_mode", "Auto"))
zone_max_atr = min(10.0, max(0.1, float(params.get("zone_max_atr", 1.5))))
zone_half_atr = min(5.0, max(0.1, float(params.get("zone_half_atr", 0.5))))
show_trigger = bool(params.get("show_trigger", True))
trigger_entry = bool(params.get("trigger_entry", True))
trigger_requires_untouched = bool(params.get("trigger_requires_untouched", True))
zone_length = min(20, max(1, int(params.get("zone_length", 3))))
show_bos = bool(params.get("show_bos", True))
pivot_length = min(20, max(1, int(params.get("pivot_length", 3))))
bearish_color = str(params.get("bearish_color", "#FF1744"))
bullish_color = str(params.get("bullish_color", "#00E676"))
accent_color = str(params.get("accent_color", "#00BCD4"))
panel_background = str(params.get("panel_background", "#0A1628"))
show_dashboard = bool(params.get("show_dashboard", True))
dashboard_position = str(params.get("dashboard_position", "bottom-left"))
if dashboard_position not in ("top-left", "top-right", "bottom-left", "bottom-right"):
    dashboard_position = "bottom-left"

timeframe_seconds = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1H": 3600,
    "2H": 7200,
    "4H": 14400,
    "6H": 21600,
    "8H": 28800,
    "12H": 43200,
    "1D": 86400,
    "1W": 604800,
}

df = df.copy()
n = len(df)

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
    if len(times) and np.nanmax(np.abs(times)) > 10_000_000_000:
        result["time"] = times / 1000.0
    return result

base_frame = normalized_frame(df)
base_open = base_frame["open"].to_numpy(dtype=float)
base_high = base_frame["high"].to_numpy(dtype=float)
base_low = base_frame["low"].to_numpy(dtype=float)
base_close = base_frame["close"].to_numpy(dtype=float)
base_time = base_frame["time"].to_numpy(dtype=float)

chart_timeframe = str(ctx.timeframe or "1D")
if chart_timeframe not in timeframe_seconds:
    chart_timeframe = "1D"
chart_seconds = timeframe_seconds[chart_timeframe]
signal_seconds = timeframe_seconds[signal_timeframe]
ma_seconds = timeframe_seconds[ma_timeframe]

def selected_frame(timeframe):
    if timeframe == chart_timeframe:
        return base_frame.copy()
    source = ctx.raw_series(timeframe)
    if source is None or len(source) == 0:
        return base_frame.copy()
    return normalized_frame(source)

signal_frame = base_frame.copy() if chart_seconds >= signal_seconds else selected_frame(signal_timeframe)
effective_signal_seconds = chart_seconds if chart_seconds >= signal_seconds else signal_seconds
ma_frame = selected_frame(ma_timeframe)

def heikin_ashi(frame):
    result = frame.copy()
    raw_open = frame["open"].to_numpy(dtype=float)
    raw_high = frame["high"].to_numpy(dtype=float)
    raw_low = frame["low"].to_numpy(dtype=float)
    raw_close = frame["close"].to_numpy(dtype=float)
    count = len(frame)
    ha_close = (raw_open + raw_high + raw_low + raw_close) / 4.0
    ha_open = np.full(count, np.nan, dtype=float)
    for index in range(count):
        if index == 0:
            ha_open[index] = (raw_open[index] + raw_close[index]) / 2.0
        else:
            ha_open[index] = (ha_open[index - 1] + ha_close[index - 1]) / 2.0
    result["open"] = ha_open
    result["high"] = np.maximum.reduce([raw_high, ha_open, ha_close])
    result["low"] = np.minimum.reduce([raw_low, ha_open, ha_close])
    result["close"] = ha_close
    return result

if candle_source == "Heikin_Ashi":
    signal_frame = heikin_ashi(signal_frame)

def sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if length <= 0:
        return result
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result

def ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result

def rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    result[length - 1] = float(np.mean(values[:length]))
    for index in range(length, len(values)):
        result[index] = (result[index - 1] * (length - 1) + values[index]) / length
    return result

def atr_values(frame, length=14):
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    closes = frame["close"].to_numpy(dtype=float)
    true_range = np.zeros(len(frame), dtype=float)
    for index in range(len(frame)):
        if index == 0:
            true_range[index] = highs[index] - lows[index]
        else:
            true_range[index] = max(
                highs[index] - lows[index],
                abs(highs[index] - closes[index - 1]),
                abs(lows[index] - closes[index - 1]),
            )
    return rma(true_range, length)

def align_completed(source_times, source_values, source_seconds):
    result = np.full(n, np.nan, dtype=float)
    source_close_times = source_times + source_seconds
    base_close_times = base_time + chart_seconds
    for index in range(n):
        source_index = int(np.searchsorted(source_close_times, base_close_times[index], side="right") - 1)
        if source_index >= 0 and source_index < len(source_values):
            value = source_values[source_index]
            if np.isfinite(value):
                result[index] = float(value)
    return result

def aligned_source_indices(source_times, source_seconds):
    source_close_times = source_times + source_seconds
    base_close_times = base_time + chart_seconds
    result = np.full(n, -1, dtype=int)
    for index in range(n):
        result[index] = int(np.searchsorted(source_close_times, base_close_times[index], side="right") - 1)
    return result

signal_open = signal_frame["open"].to_numpy(dtype=float)
signal_high = signal_frame["high"].to_numpy(dtype=float)
signal_low = signal_frame["low"].to_numpy(dtype=float)
signal_close = signal_frame["close"].to_numpy(dtype=float)
signal_time = signal_frame["time"].to_numpy(dtype=float)
signal_count = len(signal_frame)

red = np.zeros(signal_count, dtype=bool)
green = np.zeros(signal_count, dtype=bool)
up_move = np.zeros(signal_count, dtype=int)
down_move = np.zeros(signal_count, dtype=int)
up_run = np.zeros(signal_count, dtype=int)
down_run = np.zeros(signal_count, dtype=int)
for index in range(1, signal_count):
    prior_close = signal_close[index - 1]
    prior_red = signal_close[index] < prior_close
    candle_red = signal_close[index] < signal_open[index]
    prior_green = signal_close[index] > prior_close
    candle_green = signal_close[index] > signal_open[index]
    red[index] = prior_red if color_definition == "Prior_Close" else candle_red if color_definition == "Candle_Color" else prior_red and candle_red
    green[index] = prior_green if color_definition == "Prior_Close" else candle_green if color_definition == "Candle_Color" else prior_green and candle_green
    up_move[index] = 1 if signal_close[index] > prior_close else 0
    down_move[index] = 1 if signal_close[index] < prior_close else 0
    up_run[index] = up_run[index - 1] + 1 if up_move[index] else 0
    down_run[index] = down_run[index - 1] + 1 if down_move[index] else 0

frd_armed = np.zeros(signal_count, dtype=bool)
fgd_armed = np.zeros(signal_count, dtype=bool)
frd_day = np.zeros(signal_count, dtype=bool)
fgd_day = np.zeros(signal_count, dtype=bool)
frd_high = np.full(signal_count, np.nan, dtype=float)
frd_low = np.full(signal_count, np.nan, dtype=float)
frd_close = np.full(signal_count, np.nan, dtype=float)
frd_time = np.full(signal_count, np.nan, dtype=float)
peak_value = np.full(signal_count, np.nan, dtype=float)
peak_time = np.full(signal_count, np.nan, dtype=float)
fgd_high = np.full(signal_count, np.nan, dtype=float)
fgd_low = np.full(signal_count, np.nan, dtype=float)
fgd_close = np.full(signal_count, np.nan, dtype=float)
fgd_time = np.full(signal_count, np.nan, dtype=float)
trough_value = np.full(signal_count, np.nan, dtype=float)
trough_time = np.full(signal_count, np.nan, dtype=float)
bos_up = np.zeros(signal_count, dtype=bool)
bos_down = np.zeros(signal_count, dtype=bool)
bos_up_level = np.full(signal_count, np.nan, dtype=float)
bos_down_level = np.full(signal_count, np.nan, dtype=float)
window_length = max(arm_max, extreme_lookback)
count_length = min_run + grace_bars
live_swing_high = np.nan
live_swing_low = np.nan

for index in range(signal_count):
    start = max(0, index - window_length + 1)
    peak_index = start + int(np.argmax(signal_high[start:index + 1]))
    trough_index = start + int(np.argmin(signal_low[start:index + 1]))
    peak_value[index] = signal_high[peak_index]
    peak_time[index] = signal_time[peak_index]
    trough_value[index] = signal_low[trough_index]
    trough_time[index] = signal_time[trough_index]
    if index > window_length:
        first_red = next((candidate for candidate in range(peak_index, index + 1) if red[candidate]), -1)
        first_green = next((candidate for candidate in range(trough_index, index + 1) if green[candidate]), -1)
        count_start_up = max(0, peak_index - count_length + 1)
        count_start_down = max(0, trough_index - count_length + 1)
        up_count = int(np.sum(up_move[count_start_up:peak_index + 1]))
        down_count = int(np.sum(down_move[count_start_down:trough_index + 1]))
        if index - peak_index <= arm_max and up_count >= min_run and first_red >= 0:
            frd_armed[index] = True
            frd_day[index] = first_red == index
            frd_high[index] = signal_high[first_red]
            frd_low[index] = signal_low[first_red]
            frd_close[index] = signal_close[first_red]
            frd_time[index] = signal_time[first_red]
        if index - trough_index <= arm_max and down_count >= min_run and first_green >= 0:
            fgd_armed[index] = True
            fgd_day[index] = first_green == index
            fgd_high[index] = signal_high[first_green]
            fgd_low[index] = signal_low[first_green]
            fgd_close[index] = signal_close[first_green]
            fgd_time[index] = signal_time[first_green]

    pivot_index = index - pivot_length
    if pivot_index >= pivot_length:
        left = pivot_index - pivot_length
        right = index + 1
        pivot_high_window = signal_high[left:right]
        pivot_low_window = signal_low[left:right]
        if signal_high[pivot_index] >= float(np.max(pivot_high_window)):
            live_swing_high = signal_high[pivot_index]
        if signal_low[pivot_index] <= float(np.min(pivot_low_window)):
            live_swing_low = signal_low[pivot_index]
    if np.isfinite(live_swing_high) and signal_close[index] > live_swing_high:
        bos_up[index] = True
        bos_up_level[index] = live_swing_high
        live_swing_high = np.nan
    if np.isfinite(live_swing_low) and signal_close[index] < live_swing_low:
        bos_down[index] = True
        bos_down_level[index] = live_swing_low
        live_swing_low = np.nan

signal_indices = aligned_source_indices(signal_time, effective_signal_seconds)

def align_signal_numeric(values):
    result = np.full(n, np.nan, dtype=float)
    for index, source_index in enumerate(signal_indices):
        if 0 <= source_index < len(values) and np.isfinite(values[source_index]):
            result[index] = float(values[source_index])
    return result

def signal_event(values):
    result = np.zeros(n, dtype=bool)
    prior_index = -1
    for index, source_index in enumerate(signal_indices):
        if source_index >= 0 and source_index != prior_index and source_index < len(values):
            result[index] = bool(values[source_index])
        prior_index = source_index
    return result

aligned_frd_armed = np.asarray([bool(frd_armed[source_index]) if 0 <= source_index < signal_count else False for source_index in signal_indices])
aligned_fgd_armed = np.asarray([bool(fgd_armed[source_index]) if 0 <= source_index < signal_count else False for source_index in signal_indices])
frd_day_event = signal_event(frd_day)
fgd_day_event = signal_event(fgd_day)
bos_up_event = signal_event(bos_up) if show_bos else np.zeros(n, dtype=bool)
bos_down_event = signal_event(bos_down) if show_bos else np.zeros(n, dtype=bool)

aligned_frd_high = align_signal_numeric(frd_high)
aligned_frd_low = align_signal_numeric(frd_low)
aligned_frd_close = align_signal_numeric(frd_close)
aligned_frd_time = align_signal_numeric(frd_time)
aligned_peak = align_signal_numeric(peak_value)
aligned_peak_time = align_signal_numeric(peak_time)
aligned_fgd_high = align_signal_numeric(fgd_high)
aligned_fgd_low = align_signal_numeric(fgd_low)
aligned_fgd_close = align_signal_numeric(fgd_close)
aligned_fgd_time = align_signal_numeric(fgd_time)
aligned_trough = align_signal_numeric(trough_value)
aligned_trough_time = align_signal_numeric(trough_time)
aligned_up_run = align_signal_numeric(up_run)
aligned_down_run = align_signal_numeric(down_run)
aligned_bos_up_level = align_signal_numeric(bos_up_level)
aligned_bos_down_level = align_signal_numeric(bos_down_level)

ma_close = ma_frame["close"].to_numpy(dtype=float)
ma_time = ma_frame["time"].to_numpy(dtype=float)
ma_atr = atr_values(ma_frame, 14)
ma_values = ema(ma_close, ma_length) if ma_type == "EMA" else sma(ma_close, ma_length)
ma_slope = np.full(len(ma_values), np.nan, dtype=float)
for index in range(slope_length, len(ma_values)):
    if np.isfinite(ma_values[index]) and np.isfinite(ma_values[index - slope_length]) and np.isfinite(ma_atr[index]) and ma_atr[index] > 0:
        ma_slope[index] = (ma_values[index] - ma_values[index - slope_length]) / ma_atr[index]

ma_aligned = align_completed(ma_time, ma_values, ma_seconds)
slope_aligned = align_completed(ma_time, ma_slope, ma_seconds)
base_atr = atr_values(base_frame, 14)

ma_cross_down = np.zeros(n, dtype=bool)
ma_cross_up = np.zeros(n, dtype=bool)
if chart_seconds < ma_seconds:
    source_cross_down = np.zeros(len(ma_values), dtype=bool)
    source_cross_up = np.zeros(len(ma_values), dtype=bool)
    for index in range(1, len(ma_values)):
        if np.isfinite(ma_values[index]) and np.isfinite(ma_values[index - 1]):
            source_cross_down[index] = ma_close[index] < ma_values[index] and ma_close[index - 1] >= ma_values[index - 1]
            source_cross_up[index] = ma_close[index] > ma_values[index] and ma_close[index - 1] <= ma_values[index - 1]
    ma_source_indices = aligned_source_indices(ma_time, ma_seconds)
    prior_source = -1
    for index, source_index in enumerate(ma_source_indices):
        if source_index >= 0 and source_index != prior_source:
            ma_cross_down[index] = bool(source_cross_down[source_index])
            ma_cross_up[index] = bool(source_cross_up[source_index])
        prior_source = source_index
else:
    for index in range(1, n):
        if np.isfinite(ma_aligned[index]) and np.isfinite(ma_aligned[index - 1]):
            ma_cross_down[index] = base_close[index] < ma_aligned[index] and base_close[index - 1] >= ma_aligned[index - 1]
            ma_cross_up[index] = base_close[index] > ma_aligned[index] and base_close[index - 1] <= ma_aligned[index - 1]

frd_fire = np.zeros(n, dtype=bool)
fgd_fire = np.zeros(n, dtype=bool)
frd_break = np.zeros(n, dtype=bool)
fgd_break = np.zeros(n, dtype=bool)
sell_zone = [None for _ in range(n)]
buy_zone = [None for _ in range(n)]
sell_trigger = [None for _ in range(n)]
buy_trigger = [None for _ in range(n)]
sell_active = False
buy_active = False
sell_level = np.nan
buy_level = np.nan
sell_touch_level = np.nan
buy_touch_level = np.nan
sell_peak = np.nan
buy_trough = np.nan
sell_touched = False
buy_touched = False
sell_expiry = -1.0
buy_expiry = -1.0
last_cross_down_time = np.nan
last_cross_up_time = np.nan
last_frd_peak_time = np.nan
last_fgd_trough_time = np.nan
last_signal = "—"
last_signal_color = "#9CA3AF"

for index in range(n):
    current_time = base_time[index]
    gap_seconds = (ma_gap_bars + 1) * effective_signal_seconds
    if np.isfinite(last_cross_down_time) and current_time - last_cross_down_time > gap_seconds:
        last_cross_down_time = np.nan
    if np.isfinite(last_cross_up_time) and current_time - last_cross_up_time > gap_seconds:
        last_cross_up_time = np.nan
    if ma_cross_down[index]:
        last_cross_down_time = current_time
        last_cross_up_time = np.nan
    if ma_cross_up[index]:
        last_cross_up_time = current_time
        last_cross_down_time = np.nan

    slope_down_ok = not use_slope_filter or not np.isfinite(slope_aligned[index]) or slope_aligned[index] < slope_limit
    slope_up_ok = not use_slope_filter or not np.isfinite(slope_aligned[index]) or slope_aligned[index] > -slope_limit
    fresh_frd_peak = not np.isfinite(last_frd_peak_time) or aligned_peak_time[index] != last_frd_peak_time
    fresh_fgd_trough = not np.isfinite(last_fgd_trough_time) or aligned_trough_time[index] != last_fgd_trough_time
    frd_live = aligned_frd_armed[index] and fresh_frd_peak
    fgd_live = aligned_fgd_armed[index] and fresh_fgd_trough
    if require_ma:
        frd_fire[index] = (
            frd_live and np.isfinite(ma_aligned[index]) and base_close[index] < ma_aligned[index]
            and slope_down_ok and (ma_cross_down[index] or (frd_day_event[index] and np.isfinite(last_cross_down_time)))
        )
        fgd_fire[index] = (
            fgd_live and np.isfinite(ma_aligned[index]) and base_close[index] > ma_aligned[index]
            and slope_up_ok and (ma_cross_up[index] or (fgd_day_event[index] and np.isfinite(last_cross_up_time)))
        )
    else:
        frd_fire[index] = frd_live and frd_day_event[index]
        fgd_fire[index] = fgd_live and fgd_day_event[index]

    if frd_fire[index]:
        width = zone_half_atr * base_atr[index] if np.isfinite(base_atr[index]) else 0.0
        signal_far = base_close[index] > aligned_frd_high[index] or aligned_frd_close[index] - base_close[index] > zone_max_atr * base_atr[index]
        use_signal_zone = zone_mode == "Signal_Day" or (zone_mode == "Auto" and not signal_far)
        zone_top = aligned_frd_high[index] if use_signal_zone else min(ma_aligned[index] + width, aligned_peak[index])
        zone_bottom = aligned_frd_close[index] if use_signal_zone else min(ma_aligned[index], max(base_close[index], ma_aligned[index] - width))
        sell_level = aligned_frd_low[index] if use_signal_zone else base_low[index]
        sell_touch_level = zone_bottom
        sell_peak = aligned_peak[index]
        sell_expiry = current_time + zone_length * effective_signal_seconds
        sell_active = trigger_entry and base_close[index] > sell_level
        sell_touched = False
        buy_active = False
        last_frd_peak_time = aligned_peak_time[index]
        last_cross_down_time = np.nan
        last_signal = "FRD confirmed"
        last_signal_color = bearish_color
        if show_zones:
            sell_zone[index] = {"upper": float(zone_top), "lower": float(zone_bottom), "color": "rgba(255,23,68,0.16)"}
        if show_trigger:
            sell_trigger[index] = float(sell_level)
    if fgd_fire[index]:
        width = zone_half_atr * base_atr[index] if np.isfinite(base_atr[index]) else 0.0
        signal_far = base_close[index] < aligned_fgd_low[index] or base_close[index] - aligned_fgd_close[index] > zone_max_atr * base_atr[index]
        use_signal_zone = zone_mode == "Signal_Day" or (zone_mode == "Auto" and not signal_far)
        zone_bottom = aligned_fgd_low[index] if use_signal_zone else max(ma_aligned[index] - width, aligned_trough[index])
        zone_top = aligned_fgd_close[index] if use_signal_zone else max(ma_aligned[index], min(base_close[index], ma_aligned[index] + width))
        buy_level = aligned_fgd_high[index] if use_signal_zone else base_high[index]
        buy_touch_level = zone_top
        buy_trough = aligned_trough[index]
        buy_expiry = current_time + zone_length * effective_signal_seconds
        buy_active = trigger_entry and base_close[index] < buy_level
        buy_touched = False
        sell_active = False
        last_fgd_trough_time = aligned_trough_time[index]
        last_cross_up_time = np.nan
        last_signal = "FGD confirmed"
        last_signal_color = bullish_color
        if show_zones:
            buy_zone[index] = {"upper": float(zone_top), "lower": float(zone_bottom), "color": "rgba(0,230,118,0.16)"}
        if show_trigger:
            buy_trigger[index] = float(buy_level)

    if sell_active and not frd_fire[index]:
        if current_time > sell_expiry or base_close[index] > sell_peak or fgd_fire[index]:
            sell_active = False
        else:
            if base_high[index] >= sell_touch_level:
                sell_touched = True
            if base_close[index] < sell_level and (not trigger_requires_untouched or not sell_touched):
                frd_break[index] = True
                sell_active = False
                last_signal = "FRD trigger break"
                last_signal_color = bearish_color
    if buy_active and not fgd_fire[index]:
        if current_time > buy_expiry or base_close[index] < buy_trough or frd_fire[index]:
            buy_active = False
        else:
            if base_low[index] <= buy_touch_level:
                buy_touched = True
            if base_close[index] > buy_level and (not trigger_requires_untouched or not buy_touched):
                fgd_break[index] = True
                buy_active = False
                last_signal = "FGD trigger break"
                last_signal_color = bullish_color

    if sell_active or (np.isfinite(sell_expiry) and current_time <= sell_expiry and np.isfinite(sell_level)):
        if show_trigger:
            sell_trigger[index] = float(sell_level)
    if buy_active or (np.isfinite(buy_expiry) and current_time <= buy_expiry and np.isfinite(buy_level)):
        if show_trigger:
            buy_trigger[index] = float(buy_level)

ma_plot = [float(value) if np.isfinite(value) else None for value in ma_aligned]
armed_frd = [float(base_high[index]) if show_armed and frd_day_event[index] and aligned_frd_armed[index] else None for index in range(n)]
armed_fgd = [float(base_low[index]) if show_armed and fgd_day_event[index] and aligned_fgd_armed[index] else None for index in range(n)]

def event_points(mask, values, text, color):
    return [
        {"value": float(values[index]), "text": text, "color": color} if mask[index] and np.isfinite(values[index]) else None
        for index in range(n)
    ]

plots = [
    {
        "name": "47 MA Confirmation",
        "data": ma_plot,
        "color": accent_color if show_ma else "rgba(0,188,212,0.001)",
        "type": "line",
        "lineWidth": 2,
        "overlay": True,
    },
    {"name": "FRD Armed", "title": False, "data": armed_frd, "color": "rgba(255,23,68,0.70)", "type": "circle", "size": 3, "overlay": True},
    {"name": "FGD Armed", "title": False, "data": armed_fgd, "color": "rgba(0,230,118,0.70)", "type": "circle", "size": 3, "overlay": True},
]
if show_zones:
    plots.append({"name": "FRD Sell Zone", "title": False, "data": sell_zone, "color": "rgba(255,23,68,0.16)", "type": "cloud", "overlay": True})
    plots.append({"name": "FGD Buy Zone", "title": False, "data": buy_zone, "color": "rgba(0,230,118,0.16)", "type": "cloud", "overlay": True})
if show_trigger:
    plots.append({"name": "FRD Trigger", "title": False, "data": sell_trigger, "color": bearish_color, "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True})
    plots.append({"name": "FGD Trigger", "title": False, "data": buy_trigger, "color": bullish_color, "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True})

signals = [
    {"type": "sell", "name": "First Red Day", "text": "FRD", "color": bearish_color, "data": event_points(frd_fire, base_high, "FRD", bearish_color), "renderMode": "events"},
    {"type": "buy", "name": "First Green Day", "text": "FGD", "color": bullish_color, "data": event_points(fgd_fire, base_low, "FGD", bullish_color), "renderMode": "events"},
    {"type": "sell", "name": "FRD Trigger Entry", "text": "FRD BRK", "color": bearish_color, "data": event_points(frd_break, base_high, "FRD BRK", bearish_color), "renderMode": "events"},
    {"type": "buy", "name": "FGD Trigger Entry", "text": "FGD BRK", "color": bullish_color, "data": event_points(fgd_break, base_low, "FGD BRK", bullish_color), "renderMode": "events"},
]
if show_bos:
    signals.append({"type": "buy", "name": "Bullish BOS", "text": "UP", "color": bullish_color, "data": event_points(bos_up_event, base_low, "UP", bullish_color), "renderMode": "events"})
    signals.append({"type": "sell", "name": "Bearish BOS", "text": "DN", "color": bearish_color, "data": event_points(bos_down_event, base_high, "DN", bearish_color), "renderMode": "events"})

tables = []
if show_dashboard:
    final_ma = ma_aligned[-1] if n else np.nan
    final_close = base_close[-1] if n else np.nan
    final_up = int(aligned_up_run[-1]) if n and np.isfinite(aligned_up_run[-1]) else 0
    final_down = int(aligned_down_run[-1]) if n and np.isfinite(aligned_down_run[-1]) else 0
    if sell_active:
        status = "FRD zone active"
    elif buy_active:
        status = "FGD zone active"
    elif n and aligned_frd_armed[-1]:
        status = "FRD armed"
    elif n and aligned_fgd_armed[-1]:
        status = "FGD armed"
    elif final_up >= min_run:
        status = "Watching FRD"
    elif final_down >= min_run:
        status = "Watching FGD"
    else:
        status = "No setup"
    ma_state = "—" if not np.isfinite(final_ma) else "Above" if final_close > final_ma else "Below"
    tables.append({
        "position": dashboard_position,
        "backgroundColor": panel_background,
        "borderColor": accent_color,
        "rows": [
            ["FIRST RED / GREEN DAY", ""],
            ["Signal TF", signal_timeframe + (" HA" if candle_source == "Heikin_Ashi" else "")],
            ["Definition", color_definition.replace("_", " ")],
            ["Higher closes", str(final_up)],
            ["Lower closes", str(final_down)],
            ["Last signal", last_signal],
            [str(ma_length) + " " + ma_type + " (" + ma_timeframe + ")", ma_state],
            ["Status", status],
        ],
        "textColor": "#FFFFFF",
        "accentColor": last_signal_color,
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": [],
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "signalSourceIndex": [int(value) for value in signal_indices],
        "frdArmed": aligned_frd_armed.tolist(), "fgdArmed": aligned_fgd_armed.tolist(),
        "frdDay": frd_day_event.tolist(), "fgdDay": fgd_day_event.tolist(),
        "frdFire": frd_fire.tolist(), "fgdFire": fgd_fire.tolist(),
        "frdBreak": frd_break.tolist(), "fgdBreak": fgd_break.tolist(),
        "bosUp": bos_up_event.tolist(), "bosDown": bos_down_event.tolist(),
        "ma": [float(value) if np.isfinite(value) else None for value in ma_aligned],
        "maSlopeAtr": [float(value) if np.isfinite(value) else None for value in slope_aligned],
        "maCrossDown": ma_cross_down.tolist(), "maCrossUp": ma_cross_up.tolist(),
        "baseATR": [float(value) if np.isfinite(value) else None for value in base_atr],
        "frdHigh": [float(value) if np.isfinite(value) else None for value in aligned_frd_high],
        "frdLow": [float(value) if np.isfinite(value) else None for value in aligned_frd_low],
        "frdClose": [float(value) if np.isfinite(value) else None for value in aligned_frd_close],
        "peak": [float(value) if np.isfinite(value) else None for value in aligned_peak],
        "fgdHigh": [float(value) if np.isfinite(value) else None for value in aligned_fgd_high],
        "fgdLow": [float(value) if np.isfinite(value) else None for value in aligned_fgd_low],
        "fgdClose": [float(value) if np.isfinite(value) else None for value in aligned_fgd_close],
        "trough": [float(value) if np.isfinite(value) else None for value in aligned_trough],
        "upRun": [int(value) if np.isfinite(value) else 0 for value in aligned_up_run],
        "downRun": [int(value) if np.isfinite(value) else 0 for value in aligned_down_run],
        "sellActive": bool(sell_active), "buyActive": bool(buy_active),
        "effectiveSignalSeconds": int(effective_signal_seconds),
        "signalTimeframe": signal_timeframe, "maTimeframe": ma_timeframe,
    },
}
