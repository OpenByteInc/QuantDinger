# ============================================================
# QuantDinger Follow Line Classic
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Follow Line Classic"
my_indicator_description = (
    "A Bollinger breakout state machine that trails price by the current low "
    "or high, optionally offset by Wilder ATR."
)

# @param atr_period int 5 ATR period range=1:300:1
# @param band_period int 21 Bollinger period range=1:300:1
# @param band_deviation float 1.0 Bollinger deviation range=0.1:10.0:0.1
# @param use_atr_filter bool true Apply ATR offset
# @param show_signals bool true Show reversal labels

atr_period = max(1, int(params.get("atr_period", 5)))
band_period = max(1, int(params.get("band_period", 21)))
band_deviation = max(0.1, float(params.get("band_deviation", 1.0)))
use_atr_filter = bool(params.get("use_atr_filter", True))
show_signals = bool(params.get("show_signals", True))

df = df.copy()
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)
n = len(df)

band_middle = close.rolling(window=band_period, min_periods=band_period).mean()
band_spread = close.rolling(window=band_period, min_periods=band_period).std(ddof=0) * band_deviation
band_upper = band_middle + band_spread
band_lower = band_middle - band_spread

previous_close = close.shift(1)
true_range = pd.concat(
    [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
).max(axis=1, skipna=True).to_numpy(dtype=float)
atr = np.full(n, np.nan, dtype=float)
if n >= atr_period:
    atr[atr_period - 1] = float(np.mean(true_range[:atr_period]))
    for index in range(atr_period, n):
        atr[index] = (atr[index - 1] * (atr_period - 1.0) + true_range[index]) / atr_period

high_values = high.to_numpy(dtype=float)
low_values = low.to_numpy(dtype=float)
close_values = close.to_numpy(dtype=float)
upper_values = band_upper.to_numpy(dtype=float)
lower_values = band_lower.to_numpy(dtype=float)
breakout_state = np.zeros(n, dtype=int)
follow_line = np.zeros(n, dtype=float)
trend_state = np.zeros(n, dtype=int)
for index in range(n):
    previous_breakout = breakout_state[index - 1] if index > 0 else 0
    if np.isfinite(upper_values[index]) and close_values[index] > upper_values[index]:
        current_breakout = 1
    elif np.isfinite(lower_values[index]) and close_values[index] < lower_values[index]:
        current_breakout = -1
    else:
        current_breakout = previous_breakout
    breakout_state[index] = current_breakout

    previous_line = follow_line[index - 1] if index > 0 else 0.0
    offset = atr[index] if use_atr_filter and np.isfinite(atr[index]) else 0.0
    current_line = previous_line
    if current_breakout == 1:
        current_line = max(low_values[index] - offset, previous_line)
    elif current_breakout == -1:
        candidate = high_values[index] + offset
        current_line = min(candidate, previous_line)
    follow_line[index] = current_line

    previous_trend = trend_state[index - 1] if index > 0 else 0
    if current_line > previous_line:
        trend_state[index] = 1
    elif current_line < previous_line:
        trend_state[index] = -1
    else:
        trend_state[index] = previous_trend

buy_event = np.concatenate(([False], (trend_state[:-1] == -1) & (trend_state[1:] == 1)))
sell_event = np.concatenate(([False], (trend_state[:-1] == 1) & (trend_state[1:] == -1)))
line_plot = []
for index in range(n):
    line_plot.append(
        {
            "value": float(follow_line[index]),
            "color": "#0962E8" if trend_state[index] > 0 else "#DC143C",
        }
    )

buy_marks = [
    float(follow_line[index] - (atr[index] if np.isfinite(atr[index]) else 0.0))
    if show_signals and buy_event[index]
    else None
    for index in range(n)
]
sell_marks = [
    float(follow_line[index] + (atr[index] if np.isfinite(atr[index]) else 0.0))
    if show_signals and sell_event[index]
    else None
    for index in range(n)
]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Follow Line", "data": line_plot, "color": "#0962E8", "type": "line", "lineWidth": 2, "overlay": True}
    ],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "Buy", "data": buy_marks, "color": "#0000FF", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "Sell", "data": sell_marks, "color": "#660F0F", "renderMode": "events"},
    ],
    "layers": [],
}
