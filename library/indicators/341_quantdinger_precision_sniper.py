# ============================================================
# QuantDinger Precision Sniper
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Precision Sniper"
my_indicator_description = "Scores crossover evidence, confirms candidates, builds ATR/structure trade plans, and simulates partial targets, step stops, costs, and closed-trade statistics."
indicator_timeframe_param = "higher_timeframe"
indicator_timeframes = ["1m", "5m", "15m", "30m", "1H", "4H", "1D", "1W"]

# @param price_source str Close Price source values=Close,HL2,HLC3,OHLC4
# @param preset str Auto Parameter profile values=Auto,Conservative,Default,Aggressive,Scalping,Swing,Crypto 24/7,Custom
# @param htf_mode str Soft_score Higher-timeframe mode values=Off,Soft_score,Hard_filter
# @param higher_timeframe str Off Higher timeframe values=Off,5m,15m,30m,1H,4H,1D,1W
# @param vwap_mode str Auto Session VWAP values=Auto,On,Off
# @param warmup_periods int 3 Warmup periods range=2:10:1
# @param fast_length int 9 Fast EMA for Custom range=3:50:1
# @param slow_length int 21 Slow EMA for Custom range=10:100:1
# @param trend_length int 55 Trend EMA for Custom range=20:200:1
# @param rsi_length int 13 RSI length for Custom range=5:30:1
# @param atr_length int 14 ATR length for Custom range=5:50:1
# @param minimum_score int 5 Minimum score out of ten for Custom range=1:10:1
# @param stop_multiplier float 1.5 ATR stop multiplier for Custom range=0.5:5:0.1
# @param grade_filter str All Grade filter values=All,A+ and A,A+ Only
# @param hide_c_grade bool true Require at least B grade
# @param high_volatility_mode str Skip_Signals High-volatility action values=Off,Skip_Signals,Widen_SL
# @param volatility_threshold float 1.3 High ATR to mean ATR ratio range=1.05:3:0.05
# @param volatility_widening float 1.5 High-volatility stop multiplier range=1:3:0.1
# @param confirmation_window int 0 Extra candidate confirmation bars range=0:10:1
# @param maximum_extension float 0 Maximum fast-EMA distance in ATR; zero disables range=0:10:0.1
# @param tp1_r float 1.0 First target in R range=0.5:5:0.1
# @param tp2_r float 2.0 Second target in R range=1:8:0.1
# @param tp3_r float 3.0 Third target in R range=1.5:12:0.5
# @param tp1_close_percent float 0.0 Original position closed at TP1 range=0:99:1
# @param tp2_close_percent float 0.0 Original position closed at TP2 range=0:99:1
# @param step_stop bool true Advance stop after targets
# @param close_at_tp3 bool true Close the remainder at TP3
# @param use_structure bool true Allow recent structure to widen the stop
# @param structure_lookback int 10 Previous bars used for structure range=3:30:1
# @param structure_policy str Cap_and_flag Structure beyond cap values=Cap_and_flag,Skip_entry
# @param minimum_risk_ticks int 2 Minimum initial risk in ticks range=1:1000:1
# @param minimum_tick float 0.01 Instrument minimum price increment range=0.00000001:100:0.00000001
# @param commission_percent float 0.0 Commission per fill side range=0:5:0.01
# @param spread_ticks float 0.0 Estimated round-trip spread range=0:1000:0.1
# @param slippage_ticks float 0.0 Adverse slippage per fill side range=0:1000:0.1
# @param require_positive_prices bool true Require positive plan prices
# @param session_mode str Info_only Trading window values=Off,Info_only,Filter_entries
# @param session_hours str 0000-0000 Session hours
# @param session_days str 1234567 Session days
# @param session_timezone str Etc/UTC IANA session timezone
# @param start_time float 1577836800000 Entry and statistics start
# @param end_time float 4102444800000 Entry and statistics end
# @param show_levels bool true Show nearest known levels
# @param pivot_length int 3 Confirmed pivot bars on each side range=1:20:1
# @param show_ribbon bool true Show EMA ribbon
# @param show_trade_levels bool true Show active trade levels
# @param show_background bool false Show trend background
# @param show_dashboard bool true Show dashboard
# @param show_journal bool false Show recent-trade journal
# @param journal_rows int 5 Recent trades shown range=1:15:1
# @param show_signals bool true Show entry and exit markers


def _bounded_int(name, default, low, high):
    return min(high, max(low, int(params.get(name, default))))


def _bounded_float(name, default, low, high):
    return min(high, max(low, float(params.get(name, default))))


price_source = str(params.get("price_source", "Close"))
preset_input = str(params.get("preset", "Auto"))
htf_mode = str(params.get("htf_mode", "Soft_score")).replace("_", " ")
higher_timeframe = str(params.get("higher_timeframe", "Off"))
vwap_mode = str(params.get("vwap_mode", "Auto"))
warmup_periods = _bounded_int("warmup_periods", 3, 2, 10)
custom_fast = _bounded_int("fast_length", 9, 3, 50)
custom_slow = _bounded_int("slow_length", 21, 10, 100)
custom_trend = _bounded_int("trend_length", 55, 20, 200)
custom_rsi = _bounded_int("rsi_length", 13, 5, 30)
custom_atr = _bounded_int("atr_length", 14, 5, 50)
custom_score = _bounded_int("minimum_score", 5, 1, 10)
custom_stop = _bounded_float("stop_multiplier", 1.5, 0.5, 5.0)
grade_filter = str(params.get("grade_filter", "All"))
hide_c_grade = bool(params.get("hide_c_grade", True))
high_volatility_mode = str(params.get("high_volatility_mode", "Skip_Signals")).replace("_", " ")
volatility_threshold = _bounded_float("volatility_threshold", 1.3, 1.05, 3.0)
volatility_widening = _bounded_float("volatility_widening", 1.5, 1.0, 3.0)
confirmation_window = _bounded_int("confirmation_window", 0, 0, 10)
maximum_extension = _bounded_float("maximum_extension", 0.0, 0.0, 10.0)
tp1_r = _bounded_float("tp1_r", 1.0, 0.5, 5.0)
tp2_r = _bounded_float("tp2_r", 2.0, 1.0, 8.0)
tp3_r = _bounded_float("tp3_r", 3.0, 1.5, 12.0)
tp1_fraction = _bounded_float("tp1_close_percent", 0.0, 0.0, 99.0) / 100.0
tp2_fraction = _bounded_float("tp2_close_percent", 0.0, 0.0, 99.0) / 100.0
step_stop = bool(params.get("step_stop", True))
close_at_tp3 = bool(params.get("close_at_tp3", True))
use_structure = bool(params.get("use_structure", True))
structure_lookback = _bounded_int("structure_lookback", 10, 3, 30)
structure_policy = str(params.get("structure_policy", "Cap_and_flag")).replace("_", " ")
minimum_risk_ticks = _bounded_int("minimum_risk_ticks", 2, 1, 1000)
minimum_tick = _bounded_float("minimum_tick", 0.01, 0.00000001, 100.0)
commission_percent = _bounded_float("commission_percent", 0.0, 0.0, 5.0)
spread_ticks = _bounded_float("spread_ticks", 0.0, 0.0, 1000.0)
slippage_ticks = _bounded_float("slippage_ticks", 0.0, 0.0, 1000.0)
require_positive_prices = bool(params.get("require_positive_prices", True))
session_mode = str(params.get("session_mode", "Info_only")).replace("_", " ")
session_hours = str(params.get("session_hours", "0000-0000"))
session_days = str(params.get("session_days", "1234567"))
session_timezone = str(params.get("session_timezone", "Etc/UTC"))
start_time = float(params.get("start_time", 1577836800000.0))
end_time = float(params.get("end_time", 4102444800000.0))
show_levels = bool(params.get("show_levels", True))
pivot_length = _bounded_int("pivot_length", 3, 1, 20)
show_ribbon = bool(params.get("show_ribbon", True))
show_trade_levels = bool(params.get("show_trade_levels", True))
show_background = bool(params.get("show_background", False))
show_dashboard = bool(params.get("show_dashboard", True))
show_journal = bool(params.get("show_journal", False))
journal_rows = _bounded_int("journal_rows", 5, 1, 15)
show_signals = bool(params.get("show_signals", True))


def _normalize(frame):
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


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if length <= 0:
        return result
    running = 0.0
    valid = 0
    for index, value in enumerate(values):
        if np.isfinite(value):
            running += float(value)
            valid += 1
        if index >= length and np.isfinite(values[index - length]):
            running -= float(values[index - length])
            valid -= 1
        if index >= length - 1 and valid == length:
            result[index] = running / length
    return result


def _ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def _rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    seed = []
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed.append(float(value))
            if len(seed) == length:
                previous = sum(seed) / length
                result[index] = previous
        else:
            previous = (previous * (length - 1.0) + float(value)) / length
            result[index] = previous
    return result


def _rsi(values, length):
    changes = np.full(len(values), np.nan, dtype=float)
    if len(values) > 1:
        changes[1:] = values[1:] - values[:-1]
    gains = np.where(np.isfinite(changes), np.maximum(changes, 0.0), np.nan)
    losses = np.where(np.isfinite(changes), np.maximum(-changes, 0.0), np.nan)
    average_gain = _rma(gains, length)
    average_loss = _rma(losses, length)
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(len(values)):
        if np.isfinite(average_gain[index]) and np.isfinite(average_loss[index]):
            if average_loss[index] == 0.0:
                result[index] = 100.0 if average_gain[index] > 0.0 else 50.0
            else:
                result[index] = 100.0 - 100.0 / (1.0 + average_gain[index] / average_loss[index])
    return result


def _true_range(high_values, low_values, close_values):
    result = np.full(len(close_values), np.nan, dtype=float)
    for index in range(len(close_values)):
        if index == 0:
            result[index] = high_values[index] - low_values[index]
        else:
            result[index] = max(high_values[index] - low_values[index], abs(high_values[index] - close_values[index - 1]), abs(low_values[index] - close_values[index - 1]))
    return result


def _dmi(high_values, low_values, close_values, length=14):
    count = len(close_values)
    plus = np.full(count, np.nan, dtype=float)
    minus = np.full(count, np.nan, dtype=float)
    plus_raw = np.full(count, np.nan, dtype=float)
    minus_raw = np.full(count, np.nan, dtype=float)
    for index in range(1, count):
        up = high_values[index] - high_values[index - 1]
        down = low_values[index - 1] - low_values[index]
        plus_raw[index] = up if up > down and up > 0.0 else 0.0
        minus_raw[index] = down if down > up and down > 0.0 else 0.0
    smoothed_tr = _rma(_true_range(high_values, low_values, close_values), length)
    smoothed_plus = _rma(plus_raw, length)
    smoothed_minus = _rma(minus_raw, length)
    dx = np.full(count, np.nan, dtype=float)
    for index in range(count):
        if np.isfinite(smoothed_tr[index]) and smoothed_tr[index] > 0.0:
            plus[index] = 100.0 * smoothed_plus[index] / smoothed_tr[index]
            minus[index] = 100.0 * smoothed_minus[index] / smoothed_tr[index]
            denominator = plus[index] + minus[index]
            dx[index] = 100.0 * abs(plus[index] - minus[index]) / denominator if denominator > 0.0 else 0.0
    return plus, minus, _rma(dx, length)


def _timeframe_key(value):
    text = str(value or "1H").strip()
    aliases = {"1": "1m", "5": "5m", "15": "15m", "30": "30m", "60": "1H", "240": "4H", "D": "1D", "W": "1W", "1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W"}
    return aliases.get(text, text)


timeframe_seconds = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
df = df.copy()
frame = _normalize(df)
time_values = frame["time"].to_numpy(dtype=float)
opened = frame["open"].to_numpy(dtype=float)
high = frame["high"].to_numpy(dtype=float)
low = frame["low"].to_numpy(dtype=float)
close = frame["close"].to_numpy(dtype=float)
volume = frame["volume"].fillna(0.0).to_numpy(dtype=float)
n = len(frame)
runtime_ctx = globals().get("ctx")
chart_timeframe = _timeframe_key(getattr(runtime_ctx, "timeframe", None) or "1H")
chart_seconds = timeframe_seconds.get(chart_timeframe, 3600)
if start_time > 10_000_000_000:
    start_time /= 1000.0
if end_time > 10_000_000_000:
    end_time /= 1000.0

if price_source == "HL2":
    source = (high + low) / 2.0
elif price_source == "HLC3":
    source = (high + low + close) / 3.0
elif price_source == "OHLC4":
    source = (opened + high + low + close) / 4.0
else:
    source = close.copy()

if preset_input == "Auto":
    preset = "Scalping" if chart_seconds <= 300 else "Default" if chart_seconds <= 3600 else "Conservative" if chart_seconds < 14400 else "Swing"
else:
    preset = preset_input
profiles = {
    "Scalping": (5, 13, 34, 8, 10, 4, 0.8),
    "Aggressive": (8, 18, 50, 11, 12, 3, 1.2),
    "Default": (9, 21, 55, 13, 14, 5, 1.5),
    "Conservative": (12, 26, 89, 14, 14, 7, 2.0),
    "Swing": (13, 34, 89, 21, 20, 6, 2.5),
    "Crypto 24/7": (9, 21, 55, 14, 20, 5, 2.0),
    "Custom": (custom_fast, custom_slow, custom_trend, custom_rsi, custom_atr, custom_score, custom_stop),
}
fast_length, slow_length, trend_length, rsi_length, atr_length, minimum_score, stop_multiplier = profiles.get(preset, profiles["Custom"])
if not (fast_length < slow_length < trend_length):
    raise ValueError("EMA periods must satisfy fast < slow < trend")
if not (tp1_r < tp2_r < tp3_r):
    raise ValueError("Target multipliers must satisfy TP1 < TP2 < TP3")
if tp1_fraction + tp2_fraction >= 1.0:
    raise ValueError("TP1 and TP2 partial fractions must total less than 100 percent")
if start_time >= end_time:
    raise ValueError("The statistics start must precede the end")

ema_fast = _ema(source, fast_length)
ema_slow = _ema(source, slow_length)
ema_trend = _ema(source, trend_length)
true_range = _true_range(high, low, close)
atr = _rma(true_range, atr_length)
atr_mean = _sma(atr, 42)
rsi = _rsi(source, rsi_length)
macd_line = _ema(source, 12) - _ema(source, 26)
macd_signal = _ema(macd_line, 9)
macd_histogram = macd_line - macd_signal
di_plus, di_minus, adx = _dmi(high, low, close, 14)
volume_mean = _sma(volume, 20)
has_volume = bool(np.any(np.isfinite(volume) & (volume > 0.0)))

vwap = np.full(n, np.nan, dtype=float)
if n:
    dates = pd.to_datetime(time_values, unit="s", utc=True).floor("D")
    running_pv = 0.0
    running_volume = 0.0
    previous_date = None
    for index in range(n):
        if previous_date is None or dates[index] != previous_date:
            running_pv = 0.0
            running_volume = 0.0
            previous_date = dates[index]
        if np.isfinite(volume[index]) and volume[index] > 0.0:
            running_volume += volume[index]
            running_pv += volume[index] * (high[index] + low[index] + close[index]) / 3.0
        if running_volume > 0.0:
            vwap[index] = running_pv / running_volume
vwap_enabled = chart_seconds < 86400 and (vwap_mode == "On" or (vwap_mode == "Auto" and preset != "Crypto 24/7"))


def _align_last_closed_htf(frame_values, value_series):
    result = np.full(n, np.nan, dtype=float)
    if frame_values is None or len(frame_values) == 0:
        return result
    htf_times = frame_values["time"].to_numpy(dtype=float)
    for index, moment in enumerate(time_values):
        source_index = int(np.searchsorted(htf_times, moment, side="right") - 2)
        if 0 <= source_index < len(value_series) and np.isfinite(value_series[source_index]):
            result[index] = float(value_series[source_index])
    return result


htf_enabled = htf_mode != "Off" and higher_timeframe not in ("", "Off", "None")
htf_fast = np.full(n, np.nan, dtype=float)
htf_slow = np.full(n, np.nan, dtype=float)
htf_bias = np.zeros(n, dtype=int)
htf_available = not htf_enabled
if htf_enabled:
    requested_key = _timeframe_key(higher_timeframe)
    if timeframe_seconds.get(requested_key, 0) <= chart_seconds:
        raise ValueError("Enabled higher timeframe must be strictly above the chart timeframe")
    raw_series = getattr(runtime_ctx, "raw_series", None)
    raw_htf = raw_series(requested_key) if callable(raw_series) else None
    htf_available = raw_htf is not None and len(raw_htf) > 0
    if htf_available:
        htf_frame = _normalize(raw_htf)
        htf_source = htf_frame["close"].to_numpy(dtype=float)
        if price_source == "HL2":
            htf_source = (htf_frame["high"].to_numpy(dtype=float) + htf_frame["low"].to_numpy(dtype=float)) / 2.0
        elif price_source == "HLC3":
            htf_source = (htf_frame["high"].to_numpy(dtype=float) + htf_frame["low"].to_numpy(dtype=float) + htf_frame["close"].to_numpy(dtype=float)) / 3.0
        elif price_source == "OHLC4":
            htf_source = (htf_frame["open"].to_numpy(dtype=float) + htf_frame["high"].to_numpy(dtype=float) + htf_frame["low"].to_numpy(dtype=float) + htf_frame["close"].to_numpy(dtype=float)) / 4.0
        htf_fast = _align_last_closed_htf(htf_frame, _ema(htf_source, fast_length))
        htf_slow = _align_last_closed_htf(htf_frame, _ema(htf_source, slow_length))
        htf_bias = np.where(np.isfinite(htf_fast) & np.isfinite(htf_slow), np.where(htf_fast > htf_slow, 1, np.where(htf_fast < htf_slow, -1, 0)), 0)


def _session_mask():
    if n == 0:
        return np.zeros(0, dtype=bool)
    try:
        local = pd.DatetimeIndex(pd.to_datetime(time_values, unit="s", utc=True)).tz_convert(session_timezone)
    except Exception:
        local = pd.DatetimeIndex(pd.to_datetime(time_values, unit="s", utc=True))
    try:
        start_text, end_text = session_hours.split("-", 1)
        start_minutes = int(start_text[:2]) * 60 + int(start_text[2:4])
        end_minutes = int(end_text[:2]) * 60 + int(end_text[2:4])
    except Exception:
        start_minutes, end_minutes = 0, 0
    pine_days = ((local.weekday + 1) % 7) + 1
    allowed_days = {int(value) for value in session_days if value in "1234567"}
    minutes = local.hour * 60 + local.minute
    if start_minutes == end_minutes:
        inside_time = np.ones(n, dtype=bool)
    elif start_minutes < end_minutes:
        inside_time = (minutes >= start_minutes) & (minutes < end_minutes)
    else:
        inside_time = (minutes >= start_minutes) | (minutes < end_minutes)
    return np.asarray(inside_time & np.isin(pine_days, list(allowed_days)), dtype=bool)


in_session = _session_mask()
date_allowed = (time_values >= start_time) & ((time_values + chart_seconds) < end_time)
session_allowed = np.ones(n, dtype=bool) if session_mode != "Filter entries" else in_session.copy()
warmup_bars = max(trend_length * warmup_periods, atr_length + 42)
warmed = np.arange(n) >= warmup_bars
volume_ready = np.ones(n, dtype=bool) if not has_volume else (np.isfinite(volume) & (volume >= 0.0) & np.isfinite(volume_mean) & (volume_mean > 0.0))
vwap_ready = np.ones(n, dtype=bool) if not (vwap_enabled and has_volume) else np.isfinite(vwap)
htf_ready = np.ones(n, dtype=bool) if not htf_enabled else (htf_available & np.isfinite(htf_fast) & np.isfinite(htf_slow))
ready = warmed & np.isfinite(atr) & (atr > 0.0) & np.isfinite(atr_mean) & (atr_mean > 0.0) & np.isfinite(rsi) & np.isfinite(adx) & volume_ready & vwap_ready & htf_ready
volatility_ratio = np.divide(atr, atr_mean, out=np.full(n, np.nan), where=np.isfinite(atr_mean) & (atr_mean > 0.0))
high_volatility = np.isfinite(volatility_ratio) & (volatility_ratio > volatility_threshold)
volatility_stop_multiplier = np.where((high_volatility_mode == "Widen SL") & high_volatility, volatility_widening, 1.0)
bull_cross = np.zeros(n, dtype=bool)
bear_cross = np.zeros(n, dtype=bool)
if n > 1:
    bull_cross[1:] = (ema_fast[1:] > ema_slow[1:]) & (ema_fast[:-1] <= ema_slow[:-1])
    bear_cross[1:] = (ema_fast[1:] < ema_slow[1:]) & (ema_fast[:-1] >= ema_slow[:-1])
bull_momentum = (close > ema_fast) & (close > ema_slow)
bear_momentum = (close < ema_fast) & (close < ema_slow)
trend_direction = np.where((ema_fast > ema_slow) & (close > ema_trend), 1, np.where((ema_fast < ema_slow) & (close < ema_trend), -1, 0))
trending = adx >= 20.0
regime = np.where(~trending, 0, np.where(trend_direction != 0, 1, 2))
volume_above = has_volume & volume_ready & (volume > volume_mean * 1.2)
score_max = 5.0 + (1.0 if has_volume else 0.0) + (1.0 if vwap_enabled and has_volume else 0.0) + (1.5 if htf_enabled and htf_mode == "Soft score" else 0.0)
bull_score = ((close > ema_trend).astype(float) + ((rsi > 50.0) & (rsi < 75.0)).astype(float) + (macd_histogram > 0.0).astype(float) + (macd_histogram > np.roll(macd_histogram, 1)).astype(float) + (trending & (di_plus > di_minus)).astype(float) + volume_above.astype(float))
bear_score = ((close < ema_trend).astype(float) + ((rsi < 50.0) & (rsi > 25.0)).astype(float) + (macd_histogram < 0.0).astype(float) + (macd_histogram < np.roll(macd_histogram, 1)).astype(float) + (trending & (di_minus > di_plus)).astype(float) + volume_above.astype(float))
if n:
    bull_score[0] = max(0.0, bull_score[0] - float(macd_histogram[0] > np.roll(macd_histogram, 1)[0]))
    bear_score[0] = max(0.0, bear_score[0] - float(macd_histogram[0] < np.roll(macd_histogram, 1)[0]))
if vwap_enabled and has_volume:
    bull_score += (close > vwap).astype(float)
    bear_score += (close < vwap).astype(float)
if htf_enabled and htf_mode == "Soft score":
    bull_score += (htf_bias == 1).astype(float) * 1.5
    bear_score += (htf_bias == -1).astype(float) * 1.5
required_ratio = max(minimum_score / 10.0, 0.5 if hide_c_grade else 0.0, 0.8 if grade_filter == "A+ Only" else 0.65 if grade_filter == "A+ and A" else 0.0)

swing_low = np.full(n, np.nan, dtype=float)
swing_high = np.full(n, np.nan, dtype=float)
for index in range(n):
    left = max(0, index - structure_lookback)
    swing_low[index] = float(np.min(low[left:index + 1]))
    swing_high[index] = float(np.max(high[left:index + 1]))


def _round_tick(value):
    return round(value / minimum_tick) * minimum_tick


def _round_down(value):
    return np.floor(value / minimum_tick + 1e-8) * minimum_tick


def _round_up(value):
    return np.ceil(value / minimum_tick - 1e-8) * minimum_tick


def _make_plan(index, direction):
    entry = _round_tick(close[index])
    atr_distance = atr[index] * stop_multiplier * volatility_stop_multiplier[index]
    atr_stop = entry - direction * atr_distance
    structure_stop = swing_low[index] - atr[index] * 0.2 if direction == 1 else swing_high[index] + atr[index] * 0.2
    raw_stop = min(atr_stop, structure_stop) if use_structure and direction == 1 else max(atr_stop, structure_stop) if use_structure else atr_stop
    capped = use_structure and abs(entry - raw_stop) > atr_distance * 1.5
    bounded = entry - direction * atr_distance * 1.5 if capped else raw_stop
    stop = _round_down(bounded) if direction == 1 else _round_up(bounded)
    risk = abs(entry - stop)
    target1 = _round_tick(entry + direction * risk * tp1_r)
    target2 = _round_tick(entry + direction * risk * tp2_r)
    target3 = _round_tick(entry + direction * risk * tp3_r)
    valid = np.all(np.isfinite([entry, stop, risk, target1, target2, target3])) and risk >= minimum_risk_ticks * minimum_tick - minimum_tick * 1e-6 and direction * (entry - stop) > 0.0 and direction * (target1 - entry) > 0.0 and direction * (target2 - target1) > 0.0 and direction * (target3 - target2) > 0.0
    if require_positive_prices:
        valid = valid and min(entry, stop, target1, target2, target3) > 0.0
    origin = "ATR" if not use_structure else "ATR capped" if capped else "Structure" if raw_stop != atr_stop else "ATR"
    return {"entry": float(entry), "stop": float(stop), "risk": float(risk), "tp1": float(target1), "tp2": float(target2), "tp3": float(target3), "capped": bool(capped), "valid": bool(valid), "origin": origin}


def _gate(index, direction, plan):
    evidence = bull_score[index] if direction == 1 else bear_score[index]
    momentum = bull_momentum[index] if direction == 1 else bear_momentum[index]
    if not ready[index]:
        return 1
    if not date_allowed[index]:
        return 2
    if not session_allowed[index]:
        return 3
    if high_volatility_mode == "Skip Signals" and high_volatility[index]:
        return 4
    if not momentum:
        return 5
    if (direction == 1 and rsi[index] >= 75.0) or (direction == -1 and rsi[index] <= 25.0):
        return 6
    if htf_enabled and htf_mode == "Hard filter" and htf_bias[index] != direction:
        return 7
    if evidence + 1e-9 < required_ratio * score_max:
        return 8
    if not plan["valid"]:
        return 9
    if structure_policy == "Skip entry" and plan["capped"]:
        return 10
    if maximum_extension > 0.0 and atr[index] > 0.0 and abs(close[index] - ema_fast[index]) / atr[index] > maximum_extension:
        return 11
    return 0


rejection_names = ["Opposite cross or already open", "Data not ready", "Outside date window", "Outside trading session", "High volatility", "Price momentum lost", "RSI extreme", "HTF opposes", "Insufficient score", "Invalid risk geometry", "Structure exceeds cap", "Fast EMA extension"]
rejected = [0] * 12
pending_direction = 0
pending_bar = -1
candidates = 0
accepted = 0
last_decision = "Waiting for crossover"
trade = {"active": False, "dir": 0}
journal = []
events = []
entry_long = np.zeros(n, dtype=bool)
entry_short = np.zeros(n, dtype=bool)
exit_long = np.zeros(n, dtype=bool)
exit_short = np.zeros(n, dtype=bool)
target_events = np.zeros((3, n), dtype=bool)
stop_events = np.zeros(n, dtype=bool)
entry_plot = np.full(n, np.nan, dtype=float)
initial_stop_plot = np.full(n, np.nan, dtype=float)
step_stop_plot = np.full(n, np.nan, dtype=float)
tp1_plot = np.full(n, np.nan, dtype=float)
tp2_plot = np.full(n, np.nan, dtype=float)
tp3_plot = np.full(n, np.nan, dtype=float)
total_r = 0.0
peak_r = 0.0
maximum_drawdown_r = 0.0
wins = 0
losses = 0
breakevens = 0
loss_streak = 0
maximum_loss_streak = 0
ambiguous_trades = 0
reached_tp1 = 0
reached_tp2 = 0
total_duration = 0
total_mfe = 0.0
total_mae = 0.0


def _side_cost(fill_price):
    return abs(fill_price) * commission_percent / 100.0 + (spread_ticks * 0.5 + slippage_ticks) * minimum_tick


def _take_partial(position, fill_price, requested_fraction):
    fraction = min(position["remaining"], max(0.0, requested_fraction))
    position["gross_r"] += fraction * position["dir"] * (fill_price - position["entry"]) / position["risk"]
    position["cost_r"] += fraction * _side_cost(fill_price) / position["risk"]
    position["remaining"] = max(0.0, position["remaining"] - fraction)


def _finish(position, index, fill_price, reason):
    _take_partial(position, fill_price, position["remaining"])
    position["active"] = False
    position["exit_bar"] = index
    position["exit_time"] = float(time_values[index] + chart_seconds)
    position["exit_price"] = float(fill_price)
    position["reason"] = reason


def _observe(position, favorable, adverse):
    position["mfe"] = max(position["mfe"], max(0.0, position["dir"] * (favorable - position["entry"]) / position["risk"]))
    position["mae"] = max(position["mae"], max(0.0, -position["dir"] * (adverse - position["entry"]) / position["risk"]))


for index in range(n):
    signal = 0
    if (bull_cross[index] or bear_cross[index]) and date_allowed[index] and warmed[index]:
        if pending_direction != 0:
            rejected[0] += 1
            events.append({"bar": index, "type": "candidate_cancelled", "direction": pending_direction, "reason": "Opposite crossover"})
        pending_direction = 1 if bull_cross[index] else -1
        pending_bar = index
        candidates += 1
    if pending_direction != 0:
        plan = _make_plan(index, pending_direction)
        code = _gate(index, pending_direction, plan)
        if not (bull_momentum[index] if pending_direction == 1 else bear_momentum[index]):
            code = 5
        last_chance = index - pending_bar >= confirmation_window
        already_open = bool(trade.get("active")) and trade.get("dir") == pending_direction
        if code == 0 and not already_open:
            signal = pending_direction
            last_decision = ("Long" if signal == 1 else "Short") + " accepted"
            accepted += 1
            pending_direction = 0
        else:
            last_decision = "Same direction already open" if already_open else rejection_names[code]
            if last_chance or code == 5 or already_open or not date_allowed[index]:
                rejected[0 if already_open else code] += 1
                events.append({"bar": index, "type": "candidate_cancelled", "direction": pending_direction, "reason": last_decision})
                pending_direction = 0

    if trade.get("active") and index > trade["entry_bar"]:
        old_stop = trade["stop"]
        stopped = low[index] <= old_stop if trade["dir"] == 1 else high[index] >= old_stop
        touch1 = not trade["hit1"] and (high[index] >= trade["tp1"] if trade["dir"] == 1 else low[index] <= trade["tp1"])
        touch2 = not trade["hit2"] and (high[index] >= trade["tp2"] if trade["dir"] == 1 else low[index] <= trade["tp2"])
        touch3 = not trade["hit3"] and (high[index] >= trade["tp3"] if trade["dir"] == 1 else low[index] <= trade["tp3"])
        if stopped:
            fill_price = min(opened[index], old_stop) if trade["dir"] == 1 else max(opened[index], old_stop)
            trade["ambiguous"] = bool(trade["ambiguous"] or touch1 or touch2 or touch3)
            _observe(trade, opened[index], opened[index])
            _observe(trade, fill_price, fill_price)
            _finish(trade, index, fill_price, "SL" if old_stop == trade["initial_stop"] else "Step stop")
            stop_events[index] = True
        else:
            if touch1:
                trade["hit1"] = True
                _take_partial(trade, trade["tp1"], tp1_fraction)
                target_events[0, index] = True
                events.append({"bar": index, "type": "tp1_hit", "price": trade["tp1"], "trade_id": trade["id"]})
            if touch2:
                trade["hit2"] = True
                _take_partial(trade, trade["tp2"], tp2_fraction)
                target_events[1, index] = True
                events.append({"bar": index, "type": "tp2_hit", "price": trade["tp2"], "trade_id": trade["id"]})
            if touch3:
                trade["hit3"] = True
                target_events[2, index] = True
                events.append({"bar": index, "type": "tp3_hit", "price": trade["tp3"], "trade_id": trade["id"]})
                if close_at_tp3:
                    _observe(trade, opened[index], opened[index])
                    _observe(trade, trade["tp3"], trade["tp3"])
                    _finish(trade, index, trade["tp3"], "TP3")
            if trade.get("active"):
                _observe(trade, high[index] if trade["dir"] == 1 else low[index], low[index] if trade["dir"] == 1 else high[index])
                if signal == -trade["dir"]:
                    _finish(trade, index, close[index], "Reversal")
                elif time_values[index] + chart_seconds >= end_time:
                    _finish(trade, index, close[index], "Date end")
                elif step_stop:
                    next_stop = trade["tp2"] if trade["hit3"] else trade["tp1"] if trade["hit2"] else trade["entry"] if trade["hit1"] else trade["stop"]
                    if trade["dir"] * (next_stop - trade["stop"]) > minimum_tick * 0.1:
                        trade["stop"] = next_stop
                        events.append({"bar": index, "type": "stop_changed", "price": next_stop, "trade_id": trade["id"]})

        if not trade.get("active"):
            net_r = trade["gross_r"] - trade["cost_r"]
            total_r += net_r
            peak_r = max(peak_r, total_r)
            maximum_drawdown_r = max(maximum_drawdown_r, peak_r - total_r)
            total_duration += index - trade["entry_bar"]
            total_mfe += trade["mfe"]
            total_mae += trade["mae"]
            reached_tp1 += int(trade["hit1"])
            reached_tp2 += int(trade["hit2"])
            ambiguous_trades += int(trade["ambiguous"])
            if net_r > 1e-8:
                wins += 1
                loss_streak = 0
            elif net_r < -1e-8:
                losses += 1
                loss_streak += 1
                maximum_loss_streak = max(maximum_loss_streak, loss_streak)
            else:
                breakevens += 1
                loss_streak = 0
            if trade["dir"] == 1:
                exit_long[index] = True
            else:
                exit_short[index] = True
            events.append({"bar": index, "type": "exit", "price": trade["exit_price"], "reason": trade["reason"], "net_r": net_r, "trade_id": trade["id"]})
            journal.insert(0, trade.copy())
            journal = journal[:50]

    if signal != 0 and not trade.get("active"):
        plan = _make_plan(index, signal)
        evidence = bull_score[index] if signal == 1 else bear_score[index]
        ratio = evidence / score_max if score_max > 0.0 else 0.0
        grade = "A+" if ratio >= 0.8 else "A" if ratio >= 0.65 else "B" if ratio >= 0.5 else "C"
        trade = {"active": True, "dir": signal, "id": f"precision:{index}:{int(time_values[index] + chart_seconds)}", "entry_bar": index, "entry_time": float(time_values[index] + chart_seconds), "entry": plan["entry"], "initial_stop": plan["stop"], "stop": plan["stop"], "risk": plan["risk"], "tp1": plan["tp1"], "tp2": plan["tp2"], "tp3": plan["tp3"], "hit1": False, "hit2": False, "hit3": False, "remaining": 1.0, "gross_r": 0.0, "cost_r": _side_cost(plan["entry"]) / plan["risk"], "score": float(evidence), "score_max": float(score_max), "grade": grade, "regime": int(regime[index]), "origin": plan["origin"], "ambiguous": False, "mfe": 0.0, "mae": 0.0, "reason": ""}
        entry_long[index] = signal == 1
        entry_short[index] = signal == -1
        events.append({"bar": index, "type": "entry", "price": trade["entry"], "direction": signal, "grade": grade, "score": float(evidence), "trade_id": trade["id"]})

    if trade.get("dir", 0) != 0 and (trade.get("active") or trade.get("exit_bar") == index):
        entry_plot[index] = trade["entry"]
        initial_stop_plot[index] = trade["initial_stop"]
        step_stop_plot[index] = trade["stop"]
        tp1_plot[index] = trade["tp1"]
        tp2_plot[index] = trade["tp2"]
        tp3_plot[index] = trade["tp3"]


# Known levels are calculated independently from the trade engine.
prior_day_high = np.full(n, np.nan, dtype=float)
prior_day_low = np.full(n, np.nan, dtype=float)
session_high = np.full(n, np.nan, dtype=float)
session_low = np.full(n, np.nan, dtype=float)
known_high = np.full(n, np.nan, dtype=float)
known_low = np.full(n, np.nan, dtype=float)
nearest_resistance = np.full(n, np.nan, dtype=float)
nearest_support = np.full(n, np.nan, dtype=float)
if n:
    utc_days = pd.to_datetime(time_values, unit="s", utc=True).floor("D")
    day_high = {}
    day_low = {}
    for index, day in enumerate(utc_days):
        day_high[day] = max(day_high.get(day, high[index]), high[index])
        day_low[day] = min(day_low.get(day, low[index]), low[index])
    ordered_days = list(dict.fromkeys(utc_days))
    previous_day = {ordered_days[index]: ordered_days[index - 1] for index in range(1, len(ordered_days))}
    running_high = np.nan
    running_low = np.nan
    previous_inside = False
    latest_high = np.nan
    latest_low = np.nan
    for index in range(n):
        if utc_days[index] in previous_day:
            prior = previous_day[utc_days[index]]
            prior_day_high[index] = day_high[prior]
            prior_day_low[index] = day_low[prior]
        if in_session[index]:
            if not previous_inside:
                running_high, running_low = high[index], low[index]
            else:
                running_high, running_low = max(running_high, high[index]), min(running_low, low[index])
            session_high[index], session_low[index] = running_high, running_low
        previous_inside = bool(in_session[index])
        confirmation = index - pivot_length
        if confirmation >= pivot_length and confirmation + pivot_length <= index:
            window_high = high[confirmation - pivot_length:confirmation + pivot_length + 1]
            window_low = low[confirmation - pivot_length:confirmation + pivot_length + 1]
            if high[confirmation] >= np.max(window_high):
                latest_high = high[confirmation]
            if low[confirmation] <= np.min(window_low):
                latest_low = low[confirmation]
        known_high[index], known_low[index] = latest_high, latest_low
        upper_candidates = [value for value in (prior_day_high[index], session_high[index] if in_session[index] else np.nan, latest_high) if np.isfinite(value) and value > close[index]]
        lower_candidates = [value for value in (prior_day_low[index], session_low[index] if in_session[index] else np.nan, latest_low) if np.isfinite(value) and value < close[index]]
        if upper_candidates:
            nearest_resistance[index] = min(upper_candidates)
        if lower_candidates:
            nearest_support[index] = max(lower_candidates)

closed_count = len(journal)
win_rate = 100.0 * wins / closed_count if closed_count else np.nan
expectancy = total_r / closed_count if closed_count else np.nan
gross_win = sum(max(0.0, item["gross_r"] - item["cost_r"]) for item in journal)
gross_loss = sum(max(0.0, -(item["gross_r"] - item["cost_r"])) for item in journal)
profit_factor = gross_win / gross_loss if gross_loss > 0.0 else np.nan
active_side = "Long" if trade.get("active") and trade.get("dir") == 1 else "Short" if trade.get("active") else "Flat"

tables = []
if show_dashboard:
    last = n - 1
    volatility_text = "N/A" if not n or not np.isfinite(volatility_ratio[last]) else "High" if high_volatility[last] else "Low" if volatility_ratio[last] < 0.7 else "Normal"
    data_status = "Ready" if n and ready[last] else "Warmup or missing data"
    rows = [
        [{"text": "Market"}, {"text": f"{preset} · {chart_timeframe}"}],
        [{"text": "Regime"}, {"text": "Trend" if n and trending[last] else "Range"}],
        [{"text": "Volatility"}, {"text": f"{volatility_text} · {volatility_ratio[last]:.2f}x" if n and np.isfinite(volatility_ratio[last]) else volatility_text}],
        [{"text": "Data"}, {"text": data_status}],
        [{"text": "Candidate"}, {"text": ("Long" if pending_direction == 1 else "Short") if pending_direction else last_decision}],
        [{"text": "Trade"}, {"text": active_side}],
        [{"text": "Closed"}, {"text": f"{closed_count} · {wins}W/{losses}L/{breakevens}BE"}],
        [{"text": "Win rate"}, {"text": f"{win_rate:.2f}%" if np.isfinite(win_rate) else "N/A"}],
        [{"text": "Expectancy / PF"}, {"text": f"{expectancy:.3f}R / {profit_factor:.2f}" if np.isfinite(profit_factor) else f"{expectancy:.3f}R / N/A" if np.isfinite(expectancy) else "N/A"}],
        [{"text": "Net / max DD"}, {"text": f"{total_r:.3f}R / {maximum_drawdown_r:.3f}R"}],
    ]
    tables.append({"title": "Precision Sniper", "position": "top-right", "compact": True, "rows": rows})
if show_journal:
    rows = []
    for item in journal[:journal_rows]:
        net_r = item["gross_r"] - item["cost_r"]
        rows.append([{"text": str(item["entry_bar"])}, {"text": "Long" if item["dir"] == 1 else "Short"}, {"text": item["grade"]}, {"text": item["reason"]}, {"text": f"{net_r:.3f}R"}])
    tables.append({"title": "Recent model trades", "position": "bottom-left", "compact": True, "rows": rows or [[{"text": "No closed trades"}]]})

background_colors = []
for direction in trend_direction:
    background_colors.append("rgba(0,184,118,0.06)" if show_background and direction == 1 else "rgba(227,72,80,0.06)" if show_background and direction == -1 else None)


def _signal_data(mask, prices, text, color):
    return [{"active": True, "price": float(prices[index]), "text": text, "color": color} if mask[index] and np.isfinite(prices[index]) else None for index in range(n)]


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "EMA Fast", "data": _finite(ema_fast) if show_ribbon else [None] * n, "color": "#00B876", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "EMA Slow", "data": _finite(ema_slow) if show_ribbon else [None] * n, "color": "#E34850", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "EMA Ribbon", "title": False, "data": [{"upper": float(max(ema_fast[index], ema_slow[index])), "lower": float(min(ema_fast[index], ema_slow[index])), "color": "rgba(0,184,118,0.12)" if ema_fast[index] > ema_slow[index] else "rgba(227,72,80,0.12)"} if show_ribbon and np.all(np.isfinite([ema_fast[index], ema_slow[index]])) else None for index in range(n)], "color": "rgba(0,184,118,0.12)", "type": "cloud", "overlay": True},
        {"name": "EMA Trend", "data": _finite(ema_trend) if show_ribbon else [None] * n, "color": "#9E9E9E", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Nearest Resistance", "data": _finite(nearest_resistance) if show_levels else [None] * n, "color": "rgba(227,72,80,0.65)", "type": "step", "lineWidth": 1, "overlay": True},
        {"name": "Nearest Support", "data": _finite(nearest_support) if show_levels else [None] * n, "color": "rgba(0,184,118,0.65)", "type": "step", "lineWidth": 1, "overlay": True},
        {"name": "Entry", "data": _finite(entry_plot) if show_trade_levels else [None] * n, "color": "#2979FF", "type": "step", "lineWidth": 1, "overlay": True},
        {"name": "Initial Stop", "data": _finite(initial_stop_plot) if show_trade_levels else [None] * n, "color": "#E34850", "type": "step", "lineWidth": 1, "overlay": True},
        {"name": "Effective Stop", "data": _finite(step_stop_plot) if show_trade_levels else [None] * n, "color": "#FF9800", "type": "step", "lineWidth": 1, "overlay": True},
        {"name": "TP1", "data": _finite(tp1_plot) if show_trade_levels else [None] * n, "color": "#00B876", "type": "step", "lineWidth": 1, "overlay": True},
        {"name": "TP2", "data": _finite(tp2_plot) if show_trade_levels else [None] * n, "color": "#00B876", "type": "step", "lineWidth": 1, "overlay": True},
        {"name": "TP3", "data": _finite(tp3_plot) if show_trade_levels else [None] * n, "color": "#00B876", "type": "step", "lineWidth": 1, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "name": "Long Entry", "data": _signal_data(entry_long & show_signals, low, "Long", "#00B876"), "color": "#00B876", "renderMode": "events"},
        {"type": "sell", "name": "Short Entry", "data": _signal_data(entry_short & show_signals, high, "Short", "#E34850"), "color": "#E34850", "renderMode": "events"},
        {"type": "sell", "name": "Long Exit", "data": _signal_data(exit_long & show_signals, high, "Exit", "#FFEB3B"), "color": "#FFEB3B", "renderMode": "events"},
        {"type": "buy", "name": "Short Exit", "data": _signal_data(exit_short & show_signals, low, "Exit", "#FFEB3B"), "color": "#FFEB3B", "renderMode": "events"},
    ],
    "layers": [],
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": background_colors,
    "calculatedVars": {
        "preset": preset, "effectiveLengths": {"fast": fast_length, "slow": slow_length, "trend": trend_length, "rsi": rsi_length, "atr": atr_length},
        "emaFast": _finite(ema_fast), "emaSlow": _finite(ema_slow), "emaTrend": _finite(ema_trend), "atr": _finite(atr), "atrMean": _finite(atr_mean), "rsi": _finite(rsi),
        "macdHistogram": _finite(macd_histogram), "diPlus": _finite(di_plus), "diMinus": _finite(di_minus), "adx": _finite(adx), "vwap": _finite(vwap),
        "htfAvailable": bool(htf_available), "htfFast": _finite(htf_fast), "htfSlow": _finite(htf_slow), "htfBias": [int(value) for value in htf_bias],
        "ready": ready.tolist(), "volatilityRatio": _finite(volatility_ratio), "highVolatility": high_volatility.tolist(), "trendDirection": [int(value) for value in trend_direction], "regime": [int(value) for value in regime],
        "bullScore": _finite(bull_score), "bearScore": _finite(bear_score), "scoreMax": float(score_max), "requiredRatio": float(required_ratio),
        "bullCross": bull_cross.tolist(), "bearCross": bear_cross.tolist(), "entryLong": entry_long.tolist(), "entryShort": entry_short.tolist(), "exitLong": exit_long.tolist(), "exitShort": exit_short.tolist(),
        "targetEvents": target_events.tolist(), "stopEvents": stop_events.tolist(), "events": events, "candidates": int(candidates), "accepted": int(accepted), "rejected": rejected,
        "lastDecision": last_decision, "activeTrade": trade if trade.get("active") else None, "journal": journal,
        "statistics": {"closed": closed_count, "wins": wins, "losses": losses, "breakevens": breakevens, "netR": float(total_r), "winRate": float(win_rate) if np.isfinite(win_rate) else None, "expectancy": float(expectancy) if np.isfinite(expectancy) else None, "profitFactor": float(profit_factor) if np.isfinite(profit_factor) else None, "maxDrawdownR": float(maximum_drawdown_r), "maxLossStreak": maximum_loss_streak, "ambiguousTrades": ambiguous_trades, "tp1Reached": reached_tp1, "tp2Reached": reached_tp2, "averageDuration": total_duration / closed_count if closed_count else None, "averageMFE": total_mfe / closed_count if closed_count else None, "averageMAE": total_mae / closed_count if closed_count else None},
        "priorDayHigh": _finite(prior_day_high), "priorDayLow": _finite(prior_day_low), "sessionHigh": _finite(session_high), "sessionLow": _finite(session_low), "knownHigh": _finite(known_high), "knownLow": _finite(known_low),
    },
}
