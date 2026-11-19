# ============================================================
# QuantDinger MESA Phase-Adaptive Band Trend
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

import math

my_indicator_name = "QuantDinger MESA Phase-Adaptive Band Trend"
my_indicator_description = "Uses Hilbert phase activity to adapt return-volatility bands around an EMA trend and maintain a directional regime."

# @param source str HL2 Price source values=HL2,Close,HLC3,OHLC4,Open,High,Low
# @param fast_limit float 0.50 Fast phase-response limit range=0.01:1:0.01
# @param slow_limit float 0.05 Slow phase-response limit range=0.001:1:0.001
# @param trend_length int 20 EMA trend length range=2:500:1
# @param volatility_length int 20 Return-volatility length range=2:500:1
# @param active_multiplier float 1.5 Active-market band multiplier range=0.1:10:0.1
# @param slow_multiplier float 3.0 Quiet-market band multiplier range=0.1:10:0.1
# @param show_bands bool true Show adaptive bands
# @param show_fill bool true Show band fill
# @param show_signals bool true Show regime-change signals
# @param color_bars bool true Color candles by regime
# @param bullish_color color #39FF14 Bullish color
# @param bearish_color color #8A2BE2 Bearish color

source_name = str(params.get("source", "HL2")).upper()
if source_name not in ("HL2", "CLOSE", "HLC3", "OHLC4", "OPEN", "HIGH", "LOW"):
    source_name = "HL2"
fast_limit = min(1.0, max(0.01, float(params.get("fast_limit", 0.50))))
slow_limit = min(1.0, max(0.001, float(params.get("slow_limit", 0.05))))
trend_length = min(500, max(2, int(params.get("trend_length", 20))))
volatility_length = min(500, max(2, int(params.get("volatility_length", 20))))
active_multiplier = max(0.1, float(params.get("active_multiplier", 1.5)))
slow_multiplier = max(0.1, float(params.get("slow_multiplier", 3.0)))
show_bands = bool(params.get("show_bands", True))
show_fill = bool(params.get("show_fill", True))
show_signals = bool(params.get("show_signals", True))
color_bars = bool(params.get("color_bars", True))
bullish_color = str(params.get("bullish_color", "#39FF14"))
bearish_color = str(params.get("bearish_color", "#8A2BE2"))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _lag(values, index, distance, fallback=0.0):
    cursor = index - distance
    return float(values[cursor]) if cursor >= 0 and np.isfinite(values[cursor]) else float(fallback)


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


df = df.copy()
frame = df.reset_index(drop=True)
if "time" not in frame.columns:
    frame["time"] = np.arange(len(frame), dtype=float) * 3600000.0
if "volume" not in frame.columns:
    frame["volume"] = 0.0
for column in ("time", "open", "high", "low", "close", "volume"):
    frame[column] = pd.to_numeric(frame[column], errors="coerce")
frame = frame.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened = frame["open"].to_numpy(dtype=float)
high = frame["high"].to_numpy(dtype=float)
low = frame["low"].to_numpy(dtype=float)
close = frame["close"].to_numpy(dtype=float)
n = len(frame)
if source_name == "CLOSE":
    source = close.copy()
elif source_name == "HLC3":
    source = (high + low + close) / 3.0
elif source_name == "OHLC4":
    source = (opened + high + low + close) / 4.0
elif source_name == "OPEN":
    source = opened.copy()
elif source_name == "HIGH":
    source = high.copy()
elif source_name == "LOW":
    source = low.copy()
else:
    source = (high + low) / 2.0

smooth = np.full(n, np.nan, dtype=float)
period = np.full(n, np.nan, dtype=float)
detrender = np.full(n, np.nan, dtype=float)
q1 = np.full(n, np.nan, dtype=float)
i1 = np.full(n, np.nan, dtype=float)
j_i = np.full(n, np.nan, dtype=float)
j_q = np.full(n, np.nan, dtype=float)
i2 = np.zeros(n, dtype=float)
q2 = np.zeros(n, dtype=float)
re = np.zeros(n, dtype=float)
im = np.zeros(n, dtype=float)
phase = np.zeros(n, dtype=float)
delta_phase = np.full(n, np.nan, dtype=float)
alpha_phase = np.full(n, np.nan, dtype=float)
phase_activity = np.full(n, np.nan, dtype=float)

phase_range = max(fast_limit - slow_limit, 0.0001)
for index in range(n):
    src_1 = source[index - 1] if index >= 1 else source[index]
    src_2 = source[index - 2] if index >= 2 else source[index]
    src_3 = source[index - 3] if index >= 3 else source[index]
    smooth[index] = (4.0 * source[index] + 3.0 * src_1 + 2.0 * src_2 + src_3) / 10.0
    previous_period = period[index - 1] if index >= 1 and np.isfinite(period[index - 1]) else 10.0
    factor = 0.075 * previous_period + 0.54
    detrender[index] = (0.0962 * smooth[index] + 0.5769 * _lag(smooth, index, 2) - 0.5769 * _lag(smooth, index, 4) - 0.0962 * _lag(smooth, index, 6)) * factor
    q1[index] = (0.0962 * detrender[index] + 0.5769 * _lag(detrender, index, 2) - 0.5769 * _lag(detrender, index, 4) - 0.0962 * _lag(detrender, index, 6)) * factor
    i1[index] = _lag(detrender, index, 3)
    j_i[index] = (0.0962 * i1[index] + 0.5769 * _lag(i1, index, 2) - 0.5769 * _lag(i1, index, 4) - 0.0962 * _lag(i1, index, 6)) * factor
    j_q[index] = (0.0962 * q1[index] + 0.5769 * _lag(q1, index, 2) - 0.5769 * _lag(q1, index, 4) - 0.0962 * _lag(q1, index, 6)) * factor
    previous_i2 = i2[index - 1] if index >= 1 else 0.0
    previous_q2 = q2[index - 1] if index >= 1 else 0.0
    i2[index] = 0.2 * (i1[index] - j_q[index]) + 0.8 * previous_i2
    q2[index] = 0.2 * (q1[index] + j_i[index]) + 0.8 * previous_q2
    re_raw = i2[index] * previous_i2 + q2[index] * previous_q2
    im_raw = i2[index] * previous_q2 - q2[index] * previous_i2
    re[index] = 0.2 * re_raw + 0.8 * (re[index - 1] if index >= 1 else 0.0)
    im[index] = 0.2 * im_raw + 0.8 * (im[index - 1] if index >= 1 else 0.0)
    angle = math.atan(im[index] / re[index]) * 180.0 / math.pi if re[index] != 0.0 else 0.0
    raw_period = abs(360.0 / angle) if angle != 0.0 else previous_period
    limited = min(raw_period, 1.5 * previous_period)
    limited = max(limited, 0.67 * previous_period)
    limited = max(6.0, min(50.0, limited))
    period[index] = 0.2 * limited + 0.8 * previous_period
    previous_phase = phase[index - 1] if index >= 1 else 0.0
    phase[index] = math.atan(q1[index] / i1[index]) * 180.0 / math.pi if i1[index] != 0.0 else previous_phase
    delta_phase[index] = max(previous_phase - phase[index], 1.0)
    alpha_phase[index] = max(fast_limit / delta_phase[index], slow_limit)
    phase_activity[index] = max(0.0, min(1.0, (alpha_phase[index] - slow_limit) / phase_range))

trend = _ema(source, trend_length)
price_return = np.zeros(n, dtype=float)
for index in range(1, n):
    price_return[index] = (source[index] - source[index - 1]) / source[index - 1] if source[index - 1] != 0.0 else 0.0
rolling_volatility = pd.Series(price_return, dtype=float).rolling(volatility_length, min_periods=volatility_length).std(ddof=0).to_numpy(dtype=float)
volatility = np.full(n, np.nan, dtype=float)
if n > 1:
    volatility[1:] = rolling_volatility[:-1]
adaptive_multiplier = slow_multiplier - (slow_multiplier - active_multiplier) * phase_activity
upper_band = trend + adaptive_multiplier * volatility * trend
lower_band = trend - adaptive_multiplier * volatility * trend

regime = np.zeros(n, dtype=int)
long_state = np.zeros(n, dtype=bool)
short_state = np.zeros(n, dtype=bool)
long_signal = np.zeros(n, dtype=bool)
short_signal = np.zeros(n, dtype=bool)
for index in range(n):
    long_state[index] = np.isfinite(upper_band[index]) and close[index] > upper_band[index]
    short_state[index] = np.isfinite(lower_band[index]) and close[index] < lower_band[index]
    previous = regime[index - 1] if index >= 1 else 0
    regime[index] = 1 if long_state[index] else -1 if short_state[index] else previous
    long_signal[index] = regime[index] == 1 and previous != 1
    short_signal[index] = regime[index] == -1 and previous != -1

trend_colors = [bullish_color if value == 1 else bearish_color for value in regime]
band_cloud = []
for index in range(n):
    if show_bands and show_fill and np.all(np.isfinite([upper_band[index], lower_band[index]])):
        band_cloud.append({"upper": float(upper_band[index]), "lower": float(lower_band[index]), "color": trend_colors[index]})
    else:
        band_cloud.append(None)

signals = []
if show_signals:
    signals = [
        {"type": "buy", "name": "Long Signal", "text": "LONG", "data": [float(low[index]) if long_signal[index] else None for index in range(n)], "color": bullish_color, "renderMode": "events"},
        {"type": "sell", "name": "Short Signal", "text": "SHORT", "data": [float(high[index]) if short_signal[index] else None for index in range(n)], "color": bearish_color, "renderMode": "events"},
    ]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Trend", "data": _finite(trend), "color": trend_colors, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Upper Phase Band", "data": _finite(upper_band) if show_bands else [None] * n, "color": "rgba(57,255,20,0.65)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Lower Phase Band", "data": _finite(lower_band) if show_bands else [None] * n, "color": "rgba(138,43,226,0.65)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Phase Band Fill", "title": False, "data": band_cloud, "color": bullish_color, "type": "cloud", "opacity": 0.10, "overlay": True},
    ],
    "signals": signals,
    "layers": [],
    "tables": [],
    "bar_colors": trend_colors if color_bars else [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "source": _finite(source), "smooth": _finite(smooth), "period": _finite(period), "detrender": _finite(detrender),
        "quadrature1": _finite(q1), "inPhase1": _finite(i1), "inPhase2": _finite(i2), "quadrature2": _finite(q2),
        "real": _finite(re), "imaginary": _finite(im), "phase": _finite(phase), "deltaPhase": _finite(delta_phase),
        "alphaPhase": _finite(alpha_phase), "phaseActivity": _finite(phase_activity), "trend": _finite(trend),
        "priceReturn": _finite(price_return), "volatility": _finite(volatility), "adaptiveMultiplier": _finite(adaptive_multiplier),
        "upperBand": _finite(upper_band), "lowerBand": _finite(lower_band), "long": long_state.tolist(), "short": short_state.tolist(),
        "regime": regime.tolist(), "longSignal": long_signal.tolist(), "shortSignal": short_signal.tolist(), "trendColors": trend_colors,
    },
}
