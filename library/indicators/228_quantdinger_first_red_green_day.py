# ============================================================
# QuantDinger First Red / Green Day
# Independent Python implementation for the QuantDinger indicator runtime.
# Confirmed-bar workflow only; realtime forming alerts require live ticks.
# ============================================================

my_indicator_name = "QuantDinger First Red / Green Day"
my_indicator_description = (
    "Finds the first red day after an advancing extreme and the first green day "
    "after a declining extreme, with confirmed multi-timeframe MA, zones, triggers, and BOS."
)

indicator_timeframes = ["4H", "1D", "1W"]

# @param candle_source str real Signal candle source values=real,heikin_ashi
# @param signal_timeframe str 1D Signal timeframe values=4H,1D,1W
# @param color_definition str prior_close Red and green definition values=prior_close,candle_color,both
# @param minimum_run int 3 Minimum directional closes range=1:20:1
# @param grace_bars int 2 Allowed pause bars range=0:10:1
# @param extreme_lookback int 10 Extreme lookback range=2:500:1
# @param use_ma bool true Require MA break
# @param ma_timeframe str 4H MA timeframe values=4H,1D,1W
# @param ma_length int 47 MA length range=1:500:1
# @param ma_type str SMA MA type values=SMA,EMA
# @param arm_max int 15 Maximum signal bars from extreme range=2:60:1
# @param ma_break_gap int 3 Maximum signal bars from MA break range=0:20:1
# @param use_slope bool true Apply MA slope filter
# @param slope_length int 10 MA slope lookback range=2:100:1
# @param slope_max float 1.0 Maximum slope in ATRs range=0.1:10.0:0.1
# @param show_armed bool true Show armed signal days
# @param show_ma bool false Plot confirmation MA
# @param show_zones bool true Show setup zones
# @param zone_mode str Auto Zone mode values=Auto,Signal_Day,MA_Retest
# @param zone_max_atr float 1.5 Maximum signal-zone distance in ATRs range=0.1:10.0:0.1
# @param zone_atr_width float 0.5 MA zone half-width in ATRs range=0.1:10.0:0.1
# @param show_trigger bool true Show trigger line
# @param use_trigger_entry bool true Enable trigger-line entry
# @param trigger_no_touch bool true Require untapped zone for trigger entry
# @param zone_length int 3 Zone length in signal bars range=1:20:1
# @param max_zones int 10 Maximum retained zones range=1:50:1
# @param show_bos bool true Show break of structure
# @param pivot_length int 3 Swing strength range=1:20:1
# @param bearish_color str #FF1744 Bearish color
# @param bullish_color str #00E676 Bullish color
# @param accent_color str #00BCD4 Accent color
# @param show_dashboard bool true Show dashboard
# @param dashboard_position str top-right Dashboard position values=top-right,top-left,bottom-right,bottom-left
# @param max_labels int 12 Recent compact labels range=1:50:1
# @param label_lookback int 180 Label display lookback bars range=20:500:10

candle_source = str(params.get("candle_source", "real")).lower()
requested_signal_timeframe = str(params.get("signal_timeframe", "1D")).upper()
color_definition = str(params.get("color_definition", "prior_close")).lower()
minimum_run = min(20, max(1, int(params.get("minimum_run", 3))))
grace_bars = min(10, max(0, int(params.get("grace_bars", 2))))
extreme_lookback = min(500, max(2, int(params.get("extreme_lookback", 10))))
use_ma = bool(params.get("use_ma", True))
requested_ma_timeframe = str(params.get("ma_timeframe", "4H")).upper()
ma_length = min(500, max(1, int(params.get("ma_length", 47))))
ma_type = str(params.get("ma_type", "SMA")).upper()
arm_max = min(60, max(2, int(params.get("arm_max", 15))))
ma_break_gap = min(20, max(0, int(params.get("ma_break_gap", 3))))
use_slope = bool(params.get("use_slope", True))
slope_length = min(100, max(2, int(params.get("slope_length", 10))))
slope_max = min(10.0, max(0.1, float(params.get("slope_max", 1.0))))
show_armed = bool(params.get("show_armed", True))
show_ma = bool(params.get("show_ma", False))
show_zones = bool(params.get("show_zones", True))
zone_mode = str(params.get("zone_mode", "Auto"))
zone_max_atr = min(10.0, max(0.1, float(params.get("zone_max_atr", 1.5))))
zone_atr_width = min(10.0, max(0.1, float(params.get("zone_atr_width", 0.5))))
show_trigger = bool(params.get("show_trigger", True))
use_trigger_entry = bool(params.get("use_trigger_entry", True))
trigger_no_touch = bool(params.get("trigger_no_touch", True))
zone_length = min(20, max(1, int(params.get("zone_length", 3))))
max_zones = min(50, max(1, int(params.get("max_zones", 10))))
show_bos = bool(params.get("show_bos", True))
pivot_length = min(20, max(1, int(params.get("pivot_length", 3))))
bearish_color = str(params.get("bearish_color", "#FF1744"))
bullish_color = str(params.get("bullish_color", "#00E676"))
accent_color = str(params.get("accent_color", "#00BCD4"))
show_dashboard = bool(params.get("show_dashboard", True))
dashboard_position = str(params.get("dashboard_position", "top-right")).lower()
max_labels = min(50, max(1, int(params.get("max_labels", 12))))
label_lookback = min(500, max(20, int(params.get("label_lookback", 180))))

frame = df.copy()
frame = frame.reset_index(drop=True)
n = len(frame)
if "time" not in frame.columns:
    frame["time"] = np.arange(n, dtype=float) * 3600.0
time_values = pd.to_numeric(frame["time"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
base_time_seconds = time_values.copy()
if n and np.nanmax(np.abs(base_time_seconds)) > 10000000000:
    base_time_seconds = base_time_seconds / 1000.0

timeframe_seconds = {"1M": 60, "5M": 300, "15M": 900, "30M": 1800, "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
runtime_context = globals().get("ctx")
chart_timeframe = str(getattr(runtime_context, "timeframe", "1H") or "1H").upper()
chart_seconds = timeframe_seconds.get(chart_timeframe, 3600)
signal_seconds_requested = timeframe_seconds.get(requested_signal_timeframe, 86400)
same_signal_timeframe = chart_seconds >= signal_seconds_requested
resolved_signal_timeframe = chart_timeframe if same_signal_timeframe else requested_signal_timeframe
effective_signal_seconds = chart_seconds if same_signal_timeframe else signal_seconds_requested
resolved_ma_timeframe = requested_ma_timeframe if requested_ma_timeframe in timeframe_seconds else "4H"
ma_seconds = timeframe_seconds.get(resolved_ma_timeframe, 14400)


def normalize_ohlc(raw):
    if raw is None or len(raw) == 0:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    result = raw.copy().reset_index(drop=True)
    if "time" not in result.columns:
        result["time"] = np.arange(len(result), dtype=float) * 3600.0
    for column in ("time", "open", "high", "low", "close", "volume"):
        result[column] = pd.to_numeric(result.get(column), errors="coerce")
    result["volume"] = result["volume"].fillna(0.0)
    result = result.dropna(subset=["time", "open", "high", "low", "close"])
    if len(result) and np.nanmax(np.abs(result["time"].to_numpy(dtype=float))) > 10000000000:
        result["time"] = result["time"] / 1000.0
    return result.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)


def heikin_ashi(source):
    result = source.copy().reset_index(drop=True)
    count = len(result)
    if not count:
        return result
    source_open = result["open"].to_numpy(dtype=float)
    source_high = result["high"].to_numpy(dtype=float)
    source_low = result["low"].to_numpy(dtype=float)
    source_close = result["close"].to_numpy(dtype=float)
    ha_close = (source_open + source_high + source_low + source_close) * 0.25
    ha_open = np.zeros(count, dtype=float)
    ha_open[0] = (source_open[0] + source_close[0]) * 0.5
    for index in range(1, count):
        ha_open[index] = (ha_open[index - 1] + ha_close[index - 1]) * 0.5
    result["open"] = ha_open
    result["close"] = ha_close
    result["high"] = np.maximum(source_high, np.maximum(ha_open, ha_close))
    result["low"] = np.minimum(source_low, np.minimum(ha_open, ha_close))
    return result


base_real = normalize_ohlc(frame)


def requested_frame(timeframe_name):
    if timeframe_name == chart_timeframe:
        raw = base_real.copy()
    else:
        raw_source = (
            runtime_context.raw_series(timeframe_name)
            if runtime_context is not None and hasattr(runtime_context, "raw_series")
            else None
        )
        raw = normalize_ohlc(raw_source)
        if not len(raw):
            raw = base_real.copy()
    return heikin_ashi(raw) if candle_source == "heikin_ashi" else raw


base_calc = requested_frame(chart_timeframe)
base_open = base_calc["open"].to_numpy(dtype=float)
base_high = base_calc["high"].to_numpy(dtype=float)
base_low = base_calc["low"].to_numpy(dtype=float)
base_close = base_calc["close"].to_numpy(dtype=float)


def true_range_atr(source, length=14):
    count = len(source)
    result = np.full(count, np.nan, dtype=float)
    if not count:
        return result
    highs = source["high"].to_numpy(dtype=float)
    lows = source["low"].to_numpy(dtype=float)
    closes = source["close"].to_numpy(dtype=float)
    tr = np.zeros(count, dtype=float)
    tr[0] = highs[0] - lows[0]
    for index in range(1, count):
        tr[index] = max(highs[index] - lows[index], abs(highs[index] - closes[index - 1]), abs(lows[index] - closes[index - 1]))
    if count >= length:
        result[length - 1] = float(np.mean(tr[:length]))
        for index in range(length, count):
            result[index] = (result[index - 1] * (length - 1) + tr[index]) / float(length)
    return result


def moving_average(values, length, average_type):
    if average_type == "EMA":
        result = np.full(len(values), np.nan, dtype=float)
        if len(values):
            alpha = 2.0 / (float(length) + 1.0)
            result[0] = float(values[0])
            for index in range(1, len(values)):
                result[index] = alpha * float(values[index]) + (1.0 - alpha) * result[index - 1]
        return result
    return pd.Series(values, dtype=float).rolling(length, min_periods=length).mean().to_numpy(dtype=float)


def candle_flags(opens, closes):
    count = len(closes)
    red = np.zeros(count, dtype=bool)
    green = np.zeros(count, dtype=bool)
    for index in range(1, count):
        if color_definition == "candle_color":
            red[index] = closes[index] < opens[index]
            green[index] = closes[index] > opens[index]
        elif color_definition == "both":
            red[index] = closes[index] < closes[index - 1] and closes[index] < opens[index]
            green[index] = closes[index] > closes[index - 1] and closes[index] > opens[index]
        else:
            red[index] = closes[index] < closes[index - 1]
            green[index] = closes[index] > closes[index - 1]
    return red, green


def signal_core(source):
    count = len(source)
    times = source["time"].to_numpy(dtype=float)
    opens = source["open"].to_numpy(dtype=float)
    highs = source["high"].to_numpy(dtype=float)
    lows = source["low"].to_numpy(dtype=float)
    closes = source["close"].to_numpy(dtype=float)
    red, green = candle_flags(opens, closes)
    up_run = np.zeros(count, dtype=int)
    down_run = np.zeros(count, dtype=int)
    higher = np.zeros(count, dtype=int)
    lower = np.zeros(count, dtype=int)
    for index in range(1, count):
        higher[index] = int(closes[index] > closes[index - 1])
        lower[index] = int(closes[index] < closes[index - 1])
        up_run[index] = up_run[index - 1] + 1 if higher[index] else 0
        down_run[index] = down_run[index - 1] + 1 if lower[index] else 0
    count_length = minimum_run + grace_bars
    up_count = pd.Series(higher, dtype=float).rolling(count_length, min_periods=count_length).sum().fillna(0.0).to_numpy(dtype=float)
    down_count = pd.Series(lower, dtype=float).rolling(count_length, min_periods=count_length).sum().fillna(0.0).to_numpy(dtype=float)
    window = max(arm_max, extreme_lookback)

    keys_float = ("frdHigh", "frdLow", "frdClose", "frdTime", "peak", "peakTime", "fgdHigh", "fgdLow", "fgdClose", "fgdTime", "trough", "troughTime", "currentPeak", "currentTrough", "localHigh", "localLow", "swingHigh", "swingHighTime", "swingLow", "swingLowTime")
    result = {key: np.full(count, np.nan, dtype=float) for key in keys_float}
    for key in ("frdArm", "frdDay", "fgdArm", "fgdDay", "bosUp", "bosDown"):
        result[key] = np.zeros(count, dtype=bool)
    result["upRun"] = up_run
    result["downRun"] = down_run
    result["open"] = opens.copy()
    result["close"] = closes.copy()
    result["red"] = red
    result["green"] = green

    active_swing_high = np.nan
    active_swing_high_time = np.nan
    active_high = False
    active_swing_low = np.nan
    active_swing_low_time = np.nan
    active_low = False

    for index in range(count):
        lookback_start = max(0, index - extreme_lookback + 1)
        result["localHigh"][index] = float(np.max(highs[lookback_start:index + 1]))
        result["localLow"][index] = float(np.min(lows[lookback_start:index + 1]))
        run_up_start = max(0, index - max(int(up_run[index]), 1) + 1)
        run_down_start = max(0, index - max(int(down_run[index]), 1) + 1)
        result["currentPeak"][index] = float(np.max(highs[run_up_start:index + 1]))
        result["currentTrough"][index] = float(np.min(lows[run_down_start:index + 1]))

        if index > window:
            start = max(0, index - window + 1)
            high_slice = highs[start:index + 1]
            low_slice = lows[start:index + 1]
            peak_relative = int(np.where(high_slice == np.max(high_slice))[0][-1])
            trough_relative = int(np.where(low_slice == np.min(low_slice))[0][-1])
            peak_index = start + peak_relative
            trough_index = start + trough_relative
            peak_offset = index - peak_index
            trough_offset = index - trough_index

            frd_index = -1
            for offset in range(peak_offset, -1, -1):
                candidate = index - offset
                if red[candidate]:
                    frd_index = candidate
                    break
            fgd_index = -1
            for offset in range(trough_offset, -1, -1):
                candidate = index - offset
                if green[candidate]:
                    fgd_index = candidate
                    break

            frd_arm = peak_offset <= arm_max and up_count[peak_index] >= minimum_run and frd_index >= 0
            fgd_arm = trough_offset <= arm_max and down_count[trough_index] >= minimum_run and fgd_index >= 0
            result["frdArm"][index] = frd_arm
            result["frdDay"][index] = frd_arm and frd_index == index
            result["fgdArm"][index] = fgd_arm
            result["fgdDay"][index] = fgd_arm and fgd_index == index
            if frd_index >= 0:
                result["frdHigh"][index] = highs[frd_index]
                result["frdLow"][index] = lows[frd_index]
                result["frdClose"][index] = closes[frd_index]
                result["frdTime"][index] = times[frd_index]
            if fgd_index >= 0:
                result["fgdHigh"][index] = highs[fgd_index]
                result["fgdLow"][index] = lows[fgd_index]
                result["fgdClose"][index] = closes[fgd_index]
                result["fgdTime"][index] = times[fgd_index]
            result["peak"][index] = highs[peak_index]
            result["peakTime"][index] = times[peak_index]
            result["trough"][index] = lows[trough_index]
            result["troughTime"][index] = times[trough_index]

        pivot_index = index - pivot_length
        if pivot_index >= pivot_length and index >= pivot_length * 2:
            left_highs = highs[pivot_index - pivot_length:pivot_index]
            right_highs = highs[pivot_index + 1:pivot_index + pivot_length + 1]
            left_lows = lows[pivot_index - pivot_length:pivot_index]
            right_lows = lows[pivot_index + 1:pivot_index + pivot_length + 1]
            if highs[pivot_index] > np.max(left_highs) and highs[pivot_index] >= np.max(right_highs):
                active_swing_high = highs[pivot_index]
                active_swing_high_time = times[pivot_index]
                active_high = True
            if lows[pivot_index] < np.min(left_lows) and lows[pivot_index] <= np.min(right_lows):
                active_swing_low = lows[pivot_index]
                active_swing_low_time = times[pivot_index]
                active_low = True
        if active_high and closes[index] > active_swing_high:
            result["bosUp"][index] = True
            active_high = False
        if active_low and closes[index] < active_swing_low:
            result["bosDown"][index] = True
            active_low = False
        result["swingHigh"][index] = active_swing_high
        result["swingHighTime"][index] = active_swing_high_time
        result["swingLow"][index] = active_swing_low
        result["swingLowTime"][index] = active_swing_low_time
    return result


signal_frame = requested_frame(resolved_signal_timeframe)
signal_time = signal_frame["time"].to_numpy(dtype=float)
signal_data = signal_core(signal_frame)


def align_signal(key, default_value=np.nan):
    source = signal_data[key]
    is_bool = source.dtype == bool
    aligned = np.zeros(n, dtype=bool) if is_bool else np.full(n, default_value, dtype=float)
    source_index = np.full(n, -1, dtype=int)
    previous_source = -2
    event = np.zeros(n, dtype=bool)
    for index in range(n):
        if same_signal_timeframe:
            resolved_index = min(index, len(source) - 1)
        else:
            current_index = int(np.searchsorted(signal_time, base_time_seconds[index], side="right") - 1)
            resolved_index = current_index - 1
        if resolved_index < 0 or resolved_index >= len(source):
            continue
        source_index[index] = resolved_index
        aligned[index] = source[resolved_index]
        if is_bool and bool(source[resolved_index]) and resolved_index != previous_source:
            event[index] = True
        previous_source = resolved_index
    return aligned, source_index, event


aligned_signal = {}
signal_source_index = np.full(n, -1, dtype=int)
signal_events = {}
for key in signal_data:
    aligned_signal[key], current_source_index, signal_events[key] = align_signal(key)
    if np.any(current_source_index >= 0):
        signal_source_index = current_source_index

frd_day_event = signal_events["frdDay"] if not same_signal_timeframe else aligned_signal["frdDay"]
fgd_day_event = signal_events["fgdDay"] if not same_signal_timeframe else aligned_signal["fgdDay"]
bos_up_event = (signal_events["bosUp"] if not same_signal_timeframe else aligned_signal["bosUp"]) & show_bos
bos_down_event = (signal_events["bosDown"] if not same_signal_timeframe else aligned_signal["bosDown"]) & show_bos

ma_frame = requested_frame(resolved_ma_timeframe)
ma_time = ma_frame["time"].to_numpy(dtype=float)
ma_close = ma_frame["close"].to_numpy(dtype=float)
ma_values_raw = moving_average(ma_close, ma_length, ma_type)
ma_atr = true_range_atr(ma_frame, 14)
ma_slope_raw = np.full(len(ma_frame), np.nan, dtype=float)
for index in range(slope_length, len(ma_frame)):
    if np.isfinite(ma_values_raw[index]) and np.isfinite(ma_values_raw[index - slope_length]) and np.isfinite(ma_atr[index]) and ma_atr[index] != 0.0:
        ma_slope_raw[index] = (ma_values_raw[index] - ma_values_raw[index - slope_length]) / ma_atr[index]
ma_cross_down_raw = np.zeros(len(ma_frame), dtype=bool)
ma_cross_up_raw = np.zeros(len(ma_frame), dtype=bool)
for index in range(1, len(ma_frame)):
    if np.isfinite(ma_values_raw[index]) and np.isfinite(ma_values_raw[index - 1]):
        ma_cross_down_raw[index] = ma_close[index] < ma_values_raw[index] and ma_close[index - 1] >= ma_values_raw[index - 1]
        ma_cross_up_raw[index] = ma_close[index] > ma_values_raw[index] and ma_close[index - 1] <= ma_values_raw[index - 1]

ma_ref = np.full(n, np.nan, dtype=float)
slope_ref = np.full(n, np.nan, dtype=float)
ma_source_index = np.full(n, -1, dtype=int)
ma_cross_down_event = np.zeros(n, dtype=bool)
ma_cross_up_event = np.zeros(n, dtype=bool)
ma_is_higher = ma_seconds > chart_seconds and resolved_ma_timeframe != chart_timeframe
previous_ma_source = -2
for index in range(n):
    if ma_is_higher:
        current_index = int(np.searchsorted(ma_time, base_time_seconds[index], side="right") - 1)
        source_index = current_index - 1
    else:
        source_index = min(index, len(ma_frame) - 1)
    if source_index < 0 or source_index >= len(ma_frame):
        continue
    ma_source_index[index] = source_index
    ma_ref[index] = ma_values_raw[source_index]
    slope_ref[index] = ma_slope_raw[source_index]
    if ma_is_higher:
        changed = source_index != previous_ma_source
        ma_cross_down_event[index] = changed and ma_cross_down_raw[source_index]
        ma_cross_up_event[index] = changed and ma_cross_up_raw[source_index]
    elif index > 0 and np.isfinite(ma_ref[index]) and np.isfinite(ma_ref[index - 1]):
        ma_cross_down_event[index] = base_close[index] < ma_ref[index] and base_close[index - 1] >= ma_ref[index - 1]
        ma_cross_up_event[index] = base_close[index] > ma_ref[index] and base_close[index - 1] <= ma_ref[index - 1]
    previous_ma_source = source_index

base_atr = true_range_atr(base_calc, 14)
frd_live_series = np.zeros(n, dtype=bool)
fgd_live_series = np.zeros(n, dtype=bool)
frd_fire = np.zeros(n, dtype=bool)
fgd_fire = np.zeros(n, dtype=bool)
frd_break = np.zeros(n, dtype=bool)
fgd_break = np.zeros(n, dtype=bool)
frd_zone_top = np.full(n, np.nan, dtype=float)
frd_zone_bottom = np.full(n, np.nan, dtype=float)
fgd_zone_top = np.full(n, np.nan, dtype=float)
fgd_zone_bottom = np.full(n, np.nan, dtype=float)

last_down_cross_time = np.nan
last_up_cross_time = np.nan
last_frd_peak_time = np.nan
last_fgd_trough_time = np.nan
last_signal_text = "N/A"
zones = []
trigger_down_active = False
trigger_down_time = np.nan
trigger_down_level = np.nan
trigger_down_zone = np.nan
trigger_down_peak = np.nan
trigger_down_touched = False
trigger_up_active = False
trigger_up_time = np.nan
trigger_up_level = np.nan
trigger_up_zone = np.nan
trigger_up_trough = np.nan
trigger_up_touched = False
zone_seconds = zone_length * effective_signal_seconds
cross_window_seconds = (ma_break_gap + 1) * effective_signal_seconds

for index in range(n):
    current_time = base_time_seconds[index]
    if np.isfinite(last_down_cross_time) and current_time - last_down_cross_time > cross_window_seconds:
        last_down_cross_time = np.nan
    if np.isfinite(last_up_cross_time) and current_time - last_up_cross_time > cross_window_seconds:
        last_up_cross_time = np.nan
    if ma_cross_down_event[index]:
        last_down_cross_time = current_time
        last_up_cross_time = np.nan
    if ma_cross_up_event[index]:
        last_up_cross_time = current_time
        last_down_cross_time = np.nan

    peak_time = aligned_signal["peakTime"][index]
    trough_time = aligned_signal["troughTime"][index]
    frd_live = bool(aligned_signal["frdArm"][index]) and (not np.isfinite(last_frd_peak_time) or peak_time != last_frd_peak_time)
    fgd_live = bool(aligned_signal["fgdArm"][index]) and (not np.isfinite(last_fgd_trough_time) or trough_time != last_fgd_trough_time)
    frd_live_series[index] = frd_live
    fgd_live_series[index] = fgd_live
    slope_down_ok = not use_slope or not np.isfinite(slope_ref[index]) or slope_ref[index] < slope_max
    slope_up_ok = not use_slope or not np.isfinite(slope_ref[index]) or slope_ref[index] > -slope_max
    if use_ma:
        frd_fire[index] = bool(frd_live and np.isfinite(ma_ref[index]) and base_close[index] < ma_ref[index] and slope_down_ok and (ma_cross_down_event[index] or (frd_day_event[index] and np.isfinite(last_down_cross_time))))
        fgd_fire[index] = bool(fgd_live and np.isfinite(ma_ref[index]) and base_close[index] > ma_ref[index] and slope_up_ok and (ma_cross_up_event[index] or (fgd_day_event[index] and np.isfinite(last_up_cross_time))))
    else:
        frd_fire[index] = bool(frd_live and frd_day_event[index])
        fgd_fire[index] = bool(fgd_live and fgd_day_event[index])

    if frd_fire[index]:
        atr_value = base_atr[index] if np.isfinite(base_atr[index]) else max(base_high[index] - base_low[index], 1e-9)
        width = zone_atr_width * atr_value
        signal_far = base_close[index] > aligned_signal["frdHigh"][index] or aligned_signal["frdClose"][index] - base_close[index] > zone_max_atr * atr_value
        use_signal_zone = zone_mode == "Signal_Day" or (zone_mode == "Auto" and not signal_far)
        zone_top = aligned_signal["frdHigh"][index] if use_signal_zone else min(ma_ref[index] + width, aligned_signal["peak"][index])
        zone_bottom = aligned_signal["frdClose"][index] if use_signal_zone else min(ma_ref[index], max(base_close[index], ma_ref[index] - width))
        trigger = aligned_signal["frdLow"][index] if use_signal_zone else base_low[index]
        frd_zone_top[index] = zone_top
        frd_zone_bottom[index] = zone_bottom
        zones.append({"start": current_time, "end": current_time + zone_seconds, "top": float(zone_top), "bottom": float(zone_bottom), "trigger": float(trigger), "direction": -1})
        zones = zones[-max_zones:]
        last_frd_peak_time = peak_time
        last_down_cross_time = np.nan
        last_signal_text = "FRD"
        trigger_down_active = use_trigger_entry and base_close[index] > trigger
        trigger_down_time = current_time
        trigger_down_level = trigger
        trigger_down_zone = zone_bottom
        trigger_down_peak = aligned_signal["peak"][index]
        trigger_down_touched = False

    if fgd_fire[index]:
        atr_value = base_atr[index] if np.isfinite(base_atr[index]) else max(base_high[index] - base_low[index], 1e-9)
        width = zone_atr_width * atr_value
        signal_far = base_close[index] < aligned_signal["fgdLow"][index] or base_close[index] - aligned_signal["fgdClose"][index] > zone_max_atr * atr_value
        use_signal_zone = zone_mode == "Signal_Day" or (zone_mode == "Auto" and not signal_far)
        zone_bottom = aligned_signal["fgdLow"][index] if use_signal_zone else max(ma_ref[index] - width, aligned_signal["trough"][index])
        zone_top = aligned_signal["fgdClose"][index] if use_signal_zone else max(ma_ref[index], min(base_close[index], ma_ref[index] + width))
        trigger = aligned_signal["fgdHigh"][index] if use_signal_zone else base_high[index]
        fgd_zone_top[index] = zone_top
        fgd_zone_bottom[index] = zone_bottom
        zones.append({"start": current_time, "end": current_time + zone_seconds, "top": float(zone_top), "bottom": float(zone_bottom), "trigger": float(trigger), "direction": 1})
        zones = zones[-max_zones:]
        last_fgd_trough_time = trough_time
        last_up_cross_time = np.nan
        last_signal_text = "FGD"
        trigger_up_active = use_trigger_entry and base_close[index] < trigger
        trigger_up_time = current_time
        trigger_up_level = trigger
        trigger_up_zone = zone_top
        trigger_up_trough = aligned_signal["trough"][index]
        trigger_up_touched = False

    if trigger_down_active and current_time > trigger_down_time:
        if current_time - trigger_down_time > zone_seconds or base_close[index] > trigger_down_peak or fgd_fire[index]:
            trigger_down_active = False
        else:
            if base_high[index] >= trigger_down_zone:
                trigger_down_touched = True
            if base_close[index] < trigger_down_level and (not trigger_no_touch or not trigger_down_touched):
                frd_break[index] = True
                trigger_down_active = False
                last_signal_text = "FRD BRK"

    if trigger_up_active and current_time > trigger_up_time:
        if current_time - trigger_up_time > zone_seconds or base_close[index] < trigger_up_trough or frd_fire[index]:
            trigger_up_active = False
        else:
            if base_low[index] <= trigger_up_zone:
                trigger_up_touched = True
            if base_close[index] > trigger_up_level and (not trigger_no_touch or not trigger_up_touched):
                fgd_break[index] = True
                trigger_up_active = False
                last_signal_text = "FGD BRK"

zone_cloud = [None] * n
trigger_plot = [None] * n
if show_zones or show_trigger:
    for index in range(n):
        matching = [zone for zone in zones if zone["start"] <= base_time_seconds[index] <= zone["end"]]
        if not matching:
            continue
        zone = matching[-1]
        color_value = bullish_color if zone["direction"] > 0 else bearish_color
        if show_zones:
            zone_cloud[index] = {"upper": max(zone["top"], zone["bottom"]), "lower": min(zone["top"], zone["bottom"]), "color": color_value}
        if show_trigger:
            trigger_plot[index] = {"value": zone["trigger"], "color": color_value}


def marks(events, values):
    return [float(values[index]) if events[index] else None for index in range(n)]


plots = [{"name": "FRD FGD Alignment Anchor", "title": False, "data": [float(value) for value in base_close], "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True}]
if show_ma:
    plots.append({"name": f"{ma_length} {ma_type} ({resolved_ma_timeframe})", "data": [float(value) if np.isfinite(value) else None for value in ma_ref], "color": accent_color, "type": "line", "lineWidth": 2, "overlay": True})
if show_zones:
    plots.append({"name": "FRD FGD Setup Zones", "title": False, "data": zone_cloud, "color": accent_color, "type": "cloud", "opacity": 0.15, "overlay": True})
if show_trigger:
    plots.append({"name": "Zone Trigger", "data": trigger_plot, "color": accent_color, "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True})

signals = [
    {"type": "sell", "text": "FRD", "name": "FRD", "data": marks(frd_fire, base_high), "color": bearish_color, "renderMode": "events"},
    {"type": "buy", "text": "FGD", "name": "FGD", "data": marks(fgd_fire, base_low), "color": bullish_color, "renderMode": "events"},
    {"type": "sell", "text": "FRD BRK", "name": "FRD Trigger Entry", "data": marks(frd_break, base_high), "color": bearish_color, "renderMode": "events"},
    {"type": "buy", "text": "FGD BRK", "name": "FGD Trigger Entry", "data": marks(fgd_break, base_low), "color": bullish_color, "renderMode": "events"},
]
if use_ma and show_armed:
    signals.extend([
        {"type": "sell", "text": "FRD?", "name": "FRD Day Armed", "data": marks(frd_day_event & frd_live_series, base_high), "color": "rgba(255,23,68,0.70)", "renderMode": "events"},
        {"type": "buy", "text": "FGD?", "name": "FGD Day Armed", "data": marks(fgd_day_event & fgd_live_series, base_low), "color": "rgba(0,230,118,0.70)", "renderMode": "events"},
    ])

layer_candidates = []
for index in range(max(0, n - label_lookback), n):
    if bos_up_event[index] and np.isfinite(aligned_signal["swingHigh"][index]):
        layer_candidates.append((index, "BOS+", bullish_color, aligned_signal["swingHigh"][index], "above"))
    if bos_down_event[index] and np.isfinite(aligned_signal["swingLow"][index]):
        layer_candidates.append((index, "BOS-", bearish_color, aligned_signal["swingLow"][index], "below"))
layers = []
for index, text_value, color_value, price_value, side_value in layer_candidates[-max_labels:]:
    layers.append({"type": "label", "timestamp": float(time_values[index]), "price": float(price_value), "text": text_value, "color": color_value, "fillColor": "rgba(10,22,40,0.88)", "borderColor": color_value, "textColor": color_value, "fontSize": 8, "side": side_value})

last_index = n - 1
tables = []
if show_dashboard:
    last_up_run = int(aligned_signal["upRun"][last_index]) if n else 0
    last_down_run = int(aligned_signal["downRun"][last_index]) if n else 0
    above_ma = bool(n and np.isfinite(ma_ref[last_index]) and base_close[last_index] > ma_ref[last_index])
    status = "FRD armed" if n and frd_live_series[last_index] else "FGD armed" if n and fgd_live_series[last_index] else "Watching FRD" if last_up_run >= minimum_run else "Watching FGD" if last_down_run >= minimum_run else "No setup"
    tables.append({
        "position": dashboard_position if dashboard_position in ("top-right", "top-left", "bottom-right", "bottom-left") else "top-right",
        "backgroundColor": "rgba(10,22,40,0.94)",
        "borderColor": accent_color,
        "textColor": "#F8FAFC",
        "accentColor": accent_color,
        "rows": [
            ["CC FIRST RED / GREEN", "CONFIRMED"],
            ["Signal TF", f"{resolved_signal_timeframe}{' HA' if candle_source == 'heikin_ashi' else ''}"],
            ["Definition", color_definition.replace("_", " ").title()],
            ["Higher Closes", f"{last_up_run}{' OK' if last_up_run >= minimum_run else ''}"],
            ["Lower Closes", f"{last_down_run}{' OK' if last_down_run >= minimum_run else ''}"],
            ["Last Signal", last_signal_text],
            [f"{ma_length} {ma_type} ({resolved_ma_timeframe})", "Above" if above_ma else "Below"],
            ["Status", status],
        ],
    })


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": layers,
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "resolvedSignalTimeframe": resolved_signal_timeframe,
        "resolvedMaTimeframe": resolved_ma_timeframe,
        "signalSourceIndex": [int(value) for value in signal_source_index],
        "maSourceIndex": [int(value) for value in ma_source_index],
        "signalRawCount": int(len(signal_frame)),
        "maRawCount": int(len(ma_frame)),
        "signalRed": [bool(value) for value in aligned_signal["red"]],
        "signalGreen": [bool(value) for value in aligned_signal["green"]],
        "signalOpen": finite(aligned_signal["open"]),
        "signalClose": finite(aligned_signal["close"]),
        "upRun": [int(value) if np.isfinite(value) else 0 for value in aligned_signal["upRun"]],
        "downRun": [int(value) if np.isfinite(value) else 0 for value in aligned_signal["downRun"]],
        "frdArm": [bool(value) for value in aligned_signal["frdArm"]],
        "fgdArm": [bool(value) for value in aligned_signal["fgdArm"]],
        "frdDayEvent": [bool(value) for value in frd_day_event],
        "fgdDayEvent": [bool(value) for value in fgd_day_event],
        "frdLive": [bool(value) for value in frd_live_series],
        "fgdLive": [bool(value) for value in fgd_live_series],
        "frdFire": [bool(value) for value in frd_fire],
        "fgdFire": [bool(value) for value in fgd_fire],
        "frdBreak": [bool(value) for value in frd_break],
        "fgdBreak": [bool(value) for value in fgd_break],
        "frdZoneTop": finite(frd_zone_top),
        "frdZoneBottom": finite(frd_zone_bottom),
        "fgdZoneTop": finite(fgd_zone_top),
        "fgdZoneBottom": finite(fgd_zone_bottom),
        "ma": finite(ma_ref),
        "maSlope": finite(slope_ref),
        "maCrossDown": [bool(value) for value in ma_cross_down_event],
        "maCrossUp": [bool(value) for value in ma_cross_up_event],
        "bosUp": [bool(value) for value in bos_up_event],
        "bosDown": [bool(value) for value in bos_down_event],
        "liveTickFormingAlertsSupported": False,
    },
}
