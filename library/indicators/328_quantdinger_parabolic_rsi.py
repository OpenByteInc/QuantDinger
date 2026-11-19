# ============================================================
# QuantDinger Parabolic RSI
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Parabolic RSI"
my_indicator_description = "Applies a custom parabolic stop-and-reverse state machine to Wilder RSI and highlights ordinary and threshold-qualified reversals."

# @param rsi_length int 14 RSI length range=2:200:1
# @param upper_threshold int 70 Upper RSI threshold range=50:100:1
# @param lower_threshold int 30 Lower RSI threshold range=0:50:1
# @param display_sar bool true Show parabolic RSI points
# @param sar_start float 0.02 Initial acceleration range=0.001:1:0.001
# @param sar_increment float 0.02 Acceleration increment range=0.001:1:0.001
# @param sar_maximum float 0.2 Maximum acceleration range=0.001:2:0.001

rsi_length = min(200, max(2, int(params.get("rsi_length", 14))))
upper_threshold = min(100, max(50, int(params.get("upper_threshold", 70))))
lower_threshold = min(50, max(0, int(params.get("lower_threshold", 30))))
display_sar = bool(params.get("display_sar", True))
sar_start = min(1.0, max(0.001, float(params.get("sar_start", 0.02))))
sar_increment = min(1.0, max(0.001, float(params.get("sar_increment", 0.02))))
sar_maximum = min(2.0, max(0.001, float(params.get("sar_maximum", 0.2))))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _wilder_rsi(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) <= period:
        return result
    changes = np.diff(values)
    gains = np.maximum(changes, 0.0)
    losses = np.maximum(-changes, 0.0)
    average_gain = float(np.mean(gains[:period]))
    average_loss = float(np.mean(losses[:period]))
    if average_loss == 0.0:
        result[period] = 100.0 if average_gain > 0.0 else 50.0
    else:
        result[period] = 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    for index in range(period + 1, len(values)):
        average_gain = (average_gain * (period - 1) + gains[index - 1]) / period
        average_loss = (average_loss * (period - 1) + losses[index - 1]) / period
        if average_loss == 0.0:
            result[index] = 100.0 if average_gain > 0.0 else 50.0
        else:
            result[index] = 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)
rsi = _wilder_rsi(close, rsi_length)
source_high = rsi + 1.0
source_low = rsi - 1.0
sar_rsi = np.full(n, np.nan, dtype=float)
is_below = np.zeros(n, dtype=bool)
acceleration_series = np.full(n, np.nan, dtype=float)
extreme_series = np.full(n, np.nan, dtype=float)
result = np.nan
extreme = np.nan
acceleration = np.nan
below = False

for index in range(n):
    first_trend_bar = False
    if index <= rsi_length + 2:
        if index >= 1 and np.isfinite(rsi[index]) and np.isfinite(rsi[index - 1]) and rsi[index] > rsi[index - 1]:
            below = True
            extreme = source_high[index]
            result = source_low[index - 1]
        else:
            below = False
            extreme = source_low[index]
            result = source_high[index - 1] if index >= 1 else np.nan
        first_trend_bar = True
        acceleration = sar_start

    if np.all(np.isfinite([result, acceleration, extreme])):
        result = result + acceleration * (extreme - result)

    if below:
        if np.all(np.isfinite([result, source_low[index]])) and result > source_low[index]:
            first_trend_bar = True
            below = False
            result = max(source_high[index], extreme)
            extreme = source_low[index]
            acceleration = sar_start
    else:
        if np.all(np.isfinite([result, source_high[index]])) and result < source_high[index]:
            first_trend_bar = True
            below = True
            result = min(source_low[index], extreme)
            extreme = source_high[index]
            acceleration = sar_start

    if not first_trend_bar:
        if below and np.all(np.isfinite([source_high[index], extreme])) and source_high[index] > extreme:
            extreme = source_high[index]
            acceleration = min(acceleration + sar_increment, sar_maximum)
        elif (not below) and np.all(np.isfinite([source_low[index], extreme])) and source_low[index] < extreme:
            extreme = source_low[index]
            acceleration = min(acceleration + sar_increment, sar_maximum)

    if below and np.isfinite(result):
        if index >= 1 and np.isfinite(source_low[index - 1]):
            result = min(result, source_low[index - 1])
        if index > 1 and np.isfinite(source_low[index - 2]):
            result = min(result, source_low[index - 2])
    elif (not below) and np.isfinite(result):
        if index >= 1 and np.isfinite(source_high[index - 1]):
            result = max(result, source_high[index - 1])
        if index > 1 and np.isfinite(source_high[index - 2]):
            result = max(result, source_high[index - 2])

    sar_rsi[index] = result
    is_below[index] = below
    acceleration_series[index] = acceleration
    extreme_series[index] = extreme

up_signal = np.zeros(n, dtype=bool)
down_signal = np.zeros(n, dtype=bool)
strong_up = np.zeros(n, dtype=bool)
strong_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    if is_below[index] != is_below[index - 1] and np.isfinite(sar_rsi[index]):
        up_signal[index] = bool(is_below[index])
        down_signal[index] = not bool(is_below[index])
        strong_up[index] = up_signal[index] and sar_rsi[index] <= lower_threshold
        strong_down[index] = down_signal[index] and sar_rsi[index] >= upper_threshold

weak_up = up_signal & (sar_rsi >= lower_threshold)
weak_down = down_signal & (sar_rsi <= upper_threshold)
displayed_sar = np.full(n, np.nan, dtype=float)
if display_sar:
    for index in range(n):
        if index == 0 or is_below[index] == is_below[index - 1]:
            displayed_sar[index] = sar_rsi[index]
sar_colors = ["#EEA47F" if is_below[index] else "#00539C" for index in range(n)]
weak_up_points = [float(sar_rsi[index]) if weak_up[index] else None for index in range(n)]
weak_down_points = [float(sar_rsi[index]) if weak_down[index] else None for index in range(n)]
strong_up_points = [float(sar_rsi[index]) if strong_up[index] else None for index in range(n)]
strong_down_points = [float(sar_rsi[index]) if strong_down[index] else None for index in range(n)]
background = [{"upper": float(upper_threshold), "lower": float(lower_threshold), "color": "rgba(194,146,87,0.10)"} for _ in range(n)]
overbought = [{"upper": float(rsi[index]), "lower": 50.0, "color": "rgba(255,69,0,0.22)"} if np.isfinite(rsi[index]) and rsi[index] >= 70.0 else None for index in range(n)]
oversold = [{"upper": 50.0, "lower": float(rsi[index]), "color": "rgba(255,165,0,0.22)"} if np.isfinite(rsi[index]) and rsi[index] <= 30.0 else None for index in range(n)]


def _chart_points(events, bullish, strong):
    result = [None] * n
    for index, active in enumerate(events):
        if active:
            result[index] = {
                "active": True, "price": float(low[index] if bullish else high[index]),
                "text": "Strong Up" if bullish and strong else "Strong Down" if strong else "Up" if bullish else "Down",
                "color": "#EEA47F" if bullish else "#00539C",
            }
    return result


output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "RSI", "data": _finite(rsi), "color": "#D1D5DB", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Upper Threshold", "data": [float(upper_threshold)] * n, "color": "#787B86", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Middle", "data": [50.0] * n, "color": "rgba(120,123,134,0.50)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Lower Threshold", "data": [float(lower_threshold)] * n, "color": "#787B86", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "RSI Background", "title": False, "data": background, "color": "#C29257", "type": "cloud", "overlay": False},
        {"name": "Overbought Gradient", "title": False, "data": overbought, "color": "#FF4500", "type": "cloud", "overlay": False},
        {"name": "Oversold Gradient", "title": False, "data": oversold, "color": "#FFA500", "type": "cloud", "overlay": False},
        {"name": "Parabolic RSI", "data": _finite(displayed_sar), "color": sar_colors, "type": "circle", "size": 3, "overlay": False},
        {"name": "RSI Up", "data": weak_up_points, "color": "#EEA47F", "type": "diamond", "size": 4, "overlay": False},
        {"name": "RSI Down", "data": weak_down_points, "color": "#00539C", "type": "diamond", "size": 4, "overlay": False},
        {"name": "Strong RSI Up", "data": strong_up_points, "color": "#EEA47F", "type": "diamond", "size": 5, "overlay": False},
        {"name": "Strong RSI Down", "data": strong_down_points, "color": "#00539C", "type": "diamond", "size": 5, "overlay": False},
    ],
    "signals": [
        {"type": "buy", "name": "Chart RSI Up", "data": _chart_points(weak_up, True, False), "color": "#EEA47F", "renderMode": "events"},
        {"type": "sell", "name": "Chart RSI Down", "data": _chart_points(weak_down, False, False), "color": "#00539C", "renderMode": "events"},
        {"type": "buy", "name": "Chart Strong RSI Up", "data": _chart_points(strong_up, True, True), "color": "#EEA47F", "renderMode": "events"},
        {"type": "sell", "name": "Chart Strong RSI Down", "data": _chart_points(strong_down, False, True), "color": "#00539C", "renderMode": "events"},
    ],
    "layers": [], "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "rsi": _finite(rsi), "sarRsi": _finite(sar_rsi), "displayedSar": _finite(displayed_sar),
        "isBelow": is_below.tolist(), "acceleration": _finite(acceleration_series), "extreme": _finite(extreme_series),
        "upSignal": up_signal.tolist(), "downSignal": down_signal.tolist(),
        "weakUp": weak_up.tolist(), "weakDown": weak_down.tolist(), "strongUp": strong_up.tolist(), "strongDown": strong_down.tolist(),
        "rsiLength": rsi_length, "upperThreshold": upper_threshold, "lowerThreshold": lower_threshold,
    },
}
