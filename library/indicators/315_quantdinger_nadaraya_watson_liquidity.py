# ============================================================
# QuantDinger Nadaraya-Watson Regression Liquidity
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Nadaraya-Watson Regression Liquidity"
my_indicator_description = "Normalizes Nadaraya-Watson slope momentum and tracks oscillator-phase liquidity sweep levels."

# @param normalization_length int 100 Normalization length range=1:500:1
# @param bandwidth int 30 Nadaraya-Watson bandwidth range=1:500:1
# @param lookback int 140 Nadaraya-Watson lookback range=1:1000:1
# @param oscillator_smoothing int 21 Oscillator smoothing range=1:500:1
# @param signal_length int 14 Signal length range=1:500:1
# @param atr_length int 14 ATR length range=1:500:1
# @param volatility_smoothing int 21 Volatility smoothing range=1:500:1
# @param use_swing_tips bool true Use swing points
# @param show_band_candles bool true Show regression band candles
# @param color_price_bars bool true Color price bars
# @param show_signal_line bool true Show oscillator signal line
# @param show_momentum_signals bool true Show momentum weakening signals
# @param show_rebound_signals bool true Show rebound signals

normalization_length = min(500, max(1, int(params.get("normalization_length", 100))))
bandwidth = min(500, max(1, int(params.get("bandwidth", 30))))
lookback = min(1000, max(1, int(params.get("lookback", 140))))
oscillator_smoothing = min(500, max(1, int(params.get("oscillator_smoothing", 21))))
signal_length = min(500, max(1, int(params.get("signal_length", 14))))
atr_length = min(500, max(1, int(params.get("atr_length", 14))))
volatility_smoothing = min(500, max(1, int(params.get("volatility_smoothing", 21))))
use_swing_tips = bool(params.get("use_swing_tips", True))
show_band_candles = bool(params.get("show_band_candles", True))
color_price_bars = bool(params.get("color_price_bars", True))
show_signal_line = bool(params.get("show_signal_line", True))
show_momentum_signals = bool(params.get("show_momentum_signals", True))
show_rebound_signals = bool(params.get("show_rebound_signals", True))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _sma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def _wma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, period + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.sum(window * weights) / denominator)
    return result


def _rma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    finite_indices = np.flatnonzero(np.isfinite(values))
    if len(finite_indices) < period:
        return result
    seed_indices = finite_indices[:period]
    seed_index = int(seed_indices[-1])
    previous = float(np.mean(values[seed_indices]))
    result[seed_index] = previous
    for index in range(seed_index + 1, len(values)):
        if np.isfinite(values[index]):
            previous = (previous * (period - 1) + float(values[index])) / period
            result[index] = previous
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
times = df["time"].to_numpy(dtype=float)
if len(times) and max(abs(float(value)) for value in times) < 10_000_000_000:
    times = times * 1000.0
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)

weights = np.exp(-(np.arange(lookback, dtype=float) ** 2) / (2.0 * float(bandwidth * bandwidth)))
weight_sum = float(np.sum(weights))
regression = np.full(n, np.nan, dtype=float)
for index in range(lookback - 1, n):
    window = close[index - lookback + 1:index + 1][::-1]
    if np.all(np.isfinite(window)):
        regression[index] = float(np.sum(window * weights) / weight_sum)
slope = np.full(n, np.nan, dtype=float)
if n > 1:
    valid = np.isfinite(regression[1:]) & np.isfinite(regression[:-1])
    indices = np.flatnonzero(valid) + 1
    slope[indices] = regression[indices] - regression[indices - 1]
slope_std = np.full(n, np.nan, dtype=float)
for index in range(normalization_length - 1, n):
    window = slope[index - normalization_length + 1:index + 1]
    if np.all(np.isfinite(window)):
        slope_std[index] = float(np.std(window, ddof=0))
normalized_slope = np.full(n, np.nan, dtype=float)
valid_std = np.isfinite(slope) & np.isfinite(slope_std) & (np.abs(slope_std) > 1e-12)
normalized_slope[valid_std] = slope[valid_std] / slope_std[valid_std]
oscillator = _wma(normalized_slope, oscillator_smoothing)
signal = _ema(oscillator, signal_length)

true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    true_range[index] = high[index] - low[index] if index == 0 else max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
atr = _rma(true_range, atr_length)
volatility = _sma(atr, volatility_smoothing)

cross_under = np.zeros(n, dtype=bool)
cross_over = np.zeros(n, dtype=bool)
zero_cross_up = np.zeros(n, dtype=bool)
zero_cross_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.all(np.isfinite([oscillator[index], signal[index], oscillator[index - 1], signal[index - 1]])):
        cross_under[index] = oscillator[index] < signal[index] and oscillator[index - 1] >= signal[index - 1]
        cross_over[index] = oscillator[index] > signal[index] and oscillator[index - 1] <= signal[index - 1]
    if np.all(np.isfinite([oscillator[index], oscillator[index - 1]])):
        zero_cross_up[index] = oscillator[index] > 0.0 and oscillator[index - 1] <= 0.0
        zero_cross_down[index] = oscillator[index] < 0.0 and oscillator[index - 1] >= 0.0

above_signal = oscillator > signal
below_signal = oscillator < signal
above_tip_price = np.full(n, np.nan, dtype=float)
above_tip_bar = np.full(n, -1, dtype=int)
below_tip_price = np.full(n, np.nan, dtype=float)
below_tip_bar = np.full(n, -1, dtype=int)
last_above_price = np.nan
last_above_bar = -1
last_below_price = np.nan
last_below_bar = -1
for index in range(n):
    if above_signal[index]:
        if index == 0 or not above_signal[index - 1]:
            last_above_price, last_above_bar = float(high[index]), index
        elif not np.isfinite(last_above_price) or high[index] > last_above_price:
            last_above_price, last_above_bar = float(high[index]), index
    if below_signal[index]:
        if index == 0 or not below_signal[index - 1]:
            last_below_price, last_below_bar = float(low[index]), index
        elif not np.isfinite(last_below_price) or low[index] < last_below_price:
            last_below_price, last_below_bar = float(low[index]), index
    above_tip_price[index], above_tip_bar[index] = last_above_price, last_above_bar
    below_tip_price[index], below_tip_bar[index] = last_below_price, last_below_bar

draw_upper = np.isfinite(oscillator) & (oscillator > 0.0) & cross_under
draw_lower = np.isfinite(oscillator) & (oscillator < 0.0) & cross_over
weakening_bullish = draw_upper.copy()
weakening_bearish = draw_lower.copy()
upper_swept = np.zeros(n, dtype=bool)
lower_swept = np.zeros(n, dtype=bool)
liquidity_lines = []
active_line_index = None
active_line_price = np.nan
active_line_type = 0
for index in range(n):
    if active_line_index is not None and active_line_type == 1 and close[index] > active_line_price:
        upper_swept[index] = True
    if active_line_index is not None and active_line_type == -1 and close[index] < active_line_price:
        lower_swept[index] = True
    draw_new = bool(draw_upper[index] or draw_lower[index])
    if active_line_index is not None and active_line_type != 0:
        liquidity_lines[active_line_index]["endIndex"] = index
        crossed = close[index] > active_line_price if active_line_type == 1 else close[index] < active_line_price
        if crossed or draw_new:
            active_line_type = 0
    if draw_upper[index]:
        start_index = int(above_tip_bar[index]) if use_swing_tips and above_tip_bar[index] >= 0 else index
        level_price = float(above_tip_price[index]) if use_swing_tips and np.isfinite(above_tip_price[index]) else float(high[index])
        liquidity_lines.append({"type": 1, "startIndex": start_index, "endIndex": index, "price": level_price})
        active_line_index = len(liquidity_lines) - 1
        active_line_price, active_line_type = level_price, 1
    if draw_lower[index]:
        start_index = int(below_tip_bar[index]) if use_swing_tips and below_tip_bar[index] >= 0 else index
        level_price = float(below_tip_price[index]) if use_swing_tips and np.isfinite(below_tip_price[index]) else float(low[index])
        liquidity_lines.append({"type": -1, "startIndex": start_index, "endIndex": index, "price": level_price})
        active_line_index = len(liquidity_lines) - 1
        active_line_price, active_line_type = level_price, -1
    if len(liquidity_lines) > 500:
        removed = len(liquidity_lines) - 500
        liquidity_lines = liquidity_lines[removed:]
        active_line_index = active_line_index - removed if active_line_index is not None else None

bullish_rebound = np.zeros(n, dtype=bool)
bearish_rebound = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.all(np.isfinite([oscillator[index], regression[index], close[index - 1]])):
        bullish_rebound[index] = oscillator[index] > 0.0 and close[index - 1] < regression[index] and close[index] > regression[index]
        bearish_rebound[index] = oscillator[index] < 0.0 and close[index - 1] > regression[index] and close[index] < regression[index]

overflow = np.where(np.isfinite(oscillator), np.sign(oscillator) * np.maximum(np.abs(oscillator) - 2.0, 0.0), np.nan)
bar_colors = []
for value in oscillator:
    if not np.isfinite(value):
        bar_colors.append(None)
    elif value > 0:
        alpha = min(0.70, max(0.20, 0.20 + abs(value) / 3.0 * 0.50))
        bar_colors.append(f"rgba(0,255,187,{alpha:.3f})")
    else:
        alpha = min(0.70, max(0.20, 0.20 + abs(value) / 3.0 * 0.50))
        bar_colors.append(f"rgba(255,17,0,{alpha:.3f})")

oscillator_fill = []
zero_fill = []
for index in range(n):
    if np.all(np.isfinite([oscillator[index], overflow[index]])):
        color = bar_colors[index] or "rgba(128,128,128,0.20)"
        oscillator_fill.append({"upper": float(max(oscillator[index], overflow[index])), "lower": float(min(oscillator[index], overflow[index])), "color": color})
        zero_fill.append({"upper": float(max(0.0, overflow[index])), "lower": float(min(0.0, overflow[index])), "color": "rgba(0,255,187,0.45)" if oscillator[index] > 0 else "rgba(255,17,0,0.45)"})
    else:
        oscillator_fill.append(None)
        zero_fill.append(None)

band_candles = []
for index in range(n):
    if show_band_candles and np.all(np.isfinite([regression[index], oscillator[index], volatility[index]])):
        band_candles.append({
            "open": float(regression[index] + (-volatility[index] if oscillator[index] > 0 else volatility[index])),
            "high": float(high[index] if oscillator[index] < 0 else regression[index]),
            "low": float(low[index] if oscillator[index] > 0 else regression[index]),
            "close": float(regression[index]), "color": bar_colors[index], "fillColor": bar_colors[index], "borderColor": bar_colors[index], "wickColor": bar_colors[index],
        })
    else:
        band_candles.append(None)

signals = [
    {"type": "sell", "name": "Weakening Bullish Momentum", "text": "●", "data": [float(high[index]) if show_momentum_signals and weakening_bullish[index] else None for index in range(n)], "color": "#00FFBB", "renderMode": "events"},
    {"type": "buy", "name": "Weakening Bearish Momentum", "text": "●", "data": [float(low[index]) if show_momentum_signals and weakening_bearish[index] else None for index in range(n)], "color": "#FF1100", "renderMode": "events"},
    {"type": "buy", "name": "Bullish Rebound", "text": "▲", "data": [float(regression[index] - volatility[index] * 1.5) if show_rebound_signals and bullish_rebound[index] and np.all(np.isfinite([regression[index], volatility[index]])) else None for index in range(n)], "color": "#00FFBB", "renderMode": "events"},
    {"type": "sell", "name": "Bearish Rebound", "text": "▼", "data": [float(regression[index] + volatility[index] * 1.5) if show_rebound_signals and bearish_rebound[index] and np.all(np.isfinite([regression[index], volatility[index]])) else None for index in range(n)], "color": "#FF1100", "renderMode": "events"},
]

layers = []
for line in liquidity_lines:
    start = float(times[line["startIndex"]])
    end = float(times[line["endIndex"]])
    layers.append({"type": "line", "role": "Upper Liquidity" if line["type"] == 1 else "Lower Liquidity", "start": start, "end": end, "y1": line["price"], "y2": line["price"], "color": "#FF1100" if line["type"] == 1 else "#00FFBB", "lineWidth": 2})

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Oscillator", "data": _finite(oscillator), "color": "rgba(59,130,246,0.01)", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Signal Line", "data": _finite(signal) if show_signal_line else [None] * n, "color": "rgba(220,220,220,0.50)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Zero Line", "data": [0.0] * n, "color": "#A0A0A0", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Oscillator Fill", "title": False, "data": oscillator_fill, "color": "#3B82F6", "type": "cloud", "overlay": False},
        {"name": "Overflow Fill", "title": False, "data": zero_fill, "color": "#00FFBB", "type": "cloud", "overlay": False},
        {"name": "Nadaraya-Watson Band", "data": band_candles, "color": "#00FFBB", "type": "candlestick", "overlay": True},
    ],
    "signals": signals,
    "layers": layers,
    "tables": [],
    "bar_colors": bar_colors if color_price_bars else [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "regression": _finite(regression), "slope": _finite(slope), "slopeStd": _finite(slope_std), "normalizedSlope": _finite(normalized_slope),
        "oscillator": _finite(oscillator), "signal": _finite(signal), "atr": _finite(atr), "volatility": _finite(volatility), "overflow": _finite(overflow),
        "aboveTipPrice": _finite(above_tip_price), "aboveTipBar": above_tip_bar.tolist(), "belowTipPrice": _finite(below_tip_price), "belowTipBar": below_tip_bar.tolist(),
        "drawUpperLine": draw_upper.tolist(), "drawLowerLine": draw_lower.tolist(), "upperLevelSwept": upper_swept.tolist(), "lowerLevelSwept": lower_swept.tolist(),
        "bullishZeroCross": zero_cross_up.tolist(), "bearishZeroCross": zero_cross_down.tolist(), "bullishSignalCross": cross_over.tolist(), "bearishSignalCross": cross_under.tolist(),
        "bullishRebound": bullish_rebound.tolist(), "bearishRebound": bearish_rebound.tolist(), "liquidityLines": liquidity_lines,
    },
}
