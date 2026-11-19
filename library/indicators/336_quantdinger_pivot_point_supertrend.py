# ============================================================
# QuantDinger Pivot Point SuperTrend
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Pivot Point SuperTrend"
my_indicator_description = "Combines a weighted pivot center with Wilder ATR bands to produce a ratcheting trend stop, reversal labels, and optional pivot support/resistance."

# @param pivot_period int 2 Pivot point period range=1:50:1
# @param atr_factor float 3.0 ATR factor range=1:20:0.1
# @param atr_period int 10 ATR period range=1:500:1
# @param show_pivots bool false Show pivot points
# @param show_labels bool true Show buy/sell labels
# @param show_center bool false Show pivot center line
# @param show_support_resistance bool false Show support/resistance

pivot_period = min(50, max(1, int(params.get("pivot_period", 2))))
atr_factor = max(1.0, float(params.get("atr_factor", 3.0)))
atr_period = max(1, int(params.get("atr_period", 10)))
show_pivots = bool(params.get("show_pivots", False))
show_labels = bool(params.get("show_labels", True))
show_center = bool(params.get("show_center", False))
show_support_resistance = bool(params.get("show_support_resistance", False))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _atr(high_values, low_values, close_values, length):
    size = len(close_values)
    true_range = np.full(size, np.nan, dtype=float)
    result = np.full(size, np.nan, dtype=float)
    for index in range(size):
        true_range[index] = (
            high_values[index] - low_values[index]
            if index == 0
            else max(
                high_values[index] - low_values[index],
                abs(high_values[index] - close_values[index - 1]),
                abs(low_values[index] - close_values[index - 1]),
            )
        )
        if index == length - 1:
            result[index] = float(np.mean(true_range[:length]))
        elif index >= length:
            result[index] = (result[index - 1] * (length - 1) + true_range[index]) / length
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

atr = _atr(high, low, close, atr_period)
pivot_high = np.full(n, np.nan, dtype=float)
pivot_low = np.full(n, np.nan, dtype=float)
center_line = np.full(n, np.nan, dtype=float)
upper_band = np.full(n, np.nan, dtype=float)
lower_band = np.full(n, np.nan, dtype=float)
trailing_up = np.full(n, np.nan, dtype=float)
trailing_down = np.full(n, np.nan, dtype=float)
trailing_stop = np.full(n, np.nan, dtype=float)
visible_stop = np.full(n, np.nan, dtype=float)
trend = np.ones(n, dtype=int)
buy_signal = np.zeros(n, dtype=bool)
sell_signal = np.zeros(n, dtype=bool)
support_state = np.full(n, np.nan, dtype=float)
resistance_state = np.full(n, np.nan, dtype=float)
support_display = np.full(n, np.nan, dtype=float)
resistance_display = np.full(n, np.nan, dtype=float)
current_center = np.nan
current_support = np.nan
current_resistance = np.nan

for index in range(n):
    center_index = index - pivot_period
    if index >= pivot_period * 2:
        high_window = high[center_index - pivot_period:center_index + pivot_period + 1]
        low_window = low[center_index - pivot_period:center_index + pivot_period + 1]
        high_match = all(high[center_index] >= value for value in high_window[:pivot_period])
        high_match = high_match and all(high[center_index] > value for value in high_window[pivot_period + 1:])
        low_match = all(low[center_index] <= value for value in low_window[:pivot_period])
        low_match = low_match and all(low[center_index] < value for value in low_window[pivot_period + 1:])
        if high_match:
            pivot_high[index] = high[center_index]
        if low_match:
            pivot_low[index] = low[center_index]

    latest_pivot = pivot_high[index] if np.isfinite(pivot_high[index]) else pivot_low[index]
    if np.isfinite(latest_pivot):
        current_center = float(latest_pivot) if not np.isfinite(current_center) else (current_center * 2.0 + latest_pivot) / 3.0
    center_line[index] = current_center
    if np.all(np.isfinite([current_center, atr[index]])):
        upper_band[index] = current_center - atr_factor * atr[index]
        lower_band[index] = current_center + atr_factor * atr[index]

    previous_up = trailing_up[index - 1] if index > 0 else np.nan
    previous_down = trailing_down[index - 1] if index > 0 else np.nan
    previous_close = close[index - 1] if index > 0 else np.nan
    trailing_up[index] = max(upper_band[index], previous_up) if np.all(np.isfinite([previous_close, previous_up, upper_band[index]])) and previous_close > previous_up else upper_band[index]
    trailing_down[index] = min(lower_band[index], previous_down) if np.all(np.isfinite([previous_close, previous_down, lower_band[index]])) and previous_close < previous_down else lower_band[index]
    previous_trend = trend[index - 1] if index > 0 else 1
    if np.isfinite(previous_down) and close[index] > previous_down:
        trend[index] = 1
    elif np.isfinite(previous_up) and close[index] < previous_up:
        trend[index] = -1
    else:
        trend[index] = previous_trend
    trailing_stop[index] = trailing_up[index] if trend[index] == 1 else trailing_down[index]
    if index > 0 and trend[index] == trend[index - 1]:
        visible_stop[index] = trailing_stop[index]
    if index > 0:
        buy_signal[index] = trend[index] == 1 and trend[index - 1] == -1
        sell_signal[index] = trend[index] == -1 and trend[index - 1] == 1

    if np.isfinite(pivot_low[index]):
        current_support = float(pivot_low[index])
    if np.isfinite(pivot_high[index]):
        current_resistance = float(pivot_high[index])
    support_state[index] = current_support
    resistance_state[index] = current_resistance
    target = index - pivot_period
    if 0 <= target < n:
        support_display[target] = current_support
        resistance_display[target] = current_resistance

center_colors = ["#0000FF" if np.isfinite(center_line[index]) and center_line[index] < (high[index] + low[index]) / 2.0 else "#FF0000" for index in range(n)]
stop_colors = ["#00FF00" if trend[index] == 1 else "#FF0000" for index in range(n)]


def _pivot_points(values, high_side):
    result = [None] * n
    if not show_pivots:
        return result
    for confirmation in np.flatnonzero(np.isfinite(values)):
        target = confirmation - pivot_period
        if 0 <= target < n:
            result[target] = {
                "active": True, "price": float(values[confirmation]), "text": "H" if high_side else "L",
                "color": "#FF0000" if high_side else "#00FF00",
            }
    return result


signals = [
    {"type": "sell", "name": "Pivot High", "data": _pivot_points(pivot_high, True), "color": "#FF0000", "renderMode": "events"},
    {"type": "buy", "name": "Pivot Low", "data": _pivot_points(pivot_low, False), "color": "#00FF00", "renderMode": "events"},
    {"type": "buy", "name": "Buy", "data": [{"active": True, "price": float(trailing_stop[index]), "text": "Buy", "color": "#00FF00"} if show_labels and buy_signal[index] and np.isfinite(trailing_stop[index]) else None for index in range(n)], "color": "#00FF00", "renderMode": "events"},
    {"type": "sell", "name": "Sell", "data": [{"active": True, "price": float(trailing_stop[index]), "text": "Sell", "color": "#FF0000"} if show_labels and sell_signal[index] and np.isfinite(trailing_stop[index]) else None for index in range(n)], "color": "#FF0000", "renderMode": "events"},
]

output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "Pivot Point SuperTrend", "data": _finite(visible_stop), "color": stop_colors, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Pivot Center", "data": _finite(center_line) if show_center else [None] * n, "color": center_colors, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Pivot Support", "data": _finite(support_display) if show_support_resistance else [None] * n, "color": "#00FF00", "type": "scatter", "size": 2, "overlay": True},
        {"name": "Pivot Resistance", "data": _finite(resistance_display) if show_support_resistance else [None] * n, "color": "#FF0000", "type": "scatter", "size": 2, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": signals, "layers": [], "tables": [],
    "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "atr": _finite(atr), "pivotHigh": _finite(pivot_high), "pivotLow": _finite(pivot_low),
        "center": _finite(center_line), "upperBand": _finite(upper_band), "lowerBand": _finite(lower_band),
        "trailingUp": _finite(trailing_up), "trailingDown": _finite(trailing_down),
        "trailingStop": _finite(trailing_stop), "visibleStop": _finite(visible_stop), "trend": trend.tolist(),
        "buySignal": buy_signal.tolist(), "sellSignal": sell_signal.tolist(),
        "support": _finite(support_state), "resistance": _finite(resistance_state),
        "supportDisplay": _finite(support_display), "resistanceDisplay": _finite(resistance_display),
    },
}
