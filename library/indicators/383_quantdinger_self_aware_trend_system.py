# ============================================================
# QuantDinger Self-Aware Trend System
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Self-Aware Trend System"
my_indicator_description = "Runs a quality-adaptive asymmetric SuperTrend with confluence scoring, dynamic risk plans, partial exits and rolling model statistics."

# @param preset str Auto Parameter preset values=Auto,Custom,Scalping,Default,Swing,Crypto
# @param atr_length int 13 Custom ATR length range=5:100:1
# @param base_multiplier float 2.0 Custom band width range=0.5:5:0.1
# @param source str close Band source values=close,open,high,low,hl2,hlc3,ohlc4
# @param use_adaptive bool true Enable efficiency adaptation
# @param efficiency_length int 20 Custom efficiency length range=5:100:1
# @param adaptation_strength float 0.5 Efficiency adaptation strength range=0:1:0.05
# @param atr_baseline_length int 100 ATR baseline length range=20:500:1
# @param use_tqi bool true Enable trend quality engine
# @param quality_strength float 0.4 Quality influence range=0:1:0.05
# @param quality_curve float 1.5 Quality curve power range=1:3:0.1
# @param smooth_multipliers bool true Smooth adaptive multipliers
# @param use_asymmetric_bands bool true Enable asymmetric bands
# @param asymmetry_strength float 0.5 Asymmetry strength range=0:1:0.05
# @param use_efficiency_atr bool true Efficiency-weight ATR
# @param use_character_flip bool true Enable character-flip detection
# @param character_flip_min_age int 5 Character-flip minimum age range=1:50:1
# @param character_flip_high float 0.55 Character-flip high TQI range=0.3:0.9:0.05
# @param character_flip_low float 0.25 Character-flip low TQI range=0:0.5:0.05
# @param tqi_weight_efficiency float 0.35 TQI efficiency weight range=0:1:0.05
# @param tqi_weight_volatility float 0.20 TQI volatility weight range=0:1:0.05
# @param tqi_weight_structure float 0.25 TQI structure weight range=0:1:0.05
# @param tqi_weight_momentum float 0.20 TQI momentum weight range=0:1:0.05
# @param structure_length int 20 Structure window range=5:100:1
# @param momentum_length int 10 Momentum persistence window range=3:50:1
# @param tqi_volatility_mode str atr TQI volatility source values=atr,volume
# @param use_structure_score bool true Include structure score
# @param pivot_length int 3 Pivot strength range=2:10:1
# @param pivot_max_age int 100 Maximum pivot age range=5:5000:1
# @param use_rsi_score bool true Include RSI score
# @param rsi_length int 14 Custom RSI length range=5:50:1
# @param rsi_overbought int 70 RSI overbought range=55:90:1
# @param rsi_oversold int 30 RSI oversold range=10:45:1
# @param rsi_lookback int 20 RSI memory range=3:100:1
# @param use_volume_score bool true Include volume score
# @param volume_length int 20 Volume Z window range=5:100:1
# @param show_risk bool true Show TP and SL levels
# @param stop_atr_multiplier float 1.5 Custom stop buffer range=0.3:5:0.1
# @param stop_max_distance float 4.0 Maximum stop distance range=1:15:0.5
# @param tp_mode str Fixed Take-profit mode values=Fixed,Dynamic
# @param tp1_r float 1.0 TP1 R multiple range=0.5:10:0.1
# @param tp2_r float 2.0 TP2 R multiple range=0.5:10:0.1
# @param tp3_r float 3.0 TP3 R multiple range=0.5:10:0.1
# @param dynamic_tqi_weight float 0.6 Dynamic TP TQI weight range=0:1:0.05
# @param dynamic_volatility_weight float 0.4 Dynamic TP volatility weight range=0:1:0.05
# @param dynamic_min_scale float 0.5 Dynamic TP minimum scale range=0.2:1:0.05
# @param dynamic_max_scale float 2.0 Dynamic TP maximum scale range=1:4:0.1
# @param dynamic_tp1_floor float 0.5 Dynamic TP1 floor range=0.2:2:0.1
# @param dynamic_tp_ceiling float 8.0 Dynamic TP ceiling range=2:20:0.5
# @param show_hits bool true Mark target hits
# @param trade_timeout int 100 Trade timeout range=10:500:1
# @param enable_auto_calibration bool false Enable experimental calibration
# @param calibration_window int 20 Calibration window range=5:100:1
# @param calibration_bad_r float 0.0 Bad-edge threshold range=-2:1:0.1
# @param calibration_good_r float 0.7 Good-edge threshold range=0:5:0.1
# @param calibration_step float 0.05 Calibration quality step range=0.01:0.3:0.01
# @param calibration_cooldown int 5 Calibration cooldown range=1:50:1
# @param calibration_min_quality float 0.1 Calibration quality floor range=0:1:0.05
# @param calibration_max_quality float 0.9 Calibration quality ceiling range=0:1:0.05
# @param fee_percent float 0.0 Commission per fill range=0:5:0.01
# @param slippage_ticks int 0 Market slippage ticks range=0:1000:1
# @param minimum_risk_ticks int 2 Minimum initial risk ticks range=1:10000:1
# @param tick_size float 0.01 Instrument tick size range=0.00000001:1000:0.01
# @param show_bands bool true Show SuperTrend bands
# @param show_tqi_color bool true Color line by TQI
# @param show_signals bool true Show entry labels
# @param show_background bool false Show trend background
# @param show_dashboard bool true Show dashboard
# @param show_tqi_breakdown bool true Show TQI breakdown
# @param show_performance bool true Show performance statistics
# @param bull_color str #00E676 Bull color
# @param bear_color str #FF5252 Bear color
# @param stop_color str #FF1744 Stop color
# @param target_color str #00E676 Target color


def _bounded(name, default, low_value, high_value):
    return min(high_value, max(low_value, float(params.get(name, default))))


preset = str(params.get("preset", "Auto"))
atr_input = int(_bounded("atr_length", 13, 5, 100))
base_input = _bounded("base_multiplier", 2.0, 0.5, 5.0)
source_name = str(params.get("source", "close")).lower()
use_adaptive = bool(params.get("use_adaptive", True))
er_input = int(_bounded("efficiency_length", 20, 5, 100))
adapt_strength = _bounded("adaptation_strength", 0.5, 0.0, 1.0)
atr_baseline_length = int(_bounded("atr_baseline_length", 100, 20, 500))
use_tqi = bool(params.get("use_tqi", True))
quality_strength = _bounded("quality_strength", 0.4, 0.0, 1.0)
quality_curve = _bounded("quality_curve", 1.5, 1.0, 3.0)
smooth_multipliers = bool(params.get("smooth_multipliers", True))
use_asymmetric = bool(params.get("use_asymmetric_bands", True))
asymmetry_strength = _bounded("asymmetry_strength", 0.5, 0.0, 1.0)
use_efficiency_atr = bool(params.get("use_efficiency_atr", True))
use_character_flip = bool(params.get("use_character_flip", True))
character_min_age = int(_bounded("character_flip_min_age", 5, 1, 50))
character_high = _bounded("character_flip_high", 0.55, 0.3, 0.9)
character_low = _bounded("character_flip_low", 0.25, 0.0, 0.5)
weight_er = _bounded("tqi_weight_efficiency", 0.35, 0.0, 1.0)
weight_vol = _bounded("tqi_weight_volatility", 0.20, 0.0, 1.0)
weight_struct = _bounded("tqi_weight_structure", 0.25, 0.0, 1.0)
weight_mom = _bounded("tqi_weight_momentum", 0.20, 0.0, 1.0)
structure_length = int(_bounded("structure_length", 20, 5, 100))
momentum_length = int(_bounded("momentum_length", 10, 3, 50))
volatility_mode = str(params.get("tqi_volatility_mode", "atr")).lower()
use_structure_score = bool(params.get("use_structure_score", True))
pivot_length = int(_bounded("pivot_length", 3, 2, 10))
pivot_max_age = int(_bounded("pivot_max_age", 100, 5, 5000))
use_rsi_score = bool(params.get("use_rsi_score", True))
rsi_input = int(_bounded("rsi_length", 14, 5, 50))
rsi_overbought = int(_bounded("rsi_overbought", 70, 55, 90))
rsi_oversold = int(_bounded("rsi_oversold", 30, 10, 45))
rsi_lookback = int(_bounded("rsi_lookback", 20, 3, 100))
use_volume_score = bool(params.get("use_volume_score", True))
volume_length = int(_bounded("volume_length", 20, 5, 100))
show_risk = bool(params.get("show_risk", True))
sl_input = _bounded("stop_atr_multiplier", 1.5, 0.3, 5.0)
stop_max_distance = _bounded("stop_max_distance", 4.0, 1.0, 15.0)
tp_mode = str(params.get("tp_mode", "Fixed"))
tp_values = sorted([_bounded("tp1_r", 1.0, 0.5, 10.0), _bounded("tp2_r", 2.0, 0.5, 10.0), _bounded("tp3_r", 3.0, 0.5, 10.0)])
fixed_tp1, fixed_tp2, fixed_tp3 = tp_values
dynamic_tqi_weight = _bounded("dynamic_tqi_weight", 0.6, 0.0, 1.0)
dynamic_vol_weight = _bounded("dynamic_volatility_weight", 0.4, 0.0, 1.0)
dynamic_min_scale = _bounded("dynamic_min_scale", 0.5, 0.2, 1.0)
dynamic_max_scale = _bounded("dynamic_max_scale", 2.0, 1.0, 4.0)
dynamic_tp1_floor = _bounded("dynamic_tp1_floor", 0.5, 0.2, 2.0)
dynamic_ceiling = _bounded("dynamic_tp_ceiling", 8.0, 2.0, 20.0)
show_hits = bool(params.get("show_hits", True))
trade_timeout = int(_bounded("trade_timeout", 100, 10, 500))
auto_calibration = bool(params.get("enable_auto_calibration", False))
calibration_window = int(_bounded("calibration_window", 20, 5, 100))
calibration_bad = _bounded("calibration_bad_r", 0.0, -2.0, 1.0)
calibration_good = _bounded("calibration_good_r", 0.7, 0.0, 5.0)
calibration_step = _bounded("calibration_step", 0.05, 0.01, 0.3)
calibration_cooldown = int(_bounded("calibration_cooldown", 5, 1, 50))
calibration_min = _bounded("calibration_min_quality", 0.1, 0.0, 1.0)
calibration_max = _bounded("calibration_max_quality", 0.9, 0.0, 1.0)
fee_percent = _bounded("fee_percent", 0.0, 0.0, 5.0)
slippage_ticks = int(_bounded("slippage_ticks", 0, 0, 1000))
minimum_risk_ticks = int(_bounded("minimum_risk_ticks", 2, 1, 10000))
tick_size = _bounded("tick_size", 0.01, 0.00000001, 1000.0)
show_bands = bool(params.get("show_bands", True))
show_tqi_color = bool(params.get("show_tqi_color", True))
show_signals = bool(params.get("show_signals", True))
show_background = bool(params.get("show_background", False))
show_dashboard = bool(params.get("show_dashboard", True))
show_tqi_breakdown = bool(params.get("show_tqi_breakdown", True))
show_performance = bool(params.get("show_performance", True))
bull_color = str(params.get("bull_color", "#00E676"))
bear_color = str(params.get("bear_color", "#FF5252"))
stop_color = str(params.get("stop_color", "#FF1744"))
target_color = str(params.get("target_color", "#00E676"))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _clamp(value, low_value, high_value):
    return max(low_value, min(high_value, value))


def _safe_div(numerator, denominator, fallback=0.0):
    return numerator / denominator if np.isfinite(numerator) and np.isfinite(denominator) and denominator != 0.0 else fallback


def _sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def _rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    seed = []
    state = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        if not np.isfinite(state):
            seed.append(float(value))
            if len(seed) == length:
                state = float(np.mean(seed))
                result[index] = state
        else:
            state = (state * (length - 1.0) + float(value)) / length
            result[index] = state
    return result


def _rolling_extreme(values, length, maximum):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        finite = window[np.isfinite(window)]
        if len(finite):
            result[index] = float(np.max(finite) if maximum else np.min(finite))
    return result


def _map(value, input_low, input_high, output_low, output_high):
    ratio = _clamp(_safe_div(value - input_low, input_high - input_low, 0.0), 0.0, 1.0)
    return output_low + ratio * (output_high - output_low)


def _map_inverse(value, input_low, input_high, output_high, output_low):
    ratio = _clamp(_safe_div(value - input_low, input_high - input_low, 0.0), 0.0, 1.0)
    return output_high - ratio * (output_high - output_low)


def _round_down(value):
    return np.floor(value / tick_size + 1e-9) * tick_size


def _round_up(value):
    return np.ceil(value / tick_size - 1e-9) * tick_size


def _round_tick(value):
    return np.floor(value / tick_size + 0.5) * tick_size


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = np.nan if column == "volume" else 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
times = df["time"].to_numpy(dtype=float)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].to_numpy(dtype=float)
n = len(df)
sources = {"close": close, "open": opened, "high": high, "low": low, "hl2": (high + low) / 2.0, "hlc3": (high + low + close) / 3.0, "ohlc4": (opened + high + low + close) / 4.0}
source_name = source_name if source_name in sources else "close"
source = sources[source_name]

runtime_ctx = globals().get("ctx")
timeframe_text = str(getattr(runtime_ctx, "timeframe", None) or "1H")
timeframe_minutes = {"1m": 1.0, "3m": 3.0, "5m": 5.0, "15m": 15.0, "30m": 30.0, "1H": 60.0, "2H": 120.0, "4H": 240.0, "1D": 1440.0, "1W": 10080.0}.get(timeframe_text, 60.0)
resolved_preset = preset
if preset == "Auto":
    resolved_preset = "Scalping" if timeframe_minutes <= 5.0 else "Default" if timeframe_minutes <= 240.0 else "Swing"
preset_values = {
    "Scalping": (10, 1.5, 14, 9, 1.0), "Default": (14, 2.0, 20, 14, 1.5),
    "Swing": (21, 2.5, 30, 21, 2.0), "Crypto": (14, 2.8, 20, 14, 2.5),
    "Custom": (atr_input, base_input, er_input, rsi_input, sl_input),
}
effective_atr_length, effective_base_multiplier, effective_er_length, effective_rsi_length, effective_sl_multiplier = preset_values.get(resolved_preset, (atr_input, base_input, er_input, rsi_input, sl_input))

previous_close = np.r_[np.nan, close[:-1]]
true_range = np.maximum(high - low, np.maximum(np.abs(high - previous_close), np.abs(low - previous_close)))
if n:
    true_range[0] = high[0] - low[0]
raw_atr = _rma(true_range, effective_atr_length)
atr_baseline = _sma(raw_atr, atr_baseline_length)
change = np.diff(close, prepend=np.nan)
average_gain = _rma(np.where(np.isfinite(change), np.maximum(change, 0.0), np.nan), effective_rsi_length)
average_loss = _rma(np.where(np.isfinite(change), np.maximum(-change, 0.0), np.nan), effective_rsi_length)
rsi = np.full(n, np.nan, dtype=float)
for index in range(n):
    if np.all(np.isfinite([average_gain[index], average_loss[index]])):
        rsi[index] = 100.0 if average_loss[index] == 0.0 else 0.0 if average_gain[index] == 0.0 else 100.0 - 100.0 / (1.0 + average_gain[index] / average_loss[index])

efficiency = np.zeros(n, dtype=float)
valid_source_pairs = np.zeros(n, dtype=float)
source_change = np.abs(np.diff(source, prepend=np.nan))
for index in range(n):
    start = index - effective_er_length + 1
    if start >= 1:
        window = source_change[start:index + 1]
        valid_source_pairs[index] = float(np.count_nonzero(np.isfinite(window)))
        if np.all(np.isfinite(window)) and np.isfinite(source[index - effective_er_length]):
            efficiency[index] = _safe_div(abs(source[index] - source[index - effective_er_length]), float(np.sum(window)), 0.0)
volatility_ratio = np.asarray([_safe_div(raw_atr[index], atr_baseline[index], 1.0) for index in range(n)], dtype=float)
atr_value = raw_atr * (0.5 + 0.5 * efficiency) if use_efficiency_atr else raw_atr.copy()

volume_mean = _sma(volume, volume_length)
volume_std = np.full(n, np.nan, dtype=float)
volume_count = np.zeros(n, dtype=int)
for index in range(volume_length - 1, n):
    window = volume[index - volume_length + 1:index + 1]
    finite = window[np.isfinite(window)]
    volume_count[index] = len(finite)
    if len(finite) == volume_length:
        volume_std[index] = float(np.std(finite, ddof=0))
volume_z_raw = np.asarray([_safe_div(volume[index] - volume_mean[index], volume_std[index], 0.0) for index in range(n)], dtype=float)
has_volume = (volume_count == volume_length) & np.isfinite(volume_std) & (volume_std > 0.0)
struct_high = _rolling_extreme(high, structure_length, True)
struct_low = _rolling_extreme(low, structure_length, False)
tqi_er = np.clip(efficiency, 0.0, 1.0)
tqi_vol = np.zeros(n, dtype=float)
tqi_struct = np.zeros(n, dtype=float)
tqi_momentum = np.zeros(n, dtype=float)
tqi = np.zeros(n, dtype=float)
for index in range(n):
    tqi_vol[index] = _map(volatility_ratio[index], 0.6, 1.8, 0.0, 1.0) if volatility_mode != "volume" else (_map(volume_z_raw[index], -1.0, 2.0, 0.0, 1.0) if has_volume[index] else 0.0)
    position = _safe_div(close[index] - struct_low[index], struct_high[index] - struct_low[index], 0.5)
    tqi_struct[index] = _clamp(abs(position - 0.5) * 2.0, 0.0, 1.0)
    if index >= momentum_length:
        direction_change = close[index] - close[index - momentum_length]
        moves = close[index - momentum_length + 1:index + 1] - close[index - momentum_length:index]
        tqi_momentum[index] = float(np.count_nonzero(moves > 0.0) if direction_change > 0.0 else np.count_nonzero(moves < 0.0) if direction_change < 0.0 else 0) / momentum_length
    available_volume_weight = weight_vol if volatility_mode != "volume" or has_volume[index] else 0.0
    weight_sum = weight_er + available_volume_weight + weight_struct + weight_mom
    raw_quality = (tqi_er[index] * weight_er + tqi_vol[index] * available_volume_weight + tqi_struct[index] * weight_struct + tqi_momentum[index] * weight_mom) / weight_sum if use_tqi and weight_sum > 0.0 else 0.5
    tqi[index] = _clamp(raw_quality, 0.0, 1.0)

rsi_lowest = _rolling_extreme(rsi, rsi_lookback, False)
rsi_highest = _rolling_extreme(rsi, rsi_lookback, True)
pivot_high = np.full(n, np.nan, dtype=float)
pivot_low = np.full(n, np.nan, dtype=float)
last_pivot_high = np.full(n, np.nan, dtype=float)
last_pivot_low = np.full(n, np.nan, dtype=float)
last_pivot_high_bar = np.full(n, -1, dtype=int)
last_pivot_low_bar = np.full(n, -1, dtype=int)
running_high = running_low = np.nan
running_high_bar = running_low_bar = -1
for index in range(n):
    if index >= pivot_length * 2:
        candidate = index - pivot_length
        high_window = high[candidate - pivot_length:candidate + pivot_length + 1]
        low_window = low[candidate - pivot_length:candidate + pivot_length + 1]
        if high[candidate] == np.max(high_window):
            pivot_high[index] = high[candidate]
            running_high, running_high_bar = float(high[candidate]), candidate
        if low[candidate] == np.min(low_window):
            pivot_low[index] = low[candidate]
            running_low, running_low_bar = float(low[candidate]), candidate
    last_pivot_high[index], last_pivot_low[index] = running_high, running_low
    last_pivot_high_bar[index], last_pivot_low_bar[index] = running_high_bar, running_low_bar
valid_low_pivot = np.isfinite(last_pivot_low) & ((np.arange(n) - last_pivot_low_bar) <= pivot_max_age) & (last_pivot_low < close)
valid_high_pivot = np.isfinite(last_pivot_high) & ((np.arange(n) - last_pivot_high_bar) <= pivot_max_age) & (last_pivot_high > close)

warmup_bars = max(50, effective_atr_length + atr_baseline_length - 2, effective_rsi_length + rsi_lookback - 1, effective_er_length, momentum_length, structure_length)
warmed_up = np.zeros(n, dtype=bool)
for index in range(n):
    rsi_ready = not use_rsi_score or (index >= rsi_lookback - 1 and np.isfinite(rsi[index - rsi_lookback + 1]))
    tqi_ready = not use_tqi or weight_er + weight_struct + weight_mom + (weight_vol if volatility_mode != "volume" or has_volume[index] else 0.0) > 0.0
    source_ready = index >= effective_er_length and np.isfinite(source[index - effective_er_length]) and valid_source_pairs[index] == effective_er_length
    warmed_up[index] = index >= warmup_bars and np.isfinite(atr_baseline[index]) and raw_atr[index] > 0.0 and atr_baseline[index] > 0.0 and source_ready and rsi_ready and tqi_ready

active_multiplier = np.full(n, np.nan, dtype=float)
passive_multiplier = np.full(n, np.nan, dtype=float)
lower_band = np.full(n, np.nan, dtype=float)
upper_band = np.full(n, np.nan, dtype=float)
trend = np.ones(n, dtype=int)
st_line = np.full(n, np.nan, dtype=float)
price_flip_up = np.zeros(n, dtype=bool)
price_flip_down = np.zeros(n, dtype=bool)
character_flip_up = np.zeros(n, dtype=bool)
character_flip_down = np.zeros(n, dtype=bool)
character_flip_fired = np.zeros(n, dtype=bool)
flip_up = np.zeros(n, dtype=bool)
flip_down = np.zeros(n, dtype=bool)
dynamic_scale = np.ones(n, dtype=float)
live_tp1_r = np.full(n, fixed_tp1, dtype=float)
live_tp2_r = np.full(n, fixed_tp2, dtype=float)
live_tp3_r = np.full(n, fixed_tp3, dtype=float)
score_momentum = np.zeros(n, dtype=float)
score_efficiency = np.zeros(n, dtype=float)
score_volume = np.zeros(n, dtype=float)
score_rsi = np.zeros(n, dtype=float)
score_structure = np.zeros(n, dtype=float)
score_break = np.zeros(n, dtype=float)
score_total = np.zeros(n, dtype=float)
score_available = np.zeros(n, dtype=float)
quality_strength_history = np.full(n, quality_strength, dtype=float)
confirmed_buy = np.zeros(n, dtype=bool)
confirmed_sell = np.zeros(n, dtype=bool)
tp1_event = np.zeros(n, dtype=bool)
tp2_event = np.zeros(n, dtype=bool)
tp3_event = np.zeros(n, dtype=bool)
stop_event = np.zeros(n, dtype=bool)
timeout_event = np.zeros(n, dtype=bool)
flip_exit_event = np.zeros(n, dtype=bool)
trade_direction = np.zeros(n, dtype=int)
trade_entry = np.full(n, np.nan, dtype=float)
trade_stop = np.full(n, np.nan, dtype=float)
trade_tp1 = np.full(n, np.nan, dtype=float)
trade_tp2 = np.full(n, np.nan, dtype=float)
trade_tp3 = np.full(n, np.nan, dtype=float)
trade_remaining = np.zeros(n, dtype=float)
trade_realized_r = np.zeros(n, dtype=float)
trade_records = []
closed_returns = []
grid_count = [0] * 9
grid_ewma = [0.0] * 9
learning = {"count": 0, "cum": 0.0, "peak": 0.0, "trough": 0.0, "win": 0, "loss": 0, "maxWin": 0, "maxLoss": 0, "breakEven": 0, "since": 0, "epoch": 0, "epochSum": 0.0, "effQ": _clamp(quality_strength, calibration_min, calibration_max) if auto_calibration else quality_strength, "lastDir": 0, "lastAverage": np.nan, "status": "Collecting epoch"}
trade = {"dir": 0, "side": 0, "entryBar": -1, "cell": -1, "epoch": 0, "entry": np.nan, "sl": np.nan, "risk": np.nan, "tp1": np.nan, "tp2": np.nan, "tp3": np.nan, "r1": np.nan, "r2": np.nan, "r3": np.nan, "hit1": False, "hit2": False, "hit3": False, "taken": 0.0, "cost": 0.0, "remaining": 0.0, "tqi": np.nan, "score": np.nan, "plan": -1}
trend_start = 0
active_smoothed = passive_smoothed = np.nan
slip_price = slippage_ticks * tick_size
tp1_floor = min(dynamic_tp1_floor, dynamic_ceiling)
tp2_floor = min(dynamic_tp1_floor * (fixed_tp2 / max(fixed_tp1, 0.01)), dynamic_ceiling)
tp3_floor = min(dynamic_tp1_floor * (fixed_tp3 / max(fixed_tp1, 0.01)), dynamic_ceiling)


def _take_fill(fill, fraction):
    trade["taken"] += fraction * trade["side"] * (fill - trade["entry"]) / trade["risk"]
    trade["cost"] += fraction * abs(fill) * fee_percent / 100.0 / trade["risk"]
    trade["remaining"] = max(0.0, trade["remaining"] - fraction)


def _record_return(value, cell, entry_epoch):
    closed_returns.append(float(value))
    while len(closed_returns) > 100:
        closed_returns.pop(0)
    learning["count"] += 1
    learning["cum"] += value
    learning["peak"] = max(learning["peak"], learning["cum"])
    learning["trough"] = min(learning["trough"], learning["cum"] - learning["peak"])
    if value > 1e-8:
        learning["win"] += 1
        learning["loss"] = 0
        learning["maxWin"] = max(learning["maxWin"], learning["win"])
    elif value < -1e-8:
        learning["loss"] += 1
        learning["win"] = 0
        learning["maxLoss"] = max(learning["maxLoss"], learning["loss"])
    else:
        learning["breakEven"] += 1
        learning["win"] = 0
        learning["loss"] = 0
    if 0 <= cell < 9:
        grid_count[cell] += 1
        grid_ewma[cell] = value if grid_count[cell] == 1 else grid_ewma[cell] * 0.8 + value * 0.2
    if auto_calibration and use_tqi and entry_epoch == learning["epoch"]:
        learning["since"] += 1
        learning["epochSum"] += value
        if learning["since"] >= max(calibration_window, calibration_cooldown):
            average = learning["epochSum"] / learning["since"]
            if average < calibration_bad:
                direction = 1 if learning["lastDir"] == 0 else learning["lastDir"] if not np.isfinite(learning["lastAverage"]) or average >= learning["lastAverage"] else -learning["lastDir"]
                if (direction > 0 and learning["effQ"] >= calibration_max) or (direction < 0 and learning["effQ"] <= calibration_min):
                    direction = -direction
                next_quality = _clamp(learning["effQ"] + direction * calibration_step, calibration_min, calibration_max)
                learning["status"] = f"{learning['effQ']:.2f} -> {next_quality:.2f}"
                learning["effQ"] = next_quality
                learning["lastDir"] = direction
            else:
                learning["status"] = "Good epoch" if average > calibration_good else "Neutral epoch"
                if average > calibration_good:
                    learning["lastDir"] = 0
            learning["lastAverage"] = average
            learning["since"] = 0
            learning["epochSum"] = 0.0
            learning["epoch"] += 1


for index in range(n):
    effective_quality = learning["effQ"] if auto_calibration else quality_strength
    quality_strength_history[index] = effective_quality
    legacy = 1.0 + adapt_strength * (0.5 - efficiency[index]) if use_adaptive else 1.0
    deviation = (1.0 - tqi[index]) ** quality_curve if use_tqi else 0.5
    quality_multiplier = 1.0 - effective_quality + effective_quality * (0.6 + 0.8 * deviation)
    symmetric = effective_base_multiplier * legacy * quality_multiplier
    active_raw = symmetric
    passive_raw = symmetric
    if use_tqi and use_asymmetric:
        active_raw = symmetric * (1.0 - asymmetry_strength * tqi[index] * 0.3)
        passive_raw = symmetric * (1.0 + asymmetry_strength * tqi[index] * 0.4)
    active_smoothed = active_raw if not np.isfinite(active_smoothed) or not smooth_multipliers else active_smoothed * 0.85 + active_raw * 0.15
    passive_smoothed = passive_raw if not np.isfinite(passive_smoothed) or not smooth_multipliers else passive_smoothed * 0.85 + passive_raw * 0.15
    active_multiplier[index], passive_multiplier[index] = active_smoothed, passive_smoothed
    previous_trend = trend[index - 1] if index else 1
    lower_multiplier = active_smoothed if previous_trend == 1 else passive_smoothed
    upper_multiplier = passive_smoothed if previous_trend == 1 else active_smoothed
    lower_raw = source[index] - lower_multiplier * atr_value[index]
    upper_raw = source[index] + upper_multiplier * atr_value[index]
    if index == 0 or not np.isfinite(lower_band[index - 1]):
        lower_band[index] = lower_raw
    else:
        lower_band[index] = max(lower_raw, lower_band[index - 1]) if close[index - 1] > lower_band[index - 1] else lower_raw
    if index == 0 or not np.isfinite(upper_band[index - 1]):
        upper_band[index] = upper_raw
    else:
        upper_band[index] = min(upper_raw, upper_band[index - 1]) if close[index - 1] < upper_band[index - 1] else upper_raw
    if index:
        price_flip_up[index] = previous_trend == -1 and np.isfinite(upper_band[index - 1]) and close[index] > upper_band[index - 1]
        price_flip_down[index] = previous_trend == 1 and np.isfinite(lower_band[index - 1]) and close[index] < lower_band[index - 1]
    character_window = max(character_min_age, 3)
    quality_high = float(np.max(tqi[max(0, index - character_window + 1):index + 1]))
    character_base = use_character_flip and use_tqi and index - trend_start >= character_min_age and quality_high > character_high and tqi[index] < character_low
    if index >= character_window:
        character_flip_down[index] = character_base and previous_trend == 1 and close[index] < close[index - character_window]
        character_flip_up[index] = character_base and previous_trend == -1 and close[index] > close[index - character_window]
    trend[index] = 1 if price_flip_up[index] or character_flip_up[index] else -1 if price_flip_down[index] or character_flip_down[index] else previous_trend
    if trend[index] != previous_trend:
        trend_start = index
    flip_up[index] = index > 0 and trend[index] == 1 and previous_trend == -1
    flip_down[index] = index > 0 and trend[index] == -1 and previous_trend == 1
    character_flip_fired[index] = (character_flip_up[index] and not price_flip_up[index] and flip_up[index]) or (character_flip_down[index] and not price_flip_down[index] and flip_down[index])
    st_line[index] = lower_band[index] if trend[index] == 1 else upper_band[index]

    if tp_mode == "Dynamic":
        tqi_component = _clamp(tqi[index], 0.0, 1.0)
        vol_component = _clamp(_map(volatility_ratio[index], 0.5, 2.0, 0.0, 1.0), 0.0, 1.0)
        total_weight = dynamic_tqi_weight + dynamic_vol_weight
        dynamic_scale[index] = dynamic_min_scale + ((tqi_component * dynamic_tqi_weight + vol_component * dynamic_vol_weight) / total_weight) * (dynamic_max_scale - dynamic_min_scale) if total_weight > 0.0 else 1.0
        effective_targets = [_clamp(fixed_tp1 * dynamic_scale[index], tp1_floor, dynamic_ceiling), _clamp(fixed_tp2 * dynamic_scale[index], tp2_floor, dynamic_ceiling), _clamp(fixed_tp3 * dynamic_scale[index], tp3_floor, dynamic_ceiling)]
        effective_targets.sort()
        live_tp1_r[index], live_tp2_r[index], live_tp3_r[index] = effective_targets

    direction_move = close[index] - close[index - 3] if index >= 3 and trend[index] == 1 else close[index - 3] - close[index] if index >= 3 else np.nan
    score_momentum[index] = _map(_safe_div(direction_move, atr_value[index], 0.0), 0.3, 2.0, 0.0, 17.0)
    score_efficiency[index] = _map(efficiency[index], 0.15, 0.7, 0.0, 17.0)
    score_volume[index] = _map(volume_z_raw[index], 0.0, 3.0, 0.0, 17.0) if use_volume_score and has_volume[index] else 0.0
    rsi_depth = max(0.0, rsi_oversold - rsi_lowest[index]) if trend[index] == 1 and np.isfinite(rsi_lowest[index]) else max(0.0, rsi_highest[index] - rsi_overbought) if trend[index] == -1 and np.isfinite(rsi_highest[index]) else 0.0
    score_rsi[index] = _map(rsi_depth, 0.0, 15.0, 0.0, 17.0) if use_rsi_score else 0.0
    pivot_available = valid_low_pivot[index] if trend[index] == 1 else valid_high_pivot[index]
    pivot_distance = abs(close[index] - last_pivot_low[index]) if trend[index] == 1 and np.isfinite(last_pivot_low[index]) else abs(last_pivot_high[index] - close[index]) if trend[index] == -1 and np.isfinite(last_pivot_high[index]) else 0.0
    score_structure[index] = _map_inverse(_safe_div(pivot_distance, atr_value[index], 0.0), 0.0, 1.5, 16.0, 6.0) if use_structure_score and pivot_available else 0.0
    break_depth = max(0.0, close[index] - upper_band[index - 1]) if trend[index] == 1 and index and np.isfinite(upper_band[index - 1]) else max(0.0, lower_band[index - 1] - close[index]) if trend[index] == -1 and index and np.isfinite(lower_band[index - 1]) else 0.0
    score_break[index] = _map(_safe_div(break_depth, atr_value[index], 0.0), 0.0, 1.0, 0.0, 16.0)
    score_available[index] = 50.0 + (17.0 if use_volume_score and has_volume[index] else 0.0) + (17.0 if use_rsi_score else 0.0) + (16.0 if use_structure_score and pivot_available else 0.0)
    score_total[index] = 100.0 * (score_momentum[index] + score_efficiency[index] + score_volume[index] + score_rsi[index] + score_structure[index] + score_break[index]) / score_available[index]
    raw_buy = flip_up[index] and warmed_up[index]
    raw_sell = flip_down[index] and warmed_up[index]

    if trade["dir"] != 0 and index > trade["entryBar"]:
        stop_hit = low[index] <= trade["sl"] if trade["side"] == 1 else high[index] >= trade["sl"]
        opposite = trade["side"] != trend[index]
        timed_out = index - trade["entryBar"] >= trade_timeout
        exit_reason = ""
        exit_fill = np.nan
        if stop_hit:
            exit_fill = (min(opened[index], trade["sl"]) if trade["side"] == 1 else max(opened[index], trade["sl"])) - trade["side"] * slip_price
            _take_fill(exit_fill, trade["remaining"])
            stop_event[index] = True
            exit_reason = "SL"
        else:
            if not trade["hit1"] and (high[index] >= trade["tp1"] if trade["side"] == 1 else low[index] <= trade["tp1"]):
                trade["hit1"] = True
                _take_fill(trade["tp1"], 1.0 / 3.0)
                tp1_event[index] = True
            if not trade["hit2"] and (high[index] >= trade["tp2"] if trade["side"] == 1 else low[index] <= trade["tp2"]):
                trade["hit2"] = True
                _take_fill(trade["tp2"], 1.0 / 3.0)
                tp2_event[index] = True
            if not trade["hit3"] and (high[index] >= trade["tp3"] if trade["side"] == 1 else low[index] <= trade["tp3"]):
                trade["hit3"] = True
                _take_fill(trade["tp3"], trade["remaining"])
                tp3_event[index] = True
                exit_reason, exit_fill = "TP3", trade["tp3"]
            elif opposite or timed_out:
                exit_fill = close[index] - trade["side"] * slip_price
                _take_fill(exit_fill, trade["remaining"])
                flip_exit_event[index] = opposite
                timeout_event[index] = not opposite
                exit_reason = "FLIP" if opposite else "TIMEOUT"
        if exit_reason:
            net_return = trade["taken"] - trade["cost"]
            _record_return(net_return, trade["cell"], trade["epoch"])
            if 0 <= trade["plan"] < len(trade_records):
                trade_records[trade["plan"]]["endIndex"] = index
                trade_records[trade["plan"]]["result"] = exit_reason
                trade_records[trade["plan"]]["netR"] = float(net_return)
            trade["dir"] = 0

    if (raw_buy or raw_sell) and trade["dir"] == 0:
        direction = 1 if raw_buy else -1
        new_entry = _round_tick(close[index] + direction * slip_price)
        pivot_usable = valid_low_pivot[index] if direction == 1 else valid_high_pivot[index]
        pivot_base = last_pivot_low[index] if direction == 1 and pivot_usable else last_pivot_high[index] if direction == -1 and pivot_usable else low[index] if direction == 1 else high[index]
        stop_buffer = effective_sl_multiplier * atr_value[index]
        stop_cap = max(stop_max_distance, effective_sl_multiplier) * atr_value[index]
        raw_stop = max(min(pivot_base - stop_buffer, new_entry - stop_buffer), new_entry - stop_cap) if direction == 1 else min(max(pivot_base + stop_buffer, new_entry + stop_buffer), new_entry + stop_cap)
        new_stop = _round_down(raw_stop) if direction == 1 else _round_up(raw_stop)
        new_risk = direction * (new_entry - new_stop)
        distance1 = max(tick_size, _round_down(new_risk * live_tp1_r[index]), _round_up(new_risk * tp1_floor) if tp_mode == "Dynamic" else 0.0)
        distance2 = max(distance1, _round_down(new_risk * live_tp2_r[index]), _round_up(new_risk * tp2_floor) if tp_mode == "Dynamic" else 0.0)
        distance3 = max(distance2, _round_down(new_risk * live_tp3_r[index]), _round_up(new_risk * tp3_floor) if tp_mode == "Dynamic" else 0.0)
        new_tp1 = _round_tick(new_entry + direction * distance1)
        new_tp2 = _round_tick(new_entry + direction * distance2)
        new_tp3 = _round_tick(new_entry + direction * distance3)
        tolerance = tick_size * 1e-6
        ceiling_valid = tp_mode != "Dynamic" or distance3 <= new_risk * dynamic_ceiling + tolerance
        stop_live = np.isfinite(new_stop) and direction * (close[index] - new_stop) > tolerance
        levels_valid = stop_live and np.isfinite(new_risk) and new_risk >= minimum_risk_ticks * tick_size - tolerance and direction * (new_tp1 - new_entry) >= tick_size - tolerance and direction * (new_tp2 - new_tp1) >= -tolerance and direction * (new_tp3 - new_tp2) >= -tolerance and ceiling_valid
        if levels_valid:
            trade.update({"dir": direction, "side": direction, "entryBar": index, "cell": (0 if efficiency[index] < 0.25 else 1 if efficiency[index] < 0.5 else 2) * 3 + (0 if volatility_ratio[index] < 0.7 else 1 if volatility_ratio[index] < 1.3 else 2), "epoch": learning["epoch"], "entry": new_entry, "sl": new_stop, "risk": new_risk, "tp1": new_tp1, "tp2": new_tp2, "tp3": new_tp3, "r1": direction * (new_tp1 - new_entry) / new_risk, "r2": direction * (new_tp2 - new_entry) / new_risk, "r3": direction * (new_tp3 - new_entry) / new_risk, "hit1": False, "hit2": False, "hit3": False, "taken": 0.0, "cost": abs(new_entry) * fee_percent / 100.0 / new_risk, "remaining": 1.0, "tqi": tqi[index], "score": score_total[index]})
            trade_records.append({"side": direction, "startIndex": index, "endIndex": index, "entry": float(new_entry), "stop": float(new_stop), "tp1": float(new_tp1), "tp2": float(new_tp2), "tp3": float(new_tp3), "result": "Active", "netR": None})
            trade["plan"] = len(trade_records) - 1
            confirmed_buy[index] = direction == 1
            confirmed_sell[index] = direction == -1

    if trade["dir"] != 0:
        trade_records[trade["plan"]]["endIndex"] = index
        trade_direction[index] = trade["dir"]
        trade_entry[index], trade_stop[index] = trade["entry"], trade["sl"]
        trade_tp1[index], trade_tp2[index], trade_tp3[index] = trade["tp1"], trade["tp2"], trade["tp3"]
        trade_remaining[index] = trade["remaining"]
        trade_realized_r[index] = trade["taken"] - trade["cost"]

layers = []
display_first_index = max(0, n - 120)
if show_risk:
    for record in trade_records:
        end_index = int(record["endIndex"])
        if record["result"] != "Active" and end_index < display_first_index:
            continue
        start_index = max(int(record["startIndex"]), display_first_index)
        layers.extend([
            {"type": "line", "role": "Entry", "startIndex": start_index, "endIndex": end_index, "y1": record["entry"], "y2": record["entry"], "color": "#808080", "lineWidth": 2},
            {"type": "line", "role": "Stop", "startIndex": start_index, "endIndex": end_index, "y1": record["stop"], "y2": record["stop"], "color": stop_color, "lineWidth": 2},
            {"type": "line", "role": "Target 1", "startIndex": start_index, "endIndex": end_index, "y1": record["tp1"], "y2": record["tp1"], "color": target_color, "lineWidth": 1, "lineStyle": "dashed"},
            {"type": "line", "role": "Target 2", "startIndex": start_index, "endIndex": end_index, "y1": record["tp2"], "y2": record["tp2"], "color": target_color, "lineWidth": 1, "lineStyle": "dashed"},
            {"type": "line", "role": "Target 3", "startIndex": start_index, "endIndex": end_index, "y1": record["tp3"], "y2": record["tp3"], "color": target_color, "lineWidth": 2, "lineStyle": "dashed"},
        ])


def _events(mask, prices, text, color):
    return [{"active": True, "price": float(prices[index]), "text": text, "color": color} if mask[index] else None for index in range(n)]


def _limited_event_masks(masks, total_cap=16, minimum_gap=5):
    result = [np.zeros(n, dtype=bool) for _ in masks]
    candidates = []
    for mask_index, mask in enumerate(masks):
        for index in np.flatnonzero(np.asarray(mask, dtype=bool)):
            if index >= display_first_index:
                candidates.append((int(index), mask_index))
    selected_indices = []
    for index, mask_index in sorted(candidates, reverse=True):
        if len(selected_indices) >= total_cap:
            break
        if any(abs(index - selected) < minimum_gap for selected in selected_indices):
            continue
        result[mask_index][index] = True
        selected_indices.append(index)
    return result


display_buy, display_sell, display_stop, display_tp1, display_tp2, display_tp3, display_timeout, display_flip, display_character = _limited_event_masks([
    confirmed_buy if show_signals else np.zeros(n, bool),
    confirmed_sell if show_signals else np.zeros(n, bool),
    stop_event,
    tp1_event if show_hits else np.zeros(n, bool),
    tp2_event if show_hits else np.zeros(n, bool),
    tp3_event if show_hits else np.zeros(n, bool),
    timeout_event,
    flip_exit_event,
    character_flip_fired & warmed_up,
])


tables = []
last = n - 1
if show_dashboard and n:
    sample = closed_returns[-calibration_window:]
    win_rate = sum(1 for value in sample if value > 1e-8) / len(sample) if sample else 0.0
    average_return = float(np.mean(sample)) if sample else 0.0
    rows = [
        [{"text": "Preset"}, {"text": resolved_preset}], [{"text": "Trend"}, {"text": "Bullish" if trend[last] == 1 else "Bearish", "color": bull_color if trend[last] == 1 else bear_color}],
        [{"text": "TQI"}, {"text": f"{tqi[last]:.2f}" if use_tqi else "off"}], [{"text": "Q Strength"}, {"text": f"{quality_strength_history[last]:.2f}" if use_tqi else "off"}],
        [{"text": "ER / Vol"}, {"text": f"{efficiency[last]:.2f} / {volatility_ratio[last]:.2f}"}], [{"text": "RSI / Vol Z"}, {"text": f"{rsi[last]:.1f} / {volume_z_raw[last]:.2f}" if np.isfinite(rsi[last]) else "n/a"}],
        [{"text": "TP Mode"}, {"text": f"{tp_mode} x{dynamic_scale[last]:.2f}" if tp_mode == "Dynamic" else "Fixed"}], [{"text": "Status"}, {"text": "Ready" if warmed_up[last] else "Warming up"}],
    ]
    if show_tqi_breakdown:
        rows.extend([[{"text": "TQI Efficiency"}, {"text": f"{tqi_er[last]:.2f}"}], [{"text": "TQI Volatility"}, {"text": f"{tqi_vol[last]:.2f}"}], [{"text": "TQI Structure"}, {"text": f"{tqi_struct[last]:.2f}"}], [{"text": "TQI Momentum"}, {"text": f"{tqi_momentum[last]:.2f}"}]])
    if show_performance:
        rows.extend([[{"text": "Closed Trades"}, {"text": str(learning["count"])}], [{"text": "Win Rate"}, {"text": f"{win_rate * 100:.1f}%" if sample else "n/a"}], [{"text": "Average R"}, {"text": f"{average_return:+.2f}" if sample else "n/a"}], [{"text": "Max Closed DD"}, {"text": f"{learning['trough']:.2f}R"}]])
    tables.append({"title": "Self-Aware Trend", "position": "top-right", "compact": True, "rows": rows})

line_colors = []
for index in range(n):
    alpha = _map_inverse(tqi[index], 0.1, 0.9, 0.30, 1.0) if show_tqi_color and use_tqi else 1.0
    base = bull_color if trend[index] == 1 else bear_color
    line_colors.append(base + f"{int(_clamp(alpha, 0.0, 1.0) * 255):02X}" if base.startswith("#") and len(base) == 7 else base)
visible_line = [float(st_line[index]) if show_bands and warmed_up[index] and np.isfinite(st_line[index]) else None for index in range(n)]
background_colors = [f"{bull_color}14" if show_background and warmed_up[index] and trend[index] == 1 else f"{bear_color}14" if show_background and warmed_up[index] else None for index in range(n)]

output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "Adaptive SuperTrend", "title": True, "data": visible_line, "color": line_colors, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "name": "Trend Buy", "text": "BUY", "data": _events(display_buy, low, "BUY", bull_color), "color": bull_color, "renderMode": "events", "overlay": True},
        {"type": "sell", "name": "Trend Sell", "text": "SELL", "data": _events(display_sell, high, "SELL", bear_color), "color": bear_color, "renderMode": "events", "overlay": True},
        {"type": "sell", "name": "Stop Hit", "text": "SL", "data": _events(display_stop, np.where(stop_event, np.where(np.isfinite(trade_stop), trade_stop, close), close), "SL", stop_color), "color": stop_color, "renderMode": "events", "overlay": True},
        {"type": "buy", "name": "TP1 Hit", "text": "TP1", "data": _events(display_tp1, np.where(np.isfinite(trade_tp1), trade_tp1, close), "TP1", target_color), "color": target_color, "renderMode": "events", "overlay": True},
        {"type": "buy", "name": "TP2 Hit", "text": "TP2", "data": _events(display_tp2, np.where(np.isfinite(trade_tp2), trade_tp2, close), "TP2", target_color), "color": target_color, "renderMode": "events", "overlay": True},
        {"type": "buy", "name": "TP3 Hit", "text": "TP3", "data": _events(display_tp3, np.where(np.isfinite(trade_tp3), trade_tp3, close), "TP3", target_color), "color": target_color, "renderMode": "events", "overlay": True},
        {"type": "sell", "name": "Timeout Exit", "text": "TIME", "data": _events(display_timeout, close, "TIME", "#FFB300"), "color": "#FFB300", "renderMode": "events", "overlay": True},
        {"type": "sell", "name": "Flip Exit", "text": "FLIP", "data": _events(display_flip, close, "FLIP", "#9C27B0"), "color": "#9C27B0", "renderMode": "events", "overlay": True},
        {"type": "sell", "name": "Character Flip", "text": "CHAR", "data": _events(display_character, close, "CHAR", "#00BCD4"), "color": "#00BCD4", "renderMode": "events", "overlay": True},
    ],
    "layers": layers, "tables": tables, "bar_colors": [None] * n, "background_colors": background_colors,
    "calculatedVars": {
        "resolvedPreset": resolved_preset, "effectiveAtrLength": int(effective_atr_length), "effectiveBaseMultiplier": float(effective_base_multiplier), "effectiveEfficiencyLength": int(effective_er_length), "effectiveRsiLength": int(effective_rsi_length), "effectiveStopMultiplier": float(effective_sl_multiplier),
        "rawAtr": _finite(raw_atr), "atrBaseline": _finite(atr_baseline), "rsi": _finite(rsi), "efficiency": _finite(efficiency), "volatilityRatio": _finite(volatility_ratio), "atrValue": _finite(atr_value), "volumeZ": _finite(volume_z_raw), "hasVolume": has_volume.tolist(), "warmedUp": warmed_up.tolist(),
        "tqiEfficiency": _finite(tqi_er), "tqiVolatility": _finite(tqi_vol), "tqiStructure": _finite(tqi_struct), "tqiMomentum": _finite(tqi_momentum), "tqi": _finite(tqi), "qualityStrength": _finite(quality_strength_history),
        "activeMultiplier": _finite(active_multiplier), "passiveMultiplier": _finite(passive_multiplier), "lowerBand": _finite(lower_band), "upperBand": _finite(upper_band), "trend": trend.tolist(), "superTrend": _finite(st_line),
        "priceFlipUp": price_flip_up.tolist(), "priceFlipDown": price_flip_down.tolist(), "characterFlipUp": character_flip_up.tolist(), "characterFlipDown": character_flip_down.tolist(), "characterFlipFired": character_flip_fired.tolist(), "flipUp": flip_up.tolist(), "flipDown": flip_down.tolist(),
        "dynamicScale": _finite(dynamic_scale), "liveTp1R": _finite(live_tp1_r), "liveTp2R": _finite(live_tp2_r), "liveTp3R": _finite(live_tp3_r),
        "lastPivotHigh": _finite(last_pivot_high), "lastPivotLow": _finite(last_pivot_low), "validHighPivot": valid_high_pivot.tolist(), "validLowPivot": valid_low_pivot.tolist(),
        "scoreMomentum": _finite(score_momentum), "scoreEfficiency": _finite(score_efficiency), "scoreVolume": _finite(score_volume), "scoreRsi": _finite(score_rsi), "scoreStructure": _finite(score_structure), "scoreBreak": _finite(score_break), "scoreTotal": _finite(score_total), "scoreAvailable": _finite(score_available),
        "confirmedBuy": confirmed_buy.tolist(), "confirmedSell": confirmed_sell.tolist(), "tp1Event": tp1_event.tolist(), "tp2Event": tp2_event.tolist(), "tp3Event": tp3_event.tolist(), "stopEvent": stop_event.tolist(), "timeoutEvent": timeout_event.tolist(), "flipExitEvent": flip_exit_event.tolist(),
        "tradeDirection": trade_direction.tolist(), "tradeEntry": _finite(trade_entry), "tradeStop": _finite(trade_stop), "tradeTp1": _finite(trade_tp1), "tradeTp2": _finite(trade_tp2), "tradeTp3": _finite(trade_tp3), "tradeRemaining": _finite(trade_remaining), "tradeRealizedR": _finite(trade_realized_r),
        "tradeRecords": trade_records, "closedReturns": [float(value) for value in closed_returns], "gridCount": grid_count, "gridEwmaR": [float(value) for value in grid_ewma], "allTimeCount": int(learning["count"]), "allTimeDrawdownR": float(learning["trough"]), "calibrationEpoch": int(learning["epoch"]), "calibrationStatus": str(learning["status"]),
    },
}
