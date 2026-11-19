# ============================================================
# QuantDinger MACD Divergence
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger MACD Divergence"
my_indicator_description = (
    "A configurable MACD with momentum-colored histogram and confirmed regular "
    "bullish and bearish price divergences."
)

# @param fast_length int 12 Fast length range=1:200:1
# @param slow_length int 26 Slow length range=2:400:1
# @param signal_length int 9 Signal length range=1:100:1
# @param oscillator_ma int 1 Oscillator MA type range=0:1:1
# @param signal_ma int 1 Signal MA type range=0:1:1
# @param pivot_left int 5 Pivot left bars range=1:30:1
# @param pivot_right int 5 Pivot right bars range=1:30:1
# @param range_lower int 5 Minimum pivot distance range=1:200:1
# @param range_upper int 60 Maximum pivot distance range=2:500:1
# @param protect_zero bool true Require divergence on one side of zero

fast_length = max(1, int(params.get("fast_length", 12)))
slow_length = max(2, int(params.get("slow_length", 26)))
signal_length = max(1, int(params.get("signal_length", 9)))
oscillator_ma = max(0, min(1, int(params.get("oscillator_ma", 1))))
signal_ma = max(0, min(1, int(params.get("signal_ma", 1))))
pivot_left = max(1, int(params.get("pivot_left", 5)))
pivot_right = max(1, int(params.get("pivot_right", 5)))
range_lower = max(1, int(params.get("range_lower", 5)))
range_upper = max(range_lower, int(params.get("range_upper", 60)))
protect_zero = bool(params.get("protect_zero", True))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def sma(values, period):
    return pd.Series(np.asarray(values, dtype=float)).rolling(period, min_periods=period).mean().to_numpy(dtype=float)


def ema(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    seed = []
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed.append(float(value))
            if len(seed) == period:
                previous = float(np.mean(seed))
                result[index] = previous
        else:
            previous = alpha * value + (1.0 - alpha) * previous
            result[index] = previous
    return result


fast = sma(close, fast_length) if oscillator_ma == 0 else ema(close, fast_length)
slow = sma(close, slow_length) if oscillator_ma == 0 else ema(close, slow_length)
macd = fast - slow
signal = sma(macd, signal_length) if signal_ma == 0 else ema(macd, signal_length)
histogram = macd - signal

pivot_low = np.zeros(n, dtype=bool)
pivot_high = np.zeros(n, dtype=bool)
window = pivot_left + pivot_right + 1
for confirmation in range(window - 1, n):
    pivot = confirmation - pivot_right
    segment = macd[pivot - pivot_left:pivot + pivot_right + 1]
    if np.all(np.isfinite(segment)):
        pivot_low[pivot] = macd[pivot] == np.min(segment)
        pivot_high[pivot] = macd[pivot] == np.max(segment)

bullish = np.zeros(n, dtype=bool)
bearish = np.zeros(n, dtype=bool)
previous_low_pivot = -1
previous_high_pivot = -1
zero_window = pivot_left + pivot_right + 5
for index in range(n):
    if pivot_low[index]:
        if previous_low_pivot >= 0:
            distance = index - previous_low_pivot
            start = max(0, index - zero_window + 1)
            zero_ok = np.all(macd[start:index + 1] < 0.0) if protect_zero else True
            bullish[index] = range_lower <= distance <= range_upper and macd[index] > macd[previous_low_pivot] and low[index] < low[previous_low_pivot] and zero_ok
        previous_low_pivot = index
    if pivot_high[index]:
        if previous_high_pivot >= 0:
            distance = index - previous_high_pivot
            start = max(0, index - zero_window + 1)
            zero_ok = np.all(macd[start:index + 1] > 0.0) if protect_zero else True
            bearish[index] = range_lower <= distance <= range_upper and macd[index] < macd[previous_high_pivot] and high[index] > high[previous_high_pivot] and zero_ok
        previous_high_pivot = index

histogram_plot = []
for index, value in enumerate(histogram):
    if not np.isfinite(value):
        histogram_plot.append(None)
        continue
    previous = histogram[index - 1] if index > 0 and np.isfinite(histogram[index - 1]) else value
    if value >= 0.0:
        color = "#26A69A" if value > previous else "#B2DFDB"
    else:
        color = "#FFCDD2" if value > previous else "#FF5252"
    histogram_plot.append({"value": float(value), "color": color})


def finite_line(values):
    return [float(value) if np.isfinite(value) else None for value in values]


output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Histogram", "data": histogram_plot, "color": "#26A69A", "type": "histogram", "baseValue": 0.0, "lineWidth": 1, "overlay": False},
        {"name": "MACD", "data": finite_line(macd), "color": "#2962FF", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Signal", "data": finite_line(signal), "color": "#FF6D00", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Zero", "data": [0.0 for _ in range(n)], "color": "#808080", "type": "line", "lineWidth": 1, "overlay": False},
    ],
    "signals": [
        {"type": "buy", "text": "BULL", "name": "Bullish Divergence", "data": [float(macd[i]) if bullish[i] and np.isfinite(macd[i]) else None for i in range(n)], "color": "#00C853", "renderMode": "events"},
        {"type": "sell", "text": "BEAR", "name": "Bearish Divergence", "data": [float(macd[i]) if bearish[i] and np.isfinite(macd[i]) else None for i in range(n)], "color": "#FF1744", "renderMode": "events"},
    ],
    "layers": [],
}
