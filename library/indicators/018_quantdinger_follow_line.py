# ============================================================
# QuantDinger Follow Line
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Follow Line"
my_indicator_description = (
    "A Bollinger-break trailing line with an optional ATR offset and trend-flip "
    "event markers."
)

# @param band_period int 21 Band period range=1:300:1
# @param band_deviation float 1.0 Band deviation range=0.1:10.0:0.05
# @param use_atr_filter bool true Apply ATR offset
# @param atr_period int 5 ATR period range=1:300:1
# @param hide_labels bool false Hide reversal labels

band_period = max(1, int(params.get("band_period", 21)))
band_deviation = float(params.get("band_deviation", 1.0))
use_atr_filter = bool(params.get("use_atr_filter", True))
atr_period = max(1, int(params.get("atr_period", 5)))
hide_labels = bool(params.get("hide_labels", False))

df = df.copy()
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)
n = len(df)
mean = close.rolling(window=band_period, min_periods=band_period).mean()
deviation = close.rolling(window=band_period, min_periods=band_period).std(ddof=0)
upper = mean + deviation * band_deviation
lower = mean - deviation * band_deviation
signal = np.where(close > upper, 1, np.where(close < lower, -1, 0))

previous_close = close.shift(1)
true_range = pd.concat(
    [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
).max(axis=1, skipna=True)
tr_values = true_range.to_numpy(dtype=float)
atr_values = np.full(n, np.nan, dtype=float)
if n >= atr_period:
    atr_values[atr_period - 1] = float(np.mean(tr_values[:atr_period]))
    for index in range(atr_period, n):
        atr_values[index] = (atr_values[index - 1] * (atr_period - 1) + tr_values[index]) / atr_period

high_values = high.to_numpy(dtype=float)
low_values = low.to_numpy(dtype=float)
trend_line = np.full(n, np.nan, dtype=float)
trend_state = np.zeros(n, dtype=int)
previous_line = 0.0
previous_state = 0
for index in range(n):
    current = previous_line
    offset = atr_values[index] if use_atr_filter and np.isfinite(atr_values[index]) else 0.0
    if signal[index] == 1:
        candidate = low_values[index] - offset
        current = max(candidate, previous_line)
    elif signal[index] == -1:
        candidate = high_values[index] + offset
        current = min(candidate, previous_line)
    if current > previous_line:
        current_state = 1
    elif current < previous_line:
        current_state = -1
    else:
        current_state = previous_state
    trend_line[index] = current
    trend_state[index] = current_state
    previous_line = current
    previous_state = current_state

label_tr = pd.concat(
    [high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1
).max(axis=1, skipna=True).to_numpy(dtype=float)
label_atr = np.full(n, np.nan, dtype=float)
if n >= 8:
    label_atr[7] = float(np.mean(label_tr[:8]))
    for index in range(8, n):
        label_atr[index] = (label_atr[index - 1] * 7.0 + label_tr[index]) / 8.0

buy_event = np.concatenate(([False], (trend_state[:-1] == -1) & (trend_state[1:] == 1)))
sell_event = np.concatenate(([False], (trend_state[:-1] == 1) & (trend_state[1:] == -1)))
line_plot = [{"value": float(trend_line[index]), "color": "#0000FF" if trend_state[index] > 0 else "#FF0000"} for index in range(n)]
buy_marks = [float(trend_line[index] - label_atr[index]) if not hide_labels and buy_event[index] and np.isfinite(label_atr[index]) else None for index in range(n)]
sell_marks = [float(trend_line[index] + label_atr[index]) if not hide_labels and sell_event[index] and np.isfinite(label_atr[index]) else None for index in range(n)]

output = {
    "name": my_indicator_name,
    "plots": [{"name": "Follow Line", "data": line_plot, "color": "#0000FF", "type": "line", "lineWidth": 2, "overlay": True}],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "Buy", "data": buy_marks, "color": "#0000FF", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "Sell", "data": sell_marks, "color": "#FF0000", "renderMode": "events"},
    ],
    "layers": [],
}
