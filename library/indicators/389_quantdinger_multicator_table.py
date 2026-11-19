# ============================================================
# QuantDinger Multicator Table
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Multicator Table"
my_indicator_description = "A configurable market dashboard with trend, momentum, volatility, volume, Fibonacci, divergence, and overlay studies."

# @param language str EN Language values=EN,TR
# @param theme str Dark Theme values=Dark,Light
# @param preset str Manual Preset values=Manual,Scalping,Swing
# @param table_mode str Desktop Table values=Off,Desktop,Mobile
# @param table_position str top-right Table position values=top-right,bottom-right,middle-right,bottom-center,bottom-left
# @param show_hud bool true Show HUD summary
# @param hud_extra bool false Include Stoch, CCI, MFI, and Williams %R in HUD
# @param show_fibonacci bool false Show automatic Fibonacci levels
# @param fibonacci_extend_right bool true Extend Fibonacci levels
# @param fibonacci_labels bool true Show Fibonacci labels
# @param fibonacci_lookback int 233 Fibonacci lookback range=8:5000:1
# @param fib_level_1 int 618 Fibonacci level 1 range=0:1618:1
# @param fib_level_2 int 382 Fibonacci level 2 range=0:1618:1
# @param fib_level_3 int 786 Fibonacci level 3 range=0:1618:1
# @param ema_1 int 50 EMA 1 range=1:1000:1
# @param show_ema_1 bool false Show EMA 1
# @param ema_2 int 100 EMA 2 range=1:1000:1
# @param show_ema_2 bool false Show EMA 2
# @param ema_3 int 200 EMA 3 range=1:1000:1
# @param show_ema_3 bool false Show EMA 3
# @param sma_1 int 50 SMA 1 range=1:1000:1
# @param show_sma_1 bool false Show SMA 1
# @param sma_2 int 100 SMA 2 range=1:1000:1
# @param show_sma_2 bool false Show SMA 2
# @param sma_3 int 200 SMA 3 range=1:1000:1
# @param show_sma_3 bool false Show SMA 3
# @param rsi_period int 14 RSI period range=1:500:1
# @param rsi_ma_period int 10 RSI average period range=1:500:1
# @param atr_period int 14 ATR period range=1:500:1
# @param momentum_period int 14 Momentum period range=1:500:1
# @param di_length int 14 DI length range=1:500:1
# @param adx_smoothing int 14 ADX smoothing range=1:500:1
# @param macd_fast int 12 MACD fast range=1:500:1
# @param macd_slow int 26 MACD slow range=1:500:1
# @param macd_signal int 9 MACD signal range=1:500:1
# @param show_sar bool false Show Parabolic SAR
# @param sar_start float 0.02 SAR start range=0.001:1:0.001
# @param sar_increment float 0.02 SAR increment range=0.001:1:0.001
# @param sar_maximum float 0.2 SAR maximum range=0.01:2:0.01
# @param show_bollinger bool false Show Bollinger bands
# @param bollinger_length int 20 Bollinger length range=1:500:1
# @param bollinger_ma_type str SMA Bollinger basis values=SMA,EMA,RMA,WMA,VWMA
# @param bollinger_multiplier float 2.0 Bollinger deviation range=0.001:50:0.1
# @param show_ichimoku bool false Show Ichimoku cloud
# @param ichimoku_conversion int 9 Conversion length range=1:500:1
# @param ichimoku_base int 26 Base length range=1:500:1
# @param ichimoku_span_b int 52 Span B length range=1:500:1
# @param ichimoku_displacement int 26 Displacement range=1:500:1
# @param show_vwap bool false Show session VWAP
# @param show_supertrend bool false Show SuperTrend
# @param supertrend_atr int 10 SuperTrend ATR range=1:500:1
# @param supertrend_factor float 3.0 SuperTrend factor range=0.1:20:0.1
# @param show_donchian bool false Show Donchian channel
# @param donchian_length int 20 Donchian length range=1:500:1
# @param show_keltner bool false Show Keltner channel
# @param keltner_length int 20 Keltner length range=1:500:1
# @param keltner_multiplier float 1.5 Keltner multiplier range=0.1:20:0.1
# @param show_hvn bool false Show highest-volume-node line
# @param hvn_lookback int 50 HVN lookback range=10:200:1
# @param stochastic_length int 14 Stochastic length range=1:500:1
# @param cci_length int 20 CCI length range=1:500:1
# @param mfi_length int 14 MFI length range=1:500:1
# @param wpr_length int 14 Williams percent R length range=1:500:1
# @param chop_length int 14 Choppiness length range=2:500:1
# @param show_divergence bool true Show RSI and MACD divergences
# @param divergence_pivot int 5 Divergence pivot width range=2:20:1
# @param show_pivots bool false Show price pivots
# @param pivot_high_left int 10 Pivot high left range=1:100:1
# @param pivot_high_right int 10 Pivot high right range=1:100:1
# @param pivot_low_left int 10 Pivot low left range=1:100:1
# @param pivot_low_right int 10 Pivot low right range=1:100:1
# @param max_pivot_labels int 24 Maximum pivot labels range=4:80:1


def _int(name, default, low=1, high=5000):
    return min(high, max(low, int(params.get(name, default))))


def _float(name, default, low=-1e12, high=1e12):
    return min(high, max(low, float(params.get(name, default))))


language = str(params.get("language", "EN")).upper(); english = language != "TR"
theme_name = str(params.get("theme", "Dark")); dark = theme_name.lower() != "light"
preset = str(params.get("preset", "Manual")); table_mode = str(params.get("table_mode", "Desktop")); table_position = str(params.get("table_position", "top-right"))
show_hud = bool(params.get("show_hud", True)); hud_extra = bool(params.get("hud_extra", False))
show_fib = bool(params.get("show_fibonacci", False)); fib_extend = bool(params.get("fibonacci_extend_right", True)); fib_labels = bool(params.get("fibonacci_labels", True)); fib_lookback = _int("fibonacci_lookback", 233, 8, 5000)
fib_levels = [_int("fib_level_2", 382, 0, 1618) / 1000.0, 0.5, _int("fib_level_1", 618, 0, 1618) / 1000.0, _int("fib_level_3", 786, 0, 1618) / 1000.0]
ema_lengths = [_int("ema_1", 50, 1, 1000), _int("ema_2", 100, 1, 1000), _int("ema_3", 200, 1, 1000)]; ema_visible = [bool(params.get("show_ema_1", False)), bool(params.get("show_ema_2", False)), bool(params.get("show_ema_3", False))]
sma_lengths = [_int("sma_1", 50, 1, 1000), _int("sma_2", 100, 1, 1000), _int("sma_3", 200, 1, 1000)]; sma_visible = [bool(params.get("show_sma_1", False)), bool(params.get("show_sma_2", False)), bool(params.get("show_sma_3", False))]
rsi_length = 7 if preset == "Scalping" else 21 if preset == "Swing" else _int("rsi_period", 14, 1, 500); rsi_ma_length = _int("rsi_ma_period", 10, 1, 500)
atr_length = _int("atr_period", 14, 1, 500); momentum_length = _int("momentum_period", 14, 1, 500)
di_length = 10 if preset == "Scalping" else _int("di_length", 14, 1, 500); adx_smoothing = _int("adx_smoothing", 14, 1, 500)
macd_fast = 8 if preset == "Scalping" else _int("macd_fast", 12, 1, 500); macd_slow = 17 if preset == "Scalping" else _int("macd_slow", 26, 1, 500); macd_signal_length = 9 if preset == "Scalping" else _int("macd_signal", 9, 1, 500)
show_sar = bool(params.get("show_sar", False)); sar_start = _float("sar_start", 0.02, 0.001, 1); sar_increment = _float("sar_increment", 0.02, 0.001, 1); sar_maximum = _float("sar_maximum", 0.2, 0.01, 2)
show_bb = bool(params.get("show_bollinger", False)); bb_length = _int("bollinger_length", 20, 1, 500); bb_type = str(params.get("bollinger_ma_type", "SMA")).upper(); bb_multiplier = _float("bollinger_multiplier", 2.0, 0.001, 50)
show_ichimoku = bool(params.get("show_ichimoku", False)); conversion_length = _int("ichimoku_conversion", 9, 1, 500); base_length = _int("ichimoku_base", 26, 1, 500); span_b_length = _int("ichimoku_span_b", 52, 1, 500); displacement = _int("ichimoku_displacement", 26, 1, 500)
show_vwap = bool(params.get("show_vwap", False)); show_supertrend = bool(params.get("show_supertrend", False)); st_atr_length = 7 if preset == "Scalping" else 10 if preset == "Swing" else _int("supertrend_atr", 10, 1, 500); st_factor = 2.5 if preset == "Scalping" else 3.0 if preset == "Swing" else _float("supertrend_factor", 3.0, 0.1, 20)
show_donchian = bool(params.get("show_donchian", False)); donchian_length = _int("donchian_length", 20, 1, 500); show_keltner = bool(params.get("show_keltner", False)); keltner_length = _int("keltner_length", 20, 1, 500); keltner_multiplier = _float("keltner_multiplier", 1.5, 0.1, 20)
show_hvn = bool(params.get("show_hvn", False)); hvn_lookback = _int("hvn_lookback", 50, 10, 200); stochastic_length = 8 if preset == "Scalping" else 14 if preset == "Swing" else _int("stochastic_length", 14, 1, 500)
cci_length = _int("cci_length", 20, 1, 500); mfi_length = _int("mfi_length", 14, 1, 500); wpr_length = _int("wpr_length", 14, 1, 500); chop_length = _int("chop_length", 14, 2, 500)
show_divergence = bool(params.get("show_divergence", True)); divergence_pivot = _int("divergence_pivot", 5, 2, 20); show_pivots = bool(params.get("show_pivots", False)); pivot_high_left = _int("pivot_high_left", 10, 1, 100); pivot_high_right = _int("pivot_high_right", 10, 1, 100); pivot_low_left = _int("pivot_low_left", 10, 1, 100); pivot_low_right = _int("pivot_low_right", 10, 1, 100); max_pivots = _int("max_pivot_labels", 24, 4, 80)

palette = {"text": "#EAFFFF" if dark else "#0B1C28", "background": "#070B14" if dark else "#F4F7FB", "header": "#031018" if dark else "#E4EEF5", "frame": "#00F3FF" if dark else "#0B4F6C", "bull": "#00FF9C" if dark else "#166534", "bear": "#FF2D6A" if dark else "#9F1239", "neutral": "#0D1524" if dark else "#E8EEF4"}
ema_colors = ["#F8FF32", "#00F3FF", "#FF4DFF"] if dark else ["#7A5B00", "#063A8C", "#5B21B6"]
sma_colors = ["#00FF9C", "#39FF14", "#FF2D6A"] if dark else ["#14532D", "#0F766E", "#9F1239"]


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, length):
    result = np.full(len(values), np.nan)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)): result[index] = float(np.mean(window))
    return result


def _ema(values, length):
    result = np.full(len(values), np.nan); state = np.nan; alpha = 2.0 / (length + 1.0)
    for index, value in enumerate(values):
        if not np.isfinite(value): continue
        state = float(value) if not np.isfinite(state) else state + alpha * (float(value) - state)
        result[index] = state
    return result


def _rma(values, length):
    result = np.full(len(values), np.nan); state = np.nan; seed = []
    for index, value in enumerate(values):
        if not np.isfinite(value): continue
        if not np.isfinite(state):
            seed.append(float(value))
            if len(seed) == length: state = float(np.mean(seed)); result[index] = state
        else:
            state = (state * (length - 1.0) + float(value)) / length; result[index] = state
    return result


def _wma(values, length):
    result = np.full(len(values), np.nan); weights = np.arange(1, length + 1, dtype=float); total = float(np.sum(weights))
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)): result[index] = float(np.sum(window * weights) / total)
    return result


def _rolling_extreme(values, length, maximum):
    result = np.full(len(values), np.nan)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]; result[index] = float(np.max(window) if maximum else np.min(window))
    return result


def _pivot(values, left, right, high_pivot):
    result = np.full(len(values), np.nan)
    for confirmed in range(left + right, len(values)):
        pivot_index = confirmed - right; window = values[pivot_index - left:pivot_index + right + 1]; pivot_value = values[pivot_index]
        if (pivot_value == np.max(window) if high_pivot else pivot_value == np.min(window)): result[confirmed] = pivot_value
    return result


def _cross_up(first, second):
    result = np.zeros(len(first), bool)
    for index in range(1, len(first)): result[index] = np.all(np.isfinite([first[index], second[index], first[index-1], second[index-1]])) and first[index] > second[index] and first[index-1] <= second[index-1]
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns: df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
times = df["time"].to_numpy(float); opened, high, low, close, volume = (df[key].to_numpy(float) for key in ("open", "high", "low", "close", "volume")); volume = np.nan_to_num(volume, nan=0.0); n = len(df); hl2 = (high + low) / 2.0; hlc3 = (high + low + close) / 3.0
time_ms = np.where(np.abs(times) < 100000000000.0, times * 1000.0, times); previous_close = np.r_[np.nan, close[:-1]]
true_range = np.maximum(high - low, np.maximum(np.abs(high - previous_close), np.abs(low - previous_close))) if n else np.asarray([], float)
if n: true_range[0] = high[0] - low[0]
ema_values = [_ema(close, length) for length in ema_lengths]; sma_values = [_sma(close, length) for length in sma_lengths]; ema_regime = _ema(close, 200)
change_pct = np.full(n, np.nan)
if n > 1: change_pct[1:] = np.where(close[:-1] != 0, np.diff(close) / close[:-1] * 100.0, np.nan)
delta = np.diff(close, prepend=np.nan); average_gain = _rma(np.where(np.isfinite(delta), np.maximum(delta, 0.0), np.nan), rsi_length); average_loss = _rma(np.where(np.isfinite(delta), np.maximum(-delta, 0.0), np.nan), rsi_length); rsi = np.full(n, np.nan)
for index in range(n):
    if np.all(np.isfinite([average_gain[index], average_loss[index]])): rsi[index] = 100.0 if average_loss[index] == 0 else 0.0 if average_gain[index] == 0 else 100.0 - 100.0 / (1.0 + average_gain[index] / average_loss[index])
rsi_average = _sma(rsi, rsi_ma_length); atr = _rma(true_range, atr_length); momentum = np.full(n, np.nan)
if n > momentum_length: momentum[momentum_length:] = close[momentum_length:] - close[:-momentum_length]
up_move = np.diff(high, prepend=np.nan); down_move = -np.diff(low, prepend=np.nan); plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0); minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0); di_atr = _rma(true_range, di_length); plus_di = 100.0 * _rma(plus_dm, di_length) / di_atr; minus_di = 100.0 * _rma(minus_dm, di_length) / di_atr; di_total = plus_di + minus_di; dx = np.where(di_total != 0, 100.0 * np.abs(plus_di - minus_di) / di_total, np.nan); adx = _rma(dx, adx_smoothing)
macd_line = _ema(close, macd_fast) - _ema(close, macd_slow); macd_signal = _ema(macd_line, macd_signal_length); macd_hist = macd_line - macd_signal


def _parabolic_sar():
    result = np.full(n, np.nan)
    if n < 2: return result
    rising = close[1] > close[0]; extreme = high[1] if rising else low[1]; state = low[0] if rising else high[0]; acceleration = sar_start; result[1] = state
    for index in range(2, n):
        state += acceleration * (extreme - state)
        if rising:
            state = min(state, low[index-1], low[index-2])
            if low[index] < state: rising = False; state = extreme; extreme = low[index]; acceleration = sar_start
            elif high[index] > extreme: extreme = high[index]; acceleration = min(sar_maximum, acceleration + sar_increment)
        else:
            state = max(state, high[index-1], high[index-2])
            if high[index] > state: rising = True; state = extreme; extreme = high[index]; acceleration = sar_start
            elif low[index] < extreme: extreme = low[index]; acceleration = min(sar_maximum, acceleration + sar_increment)
        result[index] = state
    return result


sar = _parabolic_sar(); vol_ma = _sma(volume, 20); relative_volume = np.where(np.isfinite(vol_ma) & (vol_ma != 0), volume / vol_ma, 1.0)
stoch_high = _rolling_extreme(high, stochastic_length, True); stoch_low = _rolling_extreme(low, stochastic_length, False); stoch_range = stoch_high - stoch_low; stochastic_k = np.where(stoch_range != 0, 100.0 * (close - stoch_low) / stoch_range, np.nan); stochastic_d = _sma(stochastic_k, 3)
cci_basis = _sma(hlc3, cci_length); cci_deviation = np.full(n, np.nan)
for index in range(cci_length - 1, n): cci_deviation[index] = float(np.mean(np.abs(hlc3[index-cci_length+1:index+1] - cci_basis[index])))
cci = np.where(cci_deviation != 0, (hlc3 - cci_basis) / (0.015 * cci_deviation), np.nan)
raw_flow = hlc3 * volume; positive_flow = np.zeros(n); negative_flow = np.zeros(n)
for index in range(1, n):
    if hlc3[index] > hlc3[index-1]: positive_flow[index] = raw_flow[index]
    elif hlc3[index] < hlc3[index-1]: negative_flow[index] = raw_flow[index]
positive_sum = np.full(n, np.nan); negative_sum = np.full(n, np.nan)
for index in range(mfi_length - 1, n): positive_sum[index] = float(np.sum(positive_flow[index-mfi_length+1:index+1])); negative_sum[index] = float(np.sum(negative_flow[index-mfi_length+1:index+1]))
mfi = np.full(n, np.nan)
for index in range(n):
    if np.isfinite(positive_sum[index]) and np.isfinite(negative_sum[index]): mfi[index] = 100.0 if negative_sum[index] == 0 else 100.0 - 100.0 / (1.0 + positive_sum[index] / negative_sum[index])
wpr_high = _rolling_extreme(high, wpr_length, True); wpr_low = _rolling_extreme(low, wpr_length, False); wpr_range = wpr_high - wpr_low; wpr = np.where(wpr_range != 0, -100.0 * (wpr_high - close) / wpr_range, np.nan)
obv = np.zeros(n)
for index in range(1, n): obv[index] = obv[index-1] + (volume[index] if close[index] > close[index-1] else -volume[index] if close[index] < close[index-1] else 0.0)
vwap = np.full(n, np.nan); cumulative_pv = cumulative_volume = 0.0; previous_day = None
for index in range(n):
    day = int(time_ms[index] // 86400000.0)
    if day != previous_day: cumulative_pv = cumulative_volume = 0.0; previous_day = day
    cumulative_pv += hlc3[index] * volume[index]; cumulative_volume += volume[index]; vwap[index] = cumulative_pv / cumulative_volume if cumulative_volume else np.nan


def _supertrend():
    local_atr = _rma(true_range, st_atr_length); lower = np.full(n, np.nan); upper = np.full(n, np.nan); direction = np.ones(n, int); line = np.full(n, np.nan)
    for index in range(n):
        if not np.isfinite(local_atr[index]): continue
        basic_lower = hl2[index] - st_factor * local_atr[index]; basic_upper = hl2[index] + st_factor * local_atr[index]
        if index == 0 or not np.isfinite(local_atr[index-1]): lower[index], upper[index] = basic_lower, basic_upper
        else:
            lower[index] = basic_lower if basic_lower > lower[index-1] or close[index-1] < lower[index-1] else lower[index-1]
            upper[index] = basic_upper if basic_upper < upper[index-1] or close[index-1] > upper[index-1] else upper[index-1]
            if not np.isfinite(line[index-1]): direction[index] = 1
            elif line[index-1] == upper[index-1]: direction[index] = -1 if close[index] > upper[index] else 1
            else: direction[index] = 1 if close[index] < lower[index] else -1
        line[index] = lower[index] if direction[index] == -1 else upper[index]
    return line, direction


supertrend, supertrend_direction = _supertrend(); donchian_high = _rolling_extreme(high, donchian_length, True); donchian_low = _rolling_extreme(low, donchian_length, False); donchian_mid = (donchian_high + donchian_low) / 2.0
keltner_basis = _ema(close, keltner_length); keltner_deviation = keltner_multiplier * _rma(true_range, keltner_length); keltner_upper = keltner_basis + keltner_deviation; keltner_lower = keltner_basis - keltner_deviation
atr_one = _rma(true_range, 1); chop_high = _rolling_extreme(high, chop_length, True); chop_low = _rolling_extreme(low, chop_length, False); atr_sum = np.full(n, np.nan)
for index in range(chop_length - 1, n): atr_sum[index] = float(np.sum(atr_one[index-chop_length+1:index+1]))
chop_denominator = np.maximum(chop_high - chop_low, 1e-12); choppiness = 100.0 * np.log(np.maximum(atr_sum, 1e-12) / chop_denominator) / np.log(chop_length)
hvn_price = np.full(n, np.nan); hvn_volume = np.full(n, np.nan)
for index in range(n):
    start_index = max(0, index - hvn_lookback + 1); offset = int(np.argmax(volume[start_index:index+1])); selected = start_index + offset; hvn_price[index] = close[selected]; hvn_volume[index] = volume[selected]

bb_sma = _sma(close, bb_length); bb_ema = _ema(close, bb_length); bb_rma = _rma(close, bb_length); bb_wma = _wma(close, bb_length); volume_average = _sma(volume, bb_length); bb_vwma = np.full(n, np.nan)
for index in range(bb_length - 1, n):
    total_volume = float(np.sum(volume[index-bb_length+1:index+1])); bb_vwma[index] = float(np.sum(close[index-bb_length+1:index+1] * volume[index-bb_length+1:index+1]) / total_volume) if total_volume else np.nan
bb_basis = {"SMA": bb_sma, "EMA": bb_ema, "RMA": bb_rma, "SMMA (RMA)": bb_rma, "WMA": bb_wma, "VWMA": bb_vwma}.get(bb_type, bb_sma); bb_std = np.full(n, np.nan)
for index in range(bb_length - 1, n): bb_std[index] = float(np.std(close[index-bb_length+1:index+1], ddof=0))
bb_upper = bb_basis + bb_multiplier * bb_std; bb_lower = bb_basis - bb_multiplier * bb_std
conversion = (_rolling_extreme(high, conversion_length, True) + _rolling_extreme(low, conversion_length, False)) / 2.0; base = (_rolling_extreme(high, base_length, True) + _rolling_extreme(low, base_length, False)) / 2.0; span_a = (conversion + base) / 2.0; span_b = (_rolling_extreme(high, span_b_length, True) + _rolling_extreme(low, span_b_length, False)) / 2.0

votes = [(close > ema_regime), (adx > 25), (macd_line > macd_signal), ((rsi >= 40) & (rsi <= 65)), (supertrend_direction < 0), (stochastic_k > 50), (mfi > 50), (relative_volume >= 1), (close > donchian_mid)]
score = np.rint(np.sum(np.asarray(votes, dtype=int), axis=0) / 9.0 * 100.0).astype(int); range_regime = (adx < 20) | (choppiness > 61.8)

price_pivot_high = _pivot(high, pivot_high_left, pivot_high_right, True); price_pivot_low = _pivot(low, pivot_low_left, pivot_low_right, False); divergence_high = _pivot(high, divergence_pivot, divergence_pivot, True); divergence_low = _pivot(low, divergence_pivot, divergence_pivot, False)
bull_rsi = np.zeros(n, bool); bear_rsi = np.zeros(n, bool); bull_macd = np.zeros(n, bool); bear_macd = np.zeros(n, bool); last_high = last_high_rsi = last_high_macd = last_low = last_low_rsi = last_low_macd = np.nan
for index in range(n):
    pivot_index = index - divergence_pivot
    if np.isfinite(divergence_high[index]) and pivot_index >= 0:
        current_rsi, current_macd = rsi[pivot_index], macd_line[pivot_index]
        if np.isfinite(last_high): bear_rsi[index] = divergence_high[index] > last_high and current_rsi < last_high_rsi; bear_macd[index] = divergence_high[index] > last_high and current_macd < last_high_macd
        last_high, last_high_rsi, last_high_macd = divergence_high[index], current_rsi, current_macd
    if np.isfinite(divergence_low[index]) and pivot_index >= 0:
        current_rsi, current_macd = rsi[pivot_index], macd_line[pivot_index]
        if np.isfinite(last_low): bull_rsi[index] = divergence_low[index] < last_low and current_rsi > last_low_rsi; bull_macd[index] = divergence_low[index] < last_low and current_macd > last_low_macd
        last_low, last_low_rsi, last_low_macd = divergence_low[index], current_rsi, current_macd

layers = []
if n and show_fib:
    start = max(0, n - fib_lookback); window_high = high[start:]; window_low = low[start:]; high_offset = int(np.argmax(window_high)); low_offset = int(np.argmin(window_low)); highest = float(window_high[high_offset]); lowest = float(window_low[low_offset]); high_index = start + high_offset; low_index = start + low_offset; low_more_recent = low_index > high_index; end_index = n + 50 if fib_extend else n - 1
    levels = [("Fib High", highest, high_index, "solid"), ("Fib Low", lowest, low_index, "solid")]
    for ratio in fib_levels:
        value = highest - (highest - lowest) * ratio if low_more_recent else lowest + (highest - lowest) * ratio; levels.append((f"Fib {ratio:g}", value, n + 10, "dashed"))
    for role, value, first_index, style in levels:
        layers.append({"type": "line", "role": role, "startIndex": int(first_index), "endIndex": int(end_index), "y1": float(value), "y2": float(value), "color": "#00F3FF" if dark else "#334155", "lineWidth": 1, "lineStyle": style})
        if fib_labels: layers.append({"type": "label", "role": role + " Label", "index": int(n + 55), "price": float(value), "text": f"{role}: {value:.6g}", "color": "rgba(0,0,0,0)", "textColor": palette["text"], "size": "tiny"})
if n and show_hvn and np.isfinite(hvn_price[-1]): layers.append({"type": "line", "role": "Highest Volume Node", "startIndex": max(0, n - hvn_lookback), "endIndex": n + 10, "y1": float(hvn_price[-1]), "y2": float(hvn_price[-1]), "color": "#FFB800", "lineWidth": 1, "lineStyle": "dotted"})
if show_pivots:
    candidates = []
    for index in range(n):
        if np.isfinite(price_pivot_high[index]): candidates.append((index - pivot_high_right, float(price_pivot_high[index]), "Pivot High"))
        if np.isfinite(price_pivot_low[index]): candidates.append((index - pivot_low_right, float(price_pivot_low[index]), "Pivot Low"))
    for pivot_index, price, role in candidates[-max_pivots:]: layers.append({"type": "label", "role": role, "index": int(pivot_index), "price": price, "text": f"{price:.6g}", "color": palette["background"], "textColor": palette["text"], "size": "tiny"})

def _cell(text, color=None):
    result = {"text": str(text)}
    if color is not None: result["color"] = color
    return result


tables = []
if n and table_mode.lower() != "off":
    last = n - 1; regime = ("Range" if english else "Yatay") if range_regime[last] else (("Trend up" if english else "Trend yukarı") if close[last] > ema_regime[last] else ("Trend down" if english else "Trend aşağı")); change_text = f"{change_pct[last]:+.2f}%" if np.isfinite(change_pct[last]) else "n/a"; trend_color = palette["neutral"] if range_regime[last] else palette["bull"] if close[last] > ema_regime[last] else palette["bear"]
    values = {"Open": opened[last], "Close": close[last], "Change": change_text, "RSI": rsi[last], "ATR": atr[last], "Volume": volume[last], "ADX": adx[last], "MOM": momentum[last], "MACD": macd_line[last], "+DI/-DI": f"{plus_di[last]:.2f}/{minus_di[last]:.2f}", "Score": int(score[last]), "Regime": regime, "Stoch": stochastic_k[last], "CCI": cci[last], "MFI": mfi[last], "%R": wpr[last], "OBV": obv[last], "VWAP": vwap[last], "ST": "UP" if supertrend_direction[last] < 0 else "DOWN", "RelVol": f"{relative_volume[last]:.2f}x", "HVN": hvn_price[last], "Chop": choppiness[last], "SAR": sar[last], "Hist": macd_hist[last]}
    if table_mode.lower() == "mobile":
        order = ["Open", "Close", "Change", "RSI", "Score", "Regime", f"SMA{sma_lengths[0]}", f"EMA{ema_lengths[0]}", "ADX", "MACD", "Stoch", "CCI", "MFI", "%R", "VWAP", "ST", "RelVol", "HVN", "Chop", "OBV", "Volume", "MOM"]
        values[f"SMA{sma_lengths[0]}"] = sma_values[0][last]; values[f"EMA{ema_lengths[0]}"] = ema_values[0][last]
        rows = [[_cell(name), _cell(f"{values[name]:.6g}" if isinstance(values[name], (float, np.floating)) and np.isfinite(values[name]) else values[name])] for name in order]
    else:
        rows = [[_cell("Open"), _cell("Close"), _cell("Change"), _cell("RSI"), _cell("ATR"), _cell("Volume")], [_cell(f"{opened[last]:.6g}"), _cell(f"{close[last]:.6g}"), _cell(change_text), _cell(f"{rsi[last]:.2f}"), _cell(f"{atr[last]:.6g}"), _cell(f"{volume[last]:.6g}")], [_cell(f"SMA{x}") for x in sma_lengths] + [_cell(f"EMA{x}") for x in ema_lengths], [_cell(f"{x[last]:.6g}" if np.isfinite(x[last]) else "n/a", palette["bear"] if np.isfinite(x[last]) and x[last] > close[last] else palette["bull"]) for x in sma_values + ema_values], [_cell("ADX"), _cell("MOM"), _cell("MACD"), _cell("+DI/-DI"), _cell("Score"), _cell("Regime")], [_cell(f"{adx[last]:.2f}"), _cell(f"{momentum[last]:.6g}"), _cell(f"{macd_line[last]:.6g}"), _cell(values["+DI/-DI"]), _cell(score[last]), _cell(regime, trend_color)], [_cell("Stoch"), _cell("CCI"), _cell("MFI"), _cell("%R"), _cell("OBV"), _cell("VWAP")], [_cell(f"{stochastic_k[last]:.2f}"), _cell(f"{cci[last]:.2f}"), _cell(f"{mfi[last]:.2f}"), _cell(f"{wpr[last]:.2f}"), _cell(f"{obv[last]:.6g}"), _cell(f"{vwap[last]:.6g}")], [_cell("ST"), _cell("RelVol"), _cell("HVN"), _cell("Chop"), _cell("SAR"), _cell("Hist")], [_cell(values["ST"]), _cell(values["RelVol"]), _cell(f"{hvn_price[last]:.6g}"), _cell(f"{choppiness[last]:.2f}"), _cell(f"{sar[last]:.6g}"), _cell(f"{macd_hist[last]:.6g}")]]
    tables.append({"title": "Multicator", "position": table_position, "compact": True, "theme": theme_name, "rows": rows})
if n and show_hud:
    hud_items = [("RSI", rsi[-1]), ("MACD", macd_line[-1]), ("ADX", adx[-1]), ("MOM", momentum[-1]), ("ATR", atr[-1])]
    if hud_extra: hud_items += [("Stoch", stochastic_k[-1]), ("CCI", cci[-1]), ("MFI", mfi[-1]), ("%R", wpr[-1])]
    tables.append({"title": "Indicator HUD", "position": "middle-right", "compact": True, "theme": theme_name, "rows": [[_cell(name), _cell(f"{value:.6g}" if np.isfinite(value) else "n/a")] for name, value in hud_items]})

transparent = "rgba(0,0,0,0)"; plots = []
for index in range(3): plots.append({"name": f"EMA {index + 1}", "label": f"EMA {ema_lengths[index]}", "data": _finite(ema_values[index]) if ema_visible[index] else [None] * n, "color": ema_colors[index], "type": "line", "lineWidth": 2, "overlay": True})
for index in range(3): plots.append({"name": f"SMA {index + 1}", "label": f"SMA {sma_lengths[index]}", "data": _finite(sma_values[index]) if sma_visible[index] else [None] * n, "color": sma_colors[index], "type": "line", "lineWidth": 2, "overlay": True})
plots += [
    {"name": "Parabolic SAR", "data": _finite(sar) if show_sar else [None] * n, "color": "#00F3FF", "type": "scatter", "lineWidth": 2, "overlay": True},
    {"name": "Bollinger Basis", "data": _finite(bb_basis) if show_bb else [None] * n, "color": "#FFB800", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Bollinger Upper", "data": _finite(bb_upper) if show_bb else [None] * n, "color": "#00F3FF", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Bollinger Lower", "data": _finite(bb_lower) if show_bb else [None] * n, "color": "#00F3FF", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Bollinger Cloud", "title": False, "data": [{"upper": float(bb_upper[i]), "lower": float(bb_lower[i]), "color": "#00F3FF", "opacity": 0.12} if show_bb and np.all(np.isfinite([bb_upper[i], bb_lower[i]])) else None for i in range(n)], "color": "#00F3FF", "type": "cloud", "overlay": True},
    {"name": "Ichimoku Conversion", "data": _finite(conversion) if show_ichimoku else [None] * n, "color": "#00F3FF", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Ichimoku Base", "data": _finite(base) if show_ichimoku else [None] * n, "color": "#FF2D6A", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Ichimoku Lagging", "data": _finite(close) if show_ichimoku else [None] * n, "color": "#00FF9C", "type": "line", "lineWidth": 2, "offset": -displacement + 1, "overlay": True},
    {"name": "Ichimoku Span A", "data": _finite(span_a) if show_ichimoku else [None] * n, "color": "#00FF9C", "type": "line", "lineWidth": 1, "offset": displacement - 1, "overlay": True},
    {"name": "Ichimoku Span B", "data": _finite(span_b) if show_ichimoku else [None] * n, "color": "#FF2D6A", "type": "line", "lineWidth": 1, "offset": displacement - 1, "overlay": True},
    {"name": "Ichimoku Cloud", "title": False, "data": [{"upper": float(max(span_a[i], span_b[i])), "lower": float(min(span_a[i], span_b[i])), "color": palette["bull"] if span_a[i] > span_b[i] else palette["bear"], "opacity": 0.18} if show_ichimoku and np.all(np.isfinite([span_a[i], span_b[i]])) else None for i in range(n)], "color": palette["bull"], "type": "cloud", "offset": displacement - 1, "overlay": True},
    {"name": "VWAP", "data": _finite(vwap) if show_vwap else [None] * n, "color": "#FFB800", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "SuperTrend", "data": _finite(supertrend) if show_supertrend else [None] * n, "color": [palette["bull"] if value < 0 else palette["bear"] for value in supertrend_direction], "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Donchian High", "data": _finite(donchian_high) if show_donchian else [None] * n, "color": "#00F3FF", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Donchian Low", "data": _finite(donchian_low) if show_donchian else [None] * n, "color": "#00F3FF", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Keltner Upper", "data": _finite(keltner_upper) if show_keltner else [None] * n, "color": "#FF4DFF", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Keltner Basis", "data": _finite(keltner_basis) if show_keltner else [None] * n, "color": "#FF4DFF", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Keltner Lower", "data": _finite(keltner_lower) if show_keltner else [None] * n, "color": "#FF4DFF", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Price Guide", "title": False, "data": _finite(close), "color": transparent, "type": "line", "overlay": True},
]
for plot in plots:
    plot["title"] = False

signals = [
    {"type": "buy", "name": "RSI Bullish Divergence", "text": "RSI▲", "data": [{"active": True, "price": float(low[i]), "text": "RSI▲", "color": palette["bull"]} if show_divergence and bull_rsi[i] else None for i in range(n)], "color": palette["bull"], "renderMode": "events", "overlay": True},
    {"type": "sell", "name": "RSI Bearish Divergence", "text": "RSI▼", "data": [{"active": True, "price": float(high[i]), "text": "RSI▼", "color": palette["bear"]} if show_divergence and bear_rsi[i] else None for i in range(n)], "color": palette["bear"], "renderMode": "events", "overlay": True},
    {"type": "buy", "name": "MACD Bullish Divergence", "text": "M◆", "data": [{"active": True, "price": float(low[i]), "text": "M◆", "color": "#00F3FF"} if show_divergence and bull_macd[i] else None for i in range(n)], "color": "#00F3FF", "renderMode": "events", "overlay": True},
    {"type": "sell", "name": "MACD Bearish Divergence", "text": "M◆", "data": [{"active": True, "price": float(high[i]), "text": "M◆", "color": "#FFB800"} if show_divergence and bear_macd[i] else None for i in range(n)], "color": "#FFB800", "renderMode": "events", "overlay": True},
]

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers, "tables": tables, "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {"ema1": _finite(ema_values[0]), "ema2": _finite(ema_values[1]), "ema3": _finite(ema_values[2]), "sma1": _finite(sma_values[0]), "sma2": _finite(sma_values[1]), "sma3": _finite(sma_values[2]), "emaRegime": _finite(ema_regime), "changePct": _finite(change_pct), "rsi": _finite(rsi), "rsiAverage": _finite(rsi_average), "atr": _finite(atr), "momentum": _finite(momentum), "plusDi": _finite(plus_di), "minusDi": _finite(minus_di), "adx": _finite(adx), "macdLine": _finite(macd_line), "macdSignal": _finite(macd_signal), "macdHistogram": _finite(macd_hist), "sar": _finite(sar), "relativeVolume": _finite(relative_volume), "stochasticK": _finite(stochastic_k), "stochasticD": _finite(stochastic_d), "cci": _finite(cci), "mfi": _finite(mfi), "williamsR": _finite(wpr), "obv": _finite(obv), "vwap": _finite(vwap), "superTrend": _finite(supertrend), "superTrendDirection": supertrend_direction.tolist(), "donchianHigh": _finite(donchian_high), "donchianLow": _finite(donchian_low), "donchianMid": _finite(donchian_mid), "keltnerBasis": _finite(keltner_basis), "keltnerUpper": _finite(keltner_upper), "keltnerLower": _finite(keltner_lower), "choppiness": _finite(choppiness), "hvnPrice": _finite(hvn_price), "hvnVolume": _finite(hvn_volume), "bollingerBasis": _finite(bb_basis), "bollingerUpper": _finite(bb_upper), "bollingerLower": _finite(bb_lower), "ichimokuConversion": _finite(conversion), "ichimokuBase": _finite(base), "ichimokuSpanA": _finite(span_a), "ichimokuSpanB": _finite(span_b), "score": score.tolist(), "rangeRegime": range_regime.tolist(), "bullRsiDivergence": bull_rsi.tolist(), "bearRsiDivergence": bear_rsi.tolist(), "bullMacdDivergence": bull_macd.tolist(), "bearMacdDivergence": bear_macd.tolist(), "rsiOverbought": (rsi >= 70).tolist(), "rsiOversold": (rsi <= 30).tolist(), "macdCrossUp": _cross_up(macd_line, macd_signal).tolist(), "macdCrossDown": _cross_up(macd_signal, macd_line).tolist(), "emaCrossUp": _cross_up(close, ema_regime).tolist(), "emaCrossDown": _cross_up(ema_regime, close).tolist()},
}
