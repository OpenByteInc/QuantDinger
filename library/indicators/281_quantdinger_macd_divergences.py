# ============================================================
# QuantDinger MACD Divergences
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger MACD Divergences"
my_indicator_description = "Plots configurable MACD components and confirms regular bullish and bearish pivot divergences."

# @param fast_length int 12 Fast length range=1:1000:1
# @param slow_length int 26 Slow length range=1:1000:1
# @param source str Close Source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param signal_length int 9 Signal smoothing range=1:50:1
# @param oscillator_ma_type str EMA Oscillator MA type values=SMA,EMA
# @param signal_ma_type str EMA Signal MA type values=SMA,EMA
# @param dont_touch_zero bool true Require divergence window to stay on one side of zero
# @param pivot_right int 5 Pivot right lookback range=1:100:1
# @param pivot_left int 5 Pivot left lookback range=1:100:1
# @param maximum_range int 60 Maximum pivot distance range=1:1000:1
# @param minimum_range int 5 Minimum pivot distance range=0:1000:1
# @param plot_bullish bool true Show regular bullish divergences
# @param plot_bearish bool true Show regular bearish divergences
# @param macd_color color #2962FF MACD line color
# @param signal_color color #FF6D00 Signal line color

fast_length = min(1000, max(1, int(params.get("fast_length", 12))))
slow_length = min(1000, max(1, int(params.get("slow_length", 26))))
source_name = str(params.get("source", "Close"))
signal_length = min(50, max(1, int(params.get("signal_length", 9))))
oscillator_ma_type = str(params.get("oscillator_ma_type", "EMA"))
signal_ma_type = str(params.get("signal_ma_type", "EMA"))
dont_touch_zero = bool(params.get("dont_touch_zero", True))
pivot_right = min(100, max(1, int(params.get("pivot_right", 5))))
pivot_left = min(100, max(1, int(params.get("pivot_left", 5))))
maximum_range = min(1000, max(1, int(params.get("maximum_range", 60))))
minimum_range = min(maximum_range, max(0, int(params.get("minimum_range", 5))))
plot_bullish = bool(params.get("plot_bullish", True))
plot_bearish = bool(params.get("plot_bearish", True))
macd_color = str(params.get("macd_color", "#2962FF"))
signal_color = str(params.get("signal_color", "#FF6D00"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)
source = {
    "Open": open_price, "High": high, "Low": low, "HL2": (high + low) * 0.5,
    "HLC3": (high + low + close) / 3.0, "OHLC4": (open_price + high + low + close) / 4.0,
}.get(source_name, close)


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    finite_values = []
    for index, value in enumerate(values):
        if np.isfinite(value):
            finite_values.append(float(value))
            if len(finite_values) >= period:
                result[index] = float(np.mean(finite_values[-period:]))
    return result


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


average_function = _sma if oscillator_ma_type == "SMA" else _ema
fast_average = average_function(source, fast_length)
slow_average = average_function(source, slow_length)
macd = fast_average - slow_average
signal = (_sma if signal_ma_type == "SMA" else _ema)(macd, signal_length)
histogram = macd - signal

pivot_low = np.full(n, np.nan, dtype=float)
pivot_high = np.full(n, np.nan, dtype=float)
pivot_low_found = np.zeros(n, dtype=bool)
pivot_high_found = np.zeros(n, dtype=bool)
for confirmation in range(pivot_left + pivot_right, n):
    center = confirmation - pivot_right
    start = center - pivot_left
    window = macd[start:confirmation + 1]
    if np.all(np.isfinite(window)):
        if macd[center] == float(np.min(window)):
            pivot_low[confirmation] = float(macd[center]); pivot_low_found[confirmation] = True
        if macd[center] == float(np.max(window)):
            pivot_high[confirmation] = float(macd[center]); pivot_high_found[confirmation] = True

regular_bullish = np.zeros(n, dtype=bool)
regular_bearish = np.zeros(n, dtype=bool)
bullish_previous_confirmation = np.full(n, -1, dtype=int)
bearish_previous_confirmation = np.full(n, -1, dtype=int)
low_confirmations = []
high_confirmations = []
bullish_segments = []
bearish_segments = []
zero_window = pivot_left + pivot_right + 5

for confirmation in range(n):
    if pivot_low_found[confirmation]:
        center = confirmation - pivot_right
        if low_confirmations:
            previous = low_confirmations[-1]
            previous_center = previous - pivot_right
            bars_since_shifted = confirmation - previous - 1
            in_range = minimum_range <= bars_since_shifted <= maximum_range
            oscillator_higher_low = macd[center] > macd[previous_center] and in_range and macd[center] < 0.0
            price_lower_low = low[center] < low[previous_center]
            window_start = max(0, confirmation - zero_window + 1)
            below_zero = float(np.max(macd[window_start:confirmation + 1])) < 0.0 if np.all(np.isfinite(macd[window_start:confirmation + 1])) else False
            regular_bullish[confirmation] = plot_bullish and oscillator_higher_low and price_lower_low and (below_zero if dont_touch_zero else True)
            bullish_previous_confirmation[confirmation] = previous
            if regular_bullish[confirmation]:
                bullish_segments.append((previous_center, center, float(macd[previous_center]), float(macd[center])))
        low_confirmations.append(confirmation)
    if pivot_high_found[confirmation]:
        center = confirmation - pivot_right
        if high_confirmations:
            previous = high_confirmations[-1]
            previous_center = previous - pivot_right
            bars_since_shifted = confirmation - previous - 1
            in_range = minimum_range <= bars_since_shifted <= maximum_range
            oscillator_lower_high = macd[center] < macd[previous_center] and in_range and macd[center] > 0.0
            price_higher_high = high[center] > high[previous_center]
            window_start = max(0, confirmation - zero_window + 1)
            above_zero = float(np.min(macd[window_start:confirmation + 1])) > 0.0 if np.all(np.isfinite(macd[window_start:confirmation + 1])) else False
            regular_bearish[confirmation] = plot_bearish and oscillator_lower_high and price_higher_high and (above_zero if dont_touch_zero else True)
            bearish_previous_confirmation[confirmation] = previous
            if regular_bearish[confirmation]:
                bearish_segments.append((previous_center, center, float(macd[previous_center]), float(macd[center])))
        high_confirmations.append(confirmation)

histogram_colors = []
for index, value in enumerate(histogram):
    growing = index > 0 and np.isfinite(histogram[index - 1]) and np.isfinite(value) and histogram[index - 1] < value
    if not np.isfinite(value):
        histogram_colors.append(None)
    elif value >= 0.0:
        histogram_colors.append("#26A69A" if growing else "#B2DFDB")
    else:
        histogram_colors.append("#FFCDD2" if growing else "#FF5252")

divergence_plots = []
for segment_index, (start, end, start_value, end_value) in enumerate(bullish_segments):
    data = np.full(n, np.nan, dtype=float); data[start:end + 1] = np.linspace(start_value, end_value, end - start + 1)
    divergence_plots.append({"name": f"Regular Bullish {segment_index + 1}", "title": False, "data": _finite(data), "color": "#008000", "type": "line", "lineWidth": 2, "overlay": False})
for segment_index, (start, end, start_value, end_value) in enumerate(bearish_segments):
    data = np.full(n, np.nan, dtype=float); data[start:end + 1] = np.linspace(start_value, end_value, end - start + 1)
    divergence_plots.append({"name": f"Regular Bearish {segment_index + 1}", "title": False, "data": _finite(data), "color": "#FF0000", "type": "line", "lineWidth": 2, "overlay": False})

plots = [
    {"name": "Histogram", "data": _finite(histogram), "color": histogram_colors, "type": "histogram", "lineWidth": 1, "overlay": False},
    {"name": "MACD", "data": _finite(macd), "color": macd_color, "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Signal", "data": _finite(signal), "color": signal_color, "type": "line", "lineWidth": 1, "overlay": False},
] + divergence_plots
bullish_markers = [None] * n
bearish_markers = [None] * n
for confirmation in range(n):
    if regular_bullish[confirmation]:
        center = confirmation - pivot_right
        bullish_markers[center] = float(macd[center])
    if regular_bearish[confirmation]:
        center = confirmation - pivot_right
        bearish_markers[center] = float(macd[center])
signals = [
    {"type": "buy", "name": "Regular Bullish Divergence", "text": "Bull", "data": bullish_markers, "color": "#008000", "renderMode": "events"},
    {"type": "sell", "name": "Regular Bearish Divergence", "text": "Bear", "data": bearish_markers, "color": "#FF0000", "renderMode": "events"},
]

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": signals, "layers": [], "tables": [],
    "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "fastAverage": _finite(fast_average), "slowAverage": _finite(slow_average), "macd": _finite(macd), "signal": _finite(signal), "histogram": _finite(histogram),
        "pivotLow": _finite(pivot_low), "pivotHigh": _finite(pivot_high), "pivotLowFound": pivot_low_found.tolist(), "pivotHighFound": pivot_high_found.tolist(),
        "regularBullish": regular_bullish.tolist(), "regularBearish": regular_bearish.tolist(),
        "bullishPreviousConfirmation": bullish_previous_confirmation.tolist(), "bearishPreviousConfirmation": bearish_previous_confirmation.tolist(),
        "bullishSegments": bullish_segments, "bearishSegments": bearish_segments, "histogramColors": histogram_colors,
    },
}
