# ============================================================
# QuantDinger Range Breakout
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Range Breakout"
my_indicator_description = "Maintains a 100-bar-reset ATR range channel and marks confirmed breakouts, midpoint re-entries, and optional fakeouts."

# @param channel_width float 4.0 Channel width multiplier range=0:100:0.1
# @param show_fakeouts bool false Show X fakeout signals
# @param filter_signals_by_trend bool false Filter midpoint signals by last breakout trend
# @param upper_color str #1DAC70 Upper channel color
# @param lower_color str #DF3A79 Lower channel color
# @param gradient bool true Use gradient channel fills
# @param fill_transparency int 80 Fill transparency range=0:100:1

channel_width = min(100.0, max(0.0, float(params.get("channel_width", 4.0))))
show_fakeouts = bool(params.get("show_fakeouts", False))
filter_signals_by_trend = bool(params.get("filter_signals_by_trend", False))
upper_color = str(params.get("upper_color", "#1DAC70"))
lower_color = str(params.get("lower_color", "#DF3A79"))
gradient = bool(params.get("gradient", True))
fill_transparency = min(100, max(0, int(params.get("fill_transparency", 80))))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    running = 0.0
    valid = 0
    for index, value in enumerate(values):
        if np.isfinite(value):
            running += float(value)
            valid += 1
        if index >= length and np.isfinite(values[index - length]):
            running -= float(values[index - length])
            valid -= 1
        if index >= length - 1 and valid == length:
            result[index] = running / length
    return result


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
                previous = sum(seed) / length
                result[index] = previous
        else:
            previous = (previous * (length - 1.0) + float(value)) / length
            result[index] = previous
    return result


def _crossover(first_now, first_previous, second_now, second_previous):
    return np.all(np.isfinite([first_now, first_previous, second_now, second_previous])) and first_now > second_now and first_previous <= second_previous


def _crossunder(first_now, first_previous, second_now, second_previous):
    return np.all(np.isfinite([first_now, first_previous, second_now, second_previous])) and first_now < second_now and first_previous >= second_previous


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)
mid_price = (high + low) / 2.0

true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    true_range[index] = high[index] - low[index] if index == 0 else max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
atr_200 = _rma(true_range, 200)
channel_atr = _sma(atr_200, 100) * channel_width

center = np.full(n, np.nan, dtype=float)
upper = np.full(n, np.nan, dtype=float)
lower = np.full(n, np.nan, dtype=float)
upper_mid = np.full(n, np.nan, dtype=float)
lower_mid = np.full(n, np.nan, dtype=float)
outside_count = np.zeros(n, dtype=int)
trend = np.zeros(n, dtype=bool)
cross_upper = np.zeros(n, dtype=bool)
cross_lower = np.zeros(n, dtype=bool)
buy_signal = np.zeros(n, dtype=bool)
sell_signal = np.zeros(n, dtype=bool)
fakeout_upper = np.zeros(n, dtype=bool)
fakeout_lower = np.zeros(n, dtype=bool)
channel_changed = np.zeros(n, dtype=bool)

center_state = np.nan
upper_state = np.nan
lower_state = np.nan
upper_mid_state = np.nan
lower_mid_state = np.nan
count_state = 0
trend_state = False

for index in range(n):
    previous_center = center[index - 1] if index else np.nan
    previous_upper = upper[index - 1] if index else np.nan
    previous_lower = lower[index - 1] if index else np.nan
    previous_upper_mid = upper_mid[index - 1] if index else np.nan
    previous_lower_mid = lower_mid[index - 1] if index else np.nan
    previous_low = low[index - 1] if index else np.nan
    previous_high = high[index - 1] if index else np.nan

    if index == 301 and np.isfinite(channel_atr[index]):
        center_state = mid_price[index]
        upper_state = center_state + channel_atr[index]
        lower_state = center_state - channel_atr[index]
        upper_mid_state = (center_state + upper_state) / 2.0
        lower_mid_state = (center_state + lower_state) / 2.0

    cross_upper[index] = _crossover(low[index], previous_low, upper_state, previous_upper)
    cross_lower[index] = _crossunder(high[index], previous_high, lower_state, previous_lower)
    if (np.isfinite(upper_state) and low[index] > upper_state) or (np.isfinite(lower_state) and high[index] < lower_state):
        count_state += 1
    if cross_upper[index] or cross_lower[index] or count_state == 100:
        count_state = 0
        center_state = mid_price[index]
        upper_state = center_state + channel_atr[index]
        lower_state = center_state - channel_atr[index]
        upper_mid_state = (center_state + upper_state) / 2.0
        lower_mid_state = (center_state + lower_state) / 2.0

    unchanged = np.isfinite(center_state) and np.isfinite(previous_center) and center_state == previous_center
    if index >= 10:
        buy_signal[index] = _crossover(low[index], previous_low, lower_mid_state, previous_lower_mid) and low[index - 10] > lower_mid_state and unchanged
        sell_signal[index] = _crossunder(high[index], previous_high, upper_mid_state, previous_upper_mid) and high[index - 10] < upper_mid_state and unchanged
    fakeout_upper[index] = _crossunder(high[index], previous_high, upper_state, previous_upper) and unchanged
    fakeout_lower[index] = _crossover(low[index], previous_low, lower_state, previous_lower) and unchanged
    if cross_upper[index]:
        trend_state = True
    if cross_lower[index]:
        trend_state = False

    center[index] = center_state
    upper[index] = upper_state
    lower[index] = lower_state
    upper_mid[index] = upper_mid_state
    lower_mid[index] = lower_mid_state
    outside_count[index] = count_state
    trend[index] = trend_state
    channel_changed[index] = np.isfinite(center_state) and (not np.isfinite(previous_center) or center_state != previous_center)

upper_display = upper.copy()
lower_display = lower.copy()
upper_mid_display = upper_mid.copy()
lower_mid_display = lower_mid.copy()
center_display = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index % 2 == 0:
        center_display[index] = center[index]
    if index == 0 or channel_changed[index]:
        upper_display[index] = np.nan
        lower_display[index] = np.nan
        upper_mid_display[index] = np.nan
        lower_mid_display[index] = np.nan

upper_cloud = []
lower_cloud = []
for index in range(n):
    if np.all(np.isfinite([upper_display[index], upper_mid_display[index], center[index]])):
        upper_cloud.append({"upper": float(upper_display[index]), "lower": float(upper_mid_display[index]), "color": upper_color, "opacity": (100 - fill_transparency) / 100.0, "gradient": gradient})
    else:
        upper_cloud.append(None)
    if np.all(np.isfinite([lower_display[index], lower_mid_display[index], center[index]])):
        lower_cloud.append({"upper": float(lower_mid_display[index]), "lower": float(lower_display[index]), "color": lower_color, "opacity": (100 - fill_transparency) / 100.0, "gradient": gradient})
    else:
        lower_cloud.append(None)


def _backpainted(mask, prices, text, color, side, enabled=True):
    data = [None] * n
    if not enabled:
        return data
    for confirmation in np.flatnonzero(mask):
        target = confirmation - 1
        if target >= 0 and np.isfinite(prices[target]):
            data[target] = {"active": True, "price": float(prices[target]), "text": text, "color": color}
    return data


visible_buy = buy_signal & ((not filter_signals_by_trend) | trend)
visible_sell = sell_signal & ((not filter_signals_by_trend) | (~trend))
output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "Upper Channel", "data": _finite(upper_display), "color": upper_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Center", "data": _finite(center_display), "color": "#FFFFFF", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Lower Channel", "data": _finite(lower_display), "color": lower_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Upper Mid", "data": _finite(upper_mid_display), "color": "rgba(255,255,255,0.50)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Lower Mid", "data": _finite(lower_mid_display), "color": "rgba(255,255,255,0.50)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Upper Fill", "title": False, "data": upper_cloud, "color": upper_color, "type": "cloud", "overlay": True},
        {"name": "Lower Fill", "title": False, "data": lower_cloud, "color": lower_color, "type": "cloud", "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "name": "Bullish Break", "data": _backpainted(cross_upper, upper, "⦿", upper_color, "buy"), "color": upper_color, "renderMode": "events"},
        {"type": "sell", "name": "Bearish Break", "data": _backpainted(cross_lower, lower, "⦿", lower_color, "sell"), "color": lower_color, "renderMode": "events"},
        {"type": "buy", "name": "Buy", "data": _backpainted(visible_buy, low, "▲", upper_color, "buy"), "color": upper_color, "renderMode": "events"},
        {"type": "sell", "name": "Sell", "data": _backpainted(visible_sell, high, "▼", lower_color, "sell"), "color": lower_color, "renderMode": "events"},
        {"type": "sell", "name": "Upper Fakeout", "data": _backpainted(fakeout_upper, high, "X", lower_color, "sell", show_fakeouts), "color": lower_color, "renderMode": "events"},
        {"type": "buy", "name": "Lower Fakeout", "data": _backpainted(fakeout_lower, low, "X", upper_color, "buy", show_fakeouts), "color": upper_color, "renderMode": "events"},
    ],
    "layers": [], "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "trueRange": _finite(true_range), "atr200": _finite(atr_200), "channelAtr": _finite(channel_atr), "center": _finite(center), "upper": _finite(upper), "lower": _finite(lower), "upperMid": _finite(upper_mid), "lowerMid": _finite(lower_mid),
        "outsideCount": [int(value) for value in outside_count], "channelChanged": channel_changed.tolist(), "trend": trend.tolist(), "crossUpper": cross_upper.tolist(), "crossLower": cross_lower.tolist(),
        "buySignal": buy_signal.tolist(), "sellSignal": sell_signal.tolist(), "fakeoutUpper": fakeout_upper.tolist(), "fakeoutLower": fakeout_lower.tolist(), "visibleBuy": visible_buy.tolist(), "visibleSell": visible_sell.tolist(),
    },
}
