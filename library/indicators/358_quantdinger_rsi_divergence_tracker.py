# ============================================================
# QuantDinger RSI Divergence Tracker
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger RSI Divergence Tracker"
my_indicator_description = "Tracks rolling RSI extremes, delayed pivots, and price-versus-RSI bullish or bearish divergences."

# @param rsi_length int 14 RSI length range=1:10000:1
# @param overbought int 70 Overbought level range=0:100:1
# @param oversold int 30 Oversold level range=0:100:1
# @param midline int 50 Midline level range=0:100:1
# @param hide_midline bool false Hide midline
# @param hide_pivots bool false Hide pivot markers
# @param shorter_labels bool false Use marker-only labels
# @param hide_labels bool false Replace labels with shifted backgrounds
# @param divergence_lookback int 90 Divergence lookback range=1:10000:1

rsi_length = min(10000, max(1, int(params.get("rsi_length", 14))))
overbought = min(100, max(0, int(params.get("overbought", 70))))
oversold = min(100, max(0, int(params.get("oversold", 30))))
midline = min(100, max(0, int(params.get("midline", 50))))
hide_midline = bool(params.get("hide_midline", False))
hide_pivots = bool(params.get("hide_pivots", False))
shorter_labels = bool(params.get("shorter_labels", False))
hide_labels = bool(params.get("hide_labels", False))
divergence_lookback = min(10000, max(1, int(params.get("divergence_lookback", 90))))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


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
                previous = float(np.mean(seed))
                result[index] = previous
        else:
            previous = (previous * (length - 1.0) + float(value)) / length
            result[index] = previous
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
close = df["close"].to_numpy(dtype=float)
n = len(df)

change = np.full(n, np.nan, dtype=float)
if n > 1:
    change[1:] = np.diff(close)
average_up = _rma(np.maximum(change, 0.0), rsi_length)
average_down = _rma(np.maximum(-change, 0.0), rsi_length)
rsi = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not np.all(np.isfinite([average_up[index], average_down[index]])):
        continue
    rsi[index] = 100.0 if average_down[index] == 0.0 else 0.0 if average_up[index] == 0.0 else 100.0 - 100.0 / (1.0 + average_up[index] / average_down[index])

highest_offset = np.full(n, -1, dtype=int)
lowest_offset = np.full(n, -1, dtype=int)
tracked_high_price = np.full(n, np.nan, dtype=float)
tracked_high_rsi = np.full(n, np.nan, dtype=float)
tracked_low_price = np.full(n, np.nan, dtype=float)
tracked_low_rsi = np.full(n, np.nan, dtype=float)
pivot_high = np.zeros(n, dtype=bool)
pivot_low = np.zeros(n, dtype=bool)
bear_divergence = np.zeros(n, dtype=bool)
bull_divergence = np.zeros(n, dtype=bool)

for index in range(n):
    start = max(0, index - divergence_lookback + 1)
    window = rsi[start:index + 1]
    finite_positions = np.flatnonzero(np.isfinite(window))
    if len(finite_positions):
        values = window[finite_positions]
        high_value = float(np.max(values))
        low_value = float(np.min(values))
        latest_high = int(finite_positions[np.flatnonzero(values == high_value)[-1]])
        latest_low = int(finite_positions[np.flatnonzero(values == low_value)[-1]])
        highest_offset[index] = len(window) - 1 - latest_high
        lowest_offset[index] = len(window) - 1 - latest_low

    prior_high_price = tracked_high_price[index - 1] if index else np.nan
    prior_high_rsi = tracked_high_rsi[index - 1] if index else np.nan
    prior_low_price = tracked_low_price[index - 1] if index else np.nan
    prior_low_rsi = tracked_low_rsi[index - 1] if index else np.nan
    tracked_high_price[index] = close[index] if highest_offset[index] == 0 or not np.isfinite(prior_high_price) else prior_high_price
    tracked_high_rsi[index] = rsi[index] if highest_offset[index] == 0 or not np.isfinite(prior_high_rsi) else prior_high_rsi
    tracked_low_price[index] = close[index] if lowest_offset[index] == 0 or not np.isfinite(prior_low_price) else prior_low_price
    tracked_low_rsi[index] = rsi[index] if lowest_offset[index] == 0 or not np.isfinite(prior_low_rsi) else prior_low_rsi
    if close[index] > tracked_high_price[index]:
        tracked_high_price[index] = close[index]
    if np.isfinite(rsi[index]) and (not np.isfinite(tracked_high_rsi[index]) or rsi[index] > tracked_high_rsi[index]):
        tracked_high_rsi[index] = rsi[index]
    if close[index] < tracked_low_price[index]:
        tracked_low_price[index] = close[index]
    if np.isfinite(rsi[index]) and (not np.isfinite(tracked_low_rsi[index]) or rsi[index] < tracked_low_rsi[index]):
        tracked_low_rsi[index] = rsi[index]

    if index >= 3:
        pivot_high[index] = np.isfinite(tracked_high_rsi[index]) and tracked_high_rsi[index] == tracked_high_rsi[index - 2] and tracked_high_rsi[index - 2] != tracked_high_rsi[index - 3]
        pivot_low[index] = np.isfinite(tracked_low_rsi[index]) and tracked_low_rsi[index] == tracked_low_rsi[index - 2] and tracked_low_rsi[index - 2] != tracked_low_rsi[index - 3]
    if index >= 2 and np.all(np.isfinite([tracked_high_price[index - 1], tracked_high_price[index - 2], rsi[index - 1], tracked_high_rsi[index], rsi[index]])):
        bear_divergence[index] = tracked_high_price[index - 1] > tracked_high_price[index - 2] and rsi[index - 1] < tracked_high_rsi[index] and rsi[index] <= rsi[index - 1]
    if index >= 2 and np.all(np.isfinite([tracked_low_price[index - 1], tracked_low_price[index - 2], rsi[index - 1], tracked_low_rsi[index], rsi[index]])):
        bull_divergence[index] = tracked_low_price[index - 1] < tracked_low_price[index - 2] and rsi[index - 1] > tracked_low_rsi[index] and rsi[index] >= rsi[index - 1]

rsi_colors = ["#F23645" if np.isfinite(value) and (value > overbought or value < oversold) else "#111111" for value in rsi]
upper_cloud = [{"upper": float(overbought), "lower": float(midline), "color": "#00FF00", "opacity": 0.04} for _ in range(n)]
lower_cloud = [{"upper": float(midline), "lower": float(oversold), "color": "#F23645", "opacity": 0.04} for _ in range(n)]


def _backpaint(mask, offset, prices, text, color, event_type, enabled=True):
    data = [None] * n
    if enabled:
        for index, active in enumerate(mask):
            target = index - offset
            if active and target >= 0 and np.isfinite(prices[index]):
                data[target] = {"active": True, "price": float(prices[index]), "text": "" if shorter_labels else text, "color": color}
    return {"type": event_type, "name": text, "data": data, "color": color, "renderMode": "events"}


background_colors = [None] * n
if hide_labels:
    for index in range(n):
        if bear_divergence[index] and index >= 1:
            background_colors[index - 1] = "rgba(242,54,69,0.50)"
        elif bull_divergence[index] and index >= 1:
            background_colors[index - 1] = "rgba(8,153,129,0.50)"
        if not hide_pivots and (pivot_high[index] or pivot_low[index]) and index >= 2:
            background_colors[index - 2] = "rgba(41,98,255,0.50)"

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "RSI", "data": _finite(rsi), "color": rsi_colors, "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Overbought", "data": [float(overbought)] * n, "color": "#787B86", "type": "line", "overlay": False},
        {"name": "Oversold", "data": [float(oversold)] * n, "color": "#787B86", "type": "line", "overlay": False},
        {"name": "Midline", "title": not hide_midline, "data": [None if hide_midline else float(midline)] * n, "color": "rgba(0,0,0,0.20)", "type": "line", "overlay": False},
        {"name": "Upper Zone", "title": False, "data": upper_cloud, "color": "#00FF00", "type": "cloud", "overlay": False},
        {"name": "Lower Zone", "title": False, "data": lower_cloud, "color": "#F23645", "type": "cloud", "overlay": False},
    ],
    "signals": [
        _backpaint(bear_divergence, 1, np.r_[np.nan, rsi[:-1] + 1.0], "BEAR", "#F23645", "sell", not hide_labels),
        _backpaint(bull_divergence, 1, np.r_[np.nan, rsi[:-1] - 1.0], "BULL", "#089981", "buy", not hide_labels),
        _backpaint(pivot_high, 2, tracked_high_rsi + 1.0, "PIVOT", "#2962FF", "sell", not hide_labels and not hide_pivots),
        _backpaint(pivot_low, 2, tracked_low_rsi - 1.0, "PIVOT", "#2962FF", "buy", not hide_labels and not hide_pivots),
    ],
    "layers": [], "tables": [], "bar_colors": [None] * n, "background_colors": background_colors,
    "calculatedVars": {
        "change": _finite(change), "averageUp": _finite(average_up), "averageDown": _finite(average_down), "rsi": _finite(rsi),
        "highestOffset": [int(value) for value in highest_offset], "lowestOffset": [int(value) for value in lowest_offset],
        "trackedHighPrice": _finite(tracked_high_price), "trackedHighRsi": _finite(tracked_high_rsi), "trackedLowPrice": _finite(tracked_low_price), "trackedLowRsi": _finite(tracked_low_rsi),
        "pivotHigh": pivot_high.tolist(), "pivotLow": pivot_low.tolist(), "bearDivergence": bear_divergence.tolist(), "bullDivergence": bull_divergence.tolist(),
    },
}
