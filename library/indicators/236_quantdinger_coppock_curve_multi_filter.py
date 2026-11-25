# ============================================================
# QuantDinger Coppock Curve Multi Filter
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Coppock Curve Multi Filter"
my_indicator_description = (
    "A configurable Coppock curve with adaptive smoothing, confirmation filters, "
    "confirmed higher-timeframe alignment, and ATR-based trade-state levels."
)

indicator_timeframes = ["1H", "4H", "1D", "1W"]

# @param source str Close Source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param roc_long int 14 Long ROC length range=1:200:1
# @param roc_short int 11 Short ROC length range=1:200:1
# @param wma_length int 10 WMA smoothing length range=1:200:1
# @param use_adx_filter bool false Use ADX filter
# @param adx_threshold float 20.0 ADX threshold range=0.0:100.0:0.5
# @param adx_length int 14 ADX length range=1:100:1
# @param adaptive_filter str None Adaptive filter values=None,SMA,EMA,RMA,Double_WMA,Triple_VWMA,HMA,LLAMA,Kalman_Filter
# @param adaptive_length int 20 Adaptive filter length range=1:200:1
# @param use_divergence_filter bool false Use divergence filter
# @param divergence_lookback int 5 Divergence pivot lookback range=2:50:1
# @param use_acceleration_filter bool false Use slope acceleration filter
# @param use_volume_filter bool false Use volume confirmation filter
# @param volume_length int 20 Volume average length range=1:200:1
# @param use_mtf_filter bool false Use higher-timeframe alignment
# @param mtf_timeframe str 4H Higher timeframe values=1H,4H,1D,1W
# @param use_noise_filter bool false Use volatility-adjusted zero filter
# @param noise_multiple float 0.5 Noise band multiple range=0.1:5.0:0.1
# @param noise_length int 20 Noise band length range=1:200:1
# @param use_persistence_filter bool false Require prior directional persistence
# @param persistence_bars int 2 Persistence bars range=1:20:1
# @param lock_signal bool false Block a signal on the latest bar
# @param atr_multiple float 1.5 Stop ATR multiple range=0.1:10.0:0.1
# @param tp1_r float 1.0 TP1 R multiple range=0.1:10.0:0.1
# @param tp2_r float 2.0 TP2 R multiple range=0.1:10.0:0.1
# @param tp3_r float 3.0 TP3 R multiple range=0.1:10.0:0.1
# @param atr_length int 14 ATR length range=1:100:1
# @param show_levels bool true Show latest trade levels
# @param show_candles bool true Color host candles
# @param show_histogram bool true Show Coppock histogram
# @param show_zero_crosses bool true Show zero-cross markers
# @param show_dashboard bool true Show dashboard
# @param dashboard_position str top-right Dashboard position values=top-right,top-left,bottom-right,bottom-left
# @param bullish_color str #26A69A Bullish color
# @param bearish_color str #EF5350 Bearish color
# @param neutral_color str #787B86 Neutral color

source_name = str(params.get("source", "Close"))
roc_long = min(200, max(1, int(params.get("roc_long", 14))))
roc_short = min(200, max(1, int(params.get("roc_short", 11))))
wma_length = min(200, max(1, int(params.get("wma_length", 10))))
use_adx_filter = bool(params.get("use_adx_filter", False))
adx_threshold = min(100.0, max(0.0, float(params.get("adx_threshold", 20.0))))
adx_length = min(100, max(1, int(params.get("adx_length", 14))))
adaptive_filter = str(params.get("adaptive_filter", "None")).upper().replace(" ", "_")
adaptive_length = min(200, max(1, int(params.get("adaptive_length", 20))))
use_divergence_filter = bool(params.get("use_divergence_filter", False))
divergence_lookback = min(50, max(2, int(params.get("divergence_lookback", 5))))
use_acceleration_filter = bool(params.get("use_acceleration_filter", False))
use_volume_filter = bool(params.get("use_volume_filter", False))
volume_length = min(200, max(1, int(params.get("volume_length", 20))))
use_mtf_filter = bool(params.get("use_mtf_filter", False))
mtf_timeframe = str(params.get("mtf_timeframe", "4H")).upper()
use_noise_filter = bool(params.get("use_noise_filter", False))
noise_multiple = min(5.0, max(0.1, float(params.get("noise_multiple", 0.5))))
noise_length = min(200, max(1, int(params.get("noise_length", 20))))
use_persistence_filter = bool(params.get("use_persistence_filter", False))
persistence_bars = min(20, max(1, int(params.get("persistence_bars", 2))))
lock_signal = bool(params.get("lock_signal", False))
atr_multiple = min(10.0, max(0.1, float(params.get("atr_multiple", 1.5))))
tp1_r = min(10.0, max(0.1, float(params.get("tp1_r", 1.0))))
tp2_r = min(10.0, max(0.1, float(params.get("tp2_r", 2.0))))
tp3_r = min(10.0, max(0.1, float(params.get("tp3_r", 3.0))))
atr_length = min(100, max(1, int(params.get("atr_length", 14))))
show_levels = bool(params.get("show_levels", True))
show_candles = bool(params.get("show_candles", True))
show_histogram = bool(params.get("show_histogram", True))
show_zero_crosses = bool(params.get("show_zero_crosses", True))
show_dashboard = bool(params.get("show_dashboard", True))
dashboard_position = str(params.get("dashboard_position", "top-right"))
bullish_color = str(params.get("bullish_color", "#26A69A"))
bearish_color = str(params.get("bearish_color", "#EF5350"))
neutral_color = str(params.get("neutral_color", "#787B86"))

frame = df.copy().reset_index(drop=True)
n = len(frame)
if "time" not in frame.columns:
    frame["time"] = np.arange(n, dtype=float) * 3600.0


def numeric(source_frame, column):
    return pd.to_numeric(source_frame[column], errors="coerce").ffill().bfill().fillna(0.0).to_numpy(dtype=float)


open_values = numeric(frame, "open")
high_values = numeric(frame, "high")
low_values = numeric(frame, "low")
close_values = numeric(frame, "close")
volume_values = numeric(frame, "volume")


def select_source(source_frame, name):
    opens = numeric(source_frame, "open")
    highs = numeric(source_frame, "high")
    lows = numeric(source_frame, "low")
    closes = numeric(source_frame, "close")
    key = name.replace(" ", "").replace("_", "").upper()
    choices = {
        "OPEN": opens,
        "HIGH": highs,
        "LOW": lows,
        "HL2": (highs + lows) * 0.5,
        "HLC3": (highs + lows + closes) / 3.0,
        "OHLC4": (opens + highs + lows + closes) * 0.25,
    }
    return choices.get(key, closes).astype(float)


def sma(values, length):
    return pd.Series(values, dtype=float).rolling(length, min_periods=length).mean().to_numpy(dtype=float)


def ema(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    finite_indexes = np.flatnonzero(np.isfinite(values))
    if len(finite_indexes) < length:
        return result
    seed_indexes = finite_indexes[:length]
    seed_at = int(seed_indexes[-1])
    previous = float(np.mean(values[seed_indexes]))
    result[seed_at] = previous
    alpha = 2.0 / (length + 1.0)
    for index in range(seed_at + 1, len(values)):
        if np.isfinite(values[index]):
            previous = alpha * float(values[index]) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def rma(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    finite_indexes = np.flatnonzero(np.isfinite(values))
    if len(finite_indexes) < length:
        return result
    seed_indexes = finite_indexes[:length]
    seed_at = int(seed_indexes[-1])
    previous = float(np.mean(values[seed_indexes]))
    result[seed_at] = previous
    for index in range(seed_at + 1, len(values)):
        if np.isfinite(values[index]):
            previous = (previous * (length - 1) + float(values[index])) / float(length)
        result[index] = previous
    return result


def wma(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, length + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / denominator)
    return result


def vwma(values, volumes, length):
    values = np.asarray(values, dtype=float)
    volumes = np.asarray(volumes, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        left = index - length + 1
        value_window = values[left:index + 1]
        volume_window = volumes[left:index + 1]
        denominator = float(np.sum(volume_window))
        if np.all(np.isfinite(value_window)) and denominator != 0.0:
            result[index] = float(np.sum(value_window * volume_window) / denominator)
    return result


def adaptive(values, volumes, kind, length):
    if kind == "SMA":
        return sma(values, length)
    if kind == "EMA":
        return ema(values, length)
    if kind == "RMA":
        return rma(values, length)
    if kind == "DOUBLE_WMA":
        return wma(wma(values, length), length)
    if kind == "TRIPLE_VWMA":
        return vwma(vwma(vwma(values, volumes, length), volumes, length), volumes, length)
    if kind == "HMA":
        half = max(1, int(round(length / 2.0)))
        root = max(1, int(round(np.sqrt(length))))
        return wma(2.0 * wma(values, half) - wma(values, length), root)
    if kind == "LLAMA":
        mean = sma(values, length)
        result = np.full(len(values), np.nan, dtype=float)
        for index in range(length, len(values)):
            if np.isfinite(mean[index]) and np.isfinite(values[index - length]):
                result[index] = float(mean[index] + (values[index] - values[index - length]) * 0.5)
        return result
    if kind == "KALMAN_FILTER":
        result = np.full(len(values), np.nan, dtype=float)
        estimate = np.nan
        error = 1.0
        process_noise = 1.0 / float(length)
        for index, value in enumerate(values):
            if not np.isfinite(value):
                continue
            if not np.isfinite(estimate):
                estimate = float(value)
            predicted_error = error + process_noise
            gain = predicted_error / (predicted_error + 1.0)
            estimate = estimate + gain * (float(value) - estimate)
            error = (1.0 - gain) * predicted_error
            result[index] = estimate
        return result
    return np.asarray(values, dtype=float).copy()


def roc(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length, len(values)):
        previous = values[index - length]
        if np.isfinite(values[index]) and np.isfinite(previous) and previous != 0.0:
            result[index] = (values[index] / previous - 1.0) * 100.0
    return result


def coppock_for(source_frame):
    values = select_source(source_frame, source_name)
    volumes = numeric(source_frame, "volume")
    adapted = adaptive(values, volumes, adaptive_filter, adaptive_length)
    long_values = roc(adapted, roc_long)
    short_values = roc(adapted, roc_short)
    return adapted, long_values, short_values, wma(long_values + short_values, wma_length)


adapted_source, roc_long_values, roc_short_values, coppock = coppock_for(frame)

true_range = np.zeros(n, dtype=float)
plus_dm = np.zeros(n, dtype=float)
minus_dm = np.zeros(n, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high_values[index] - low_values[index]
    else:
        true_range[index] = max(high_values[index] - low_values[index], abs(high_values[index] - close_values[index - 1]), abs(low_values[index] - close_values[index - 1]))
        upward = high_values[index] - high_values[index - 1]
        downward = low_values[index - 1] - low_values[index]
        plus_dm[index] = upward if upward > downward and upward > 0.0 else 0.0
        minus_dm[index] = downward if downward > upward and downward > 0.0 else 0.0
atr = rma(true_range, atr_length)
adx_atr = rma(true_range, adx_length)
plus_di = np.divide(100.0 * rma(plus_dm, adx_length), adx_atr, out=np.full(n, np.nan), where=np.isfinite(adx_atr) & (adx_atr != 0.0))
minus_di = np.divide(100.0 * rma(minus_dm, adx_length), adx_atr, out=np.full(n, np.nan), where=np.isfinite(adx_atr) & (adx_atr != 0.0))
di_sum = plus_di + minus_di
dx = np.divide(100.0 * np.abs(plus_di - minus_di), di_sum, out=np.full(n, np.nan), where=np.isfinite(di_sum) & (di_sum != 0.0))
adx = rma(dx, adx_length)
adx_pass = np.logical_or(not use_adx_filter, np.isfinite(adx) & (adx >= adx_threshold))


def pivot_at(values, confirmation_index, length, high_pivot):
    center = confirmation_index - length
    if center < length or confirmation_index >= len(values):
        return None
    window = values[center - length:confirmation_index + 1]
    if len(window) != length * 2 + 1 or not np.all(np.isfinite(window)):
        return None
    candidate = values[center]
    others = np.concatenate((window[:length], window[length + 1:]))
    if high_pivot:
        return center if np.all(candidate > others) else None
    return center if np.all(candidate < others) else None


bull_divergence = np.zeros(n, dtype=bool)
bear_divergence = np.zeros(n, dtype=bool)
pivot_low_confirmation = np.zeros(n, dtype=bool)
pivot_high_confirmation = np.zeros(n, dtype=bool)
last_low = None
last_high = None
active_bull_divergence = False
active_bear_divergence = False
for index in range(n):
    low_pivot = pivot_at(low_values, index, divergence_lookback, False)
    high_pivot = pivot_at(high_values, index, divergence_lookback, True)
    if low_pivot is not None:
        pivot_low_confirmation[index] = True
        current = (float(low_values[low_pivot]), float(coppock[low_pivot]) if np.isfinite(coppock[low_pivot]) else np.nan)
        active_bull_divergence = bool(last_low is not None and np.isfinite(current[1]) and np.isfinite(last_low[1]) and current[0] < last_low[0] and current[1] > last_low[1])
        last_low = current
    if high_pivot is not None:
        pivot_high_confirmation[index] = True
        current = (float(high_values[high_pivot]), float(coppock[high_pivot]) if np.isfinite(coppock[high_pivot]) else np.nan)
        active_bear_divergence = bool(last_high is not None and np.isfinite(current[1]) and np.isfinite(last_high[1]) and current[0] > last_high[0] and current[1] < last_high[1])
        last_high = current
    bull_divergence[index] = active_bull_divergence
    bear_divergence[index] = active_bear_divergence

slope = np.full(n, np.nan, dtype=float)
slope[1:] = coppock[1:] - coppock[:-1]
rising = np.isfinite(slope) & (slope > 0.0)
falling = np.isfinite(slope) & (slope < 0.0)
rise_streak = np.zeros(n, dtype=int)
fall_streak = np.zeros(n, dtype=int)
for index in range(n):
    if rising[index]:
        rise_streak[index] = (rise_streak[index - 1] if index else 0) + 1
    elif falling[index]:
        fall_streak[index] = (fall_streak[index - 1] if index else 0) + 1

volume_average = sma(volume_values, volume_length)
volume_pass = np.logical_or(not use_volume_filter, np.isfinite(volume_average) & (volume_values > volume_average))
noise_band = pd.Series(coppock, dtype=float).rolling(noise_length, min_periods=noise_length).std(ddof=0).to_numpy(dtype=float) * noise_multiple

timeframe_seconds = {"1M": 60, "5M": 300, "15M": 900, "30M": 1800, "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
runtime_context = ctx
chart_timeframe = str(runtime_context.timeframe or "1H").upper()
chart_seconds = timeframe_seconds.get(chart_timeframe, 3600)
base_time = pd.to_numeric(frame["time"], errors="coerce").to_numpy(dtype=float)
if len(base_time) and np.nanmax(np.abs(base_time)) > 10000000000:
    base_time = base_time / 1000.0
mtf_bullish = np.zeros(n, dtype=bool)
mtf_source_index = np.full(n, -1, dtype=int)
resolved_mtf_timeframe = mtf_timeframe
if use_mtf_filter:
    raw_mtf = (
        runtime_context.raw_series(mtf_timeframe)
        if runtime_context is not None and hasattr(runtime_context, "raw_series")
        else None
    )
    if raw_mtf is None or len(raw_mtf) == 0:
        raw_mtf = frame
        resolved_mtf_timeframe = chart_timeframe
    else:
        raw_mtf = raw_mtf.copy().reset_index(drop=True)
        if "time" not in raw_mtf.columns:
            raw_mtf["time"] = np.arange(len(raw_mtf), dtype=float) * timeframe_seconds.get(mtf_timeframe, 14400)
    _, _, _, mtf_coppock = coppock_for(raw_mtf)
    mtf_time = pd.to_numeric(raw_mtf["time"], errors="coerce").to_numpy(dtype=float)
    if len(mtf_time) and np.nanmax(np.abs(mtf_time)) > 10000000000:
        mtf_time = mtf_time / 1000.0
    mtf_seconds = timeframe_seconds.get(resolved_mtf_timeframe, chart_seconds)
    indexes = np.searchsorted(mtf_time + mtf_seconds, base_time, side="right") - 1
    mtf_source_index = indexes.astype(int)
    for index, source_index in enumerate(indexes):
        if source_index >= 1 and source_index < len(mtf_coppock) and np.isfinite(mtf_coppock[source_index]) and np.isfinite(mtf_coppock[source_index - 1]):
            mtf_bullish[index] = mtf_coppock[source_index] > mtf_coppock[source_index - 1]

bull_cross = np.zeros(n, dtype=bool)
bear_cross = np.zeros(n, dtype=bool)
zero_cross_up = np.zeros(n, dtype=bool)
zero_cross_down = np.zeros(n, dtype=bool)
for index in range(2, n):
    if not (np.isfinite(coppock[index]) and np.isfinite(coppock[index - 1]) and np.isfinite(coppock[index - 2])):
        continue
    raw_bull = coppock[index] > coppock[index - 1] and coppock[index - 1] <= coppock[index - 2]
    raw_bear = coppock[index] < coppock[index - 1] and coppock[index - 1] >= coppock[index - 2]
    acceleration_long = not use_acceleration_filter or slope[index] > slope[index - 1]
    acceleration_short = not use_acceleration_filter or slope[index] < slope[index - 1]
    divergence_long = not use_divergence_filter or not bear_divergence[index]
    divergence_short = not use_divergence_filter or not bull_divergence[index]
    mtf_long = not use_mtf_filter or mtf_bullish[index]
    mtf_short = not use_mtf_filter or not mtf_bullish[index]
    persistence_long = not use_persistence_filter or fall_streak[index - 1] >= persistence_bars
    persistence_short = not use_persistence_filter or rise_streak[index - 1] >= persistence_bars
    bull_cross[index] = bool(raw_bull and adx_pass[index] and divergence_long and acceleration_long and volume_pass[index] and mtf_long and persistence_long)
    bear_cross[index] = bool(raw_bear and adx_pass[index] and divergence_short and acceleration_short and volume_pass[index] and mtf_short and persistence_short)
    noise_pass = not use_noise_filter or np.isfinite(noise_band[index]) and abs(coppock[index]) > noise_band[index]
    zero_cross_up[index] = bool(coppock[index] > 0.0 and coppock[index - 1] <= 0.0 and adx_pass[index] and noise_pass)
    zero_cross_down[index] = bool(coppock[index] < 0.0 and coppock[index - 1] >= 0.0 and adx_pass[index] and noise_pass)

if lock_signal and n:
    bull_cross[-1] = False
    bear_cross[-1] = False

entry_series = np.full(n, np.nan, dtype=float)
stop_series = np.full(n, np.nan, dtype=float)
tp1_series = np.full(n, np.nan, dtype=float)
tp2_series = np.full(n, np.nan, dtype=float)
tp3_series = np.full(n, np.nan, dtype=float)
trade_open_series = np.zeros(n, dtype=bool)
sl_hit = np.zeros(n, dtype=bool)
tp1_hit = np.zeros(n, dtype=bool)
tp2_hit = np.zeros(n, dtype=bool)
tp3_hit = np.zeros(n, dtype=bool)
active_direction = 0
entry_price = stop_price = tp1_price = tp2_price = tp3_price = np.nan
signal_index = -1
trade_open = False
sl_done = tp1_done = tp2_done = tp3_done = False
for index in range(n):
    new_direction = 1 if bull_cross[index] else -1 if bear_cross[index] else 0
    if new_direction and np.isfinite(atr[index]):
        active_direction = new_direction
        signal_index = index
        trade_open = True
        sl_done = tp1_done = tp2_done = tp3_done = False
        entry_price = float(close_values[index])
        risk = float(atr[index] * atr_multiple)
        stop_price = entry_price - risk if active_direction == 1 else entry_price + risk
        tp1_price = entry_price + risk * tp1_r if active_direction == 1 else entry_price - risk * tp1_r
        tp2_price = entry_price + risk * tp2_r if active_direction == 1 else entry_price - risk * tp2_r
        tp3_price = entry_price + risk * tp3_r if active_direction == 1 else entry_price - risk * tp3_r
    levels_live = trade_open and signal_index >= 0 and index > signal_index
    current_sl_hit = bool(levels_live and not sl_done and (low_values[index] <= stop_price if active_direction == 1 else high_values[index] >= stop_price))
    current_tp1_hit = bool(levels_live and not sl_done and not current_sl_hit and not tp1_done and (high_values[index] >= tp1_price if active_direction == 1 else low_values[index] <= tp1_price))
    current_tp2_hit = bool(levels_live and not sl_done and not current_sl_hit and not tp2_done and (high_values[index] >= tp2_price if active_direction == 1 else low_values[index] <= tp2_price))
    current_tp3_hit = bool(levels_live and not sl_done and not current_sl_hit and not tp3_done and (high_values[index] >= tp3_price if active_direction == 1 else low_values[index] <= tp3_price))
    if current_sl_hit:
        sl_done = True
        trade_open = False
    if current_tp1_hit:
        tp1_done = True
    if current_tp2_hit:
        tp2_done = True
    if current_tp3_hit:
        tp3_done = True
    sl_hit[index] = current_sl_hit
    tp1_hit[index] = current_tp1_hit
    tp2_hit[index] = current_tp2_hit
    tp3_hit[index] = current_tp3_hit
    entry_series[index] = entry_price
    stop_series[index] = stop_price
    tp1_series[index] = tp1_price
    tp2_series[index] = tp2_price
    tp3_series[index] = tp3_price
    trade_open_series[index] = trade_open


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


histogram_colors = []
bar_colors = []
for index in range(n):
    if not np.isfinite(coppock[index]):
        color = neutral_color
    elif coppock[index] >= 0.0:
        color = bullish_color if rising[index] else "#B2DFDB"
    else:
        color = "#FFCDD2" if rising[index] else "#FF5252"
    histogram_colors.append(color)
    bar_colors.append(color if show_candles else None)

plots = []
if show_histogram:
    plots.append({
        "name": "Coppock Histogram",
        "data": [{"value": float(value), "color": histogram_colors[index]} if np.isfinite(value) else None for index, value in enumerate(coppock)],
        "color": bullish_color,
        "type": "histogram",
        "histBase": 0.0,
        "overlay": False,
    })
else:
    plots.append({"name": "Coppock Scale", "title": False, "data": finite(coppock), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": False})
plots.extend([
    {"name": "Zero", "data": [0.0] * n, "color": neutral_color, "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": False},
    {"name": "Zero Up", "title": False, "data": [{"value": float(coppock[index]), "color": bullish_color, "size": 5.0} if show_zero_crosses and zero_cross_up[index] else None for index in range(n)], "color": bullish_color, "type": "scatter", "overlay": False},
    {"name": "Zero Down", "title": False, "data": [{"value": float(coppock[index]), "color": bearish_color, "size": 5.0} if show_zero_crosses and zero_cross_down[index] else None for index in range(n)], "color": bearish_color, "type": "scatter", "overlay": False},
])

layers = []
latest_signal_indexes = np.flatnonzero(bull_cross | bear_cross)
if show_levels and len(latest_signal_indexes):
    latest = int(latest_signal_indexes[-1])
    values = [("SL", stop_series[-1], bearish_color, False), ("Entry", entry_series[-1], "#2196F3", True), ("TP1", tp1_series[-1], "rgba(38,166,154,0.60)", True), ("TP2", tp2_series[-1], "rgba(38,166,154,0.80)", True), ("TP3", tp3_series[-1], bullish_color, True)]
    for text_value, price_value, color_value, dashed in values:
        if np.isfinite(price_value):
            layers.append({"type": "line", "startIndex": max(0, latest - 1), "endIndex": max(0, n - 1), "startPrice": float(price_value), "endPrice": float(price_value), "color": color_value, "lineWidth": 2 if text_value == "SL" else 1, "dashed": dashed, "extendRight": True})
            layers.append({"type": "label", "index": max(0, n - 1), "price": float(price_value), "text": text_value, "color": color_value, "fillColor": "rgba(10,15,26,0.90)", "textColor": "#FFFFFF", "fontSize": 8, "side": "right"})
    if np.isfinite(stop_series[-1]) and np.isfinite(entry_series[-1]):
        layers.append({"type": "zone", "startIndex": max(0, latest - 1), "endIndex": max(0, n - 1), "top": float(max(stop_series[-1], entry_series[-1])), "bottom": float(min(stop_series[-1], entry_series[-1])), "fillColor": "rgba(239,83,80,0.14)", "borderColor": "rgba(0,0,0,0)", "borderSize": 0})
    if np.isfinite(entry_series[-1]) and np.isfinite(tp3_series[-1]):
        layers.append({"type": "zone", "startIndex": max(0, latest - 1), "endIndex": max(0, n - 1), "top": float(max(entry_series[-1], tp3_series[-1])), "bottom": float(min(entry_series[-1], tp3_series[-1])), "fillColor": "rgba(38,166,154,0.10)", "borderColor": "rgba(0,0,0,0)", "borderSize": 0})

tables = []
if show_dashboard and n:
    last = n - 1
    bias = "BULLISH" if rising[last] and adx_pass[last] else "BEARISH" if falling[last] and adx_pass[last] else "NEUTRAL"
    rows = [
        ["COPPOCK CURVE", resolved_mtf_timeframe if use_mtf_filter else chart_timeframe],
        ["Lock", "ACTIVE" if lock_signal else "OFF"],
        ["Value", f"{coppock[last]:.4f}" if np.isfinite(coppock[last]) else "—"],
        ["Bias", bias],
        ["Long ROC", f"{roc_long_values[last]:.2f}" if np.isfinite(roc_long_values[last]) else "—"],
        ["Short ROC", f"{roc_short_values[last]:.2f}" if np.isfinite(roc_short_values[last]) else "—"],
        ["Zero", "ABOVE" if np.isfinite(coppock[last]) and coppock[last] >= 0 else "BELOW"],
    ]
    if show_levels:
        rows.extend([["Entry", f"{entry_series[last]:.4f}" if np.isfinite(entry_series[last]) else "—"], ["SL", f"{stop_series[last]:.4f}" if np.isfinite(stop_series[last]) else "—"], ["TP1", f"{tp1_series[last]:.4f}" if np.isfinite(tp1_series[last]) else "—"], ["TP2", f"{tp2_series[last]:.4f}" if np.isfinite(tp2_series[last]) else "—"], ["TP3", f"{tp3_series[last]:.4f}" if np.isfinite(tp3_series[last]) else "—"]])
    tables.append({"position": dashboard_position if dashboard_position in ("top-right", "top-left", "bottom-right", "bottom-left") else "top-right", "backgroundColor": "rgba(10,15,26,0.94)", "borderColor": "rgba(58,42,109,0.70)", "textColor": "#FFFFFF", "accentColor": bullish_color if bias == "BULLISH" else bearish_color if bias == "BEARISH" else neutral_color, "rows": rows})

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
        "adaptedSource": finite(adapted_source),
        "rocLong": finite(roc_long_values),
        "rocShort": finite(roc_short_values),
        "coppock": finite(coppock),
        "adx": finite(adx),
        "adxPass": [bool(value) for value in adx_pass],
        "bullDivergence": [bool(value) for value in bull_divergence],
        "bearDivergence": [bool(value) for value in bear_divergence],
        "pivotLowConfirmation": [bool(value) for value in pivot_low_confirmation],
        "pivotHighConfirmation": [bool(value) for value in pivot_high_confirmation],
        "riseStreak": [int(value) for value in rise_streak],
        "fallStreak": [int(value) for value in fall_streak],
        "volumeAverage": finite(volume_average),
        "noiseBand": finite(noise_band),
        "resolvedMtfTimeframe": resolved_mtf_timeframe,
        "mtfSourceIndex": [int(value) for value in mtf_source_index],
        "mtfBullish": [bool(value) for value in mtf_bullish],
        "bullCross": [bool(value) for value in bull_cross],
        "bearCross": [bool(value) for value in bear_cross],
        "zeroCrossUp": [bool(value) for value in zero_cross_up],
        "zeroCrossDown": [bool(value) for value in zero_cross_down],
        "entryPrice": finite(entry_series),
        "stopPrice": finite(stop_series),
        "tp1Price": finite(tp1_series),
        "tp2Price": finite(tp2_series),
        "tp3Price": finite(tp3_series),
        "tradeOpen": [bool(value) for value in trade_open_series],
        "slHit": [bool(value) for value in sl_hit],
        "tp1Hit": [bool(value) for value in tp1_hit],
        "tp2Hit": [bool(value) for value in tp2_hit],
        "tp3Hit": [bool(value) for value in tp3_hit],
    },
}
