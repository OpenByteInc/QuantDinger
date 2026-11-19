# ============================================================
# QuantDinger Market Shift Levels
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Market Shift Levels"
my_indicator_description = "Tracks Hull-average shift levels, colors price by side of the active level, and marks three-bar rejection patterns."

# @param length int 55 Hull moving-average length range=2:500:1
# @param label_data str Volume Reversal label data values=Volume,Price
# @param shift_up_color color #24D580 Bullish shift color
# @param shift_down_color color #E12B2B Bearish shift color

length = min(500, max(2, int(params.get("length", 55))))
label_data = str(params.get("label_data", "Volume"))
shift_up_color = str(params.get("shift_up_color", "#24D580"))
shift_down_color = str(params.get("shift_down_color", "#E12B2B"))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _wma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, period + 1, dtype=float)
    divisor = float(np.sum(weights))
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / divisor)
    return result


frame = df.copy().reset_index(drop=True)
if "time" not in frame.columns:
    frame["time"] = np.arange(len(frame), dtype=float) * 3600000.0
if "volume" not in frame.columns:
    frame["volume"] = 0.0
for column in ("time", "open", "high", "low", "close", "volume"):
    frame[column] = pd.to_numeric(frame[column], errors="coerce")
frame["volume"] = frame["volume"].fillna(0.0)
frame = frame.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
high = frame["high"].to_numpy(dtype=float)
low = frame["low"].to_numpy(dtype=float)
close = frame["close"].to_numpy(dtype=float)
volume = frame["volume"].to_numpy(dtype=float)
n = len(frame)

hma = _wma(2.0 * _wma(close, max(1, length // 2)) - _wma(close, length), max(1, int(round(np.sqrt(length)))))
delayed_hma = np.full(n, np.nan, dtype=float)
if n > 5:
    delayed_hma[5:] = hma[:-5]
cross_up = np.zeros(n, dtype=bool)
cross_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.all(np.isfinite([hma[index], delayed_hma[index], hma[index - 1], delayed_hma[index - 1]])):
        cross_up[index] = hma[index] > delayed_hma[index] and hma[index - 1] <= delayed_hma[index - 1]
        cross_down[index] = hma[index] < delayed_hma[index] and hma[index - 1] >= delayed_hma[index - 1]

level = np.full(n, np.nan, dtype=float)
current_level = np.nan
for index in range(n):
    if cross_up[index]:
        current_level = low[index]
    if cross_down[index]:
        current_level = high[index]
    level[index] = current_level
level_changed = np.zeros(n, dtype=bool)
for index in range(1, n):
    level_changed[index] = (np.isfinite(level[index]) != np.isfinite(level[index - 1])) or (np.isfinite(level[index]) and np.isfinite(level[index - 1]) and level[index] != level[index - 1])

volume_sum = pd.Series(volume, dtype=float).rolling(3, min_periods=3).sum().to_numpy(dtype=float)
bearish_reversal = np.zeros(n, dtype=bool)
bullish_reversal = np.zeros(n, dtype=bool)
bearish_label_value = np.full(n, np.nan, dtype=float)
bullish_label_value = np.full(n, np.nan, dtype=float)
for index in range(2, n):
    if not np.isfinite(level[index]):
        continue
    bearish_reversal[index] = high[index - 2] < level[index] and high[index] < level[index] and high[index - 1] > level[index]
    bullish_reversal[index] = low[index - 2] > level[index] and low[index - 1] < level[index] and low[index] > level[index]
    if bearish_reversal[index]:
        bearish_label_value[index] = volume_sum[index] if label_data == "Volume" else high[index - 1]
    if bullish_reversal[index]:
        bullish_label_value[index] = volume_sum[index] if label_data == "Volume" else high[index - 1]

shift_colors = [shift_down_color if np.isfinite(level[index]) and close[index] < level[index] else shift_up_color for index in range(n)]
level_data = [None if level_changed[index] or not np.isfinite(level[index]) else float(level[index]) for index in range(n)]
bearish_marker = [None] * n
bullish_marker = [None] * n
for index in range(2, n):
    if bearish_reversal[index]:
        bearish_marker[index - 1] = float(high[index - 1])
    if bullish_reversal[index]:
        bullish_marker[index - 1] = float(low[index - 1])
plots = [
    {"name": "Market Shift Levels", "data": level_data, "color": "rgba(190,190,190,0.85)", "type": "line", "style": "linebr", "lineStyle": "dotted", "dashed": True, "lineWidth": 1, "overlay": True},
    {"name": "Shift State", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
]
signals = [
    {"type": "sell", "name": "Bearish Rejection", "text": "▽", "data": bearish_marker, "color": "#FF0000", "renderMode": "events"},
    {"type": "buy", "name": "Bullish Rejection", "text": "△", "data": bullish_marker, "color": "#00FF00", "renderMode": "events"},
]

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": signals, "layers": [], "tables": [],
    "bar_colors": shift_colors, "background_colors": [None] * n,
    "calculatedVars": {
        "hma": _finite(hma), "delayedHMA": _finite(delayed_hma), "crossUp": cross_up.tolist(), "crossDown": cross_down.tolist(),
        "level": _finite(level), "levelChanged": level_changed.tolist(), "volumeSum": _finite(volume_sum),
        "bearishReversal": bearish_reversal.tolist(), "bullishReversal": bullish_reversal.tolist(),
        "bearishLabelValue": _finite(bearish_label_value), "bullishLabelValue": _finite(bullish_label_value), "shiftColor": shift_colors,
    },
}
