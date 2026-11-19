# ============================================================
# QuantDinger Parabolic SAR
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Parabolic SAR"
my_indicator_description = "Displays standard parabolic SAR with direction-change markers, optional labels, and directional price-state shading."

# @param sar_start float 0.02 Initial acceleration range=0.001:1:0.001
# @param sar_increment float 0.02 Acceleration increment range=0.001:1:0.001
# @param sar_maximum float 0.2 Maximum acceleration range=0.001:2:0.001
# @param point_width int 2 Point width range=1:5:1
# @param show_labels bool true Show buy and sell labels
# @param highlight_start_points bool true Highlight direction starts
# @param highlight_state bool true Shade the active direction

sar_start = min(1.0, max(0.001, float(params.get("sar_start", 0.02))))
sar_increment = min(1.0, max(0.001, float(params.get("sar_increment", 0.02))))
sar_maximum = min(2.0, max(0.001, float(params.get("sar_maximum", 0.2))))
point_width = min(5, max(1, int(params.get("point_width", 2))))
show_labels = bool(params.get("show_labels", True))
highlight_start_points = bool(params.get("highlight_start_points", True))
highlight_state = bool(params.get("highlight_state", True))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


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

sar = np.full(n, np.nan, dtype=float)
acceleration_series = np.full(n, np.nan, dtype=float)
extreme_series = np.full(n, np.nan, dtype=float)
result = extreme = acceleration = np.nan
is_below = False
for index in range(n):
    first_trend_bar = False
    if index == 1:
        if close[index] > close[index - 1]:
            is_below, extreme, result = True, high[index], low[index - 1]
        else:
            is_below, extreme, result = False, low[index], high[index - 1]
        first_trend_bar, acceleration = True, sar_start
    if np.all(np.isfinite([result, acceleration, extreme])):
        result = result + acceleration * (extreme - result)
    if is_below and np.isfinite(result) and result > low[index]:
        first_trend_bar, is_below = True, False
        result, extreme, acceleration = max(high[index], extreme), low[index], sar_start
    elif (not is_below) and np.isfinite(result) and result < high[index]:
        first_trend_bar, is_below = True, True
        result, extreme, acceleration = min(low[index], extreme), high[index], sar_start
    if not first_trend_bar:
        if is_below and np.isfinite(extreme) and high[index] > extreme:
            extreme, acceleration = high[index], min(acceleration + sar_increment, sar_maximum)
        elif (not is_below) and np.isfinite(extreme) and low[index] < extreme:
            extreme, acceleration = low[index], min(acceleration + sar_increment, sar_maximum)
    if is_below and np.isfinite(result):
        if index >= 1:
            result = min(result, low[index - 1])
        if index > 1:
            result = min(result, low[index - 2])
    elif (not is_below) and np.isfinite(result):
        if index >= 1:
            result = max(result, high[index - 1])
        if index > 1:
            result = max(result, high[index - 2])
    sar[index], acceleration_series[index], extreme_series[index] = result, acceleration, extreme

direction = np.where(sar < close, 1, -1)
buy_signal = np.zeros(n, dtype=bool)
sell_signal = np.zeros(n, dtype=bool)
for index in range(1, n):
    buy_signal[index] = direction[index] == 1 and direction[index - 1] == -1
    sell_signal[index] = direction[index] == -1 and direction[index - 1] == 1
sar_colors = ["#3388BB" if direction[index] == 1 else "#FDCC02" for index in range(n)]
ohlc4 = (opened + high + low + close) / 4.0
state_fill = []
for index in range(n):
    if highlight_state and np.isfinite(sar[index]):
        state_fill.append({
            "upper": float(max(ohlc4[index], sar[index])), "lower": float(min(ohlc4[index], sar[index])),
            "color": "rgba(0,128,0,0.15)" if direction[index] == 1 else "rgba(255,0,0,0.15)",
        })
    else:
        state_fill.append(None)
start_up = [float(sar[index]) if highlight_start_points and buy_signal[index] else None for index in range(n)]
start_down = [float(sar[index]) if highlight_start_points and sell_signal[index] else None for index in range(n)]

output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "PSAR", "data": _finite(sar), "color": sar_colors, "type": "circle", "size": point_width, "overlay": True},
        {"name": "Long Start", "data": start_up, "color": "#008000", "type": "circle", "size": 4, "overlay": True},
        {"name": "Short Start", "data": start_down, "color": "#FF0000", "type": "circle", "size": 4, "overlay": True},
        {"name": "Trade State", "title": False, "data": state_fill, "color": "#008000", "type": "cloud", "overlay": True},
    ],
    "signals": [
        {"type": "buy", "name": "Buy", "text": "Buy", "data": [float(sar[index]) if show_labels and buy_signal[index] else None for index in range(n)], "color": "#008000", "renderMode": "events"},
        {"type": "sell", "name": "Sell", "text": "Sell", "data": [float(sar[index]) if show_labels and sell_signal[index] else None for index in range(n)], "color": "#FF0000", "renderMode": "events"},
    ],
    "layers": [], "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "sar": _finite(sar), "direction": [int(value) for value in direction], "buySignal": buy_signal.tolist(), "sellSignal": sell_signal.tolist(),
        "acceleration": _finite(acceleration_series), "extreme": _finite(extreme_series),
        "showLabels": show_labels, "highlightStartPoints": highlight_start_points, "highlightState": highlight_state,
    },
}
