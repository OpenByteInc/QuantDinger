# ============================================================
# QuantDinger Adaptive Market Intelligence Engine
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Adaptive Market Intelligence Engine"
my_indicator_description = (
    "Combines trend, confirmed higher-timeframe alignment, market structure, volatility, "
    "momentum, volume participation, and a scored new-trend state machine."
)

indicator_timeframes = ["4H", "1D", "1W"]

# @param fast_ema int 21 Fast EMA range=2:500:1
# @param slow_ema int 50 Slow EMA range=3:500:1
# @param mean_ema int 20 Non-intraday mean EMA range=2:500:1
# @param use_htf bool true Use higher-timeframe confirmation
# @param htf_timeframe str 4H Higher timeframe values=4H,1D,1W
# @param major_swing_length int 8 Major swing length range=4:50:1
# @param structure_break_buffer float 0.05 Structure break buffer ATR range=0.0:1.0:0.01
# @param retest_window int 18 Retest window range=3:100:1
# @param retest_tolerance_atr float 0.18 Retest tolerance ATR range=0.02:1.0:0.01
# @param retest_max_penetration_atr float 0.40 Maximum retest penetration ATR range=0.05:2.0:0.05
# @param required_structure_pairs int 2 Consecutive structure pairs range=2:5:1
# @param dmi_length int 14 DMI length range=2:500:1
# @param adx_smoothing int 14 ADX smoothing range=2:500:1
# @param trending_adx float 22.0 Trending ADX threshold range=5.0:100.0:0.5
# @param range_adx float 18.0 Range ADX threshold range=5.0:100.0:0.5
# @param minimum_ema_separation_atr float 0.20 Minimum EMA separation ATR range=0.01:2.0:0.01
# @param structure_separation_atr float 0.30 Structure activation separation ATR range=0.01:2.0:0.01
# @param atr_length int 14 ATR length range=2:500:1
# @param atr_baseline_length int 50 ATR baseline range=10:500:1
# @param expansion_multiplier float 1.10 Volatility expansion multiplier range=1.0:5.0:0.05
# @param compression_length int 20 Compression length range=5:500:1
# @param compression_deviation float 2.0 Compression deviation range=0.5:10.0:0.1
# @param compression_baseline_length int 50 Bandwidth baseline range=10:500:1
# @param compression_multiplier float 0.75 Compression threshold range=0.3:1.0:0.05
# @param rsi_length int 14 RSI length range=2:500:1
# @param macd_fast int 12 MACD fast range=2:500:1
# @param macd_slow int 26 MACD slow range=3:500:1
# @param macd_signal int 9 MACD signal range=2:500:1
# @param use_volume bool true Use volume participation
# @param volume_length int 20 Volume average range=2:500:1
# @param volume_multiplier float 1.0 General volume multiplier range=0.5:5.0:0.05
# @param minimum_signal_score int 86 New trend minimum score range=50:100:1
# @param minimum_score_advantage int 25 Minimum bull/bear advantage range=5:60:1
# @param require_htf_for_signals bool true Require HTF alignment
# @param require_volume_for_signals bool true Require participation
# @param signal_volume_multiplier float 1.05 Signal volume multiplier range=0.5:3.0:0.05
# @param trend_reset_bars int 6 Trend reset bars range=2:30:1
# @param require_structure_alignment bool true Require major structure alignment
# @param require_strong_adx bool true Require strong ADX and DMI
# @param show_trend_ribbon bool true Show EMA trend ribbon
# @param show_mean bool true Show session VWAP or mean EMA
# @param show_structure_levels bool true Show major support and resistance
# @param show_break_labels bool true Show BOS and CHOCH labels
# @param show_retests bool true Show retest labels
# @param color_candles bool true Color candles by confluence
# @param show_dashboard bool true Show market dashboard
# @param max_labels int 8 Recent structure and signal labels range=1:100:1

fast_length = min(500, max(2, int(params.get("fast_ema", 21))))
slow_length = min(500, max(3, int(params.get("slow_ema", 50))))
mean_length = min(500, max(2, int(params.get("mean_ema", 20))))
use_htf = bool(params.get("use_htf", True))
requested_htf = str(params.get("htf_timeframe", "4H")).upper()
major_swing_length = min(50, max(4, int(params.get("major_swing_length", 8))))
structure_break_buffer = min(1.0, max(0.0, float(params.get("structure_break_buffer", 0.05))))
retest_window = min(100, max(3, int(params.get("retest_window", 18))))
retest_tolerance = min(1.0, max(0.02, float(params.get("retest_tolerance_atr", 0.18))))
retest_penetration = min(2.0, max(0.05, float(params.get("retest_max_penetration_atr", 0.40))))
required_pairs = min(5, max(2, int(params.get("required_structure_pairs", 2))))
dmi_length = min(500, max(2, int(params.get("dmi_length", 14))))
adx_smoothing = min(500, max(2, int(params.get("adx_smoothing", 14))))
trending_adx = min(100.0, max(5.0, float(params.get("trending_adx", 22.0))))
range_adx = min(100.0, max(5.0, float(params.get("range_adx", 18.0))))
minimum_ema_separation = min(2.0, max(0.01, float(params.get("minimum_ema_separation_atr", 0.20))))
structure_separation = min(2.0, max(0.01, float(params.get("structure_separation_atr", 0.30))))
atr_length = min(500, max(2, int(params.get("atr_length", 14))))
atr_baseline_length = min(500, max(10, int(params.get("atr_baseline_length", 50))))
expansion_multiplier = min(5.0, max(1.0, float(params.get("expansion_multiplier", 1.10))))
bb_length = min(500, max(5, int(params.get("compression_length", 20))))
bb_multiplier = min(10.0, max(0.5, float(params.get("compression_deviation", 2.0))))
bb_baseline_length = min(500, max(10, int(params.get("compression_baseline_length", 50))))
compression_multiplier = min(1.0, max(0.3, float(params.get("compression_multiplier", 0.75))))
rsi_length = min(500, max(2, int(params.get("rsi_length", 14))))
macd_fast_length = min(500, max(2, int(params.get("macd_fast", 12))))
macd_slow_length = min(500, max(3, int(params.get("macd_slow", 26))))
macd_signal_length = min(500, max(2, int(params.get("macd_signal", 9))))
use_volume = bool(params.get("use_volume", True))
volume_length = min(500, max(2, int(params.get("volume_length", 20))))
volume_multiplier = min(5.0, max(0.5, float(params.get("volume_multiplier", 1.0))))
minimum_signal_score = min(100, max(50, int(params.get("minimum_signal_score", 86))))
minimum_score_advantage = min(60, max(5, int(params.get("minimum_score_advantage", 25))))
require_htf_for_signals = bool(params.get("require_htf_for_signals", True))
require_volume_for_signals = bool(params.get("require_volume_for_signals", True))
signal_volume_multiplier = min(3.0, max(0.5, float(params.get("signal_volume_multiplier", 1.05))))
trend_reset_bars = min(30, max(2, int(params.get("trend_reset_bars", 6))))
require_structure_alignment = bool(params.get("require_structure_alignment", True))
require_strong_adx = bool(params.get("require_strong_adx", True))
show_trend_ribbon = bool(params.get("show_trend_ribbon", True))
show_mean = bool(params.get("show_mean", True))
show_structure_levels = bool(params.get("show_structure_levels", True))
show_break_labels = bool(params.get("show_break_labels", True))
show_retests = bool(params.get("show_retests", True))
color_candles = bool(params.get("color_candles", True))
show_dashboard = bool(params.get("show_dashboard", True))
max_labels = min(100, max(1, int(params.get("max_labels", 8))))

frame = df.copy()
frame = frame.reset_index(drop=True)
n = len(frame)
if "time" not in frame.columns:
    frame["time"] = np.arange(n, dtype=float) * 3600.0
open_values = pd.to_numeric(frame["open"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
high_values = pd.to_numeric(frame["high"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
low_values = pd.to_numeric(frame["low"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
close_values = pd.to_numeric(frame["close"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
volume_values = pd.to_numeric(frame["volume"], errors="coerce").fillna(0.0).clip(lower=0.0).to_numpy(dtype=float)
time_values = pd.to_numeric(frame["time"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
base_time_seconds = time_values.copy()
if n and np.nanmax(np.abs(base_time_seconds)) > 10000000000:
    base_time_seconds = base_time_seconds / 1000.0


def ema(values, length):
    count = len(values)
    result = np.full(count, np.nan, dtype=float)
    if not count:
        return result
    alpha = 2.0 / (float(length) + 1.0)
    result[0] = float(values[0])
    for index in range(1, count):
        result[index] = alpha * float(values[index]) + (1.0 - alpha) * result[index - 1]
    return result


def rolling_mean(values, length):
    return pd.Series(values, dtype=float).rolling(length, min_periods=length).mean().to_numpy(dtype=float)


def wilder(values, length):
    count = len(values)
    result = np.full(count, np.nan, dtype=float)
    if count < length:
        return result
    result[length - 1] = float(np.mean(values[:length]))
    alpha = 1.0 / float(length)
    for index in range(length, count):
        result[index] = alpha * float(values[index]) + (1.0 - alpha) * result[index - 1]
    return result


def rsi(values, length):
    change = np.diff(values, prepend=values[0] if len(values) else 0.0)
    gain = np.maximum(change, 0.0)
    loss = np.maximum(-change, 0.0)
    average_gain = wilder(gain, length)
    average_loss = wilder(loss, length)
    result = np.full(len(values), np.nan, dtype=float)
    valid = np.isfinite(average_gain) & np.isfinite(average_loss)
    result[valid & (average_gain == 0.0) & (average_loss == 0.0)] = 50.0
    result[valid & (average_gain > 0.0) & (average_loss == 0.0)] = 100.0
    normal = valid & (average_loss > 0.0)
    result[normal] = 100.0 - 100.0 / (1.0 + average_gain[normal] / average_loss[normal])
    return result


def normalize_context(raw):
    if raw is None or len(raw) == 0:
        return pd.DataFrame(columns=["time", "close"])
    result = raw.copy().reset_index(drop=True)
    if "time" not in result.columns:
        result["time"] = np.arange(len(result), dtype=float) * 14400.0
    if "close" not in result.columns:
        return pd.DataFrame(columns=["time", "close"])
    result["time"] = pd.to_numeric(result.get("time"), errors="coerce")
    result["close"] = pd.to_numeric(result.get("close"), errors="coerce")
    result = result.dropna(subset=["time", "close"])
    if len(result) and np.nanmax(np.abs(result["time"].to_numpy(dtype=float))) > 10000000000:
        result["time"] = result["time"] / 1000.0
    return result.sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)


timeframe_seconds = {"1M": 60, "5M": 300, "15M": 900, "30M": 1800, "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
runtime_context = globals().get("ctx")
chart_timeframe = str(getattr(runtime_context, "timeframe", "") or "").upper()
chart_seconds = timeframe_seconds.get(chart_timeframe, 0)
resolved_htf = requested_htf if requested_htf in timeframe_seconds else "4H"
if use_htf and timeframe_seconds.get(resolved_htf, 0) <= chart_seconds:
    resolved_htf = next((name for name in ("5M", "15M", "30M", "1H", "4H", "1D", "1W") if timeframe_seconds[name] > chart_seconds), "1W")

fast_ema = ema(close_values, fast_length)
slow_ema = ema(close_values, slow_length)
mean_ema = ema(close_values, mean_length)
bull_trend = (fast_ema > slow_ema) & (close_values > fast_ema)
bear_trend = (fast_ema < slow_ema) & (close_values < fast_ema)

htf_close = close_values.copy()
htf_fast = fast_ema.copy()
htf_slow = slow_ema.copy()
htf_source_index = np.full(n, -1, dtype=int)
htf_source = (
    runtime_context.raw_series(resolved_htf)
    if use_htf and runtime_context is not None and hasattr(runtime_context, "raw_series")
    else None
)
htf_frame = normalize_context(htf_source) if use_htf else pd.DataFrame()
if use_htf and len(htf_frame):
    raw_htf_time = htf_frame["time"].to_numpy(dtype=float)
    raw_htf_close = htf_frame["close"].to_numpy(dtype=float)
    raw_htf_fast = ema(raw_htf_close, fast_length)
    raw_htf_slow = ema(raw_htf_close, slow_length)
    for index in range(n):
        current_htf_index = int(np.searchsorted(raw_htf_time, base_time_seconds[index], side="right") - 1)
        confirmed_index = current_htf_index - 1
        if confirmed_index >= 0:
            htf_source_index[index] = confirmed_index
            htf_close[index] = float(raw_htf_close[confirmed_index])
            htf_fast[index] = float(raw_htf_fast[confirmed_index])
            htf_slow[index] = float(raw_htf_slow[confirmed_index])

htf_bull = (htf_fast > htf_slow) & (htf_close > htf_fast)
htf_bear = (htf_fast < htf_slow) & (htf_close < htf_fast)

hlc3 = (high_values + low_values + close_values) / 3.0
session_vwap = np.full(n, np.nan, dtype=float)
running_pv = 0.0
running_volume = 0.0
previous_day = None
for index in range(n):
    day_key = int(base_time_seconds[index] // 86400.0)
    if day_key != previous_day:
        running_pv = 0.0
        running_volume = 0.0
        previous_day = day_key
    running_pv += float(hlc3[index] * volume_values[index])
    running_volume += float(volume_values[index])
    session_vwap[index] = running_pv / running_volume if running_volume > 0.0 else np.nan
is_intraday = bool(chart_seconds and chart_seconds < timeframe_seconds["1D"])
mean_line = session_vwap.copy() if is_intraday else mean_ema.copy()
missing_mean = ~np.isfinite(mean_line)
mean_line[missing_mean] = mean_ema[missing_mean]
above_mean = close_values > mean_line
below_mean = close_values < mean_line

rsi_values = rsi(close_values, rsi_length)
macd_line = ema(close_values, macd_fast_length) - ema(close_values, macd_slow_length)
macd_signal_line = ema(macd_line, macd_signal_length)
macd_histogram = macd_line - macd_signal_line

true_range = np.zeros(n, dtype=float)
plus_dm = np.zeros(n, dtype=float)
minus_dm = np.zeros(n, dtype=float)
if n:
    true_range[0] = high_values[0] - low_values[0]
for index in range(1, n):
    true_range[index] = max(high_values[index] - low_values[index], abs(high_values[index] - close_values[index - 1]), abs(low_values[index] - close_values[index - 1]))
    up_move = high_values[index] - high_values[index - 1]
    down_move = low_values[index - 1] - low_values[index]
    plus_dm[index] = up_move if up_move > down_move and up_move > 0.0 else 0.0
    minus_dm[index] = down_move if down_move > up_move and down_move > 0.0 else 0.0
atr = wilder(true_range, atr_length)
atr_baseline = rolling_mean(atr, atr_baseline_length)
smoothed_tr = wilder(true_range, dmi_length)
smoothed_plus = wilder(plus_dm, dmi_length)
smoothed_minus = wilder(minus_dm, dmi_length)
plus_di = np.full(n, np.nan, dtype=float)
minus_di = np.full(n, np.nan, dtype=float)
valid_tr = np.isfinite(smoothed_tr) & (smoothed_tr > 0.0)
plus_di[valid_tr] = 100.0 * smoothed_plus[valid_tr] / smoothed_tr[valid_tr]
minus_di[valid_tr] = 100.0 * smoothed_minus[valid_tr] / smoothed_tr[valid_tr]
dx = np.full(n, np.nan, dtype=float)
di_sum = plus_di + minus_di
valid_di = np.isfinite(di_sum) & (di_sum > 0.0)
dx[valid_di] = 100.0 * np.abs(plus_di[valid_di] - minus_di[valid_di]) / di_sum[valid_di]
adx = wilder(np.nan_to_num(dx, nan=0.0), adx_smoothing)

bb_basis = rolling_mean(close_values, bb_length)
bb_deviation = pd.Series(close_values, dtype=float).rolling(bb_length, min_periods=bb_length).std(ddof=0).to_numpy(dtype=float) * bb_multiplier
bb_width = np.zeros(n, dtype=float)
valid_basis = np.isfinite(bb_basis) & (bb_basis != 0.0)
bb_width[valid_basis] = 2.0 * bb_deviation[valid_basis] / bb_basis[valid_basis] * 100.0
bb_width_baseline = rolling_mean(bb_width, bb_baseline_length)
ema_separation = np.zeros(n, dtype=float)
valid_atr = np.isfinite(atr) & (atr > 0.0)
ema_separation[valid_atr] = np.abs(fast_ema[valid_atr] - slow_ema[valid_atr]) / atr[valid_atr]
volatility_expansion = np.isfinite(atr_baseline) & (atr > atr_baseline * expansion_multiplier)
compression = np.isfinite(bb_width_baseline) & (bb_width < bb_width_baseline * compression_multiplier)
range_bound = compression | ((adx < range_adx) & (ema_separation < minimum_ema_separation))
directional_regime = (~range_bound) & ((adx >= trending_adx) | (ema_separation >= structure_separation) | volatility_expansion)
avg_volume = rolling_mean(volume_values, volume_length)
has_volume = np.isfinite(avg_volume) & (avg_volume > 0.0)
bull_volume = has_volume & (volume_values > avg_volume * volume_multiplier) & (close_values > np.roll(close_values, 1))
bear_volume = has_volume & (volume_values > avg_volume * volume_multiplier) & (close_values < np.roll(close_values, 1))
if n:
    bull_volume[0] = False
    bear_volume[0] = False

support = np.full(n, np.nan, dtype=float)
resistance = np.full(n, np.nan, dtype=float)
external_direction = np.zeros(n, dtype=int)
bull_break = np.zeros(n, dtype=bool)
bear_break = np.zeros(n, dtype=bool)
bull_choch = np.zeros(n, dtype=bool)
bear_choch = np.zeros(n, dtype=bool)
bull_retest = np.zeros(n, dtype=bool)
bear_retest = np.zeros(n, dtype=bool)
bull_score = np.zeros(n, dtype=int)
bear_score = np.zeros(n, dtype=int)
score_difference = np.zeros(n, dtype=int)
trend_state_series = np.zeros(n, dtype=int)
new_bull_trend = np.zeros(n, dtype=bool)
new_bear_trend = np.zeros(n, dtype=bool)
regime_names = ["TRANSITION"] * n
bias_names = ["NEUTRAL"] * n
range_locations = ["N/A"] * n
pair_counts = np.zeros((n, 2), dtype=int)
events = []

previous_major_high = np.nan
previous_major_low = np.nan
last_major_high = np.nan
last_major_low = np.nan
last_major_high_bar = -1
last_major_low_bar = -1
last_major_high_broken = False
last_major_low_broken = False
last_high_type = 0
last_low_type = 0
bull_pair_count = 0
bear_pair_count = 0
structure_direction = 0
last_bull_break_level = np.nan
last_bear_break_level = np.nan
last_bull_break_bar = -1
last_bear_break_bar = -1
bull_retest_consumed = True
bear_retest_consumed = True
trend_state = 0
trend_failure_count = 0


def confirmed_pivot(values, confirmation_index, length, is_high):
    pivot_index = confirmation_index - length
    if pivot_index < length or pivot_index + length >= len(values):
        return -1, np.nan
    window = values[pivot_index - length:pivot_index + length + 1]
    pivot_value = float(values[pivot_index])
    extreme = float(np.max(window) if is_high else np.min(window))
    return (pivot_index, pivot_value) if pivot_value == extreme else (-1, np.nan)


for index in range(n):
    pivot_high_bar, pivot_high = confirmed_pivot(high_values, index, major_swing_length, True)
    pivot_low_bar, pivot_low = confirmed_pivot(low_values, index, major_swing_length, False)

    if pivot_high_bar >= 0:
        current_high_type = 0 if not np.isfinite(previous_major_high) else 1 if pivot_high > previous_major_high else -1
        if current_high_type == 1:
            bear_pair_count = 0
        elif current_high_type == -1:
            bull_pair_count = 0
        last_major_high = pivot_high
        last_major_high_bar = pivot_high_bar
        last_major_high_broken = False
        last_high_type = current_high_type
        previous_major_high = pivot_high

    if pivot_low_bar >= 0:
        current_low_type = 0 if not np.isfinite(previous_major_low) else 1 if pivot_low > previous_major_low else -1
        completed_bull_pair = current_low_type == 1 and last_high_type == 1
        completed_bear_pair = current_low_type == -1 and last_high_type == -1
        if completed_bull_pair:
            bear_pair_count = 0
            bull_pair_count += 1
        elif completed_bear_pair:
            bull_pair_count = 0
            bear_pair_count += 1
        else:
            if current_low_type == -1:
                bull_pair_count = 0
            if current_low_type == 1:
                bear_pair_count = 0
        last_major_low = pivot_low
        last_major_low_bar = pivot_low_bar
        last_major_low_broken = False
        last_low_type = current_low_type
        previous_major_low = pivot_low

    structure_allowed = bool(directional_regime[index] and not range_bound[index] and not compression[index] and np.isfinite(atr[index]))
    if structure_allowed and np.isfinite(last_major_high) and not last_major_high_broken and close_values[index] > last_major_high + atr[index] * structure_break_buffer:
        bull_break[index] = True
        bull_choch[index] = structure_direction == -1
        last_major_high_broken = True
        last_bull_break_level = last_major_high
        last_bull_break_bar = index
        bull_retest_consumed = False
        bear_retest_consumed = True
        structure_direction = 1
        events.append((index, "CHOCH" if bull_choch[index] else "BOS", "#22D3EE" if bull_choch[index] else "#22C55E", last_major_high, "below"))
    if structure_allowed and np.isfinite(last_major_low) and not last_major_low_broken and close_values[index] < last_major_low - atr[index] * structure_break_buffer:
        bear_break[index] = True
        bear_choch[index] = structure_direction == 1
        last_major_low_broken = True
        last_bear_break_level = last_major_low
        last_bear_break_bar = index
        bear_retest_consumed = False
        bull_retest_consumed = True
        structure_direction = -1
        events.append((index, "CHOCH" if bear_choch[index] else "BOS", "#F59E0B" if bear_choch[index] else "#EF4444", last_major_low, "above"))

    if np.isfinite(atr[index]) and last_bull_break_bar >= 0 and index > last_bull_break_bar and index <= last_bull_break_bar + retest_window and not bull_retest_consumed:
        touch = low_values[index] <= last_bull_break_level + atr[index] * retest_tolerance and low_values[index] >= last_bull_break_level - atr[index] * retest_penetration
        if not range_bound[index] and touch and close_values[index] > last_bull_break_level and close_values[index] > open_values[index]:
            bull_retest[index] = True
            bull_retest_consumed = True
            events.append((index, "R>S", "#16A34A", low_values[index], "below"))
    if np.isfinite(atr[index]) and last_bear_break_bar >= 0 and index > last_bear_break_bar and index <= last_bear_break_bar + retest_window and not bear_retest_consumed:
        touch = high_values[index] >= last_bear_break_level - atr[index] * retest_tolerance and high_values[index] <= last_bear_break_level + atr[index] * retest_penetration
        if not range_bound[index] and touch and close_values[index] < last_bear_break_level and close_values[index] < open_values[index]:
            bear_retest[index] = True
            bear_retest_consumed = True
            events.append((index, "S>R", "#DC2626", high_values[index], "above"))

    valid_range = np.isfinite(last_major_high) and np.isfinite(last_major_low) and last_major_high > last_major_low
    if valid_range:
        equilibrium = (last_major_high + last_major_low) / 2.0
        tolerance = (last_major_high - last_major_low) * 0.05
        range_locations[index] = "PREMIUM" if close_values[index] > equilibrium + tolerance else "DISCOUNT" if close_values[index] < equilibrium - tolerance else "EQUILIBRIUM"

    bullish_adx = bool(np.isfinite(adx[index]) and adx[index] >= trending_adx and plus_di[index] > minus_di[index])
    bearish_adx = bool(np.isfinite(adx[index]) and adx[index] >= trending_adx and minus_di[index] > plus_di[index])
    bull_points = (15 if bull_trend[index] else 0) + (20 if htf_bull[index] else 0) + (20 if structure_direction == 1 else 0)
    bear_points = (15 if bear_trend[index] else 0) + (20 if htf_bear[index] else 0) + (20 if structure_direction == -1 else 0)
    bull_points += (10 if np.isfinite(rsi_values[index]) and rsi_values[index] > 52.0 else 0) + (5 if macd_histogram[index] > 0.0 else 0)
    bear_points += (10 if np.isfinite(rsi_values[index]) and rsi_values[index] < 48.0 else 0) + (5 if macd_histogram[index] < 0.0 else 0)
    bull_points += 10 if above_mean[index] else 0
    bear_points += 10 if below_mean[index] else 0
    if use_volume and has_volume[index]:
        bull_points += 10 if bull_volume[index] else 0
        bear_points += 10 if bear_volume[index] else 0
    else:
        bull_points += 5
        bear_points += 5
    bull_points += 10 if bullish_adx else 0
    bear_points += 10 if bearish_adx else 0
    bull_score[index] = bull_points
    bear_score[index] = bear_points
    score_difference[index] = bull_points - bear_points
    bias_names[index] = "STRONG BULLISH" if score_difference[index] >= 35 else "BULLISH" if score_difference[index] >= 15 else "STRONG BEARISH" if score_difference[index] <= -35 else "BEARISH" if score_difference[index] <= -15 else "NEUTRAL"
    regime_names[index] = "COMPRESSION" if compression[index] else "RANGE" if range_bound[index] else "TREND EXPANSION" if directional_regime[index] and volatility_expansion[index] else "TRENDING" if directional_regime[index] else "VOL EXPANSION" if volatility_expansion[index] else "TRANSITION"

    bull_htf_ok = not (require_htf_for_signals and use_htf) or bool(htf_bull[index])
    bear_htf_ok = not (require_htf_for_signals and use_htf) or bool(htf_bear[index])
    bull_participation_ok = not (require_volume_for_signals and use_volume and has_volume[index]) or bool(volume_values[index] >= avg_volume[index] * signal_volume_multiplier and close_values[index] > open_values[index])
    bear_participation_ok = not (require_volume_for_signals and use_volume and has_volume[index]) or bool(volume_values[index] >= avg_volume[index] * signal_volume_multiplier and close_values[index] < open_values[index])
    bull_structure_ok = not require_structure_alignment or structure_direction == 1
    bear_structure_ok = not require_structure_alignment or structure_direction == -1
    bull_adx_ok = not require_strong_adx or bullish_adx
    bear_adx_ok = not require_strong_adx or bearish_adx
    confirmed_bull = bool(not range_bound[index] and not compression[index] and directional_regime[index] and bull_trend[index] and above_mean[index] and bull_htf_ok and bull_structure_ok and bull_adx_ok and bull_participation_ok and bull_points >= minimum_signal_score and score_difference[index] >= minimum_score_advantage)
    confirmed_bear = bool(not range_bound[index] and not compression[index] and directional_regime[index] and bear_trend[index] and below_mean[index] and bear_htf_ok and bear_structure_ok and bear_adx_ok and bear_participation_ok and bear_points >= minimum_signal_score and score_difference[index] <= -minimum_score_advantage)

    if trend_state == 0:
        trend_failure_count = 0
        if confirmed_bull:
            trend_state = 1
            new_bull_trend[index] = True
        elif confirmed_bear:
            trend_state = -1
            new_bear_trend[index] = True
    elif trend_state == 1:
        if confirmed_bear:
            trend_state = -1
            trend_failure_count = 0
            new_bear_trend[index] = True
        else:
            healthy = bool(bull_trend[index] and not range_bound[index] and not compression[index] and score_difference[index] > 0)
            trend_failure_count = 0 if healthy else trend_failure_count + 1
            if trend_failure_count >= trend_reset_bars:
                trend_state = 0
                trend_failure_count = 0
    else:
        if confirmed_bull:
            trend_state = 1
            trend_failure_count = 0
            new_bull_trend[index] = True
        else:
            healthy = bool(bear_trend[index] and not range_bound[index] and not compression[index] and score_difference[index] < 0)
            trend_failure_count = 0 if healthy else trend_failure_count + 1
            if trend_failure_count >= trend_reset_bars:
                trend_state = 0
                trend_failure_count = 0

    if new_bull_trend[index]:
        grade = "A+" if bull_points >= 92 and score_difference[index] >= 35 else "A"
        events.append((index, f"{grade} BUY", "#22C55E", low_values[index] - (atr[index] * 0.25 if np.isfinite(atr[index]) else 0.0), "below"))
    if new_bear_trend[index]:
        grade = "A+" if bear_points >= 92 and score_difference[index] <= -35 else "A"
        events.append((index, f"{grade} SELL", "#EF4444", high_values[index] + (atr[index] * 0.25 if np.isfinite(atr[index]) else 0.0), "above"))

    support[index] = last_major_low
    resistance[index] = last_major_high
    external_direction[index] = structure_direction
    trend_state_series[index] = trend_state
    pair_counts[index, 0] = bull_pair_count
    pair_counts[index, 1] = bear_pair_count


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def trend_line(values, positive, negative):
    result = []
    for index, value in enumerate(values):
        if not np.isfinite(value):
            result.append(None)
        else:
            color_value = positive if bull_trend[index] else negative if bear_trend[index] else "#94A3B8"
            result.append({"value": float(value), "color": color_value})
    return result


plots = [{
    "name": "Engine Alignment Anchor",
    "title": False,
    "data": [float(value) for value in close_values],
    "color": "rgba(0,0,0,0)",
    "type": "line",
    "lineWidth": 1,
    "overlay": True,
}]
if show_trend_ribbon:
    fast_plot = finite(fast_ema)
    slow_plot = finite(slow_ema)
    ribbon = []
    for index in range(n):
        if fast_plot[index] is None or slow_plot[index] is None:
            ribbon.append(None)
        else:
            ribbon.append({"upper": max(fast_plot[index], slow_plot[index]), "lower": min(fast_plot[index], slow_plot[index]), "color": "#22C55E" if bull_trend[index] else "#EF4444" if bear_trend[index] else "#64748B"})
    plots.extend([
        {"name": "Fast EMA", "data": trend_line(fast_ema, "#84CC16", "#EF4444"), "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Slow EMA", "data": trend_line(slow_ema, "#16A34A", "#DC2626"), "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Adaptive Trend Ribbon", "title": False, "data": ribbon, "color": "#64748B", "type": "cloud", "opacity": 0.12, "overlay": True},
    ])
if show_mean:
    plots.append({"name": "VWAP / Mean", "data": finite(mean_line), "color": "#3B82F6", "type": "line", "lineWidth": 2, "overlay": True})
if show_structure_levels:
    plots.extend([
        {"name": "Major Resistance", "data": finite(resistance), "color": "rgba(239,68,68,0.70)", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True},
        {"name": "Major Support", "data": finite(support), "color": "rgba(34,197,94,0.70)", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": True},
    ])

visible_events = []
for event in events:
    label_text = event[1]
    if (label_text in ("BOS", "CHOCH") and not show_break_labels) or (label_text in ("R>S", "S>R") and not show_retests):
        continue
    visible_events.append(event)
deduplicated_events = {}
for event in visible_events:
    deduplicated_events[event[0]] = event
visible_events = list(deduplicated_events.values())
primary_signal_bars = [event[0] for event in visible_events if str(event[1]).startswith("A+")]
visible_events = [
    event for event in visible_events
    if event[1] not in ("R>S", "S>R") or not any(abs(event[0] - signal_bar) <= 5 for signal_bar in primary_signal_bars)
]
visible_events = visible_events[-max_labels:]
layers = []
for index, label_text, color_value, price_value, side_value in visible_events:
    layers.append({
        "type": "label",
        "timestamp": float(time_values[index]),
        "price": float(price_value),
        "text": label_text,
        "color": color_value,
        "fillColor": color_value,
        "borderColor": color_value,
        "textColor": "#FFFFFF",
        "fontSize": 8,
        "side": side_value,
    })

bar_colors = [None] * n
if color_candles:
    for index in range(n):
        normalized = min(1.0, abs(float(score_difference[index])) / 100.0)
        alpha = 0.32 + 0.42 * normalized
        bar_colors[index] = f"rgba(34,197,94,{alpha:.3f})" if score_difference[index] > 0 else f"rgba(239,68,68,{alpha:.3f})" if score_difference[index] < 0 else "rgba(148,163,184,0.35)"

last_index = n - 1
last_structure = "BULLISH" if n and external_direction[last_index] > 0 else "BEARISH" if n and external_direction[last_index] < 0 else "UNDEFINED"
last_trend_state = "BULL TREND ACTIVE" if n and trend_state_series[last_index] > 0 else "BEAR TREND ACTIVE" if n and trend_state_series[last_index] < 0 else "NEUTRAL / ARMED"
last_sequence = "MIXED"
if n and pair_counts[last_index, 0] > 0:
    last_sequence = f"BULL x{pair_counts[last_index, 0]}" if pair_counts[last_index, 0] >= required_pairs else f"BULL BUILD {pair_counts[last_index, 0]}/{required_pairs}"
elif n and pair_counts[last_index, 1] > 0:
    last_sequence = f"BEAR x{pair_counts[last_index, 1]}" if pair_counts[last_index, 1] >= required_pairs else f"BEAR BUILD {pair_counts[last_index, 1]}/{required_pairs}"

tables = []
if show_dashboard:
    tables.append({
        "position": "top-right",
        "backgroundColor": "rgba(15,23,42,0.92)",
        "borderColor": "#22C55E" if n and score_difference[last_index] > 0 else "#EF4444" if n and score_difference[last_index] < 0 else "#64748B",
        "textColor": "#F8FAFC",
        "accentColor": "#3B82F6",
        "rows": [
            ["MARKET ENGINE", "ACTIVE"],
            ["Regime", regime_names[last_index] if n else "N/A"],
            ["Bias", bias_names[last_index] if n else "N/A"],
            ["Trend State", last_trend_state],
            ["Structure Build", last_sequence],
            ["Bull / Bear Score", f"{bull_score[last_index] if n else 0} / {bear_score[last_index] if n else 0}"],
            ["Major Structure", last_structure],
            ["HTF", f"{resolved_htf} {'BULL' if n and htf_bull[last_index] else 'BEAR' if n and htf_bear[last_index] else 'NEUTRAL'}"],
            ["Resistance", f"{resistance[last_index]:.4f}" if n and np.isfinite(resistance[last_index]) else "N/A"],
            ["Support", f"{support[last_index]:.4f}" if n and np.isfinite(support[last_index]) else "N/A"],
            ["Range Location", range_locations[last_index] if n else "N/A"],
        ],
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": layers,
    "tables": tables,
    "bar_colors": bar_colors,
    "background_colors": [None] * n,
    "calculatedVars": {
        "resolvedHtf": resolved_htf,
        "htfSourceIndex": [int(value) for value in htf_source_index],
        "fastEma": finite(fast_ema),
        "slowEma": finite(slow_ema),
        "meanLine": finite(mean_line),
        "htfClose": finite(htf_close),
        "htfFast": finite(htf_fast),
        "htfSlow": finite(htf_slow),
        "atr": finite(atr),
        "rsi": finite(rsi_values),
        "macdHistogram": finite(macd_histogram),
        "plusDi": finite(plus_di),
        "minusDi": finite(minus_di),
        "adx": finite(adx),
        "rangeBound": [bool(value) for value in range_bound],
        "compression": [bool(value) for value in compression],
        "directionalRegime": [bool(value) for value in directional_regime],
        "support": finite(support),
        "resistance": finite(resistance),
        "externalDirection": [int(value) for value in external_direction],
        "bullBreak": [bool(value) for value in bull_break],
        "bearBreak": [bool(value) for value in bear_break],
        "bullRetest": [bool(value) for value in bull_retest],
        "bearRetest": [bool(value) for value in bear_retest],
        "bullScore": [int(value) for value in bull_score],
        "bearScore": [int(value) for value in bear_score],
        "scoreDifference": [int(value) for value in score_difference],
        "trendState": [int(value) for value in trend_state_series],
        "newBullTrend": [bool(value) for value in new_bull_trend],
        "newBearTrend": [bool(value) for value in new_bear_trend],
    },
}
