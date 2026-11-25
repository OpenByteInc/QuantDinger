# ============================================================
# QuantDinger Pro Scalper AI
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Pro Scalper AI"
my_indicator_description = "Combines ADX trend, ATR volatility, selectable momentum, normalized OBV, adaptive weighting and optional regression bias into immediate or latched scalping states."
indicator_timeframe_param = "higher_timeframe"
indicator_timeframes = ["1m", "5m", "15m", "30m", "1H", "4H", "1D", "1W"]

# @param use_higher_timeframe bool false Use higher-timeframe trend filter
# @param higher_timeframe str 15m Higher timeframe values=5m,15m,30m,1H,4H,1D,1W
# @param use_macd_filter bool false Reserved MACD filter
# @param use_pinbar_filter bool false Reserved pin-bar filter
# @param adx_length int 14 ADX length range=1:200:1
# @param di_length int 14 DI length range=1:200:1
# @param atr_length int 14 ATR length range=1:200:1
# @param hma_length int 21 HMA length range=1:500:1
# @param momentum_mode str Stochastic_RSI Momentum mode values=Stochastic_RSI,Fisher_RSI,Williams_R
# @param stochastic_rsi_length int 12 Stochastic RSI length range=1:200:1
# @param stochastic_k_length int 3 Stochastic RSI smoothing range=1:100:1
# @param stochastic_d_length int 3 Reserved stochastic D smoothing range=1:100:1
# @param fisher_rsi_length int 14 Fisher RSI length range=1:200:1
# @param williams_length int 14 Williams percent R length range=1:200:1
# @param tdi_rsi_length int 13 TDI RSI reference length range=1:200:1
# @param tdi_signal_length int 7 TDI signal reference length range=1:200:1
# @param trend_weight float 0.3 Trend weight range=-10:10:0.1
# @param momentum_weight float 0.3 Momentum weight range=-10:10:0.1
# @param volatility_weight float 0.2 Volatility weight range=-10:10:0.1
# @param volume_weight float 0.2 Volume weight range=-10:10:0.1
# @param upper_threshold float 25.0 Base upper threshold range=-1000:1000:0.1
# @param lower_threshold float -25.0 Base lower threshold range=-1000:1000:0.1
# @param threshold_multiplier float 0.5 Dynamic threshold multiplier range=-10:10:0.1
# @param oscillator_lookback int 50 Oscillator deviation lookback range=1:1000:1
# @param use_hysteresis bool true Reserved adaptive hysteresis
# @param hysteresis_gap float 1.0 Reserved hysteresis gap range=-100:100:0.1
# @param hysteresis_multiplier float 1.0 Reserved hysteresis volatility multiplier range=-100:100:0.1
# @param use_dynamic_weights bool true Use dynamic trend and volatility weights
# @param maximum_volatility_factor float 2.0 Maximum volatility factor range=-100:100:0.1
# @param maximum_trend_factor float 1.5 Maximum trend factor range=-100:100:0.1
# @param volume_lookback int 50 OBV normalization lookback range=1:1000:1
# @param use_ai_forecast bool false Use linear-regression forecast bias
# @param ai_length int 20 Forecast length range=1:1000:1
# @param ai_future_bars int 2 Forecast bars ahead range=1:1000:1
# @param ai_threshold_shift float 1.5 Forecast threshold shift range=-100:100:0.1
# @param smooth_ai_forecast bool true Smooth the forecast
# @param use_latching bool true Use latched signal state
# @param show_dashboard bool true Show dashboard


def _int(name, default, low=1, high=1000):
    return min(high, max(low, int(params.get(name, default))))


def _float(name, default, low=-1000.0, high=1000.0):
    return min(high, max(low, float(params.get(name, default))))


use_higher_timeframe = bool(params.get("use_higher_timeframe", False))
higher_timeframe = str(params.get("higher_timeframe", "15m"))
use_macd_filter = bool(params.get("use_macd_filter", False))
use_pinbar_filter = bool(params.get("use_pinbar_filter", False))
adx_length = _int("adx_length", 14, 1, 200)
di_length = _int("di_length", 14, 1, 200)
atr_length = _int("atr_length", 14, 1, 200)
hma_length = _int("hma_length", 21, 1, 500)
momentum_mode = str(params.get("momentum_mode", "Stochastic_RSI")).replace("_", " ")
stochastic_rsi_length = _int("stochastic_rsi_length", 12, 1, 200)
stochastic_k_length = _int("stochastic_k_length", 3, 1, 100)
stochastic_d_length = _int("stochastic_d_length", 3, 1, 100)
fisher_rsi_length = _int("fisher_rsi_length", 14, 1, 200)
williams_length = _int("williams_length", 14, 1, 200)
tdi_rsi_length = _int("tdi_rsi_length", 13, 1, 200)
tdi_signal_length = _int("tdi_signal_length", 7, 1, 200)
trend_weight = _float("trend_weight", 0.3, -10.0, 10.0)
momentum_weight = _float("momentum_weight", 0.3, -10.0, 10.0)
volatility_weight = _float("volatility_weight", 0.2, -10.0, 10.0)
volume_weight = _float("volume_weight", 0.2, -10.0, 10.0)
upper_threshold = _float("upper_threshold", 25.0)
lower_threshold = _float("lower_threshold", -25.0)
threshold_multiplier = _float("threshold_multiplier", 0.5, -10.0, 10.0)
oscillator_lookback = _int("oscillator_lookback", 50)
use_hysteresis = bool(params.get("use_hysteresis", True))
hysteresis_gap = _float("hysteresis_gap", 1.0, -100.0, 100.0)
hysteresis_multiplier = _float("hysteresis_multiplier", 1.0, -100.0, 100.0)
use_dynamic_weights = bool(params.get("use_dynamic_weights", True))
maximum_volatility_factor = _float("maximum_volatility_factor", 2.0, -100.0, 100.0)
maximum_trend_factor = _float("maximum_trend_factor", 1.5, -100.0, 100.0)
volume_lookback = _int("volume_lookback", 50)
use_ai_forecast = bool(params.get("use_ai_forecast", False))
ai_length = _int("ai_length", 20)
ai_future_bars = _int("ai_future_bars", 2)
ai_threshold_shift = _float("ai_threshold_shift", 1.5, -100.0, 100.0)
smooth_ai_forecast = bool(params.get("smooth_ai_forecast", True))
use_latching = bool(params.get("use_latching", True))
show_dashboard = bool(params.get("show_dashboard", True))


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
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def _ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
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
    change = np.full(len(values), np.nan, dtype=float)
    if len(values) > 1:
        change[1:] = values[1:] - values[:-1]
    gains = np.where(np.isfinite(change), np.maximum(change, 0.0), np.nan)
    losses = np.where(np.isfinite(change), np.maximum(-change, 0.0), np.nan)
    average_gain = _rma(gains, length)
    average_loss = _rma(losses, length)
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(len(values)):
        if np.isfinite(average_gain[index]) and np.isfinite(average_loss[index]):
            result[index] = 100.0 if average_loss[index] == 0.0 and average_gain[index] > 0.0 else 50.0 if average_loss[index] == 0.0 else 100.0 - 100.0 / (1.0 + average_gain[index] / average_loss[index])
    return result


def _rolling_min(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        finite = window[np.isfinite(window)]
        if len(finite) == length:
            result[index] = float(np.min(finite))
    return result


def _rolling_max(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        finite = window[np.isfinite(window)]
        if len(finite) == length:
            result[index] = float(np.max(finite))
    return result


def _rolling_stdev(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.std(window, ddof=0))
    return result


def _wma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, length + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / denominator)
    return result


def _hma(values, length):
    half = max(1, int(np.floor(length / 2.0 + 0.5)))
    root = max(1, int(np.floor(np.sqrt(length) + 0.5)))
    return _wma(2.0 * _wma(values, half) - _wma(values, length), root)


def _linreg(values, length, offset):
    result = np.full(len(values), np.nan, dtype=float)
    x = np.arange(length, dtype=float)
    x_mean = float(np.mean(x))
    denominator = float(np.sum((x - x_mean) ** 2))
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            y_mean = float(np.mean(window))
            slope = float(np.sum((x - x_mean) * (window - y_mean)) / denominator) if denominator > 0.0 else 0.0
            intercept = y_mean - slope * x_mean
            result[index] = intercept + slope * (length - 1 - offset)
    return result


df = df.copy()
frame = _normalize(df)
time_values = frame["time"].to_numpy(dtype=float)
opened = frame["open"].to_numpy(dtype=float)
high = frame["high"].to_numpy(dtype=float)
low = frame["low"].to_numpy(dtype=float)
close = frame["close"].to_numpy(dtype=float)
volume = frame["volume"].fillna(0.0).to_numpy(dtype=float)
n = len(frame)
timeframe_seconds = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}
timeframe_aliases = {"1": "1m", "5": "5m", "15": "15m", "30": "30m", "60": "1H", "240": "4H", "D": "1D", "W": "1W", "1h": "1H", "4h": "4H", "1d": "1D", "1w": "1W"}
runtime_ctx = ctx
runtime_timeframe = str(runtime_ctx.timeframe or "1H")
chart_timeframe = timeframe_aliases.get(runtime_timeframe, runtime_timeframe)
requested_timeframe = timeframe_aliases.get(higher_timeframe, higher_timeframe)
chart_seconds = timeframe_seconds.get(chart_timeframe, 3600)
requested_seconds = timeframe_seconds.get(requested_timeframe, chart_seconds)

true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    previous_close = close[index - 1] if index > 0 else 0.0
    true_range[index] = max(high[index] - low[index], abs(high[index] - previous_close), abs(low[index] - previous_close))
plus_dm = np.full(n, np.nan, dtype=float)
minus_dm = np.full(n, np.nan, dtype=float)
for index in range(1, n):
    up = high[index] - high[index - 1]
    down = low[index - 1] - low[index]
    plus_dm[index] = up if up > down and up > 0.0 else 0.0
    minus_dm[index] = down if down > up and down > 0.0 else 0.0
smoothed_range = _rma(true_range, di_length)
smoothed_plus = _rma(plus_dm, di_length)
smoothed_minus = _rma(minus_dm, di_length)
di_plus = np.zeros(n, dtype=float)
di_minus = np.zeros(n, dtype=float)
valid_range = np.isfinite(smoothed_range) & (smoothed_range != 0.0)
di_plus[valid_range] = 100.0 * smoothed_plus[valid_range] / smoothed_range[valid_range]
di_minus[valid_range] = 100.0 * smoothed_minus[valid_range] / smoothed_range[valid_range]
directional_ratio = np.abs(di_plus - di_minus) / np.where((di_plus + di_minus) == 0.0, 1.0, di_plus + di_minus)
adx = 100.0 * _rma(directional_ratio, adx_length)
trend_direction = np.where(di_plus > di_minus, 1.0, np.where(di_minus > di_plus, -1.0, 0.0))
normalized_trend = trend_direction * np.minimum(np.maximum((adx - 20.0) / 20.0, 0.0), 1.0)

atr = _rma(true_range, atr_length)
normalized_atr = np.divide(atr, close, out=np.full(n, np.nan), where=close != 0.0)
normalized_volatility = normalized_atr * 2.0 - 1.0

# The source exposes descriptive momentum options but compares them to unrelated
# internal names. Preserving that published behavior makes every option return zero.
stochastic_rsi = _rsi(close, stochastic_rsi_length)
stochastic_base = np.divide(stochastic_rsi - _rolling_min(stochastic_rsi, stochastic_rsi_length), np.maximum(_rolling_max(stochastic_rsi, stochastic_rsi_length) - _rolling_min(stochastic_rsi, stochastic_rsi_length), 0.0001))
stochastic_momentum = (_sma(stochastic_base, stochastic_k_length) - 0.5) * 2.0
fisher_rsi = _rsi(close, fisher_rsi_length)
fisher_normalized = np.clip(fisher_rsi / 100.0 * 2.0 - 1.0, -0.999, 0.999)
fisher_momentum = np.tanh(0.5 * np.log((1.0 + fisher_normalized) / (1.0 - fisher_normalized)))
williams_high = _rolling_max(high, williams_length)
williams_low = _rolling_min(low, williams_length)
williams_value = (williams_high - close) / np.maximum(williams_high - williams_low, 0.0001) * -100.0
williams_momentum = (williams_value + 50.0) / 50.0 - 1.0
momentum = np.zeros(n, dtype=float)

tdi_rsi = _rsi(close, tdi_rsi_length)
tdi_signal = _ema(tdi_rsi, tdi_signal_length)
obv = np.zeros(n, dtype=float)
for index in range(1, n):
    obv[index] = obv[index - 1] + (volume[index] if close[index] > close[index - 1] else -volume[index] if close[index] < close[index - 1] else 0.0)
obv_min = _rolling_min(obv, volume_lookback)
obv_max = _rolling_max(obv, volume_lookback)
normalized_volume = (obv - obv_min) / np.maximum(obv_max - obv_min, 0.0001) * 2.0 - 1.0

volatility_factor = np.ones(n, dtype=float)
trend_factor = np.ones(n, dtype=float)
if use_dynamic_weights:
    volatility_factor = np.minimum(normalized_atr / 0.005, maximum_volatility_factor)
    trend_factor = np.minimum(adx / 25.0, maximum_trend_factor)
effective_trend_weight = trend_weight * trend_factor
effective_volatility_weight = volatility_weight * volatility_factor
total_weight = effective_trend_weight + momentum_weight + effective_volatility_weight + volume_weight
oscillator_numerator = effective_trend_weight * normalized_trend + momentum_weight * momentum + effective_volatility_weight * normalized_volatility + volume_weight * normalized_volume
oscillator = np.divide(oscillator_numerator, total_weight, out=np.full(n, np.nan), where=np.isfinite(total_weight) & (total_weight != 0.0)) * 100.0
oscillator_deviation = _rolling_stdev(oscillator, oscillator_lookback)
dynamic_upper = upper_threshold + oscillator_deviation * threshold_multiplier
dynamic_lower = lower_threshold - oscillator_deviation * threshold_multiplier

ai_value = _linreg(close, ai_length, 0)
ai_slope = ai_value - _linreg(close, ai_length, 1)
ai_prediction = ai_value + ai_slope * ai_future_bars
ai_prediction_smoothed = _sma(ai_prediction, 2) if smooth_ai_forecast else ai_prediction.copy()
oscillator_low = _rolling_min(oscillator, ai_length)
oscillator_high = _rolling_max(oscillator, ai_length)
prediction_low = _rolling_min(ai_prediction_smoothed, ai_length)
prediction_high = _rolling_max(ai_prediction_smoothed, ai_length)
oscillator_range = np.maximum(oscillator_high - oscillator_low, 0.001)
prediction_range = np.maximum(prediction_high - prediction_low, 0.001)
ai_oscillator = (ai_prediction_smoothed - prediction_low) / prediction_range * oscillator_range + oscillator_low
ai_bias = np.where(use_ai_forecast & np.isfinite(ai_oscillator), ai_oscillator - (upper_threshold + lower_threshold) / 2.0, 0.0)
adjusted_upper = dynamic_upper + np.where(use_ai_forecast & (ai_bias > 0.0), ai_threshold_shift, 0.0)
adjusted_lower = dynamic_lower + np.where(use_ai_forecast & (ai_bias < 0.0), -ai_threshold_shift, 0.0)

local_hma = _hma(close, hma_length)
local_trend = np.where(close > local_hma, 1.0, np.where(close < local_hma, -1.0, 0.0))
htf_hma = np.full(n, np.nan, dtype=float)
htf_trend = np.zeros(n, dtype=float)
htf_available = not use_higher_timeframe
if use_higher_timeframe:
    raw_htf = runtime_ctx.raw_series(requested_timeframe)
    htf_available = raw_htf is not None and len(raw_htf) > 0
    if htf_available:
        htf_frame = _normalize(raw_htf)
        raw_hma = _hma(htf_frame["close"].to_numpy(dtype=float), hma_length)
        raw_time = htf_frame["time"].to_numpy(dtype=float)
        for index, moment in enumerate(time_values):
            source_index = int(np.searchsorted(raw_time, moment, side="right") - 1)
            if source_index >= 0 and moment + chart_seconds < raw_time[source_index] + requested_seconds:
                source_index -= 1
            if 0 <= source_index < len(raw_hma) and np.isfinite(raw_hma[source_index]):
                htf_hma[index] = raw_hma[source_index]
        htf_trend = np.where(close > htf_hma, 1.0, np.where(close < htf_hma, -1.0, 0.0))

strong_buy = (oscillator > adjusted_upper) & (local_trend > 0.0) & ((not use_higher_timeframe) | (htf_trend > 0.0)) & (adx > 20.0)
strong_sell = (oscillator < adjusted_lower) & (local_trend < 0.0) & ((not use_higher_timeframe) | (htf_trend < 0.0)) & (adx > 20.0)
early_buy = (oscillator > upper_threshold) & (oscillator < adjusted_upper)
early_sell = (oscillator < lower_threshold) & (oscillator > adjusted_lower)
immediate_state = []
latched_state = []
stored = "Neutral"
for index in range(n):
    immediate = "Strong Buy" if strong_buy[index] else "Strong Sell" if strong_sell[index] else "Early Buy" if early_buy[index] else "Early Sell" if early_sell[index] else "No Signal"
    immediate_state.append(immediate)
    if use_latching:
        if stored == "Neutral":
            stored = "EarlyBuy" if early_buy[index] else "EarlySell" if early_sell[index] else "Long" if strong_buy[index] else "Short" if strong_sell[index] else stored
        elif stored == "EarlyBuy":
            stored = "Long" if strong_buy[index] else "Neutral" if not early_buy[index] else stored
        elif stored == "Long":
            stored = "Short" if strong_sell[index] else "EarlySell" if early_sell[index] else stored
        elif stored == "EarlySell":
            stored = "Short" if strong_sell[index] else "Neutral" if not early_sell[index] else stored
        elif stored == "Short":
            stored = "Long" if strong_buy[index] else "EarlyBuy" if early_buy[index] else stored
    latched_state.append("Strong Buy" if stored == "Long" else "Strong Sell" if stored == "Short" else "Early Buy" if stored == "EarlyBuy" else "Early Sell" if stored == "EarlySell" else "No Signal")
active_state = latched_state if use_latching else immediate_state

cloud_colors = {"Strong Buy": "rgba(0,128,0,0.20)", "Strong Sell": "rgba(255,0,0,0.20)", "Early Buy": "rgba(0,255,0,0.20)", "Early Sell": "rgba(128,0,0,0.20)", "No Signal": "rgba(0,0,255,0.10)"}
cloud = [{"upper": float(dynamic_upper[index]), "lower": float(dynamic_lower[index]), "color": cloud_colors[active_state[index]]} if np.all(np.isfinite([dynamic_upper[index], dynamic_lower[index]])) else None for index in range(n)]


def _signal_data(mask, price_values, text, color):
    return [{"active": True, "price": float(price_values[index]), "text": text, "color": color} if mask[index] and np.isfinite(price_values[index]) else None for index in range(n)]


def _transition_events(mask):
    active = np.asarray(mask, dtype=bool)
    previous = np.concatenate(([False], active[:-1])) if len(active) else active
    return active & ~previous


immediate_strong_buy = _transition_events(strong_buy) & (not use_latching)
immediate_strong_sell = _transition_events(strong_sell) & (not use_latching)
immediate_early_buy = _transition_events(early_buy) & (not use_latching)
immediate_early_sell = _transition_events(early_sell) & (not use_latching)
latched_strong_buy = _transition_events([value == "Strong Buy" for value in latched_state]) & use_latching
latched_strong_sell = _transition_events([value == "Strong Sell" for value in latched_state]) & use_latching
latched_early_buy = _transition_events([value == "Early Buy" for value in latched_state]) & use_latching
latched_early_sell = _transition_events([value == "Early Sell" for value in latched_state]) & use_latching

tables = []
if show_dashboard:
    last = n - 1
    value = lambda series: f"{series[last]:.2f}" if n and np.isfinite(series[last]) else "N/A"
    htf_text = "Off" if not use_higher_timeframe else "Bullish" if n and htf_trend[last] > 0 else "Bearish" if n and htf_trend[last] < 0 else "Flat"
    rows = [
        [{"text": "ADX"}, {"text": value(adx)}],
        [{"text": "Osc"}, {"text": value(oscillator)}],
        [{"text": "Mom Mode"}, {"text": momentum_mode}],
        [{"text": "Trend HMA"}, {"text": "Bullish" if n and local_trend[last] > 0 else "Bearish" if n and local_trend[last] < 0 else "Flat"}],
        [{"text": "HTF Trend"}, {"text": htf_text}],
        [{"text": "Vol Factor"}, {"text": value(volatility_factor)}],
        [{"text": "Signal"}, {"text": active_state[last] if n else "No Signal"}],
        [{"text": "AI Bias"}, {"text": value(ai_bias) if use_ai_forecast else "Disabled"}],
        [{"text": "Volume"}, {"text": value(normalized_volume)}],
    ]
    tables.append({"title": "Pro Scalper AI", "position": "top-right", "compact": True, "rows": rows})

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Composite Oscillator", "data": _finite(oscillator), "color": ["#008000" if np.isfinite(value) and value >= 0.0 else "#FF0000" for value in oscillator], "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Zero Line", "data": [0.0] * n, "color": "#808080", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Dynamic Upper", "data": _finite(dynamic_upper), "color": "#800080", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Dynamic Lower", "data": _finite(dynamic_lower), "color": "#800080", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Signal Band", "title": False, "data": cloud, "color": "rgba(0,0,255,0.10)", "type": "cloud", "overlay": False},
        {"name": "AI Bias", "data": _finite(ai_bias) if use_ai_forecast else [None] * n, "color": "#0000FF", "type": "line", "lineWidth": 1, "overlay": False},
    ],
    "signals": [
        {"type": "buy", "name": "Immediate Strong Buy", "data": _signal_data(immediate_strong_buy, dynamic_lower, "●", "#008000"), "color": "#008000", "renderMode": "events", "overlay": False},
        {"type": "sell", "name": "Immediate Strong Sell", "data": _signal_data(immediate_strong_sell, dynamic_upper, "●", "#FF0000"), "color": "#FF0000", "renderMode": "events", "overlay": False},
        {"type": "buy", "name": "Immediate Early Buy", "data": _signal_data(immediate_early_buy, dynamic_lower, "×", "#00FF00"), "color": "#00FF00", "renderMode": "events", "overlay": False},
        {"type": "sell", "name": "Immediate Early Sell", "data": _signal_data(immediate_early_sell, dynamic_upper, "×", "#800000"), "color": "#800000", "renderMode": "events", "overlay": False},
        {"type": "buy", "name": "Latched Strong Buy", "data": _signal_data(latched_strong_buy, dynamic_lower, "▲", "#008000"), "color": "#008000", "renderMode": "events", "overlay": False},
        {"type": "sell", "name": "Latched Strong Sell", "data": _signal_data(latched_strong_sell, dynamic_upper, "▼", "#FF0000"), "color": "#FF0000", "renderMode": "events", "overlay": False},
        {"type": "buy", "name": "Latched Early Buy", "data": _signal_data(latched_early_buy, dynamic_lower, "◆", "#00FF00"), "color": "#00FF00", "renderMode": "events", "overlay": False},
        {"type": "sell", "name": "Latched Early Sell", "data": _signal_data(latched_early_sell, dynamic_upper, "◆", "#800000"), "color": "#800000", "renderMode": "events", "overlay": False},
    ],
    "layers": [], "tables": tables, "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "trueRange": _finite(true_range), "diPlus": _finite(di_plus), "diMinus": _finite(di_minus), "adx": _finite(adx), "normalizedTrend": _finite(normalized_trend),
        "atr": _finite(atr), "normalizedATR": _finite(normalized_atr), "normalizedVolatility": _finite(normalized_volatility),
        "momentum": _finite(momentum), "stochasticMomentum": _finite(stochastic_momentum), "fisherMomentum": _finite(fisher_momentum), "williamsMomentum": _finite(williams_momentum), "momentumSelectorMismatchPreserved": True,
        "tdiRsi": _finite(tdi_rsi), "tdiSignal": _finite(tdi_signal), "obv": _finite(obv), "normalizedVolume": _finite(normalized_volume),
        "volatilityFactor": _finite(volatility_factor), "trendFactor": _finite(trend_factor), "oscillator": _finite(oscillator), "oscillatorDeviation": _finite(oscillator_deviation),
        "dynamicUpper": _finite(dynamic_upper), "dynamicLower": _finite(dynamic_lower), "aiValue": _finite(ai_value), "aiPrediction": _finite(ai_prediction_smoothed), "aiOscillator": _finite(ai_oscillator), "aiBias": _finite(ai_bias),
        "localHma": _finite(local_hma), "localTrend": _finite(local_trend), "htfAvailable": bool(htf_available), "htfHma": _finite(htf_hma), "htfTrend": _finite(htf_trend),
        "strongBuy": strong_buy.tolist(), "strongSell": strong_sell.tolist(), "earlyBuy": early_buy.tolist(), "earlySell": early_sell.tolist(), "immediateState": immediate_state, "latchedState": latched_state, "activeState": active_state,
        "reservedInputs": {"useMacdFilter": use_macd_filter, "usePinbarFilter": use_pinbar_filter, "stochasticDLength": stochastic_d_length, "useHysteresis": use_hysteresis, "hysteresisGap": hysteresis_gap, "hysteresisMultiplier": hysteresis_multiplier},
    },
}
