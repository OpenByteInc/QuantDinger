# ============================================================
# QuantDinger MAD Adaptive Trend Score
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger MAD Adaptive Trend Score"
my_indicator_description = "Clips price to a robust rolling median/MAD envelope and scores the filter's directional persistence."

# @param source str Close Price source values=Open,High,Low,Close,HL2,HLC3,OHLC4,HLCC4
# @param mad_length int 15 MAD length range=3:500:1
# @param mad_multiplier float 5.2 MAD multiplier range=0.1:50.0:0.1
# @param score_start int 1 Score lookback start range=1:50:1
# @param score_end int 45 Score lookback end range=1:50:1
# @param show_references bool true Show reference lines
# @param show_signals bool true Show chart signals
# @param long_threshold int 40 Long threshold range=-50:50:1
# @param short_threshold int -6 Short threshold range=-50:50:1
# @param long_color color #00FF00 Long color
# @param short_color color #FF0000 Short color
# @param line_width int 5 Score line width range=1:10:1
# @param show_filter bool false Show MAD filter on price chart
# @param paint_candles bool true Paint candles by trend
# @param background_color bool false Show trend background
# @param minimum_tick float 0.00000001 Minimum price increment

source_name = str(params.get("source", "Close"))
mad_length = min(500, max(3, int(params.get("mad_length", 15))))
mad_multiplier = min(50.0, max(0.1, float(params.get("mad_multiplier", 5.2))))
score_start = min(50, max(1, int(params.get("score_start", 1))))
score_end = min(50, max(score_start, int(params.get("score_end", 45))))
show_references = bool(params.get("show_references", True))
show_signals = bool(params.get("show_signals", True))
long_threshold = min(50, max(-50, int(params.get("long_threshold", 40))))
short_threshold = min(50, max(-50, int(params.get("short_threshold", -6))))
long_color = str(params.get("long_color", "#00FF00"))
short_color = str(params.get("short_color", "#FF0000"))
line_width = min(10, max(1, int(params.get("line_width", 5))))
show_filter = bool(params.get("show_filter", False))
paint_candles = bool(params.get("paint_candles", True))
background_color = bool(params.get("background_color", False))
minimum_tick = max(1e-12, float(params.get("minimum_tick", 0.00000001)))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


frame = df.copy().reset_index(drop=True)
if "time" not in frame.columns:
    frame["time"] = np.arange(len(frame), dtype=float) * 3600000.0
if "volume" not in frame.columns:
    frame["volume"] = 0.0
for column in ("time", "open", "high", "low", "close", "volume"):
    frame[column] = pd.to_numeric(frame[column], errors="coerce")
frame = frame.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
open_price = frame["open"].to_numpy(dtype=float)
high = frame["high"].to_numpy(dtype=float)
low = frame["low"].to_numpy(dtype=float)
close = frame["close"].to_numpy(dtype=float)
n = len(frame)

sources = {
    "Open": open_price, "High": high, "Low": low, "Close": close,
    "HL2": (high + low) * 0.5, "HLC3": (high + low + close) / 3.0,
    "OHLC4": (open_price + high + low + close) * 0.25,
    "HLCC4": (high + low + close + close) * 0.25,
}
source = sources.get(source_name, close)

median_value = np.full(n, np.nan, dtype=float)
raw_mad = np.full(n, np.nan, dtype=float)
for index in range(n):
    start = max(0, index - mad_length + 1)
    window = source[start:index + 1]
    finite_window = window[np.isfinite(window)]
    if len(finite_window):
        median = float(np.median(finite_window))
        median_value[index] = median
        raw_mad[index] = float(np.median(np.abs(finite_window - median)))

scaled_mad = np.maximum(raw_mad * 1.4826, minimum_tick)
deviation = source - median_value
maximum_deviation = scaled_mad * mad_multiplier
clipped_deviation = np.clip(deviation, -maximum_deviation, maximum_deviation)
mad_filter = median_value + clipped_deviation

trend_score = np.zeros(n, dtype=float)
for index in range(n):
    score = 0.0
    for lag in range(score_start, score_end + 1):
        historical_index = index - lag
        score += 1.0 if historical_index >= 0 and np.isfinite(mad_filter[index]) and np.isfinite(mad_filter[historical_index]) and mad_filter[index] > mad_filter[historical_index] else -1.0
    trend_score[index] = score

signal = np.zeros(n, dtype=int)
long_condition = trend_score > long_threshold
short_condition = np.zeros(n, dtype=bool)
state = 0
for index in range(n):
    previous_score = trend_score[index - 1] if index > 0 else trend_score[index]
    short_condition[index] = previous_score >= short_threshold and trend_score[index] < short_threshold
    if long_condition[index] and not short_condition[index]:
        state = 1
    elif short_condition[index]:
        state = -1
    signal[index] = state

long_signal = np.zeros(n, dtype=bool)
short_signal = np.zeros(n, dtype=bool)
for index in range(1, n):
    long_signal[index] = signal[index] == 1 and signal[index - 1] == -1
    short_signal[index] = signal[index] == -1 and signal[index - 1] == 1

state_colors = [long_color if value == 1 else short_color for value in signal]
plots = [
    {"name": "Bullish Trend Score", "data": [float(trend_score[index]) if signal[index] == 1 else None for index in range(n)], "color": long_color, "type": "line", "lineWidth": line_width, "overlay": False},
    {"name": "Bearish Trend Score", "data": [float(trend_score[index]) if signal[index] == -1 else None for index in range(n)], "color": short_color, "type": "line", "lineWidth": line_width, "overlay": False},
    {"name": "Long Threshold", "data": [float(long_threshold) if show_references else None for _ in range(n)], "color": "rgba(180,180,180,0.50)", "type": "line", "style": "dashed", "lineWidth": 1, "overlay": False},
    {"name": "Short Threshold", "data": [float(short_threshold) if show_references else None for _ in range(n)], "color": "rgba(180,180,180,0.50)", "type": "line", "style": "dashed", "lineWidth": 1, "overlay": False},
    {"name": "Bullish MAD Filter", "data": [float(mad_filter[index]) if show_filter and signal[index] == 1 and np.isfinite(mad_filter[index]) else None for index in range(n)], "color": long_color, "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Bearish MAD Filter", "data": [float(mad_filter[index]) if show_filter and signal[index] == -1 and np.isfinite(mad_filter[index]) else None for index in range(n)], "color": short_color, "type": "line", "lineWidth": 2, "overlay": True},
]
signals = [
    {"type": "buy", "name": "Long Signal", "text": "L", "data": [float(low[index]) if show_signals and long_signal[index] else None for index in range(n)], "color": long_color, "renderMode": "events"},
    {"type": "sell", "name": "Short Signal", "text": "S", "data": [float(high[index]) if show_signals and short_signal[index] else None for index in range(n)], "color": short_color, "renderMode": "events"},
]
bar_colors = state_colors if paint_candles else [None] * n
background_colors = [f"{long_color}33" if value == 1 else f"{short_color}33" for value in signal] if background_color else [None] * n

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": signals, "layers": [], "tables": [],
    "bar_colors": bar_colors, "background_colors": background_colors,
    "calculatedVars": {
        "source": _finite(source), "medianValue": _finite(median_value), "rawMAD": _finite(raw_mad), "scaledMAD": _finite(scaled_mad),
        "deviation": _finite(deviation), "maximumDeviation": _finite(maximum_deviation), "clippedDeviation": _finite(clipped_deviation),
        "madFilter": _finite(mad_filter), "trendScore": _finite(trend_score), "longCondition": long_condition.tolist(),
        "shortCondition": short_condition.tolist(), "signal": signal.tolist(), "longSignal": long_signal.tolist(), "shortSignal": short_signal.tolist(),
    },
}
