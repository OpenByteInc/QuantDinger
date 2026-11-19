# ============================================================
# QuantDinger Market Structure Confluence
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Market Structure Confluence"
my_indicator_description = "Combines confirmed market-structure breaks with volatility bands and trend-side rejection signals."

# @param swing_size int 10 Time horizon range=2:200:1
# @param confirmation_type str Close BOS confirmation values=Close,Wicks
# @param line_style str Solid BOS/CHoCH line style values=Solid,Dashed,Dotted
# @param show_choch bool true Show CHoCH labels
# @param show_swings bool true Show swing labels
# @param basis_length int 100 Basis WMA length range=1:500:1
# @param atr_length int 14 ATR length range=1:200:1
# @param atr_smoothing int 21 ATR smoothing range=1:200:1
# @param volatility_multiplier float 2.0 Volatility multiplier range=0.1:20.0:0.1
# @param bullish_color color #00FFBB Bullish color
# @param bearish_color color #FF1100 Bearish color
# @param wait_for_close bool true Wait for bar close

swing_size = min(200, max(2, int(params.get("swing_size", 10))))
confirmation_type = str(params.get("confirmation_type", "Close"))
line_style = str(params.get("line_style", "Solid"))
show_choch = bool(params.get("show_choch", True))
show_swings = bool(params.get("show_swings", True))
basis_length = min(500, max(1, int(params.get("basis_length", 100))))
atr_length = min(200, max(1, int(params.get("atr_length", 14))))
atr_smoothing = min(200, max(1, int(params.get("atr_smoothing", 21))))
volatility_multiplier = min(20.0, max(0.1, float(params.get("volatility_multiplier", 2.0))))
bullish_color = str(params.get("bullish_color", "#00FFBB"))
bearish_color = str(params.get("bearish_color", "#FF1100"))
wait_for_close = bool(params.get("wait_for_close", True))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _wma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, period + 1, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / np.sum(weights))
    return result


def _rma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    if period == 1:
        return values.copy()
    if len(values) >= period:
        previous = float(np.mean(values[:period])); result[period - 1] = previous
        for index in range(period, len(values)):
            previous = (previous * (period - 1.0) + values[index]) / period; result[index] = previous
    return result


frame = df.copy().reset_index(drop=True)
if "time" not in frame.columns:
    frame["time"] = np.arange(len(frame), dtype=float) * 3600000.0
if "volume" not in frame.columns:
    frame["volume"] = 0.0
for column in ("time", "open", "high", "low", "close", "volume"):
    frame[column] = pd.to_numeric(frame[column], errors="coerce")
frame = frame.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
times = frame["time"].to_numpy(dtype=float)
if len(times) and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0
open_price = frame["open"].to_numpy(dtype=float)
high = frame["high"].to_numpy(dtype=float)
low = frame["low"].to_numpy(dtype=float)
close = frame["close"].to_numpy(dtype=float)
n = len(frame)

true_range = np.full(n, np.nan, dtype=float)
if n: true_range[0] = high[0] - low[0]
for index in range(1, n): true_range[index] = max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
volatility = _rma(true_range, atr_length)
smoothed_volatility = pd.Series(volatility, dtype=float).rolling(atr_smoothing, min_periods=atr_smoothing).mean().to_numpy(dtype=float)
basis = _wma(close, basis_length)
upper = basis + volatility_multiplier * smoothed_volatility
lower = basis - volatility_multiplier * smoothed_volatility

pivot_high = np.full(n, np.nan, dtype=float)
pivot_low = np.full(n, np.nan, dtype=float)
for index in range(2 * swing_size, n):
    candidate_index = index - swing_size
    candidate_high = high[candidate_index]
    if candidate_high > np.max(high[candidate_index - swing_size:candidate_index]) and candidate_high >= np.max(high[candidate_index + 1:candidate_index + swing_size + 1]):
        pivot_high[index] = candidate_high
    candidate_low = low[candidate_index]
    if candidate_low < np.min(low[candidate_index - swing_size:candidate_index]) and candidate_low <= np.min(low[candidate_index + 1:candidate_index + swing_size + 1]):
        pivot_low[index] = candidate_low

previous_high = np.nan
previous_low = np.nan
previous_high_index = -1
previous_low_index = -1
high_active = False
low_active = False
previous_break_direction = 0
last_break_direction = 0
hh = np.zeros(n, dtype=bool); lh = np.zeros(n, dtype=bool); hl = np.zeros(n, dtype=bool); ll = np.zeros(n, dtype=bool)
high_broken = np.zeros(n, dtype=bool); low_broken = np.zeros(n, dtype=bool)
bullish_change = np.zeros(n, dtype=bool); bearish_change = np.zeros(n, dtype=bool)
break_direction = np.zeros(n, dtype=int)
bullish_arrow = np.zeros(n, dtype=bool); bearish_arrow = np.zeros(n, dtype=bool)
structure_lines = []
swing_labels = []
break_labels = []
high_source = close if confirmation_type in ("Close", "Candle Close") else high
low_source = close if confirmation_type in ("Close", "Candle Close") else low

for index in range(n):
    if np.isfinite(pivot_high[index]):
        hh[index] = not np.isfinite(previous_high) or pivot_high[index] >= previous_high
        lh[index] = not hh[index]
        previous_high = float(pivot_high[index]); previous_high_index = index - swing_size; high_active = True
        if show_swings: swing_labels.append((previous_high_index, previous_high, "HH" if hh[index] else "LH", bullish_color if hh[index] else bearish_color, "down"))
    if np.isfinite(pivot_low[index]):
        hl[index] = not np.isfinite(previous_low) or pivot_low[index] >= previous_low
        ll[index] = not hl[index]
        previous_low = float(pivot_low[index]); previous_low_index = index - swing_size; low_active = True
        if show_swings: swing_labels.append((previous_low_index, previous_low, "HL" if hl[index] else "LL", bullish_color if hl[index] else bearish_color, "up"))

    if high_active and np.isfinite(previous_high) and high_source[index] > previous_high:
        high_broken[index] = True; high_active = False
    if low_active and np.isfinite(previous_low) and low_source[index] < previous_low:
        low_broken[index] = True; low_active = False

    bullish_arrow[index] = previous_break_direction == 1 and np.isfinite(lower[index]) and low[index] < lower[index] < high[index]
    bearish_arrow[index] = previous_break_direction == -1 and np.isfinite(upper[index]) and low[index] < upper[index] < high[index]

    if high_broken[index]:
        structure_lines.append((previous_high_index, index, previous_high, bullish_color))
        is_choch = previous_break_direction == -1 and show_choch
        break_labels.append((int(np.floor(index - (index - previous_high_index) / 2.0)), previous_high, "CHoCH" if is_choch else "BOS", bullish_color, "down"))
        last_break_direction = previous_break_direction
        previous_break_direction = 1
        bullish_change[index] = last_break_direction in (-1, 0)
    if low_broken[index]:
        structure_lines.append((previous_low_index, index, previous_low, bearish_color))
        is_choch = previous_break_direction == 1 and show_choch
        break_labels.append((int(np.floor(index - (index - previous_low_index) / 2.0)), previous_low, "CHoCH" if is_choch else "BOS", bearish_color, "up"))
        last_break_direction = previous_break_direction
        previous_break_direction = -1
        bearish_change[index] = last_break_direction in (1, 0)
    break_direction[index] = previous_break_direction

layers = []
dashed = line_style in ("Dashed", "Dotted")
for start, end, price, color in structure_lines:
    layers.append({"type": "line", "role": "BOS/CHoCH", "start": float(times[start]), "end": float(times[end]), "y1": price, "y2": price, "color": color, "lineWidth": 1, "dashed": dashed, "lineStyle": line_style.lower()})
visible_label_start = max(0, n - 180)
for index, price, text, color, direction in swing_labels + break_labels:
    if index >= visible_label_start:
        layers.append({"type": "label", "index": index, "price": price, "text": text, "textColor": color, "color": "rgba(0,0,0,0)", "direction": direction, "size": "tiny" if text in ("BOS", "CHoCH") else "small"})

plots = [
    {"name": "Lower Band", "data": [float(lower[index]) if break_direction[index] == 1 and np.isfinite(lower[index]) else None for index in range(n)], "color": bullish_color, "type": "line", "style": "linebr", "lineWidth": 3, "overlay": True},
    {"name": "Upper Band", "data": [float(upper[index]) if break_direction[index] == -1 and np.isfinite(upper[index]) else None for index in range(n)], "color": bearish_color, "type": "line", "style": "linebr", "lineWidth": 3, "overlay": True},
    {"name": "Structure State", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
]
signals = [
    {"type": "buy", "name": "Bullish Signal", "text": "▲", "data": [float(lower[index] - volatility[index]) if bullish_arrow[index] and np.isfinite(volatility[index]) else None for index in range(n)], "color": bullish_color, "renderMode": "events"},
    {"type": "sell", "name": "Bearish Signal", "text": "▼", "data": [float(upper[index] + volatility[index]) if bearish_arrow[index] and np.isfinite(volatility[index]) else None for index in range(n)], "color": bearish_color, "renderMode": "events"},
    {"type": "buy", "name": "Trend Change Bullish", "text": "●", "data": [float(lower[index]) if bullish_change[index] and np.isfinite(lower[index]) else None for index in range(n)], "color": bullish_color, "renderMode": "events"},
    {"type": "sell", "name": "Trend Change Bearish", "text": "●", "data": [float(upper[index]) if bearish_change[index] and np.isfinite(upper[index]) else None for index in range(n)], "color": bearish_color, "renderMode": "events"},
]
bar_colors = [f"{bearish_color}B3" if direction == -1 else f"{bullish_color}B3" for direction in break_direction]

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers, "tables": [],
    "bar_colors": bar_colors, "background_colors": [None] * n,
    "calculatedVars": {
        "trueRange": _finite(true_range), "volatility": _finite(volatility), "smoothedVolatility": _finite(smoothed_volatility),
        "basis": _finite(basis), "upper": _finite(upper), "lower": _finite(lower), "pivotHigh": _finite(pivot_high), "pivotLow": _finite(pivot_low),
        "hh": hh.tolist(), "lh": lh.tolist(), "hl": hl.tolist(), "ll": ll.tolist(), "highBroken": high_broken.tolist(), "lowBroken": low_broken.tolist(),
        "breakDirection": break_direction.tolist(), "bullishArrow": bullish_arrow.tolist(), "bearishArrow": bearish_arrow.tolist(),
        "bullishChange": bullish_change.tolist(), "bearishChange": bearish_change.tolist(), "barConfirmed": [True] * n,
    },
}
