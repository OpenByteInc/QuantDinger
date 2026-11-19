# ============================================================
# QuantDinger Regression Oscillator
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Regression Oscillator"
my_indicator_description = "Normalizes a reverse-index regression projection, marks zero and extreme reversions, and retains five-bar invalidation levels."

# @param length int 20 Regression lookback range=2:500:1
# @param upper float 1.5 Upper threshold range=0.1:5.0:0.1
# @param lower float -1.5 Lower threshold range=-5.0:-0.1:0.1
# @param color_candles bool false Color host candles
# @param show_invalidation_labels bool false Show repeated invalidation text labels
# @param up_color color #10CAB8 Positive color
# @param down_color color #2962FF Negative color

length = min(500, max(2, int(params.get("length", 20))))
upper = min(5.0, max(0.1, float(params.get("upper", 1.5))))
lower = min(-0.1, max(-5.0, float(params.get("lower", -1.5))))
color_candles = bool(params.get("color_candles", False))
show_invalidation_labels = bool(params.get("show_invalidation_labels", False))
up_color = str(params.get("up_color", "#10CAB8"))
down_color = str(params.get("down_color", "#2962FF"))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

raw_regression = np.full(n, np.nan, dtype=float)
x_values = np.arange(length, dtype=float)
sum_x = float(np.sum(x_values)); sum_x_squared = float(np.sum(x_values * x_values)); denominator = float(length * sum_x_squared - sum_x * sum_x)
for index in range(length - 1, n):
    values = close[index - length + 1:index + 1][::-1]
    if np.all(np.isfinite(values)):
        sum_y = float(np.sum(values)); sum_xy = float(np.sum(x_values * values))
        slope_value = float((length * sum_xy - sum_x * sum_y) / denominator)
        intercept_value = float((sum_y - slope_value * sum_x) / length)
        raw_regression[index] = -(slope_value * index + intercept_value)

rolling_mean = pd.Series(raw_regression).rolling(100, min_periods=100).mean().to_numpy(dtype=float)
rolling_std = pd.Series(raw_regression).rolling(100, min_periods=100).std(ddof=0).to_numpy(dtype=float)
oscillator = np.divide(raw_regression - rolling_mean, rolling_std, out=np.full(n, np.nan), where=np.isfinite(rolling_std) & (rolling_std != 0.0))

cross_down_zero = np.zeros(n, dtype=bool); cross_up_zero = np.zeros(n, dtype=bool); reversion_sell = np.zeros(n, dtype=bool); reversion_buy = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.all(np.isfinite([oscillator[index], oscillator[index - 1]])):
        cross_down_zero[index] = oscillator[index] < 0.0 and oscillator[index - 1] >= 0.0
        cross_up_zero[index] = oscillator[index] > 0.0 and oscillator[index - 1] <= 0.0
    if index >= 3 and np.all(np.isfinite([oscillator[index], oscillator[index - 1], oscillator[index - 2], oscillator[index - 3]])):
        reversion_sell[index] = oscillator[index] < oscillator[index - 2] and oscillator[index - 1] >= oscillator[index - 3] and oscillator[index] > upper
        reversion_buy[index] = oscillator[index] > oscillator[index - 2] and oscillator[index - 1] <= oscillator[index - 3] and oscillator[index] < lower

points = np.full(n, np.nan, dtype=float)
active_level = np.nan
for index in range(n):
    start = max(0, index - 4)
    if cross_down_zero[index]: active_level = float(np.max(high[start:index + 1]))
    elif cross_up_zero[index]: active_level = float(np.min(low[start:index + 1]))
    points[index] = active_level

point_changed = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(points[index]) and np.isfinite(points[index - 1]):
        point_changed[index] = points[index] != points[index - 1]
invalidation = np.where(np.isfinite(points) & ~point_changed, points, np.nan)


def _rgba(color, alpha):
    value = str(color).strip()
    if value.startswith("#") and len(value) == 7:
        red = int(value[1:3], 16); green = int(value[3:5], 16); blue = int(value[5:7], 16)
        return f"rgba({red},{green},{blue},{max(0.0, min(1.0, float(alpha))):.3f})"
    return value


oscillator_points = []
oscillator_area = []
invalidation_points = []
for index, value in enumerate(oscillator):
    if not np.isfinite(value):
        oscillator_points.append(None); oscillator_area.append(None)
    else:
        alpha = min(1.0, abs(float(value)) / 2.0)
        color = _rgba(up_color if value > 0.0 else down_color, alpha)
        oscillator_points.append({"value": float(value), "color": color})
        oscillator_area.append({"upper": float(max(value, 0.0)), "lower": float(min(value, 0.0)), "color": color})
    if np.isfinite(invalidation[index]):
        if np.isfinite(oscillator[index]):
            alpha = min(1.0, abs(float(oscillator[index])))
            color = _rgba(up_color if oscillator[index] > 0.0 else down_color, alpha)
        else:
            color = "rgba(148,163,184,0.50)"
        invalidation_points.append({"value": float(invalidation[index]), "color": color})
    else:
        invalidation_points.append(None)

pane_reversion_sell = [None] * n; pane_reversion_buy = [None] * n; pane_zero_down = [None] * n; pane_zero_up = [None] * n
for index in range(1, n):
    target = index - 1
    if reversion_sell[index] and np.isfinite(oscillator[target]): pane_reversion_sell[target] = float(oscillator[target])
    if reversion_buy[index] and np.isfinite(oscillator[target]): pane_reversion_buy[target] = float(oscillator[target])
    if cross_down_zero[index]: pane_zero_down[target] = 0.0
    if cross_up_zero[index]: pane_zero_up[target] = 0.0

layers = []
if n:
    layers.extend([
        {"type": "label", "index": n - 1, "price": float(upper), "text": str(upper), "textColor": "#E5E7EB", "color": up_color, "pane": "indicator", "size": "tiny"},
        {"type": "label", "index": n - 1, "price": float(lower), "text": str(lower), "textColor": "#E5E7EB", "color": down_color, "pane": "indicator", "size": "tiny"},
    ])
if show_invalidation_labels:
    for index in range(1, n):
        if point_changed[index] and np.isfinite(points[index - 1]):
            layers.append({"type": "label", "index": index, "price": float(points[index - 1]), "text": "Invalidation\nLevel", "textColor": up_color if np.isfinite(oscillator[index]) and oscillator[index] < 0.0 else down_color, "color": "rgba(0,0,0,0)", "size": "small"})

plots = [
    {"name": "Regression Oscillator", "data": oscillator_points, "color": up_color, "type": "line", "lineWidth": 2, "overlay": False},
    {"name": "Oscillator Area", "title": False, "data": oscillator_area, "color": up_color, "type": "cloud", "opacity": 0.45, "overlay": False},
    {"name": "Zero", "data": [0.0] * n, "color": "rgba(229,231,235,0.50)", "type": "line", "lineWidth": 2, "overlay": False},
    {"name": "Upper Extreme Zone", "title": False, "data": [{"upper": 4.0, "lower": float(upper), "color": up_color}] * n, "color": up_color, "type": "cloud", "opacity": 0.25, "overlay": False},
    {"name": "Lower Extreme Zone", "title": False, "data": [{"upper": float(lower), "lower": -4.0, "color": down_color}] * n, "color": down_color, "type": "cloud", "opacity": 0.25, "overlay": False},
    {"name": "Bearish Reversion Pane", "title": False, "data": pane_reversion_sell, "color": down_color, "type": "line", "lineWidth": 3, "overlay": False},
    {"name": "Bullish Reversion Pane", "title": False, "data": pane_reversion_buy, "color": up_color, "type": "line", "lineWidth": 3, "overlay": False},
    {"name": "Bearish Zero Pane", "title": False, "data": pane_zero_down, "color": down_color, "type": "line", "lineWidth": 3, "overlay": False},
    {"name": "Bullish Zero Pane", "title": False, "data": pane_zero_up, "color": up_color, "type": "line", "lineWidth": 3, "overlay": False},
    {"name": "Invalidation Level", "data": invalidation_points, "color": up_color, "type": "line", "lineWidth": 2, "overlay": True},
]
signals = [
    {"type": "buy", "text": "▲", "name": "Bullish Trend Change", "data": [float(low[i]) if cross_up_zero[i] else None for i in range(n)], "color": up_color, "renderMode": "events"},
    {"type": "sell", "text": "▽", "name": "Bearish Trend Change", "data": [float(high[i]) if cross_down_zero[i] else None for i in range(n)], "color": down_color, "renderMode": "events"},
    {"type": "buy", "text": "◆ Reversion", "name": "Bullish Mean Reversion", "data": [float(low[i]) if reversion_buy[i] else None for i in range(n)], "color": up_color, "renderMode": "events"},
    {"type": "sell", "text": "Reversion ◆", "name": "Bearish Mean Reversion", "data": [float(high[i]) if reversion_sell[i] else None for i in range(n)], "color": down_color, "renderMode": "events"},
]


def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]


output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers, "tables": [],
    "bar_colors": [up_color if color_candles and np.isfinite(value) and value >= 0.0 else down_color if color_candles and np.isfinite(value) else None for value in oscillator],
    "background_colors": [None] * n,
    "calculatedVars": {
        "rawRegression": _finite_or_none(raw_regression), "rollingMean": _finite_or_none(rolling_mean), "rollingStd": _finite_or_none(rolling_std), "oscillator": _finite_or_none(oscillator),
        "crossDownZero": cross_down_zero.tolist(), "crossUpZero": cross_up_zero.tolist(), "reversionSell": reversion_sell.tolist(), "reversionBuy": reversion_buy.tolist(),
        "points": _finite_or_none(points), "pointChanged": point_changed.tolist(), "invalidation": _finite_or_none(invalidation),
        "paneReversionSell": pane_reversion_sell, "paneReversionBuy": pane_reversion_buy, "paneZeroDown": pane_zero_down, "paneZeroUp": pane_zero_up,
    },
}
