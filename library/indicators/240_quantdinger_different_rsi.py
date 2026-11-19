# ============================================================
# QuantDinger Different RSI
# Independent Python implementation for the QuantDinger runtime.
# ============================================================

my_indicator_name = "QuantDinger Different RSI"
my_indicator_description = (
    "Compares the current source with its lookback window using squared directional "
    "differences, then combines the oscillator with an EMA-driven persistent trend state."
)

# @param source str close Source values=open,high,low,close,hl2,hlc3,ohlc4
# @param lookback int 45 Lookback range=2:500:1
# @param smoothing int 12 Smoothing range=2:500:1

source_name = str(params.get("source", "close")).lower()
lookback = min(500, max(2, int(params.get("lookback", 45))))
smoothing = min(500, max(2, int(params.get("smoothing", 12))))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

sources = {
    "open": open_price,
    "high": high,
    "low": low,
    "close": close,
    "hl2": (high + low) / 2.0,
    "hlc3": (high + low + close) / 3.0,
    "ohlc4": (open_price + high + low + close) / 4.0,
}
source = np.asarray(sources.get(source_name, close), dtype=float)


def pine_ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (float(length) + 1.0)
    state = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        state = float(value) if not np.isfinite(state) else alpha * float(value) + (1.0 - alpha) * state
        result[index] = state
    return result


different_rsi = np.full(n, np.nan, dtype=float)
gain_energy = np.full(n, np.nan, dtype=float)
loss_energy = np.full(n, np.nan, dtype=float)
for index in range(lookback - 1, n):
    differences = source[index] - source[index - lookback + 1:index]
    gains = np.maximum(differences, 0.0)
    losses = np.abs(np.minimum(differences, 0.0))
    gain = float(np.sum(gains * gains))
    loss = float(np.sum(losses * losses))
    gain_energy[index] = gain
    loss_energy[index] = loss
    if gain == 0.0:
        different_rsi[index] = 0.0
    elif loss == 0.0:
        different_rsi[index] = 100.0
    else:
        ratio = np.sqrt(gain) / np.sqrt(loss)
        different_rsi[index] = 100.0 - 100.0 / (1.0 + ratio)

rsi_average = pine_ema(different_rsi, smoothing)
trend_state = np.zeros(n, dtype=int)
state = 0
for index in range(n):
    rsi_value = different_rsi[index]
    average_value = rsi_average[index]
    if np.isfinite(rsi_value) and np.isfinite(average_value):
        if rsi_value > 50.0 and rsi_value > average_value:
            state = 1
        if rsi_value < 50.0 and rsi_value < average_value:
            state = -1
    trend_state[index] = state

neutral_color = "#484848"
bull_color = "#00FFBB"
bear_color = "#FF009D"
trend_colors = [
    bull_color if value > 0 else bear_color if value < 0 else neutral_color
    for value in trend_state
]


def line_points(values, colors=None):
    points = []
    for index, value in enumerate(values):
        if not np.isfinite(value):
            points.append(None)
            continue
        point = {"value": float(value)}
        if colors is not None:
            point["color"] = colors[index]
        points.append(point)
    return points


middle = [{"value": 50.0} for _ in range(n)]
overbought = [{"value": 80.0} for _ in range(n)]
oversold = [{"value": 20.0} for _ in range(n)]
upper_fill = [
    {"upper": 80.0, "lower": 50.0, "color": "rgba(0,195,237,0.15)"}
    for _ in range(n)
]
lower_fill = [
    {"upper": 50.0, "lower": 20.0, "color": "rgba(255,0,157,0.15)"}
    for _ in range(n)
]

candles = [
    {
        "open": float(open_price[index]),
        "high": float(high[index]),
        "low": float(low[index]),
        "close": float(close[index]),
        "color": trend_colors[index],
        "fillColor": trend_colors[index],
        "borderColor": trend_colors[index],
        "wickColor": trend_colors[index],
    }
    for index in range(n)
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Trend Candles", "title": False, "data": candles, "type": "candlestick", "overlay": True},
        {"name": "Middle", "title": False, "data": middle, "color": "#474646", "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "Overbought", "title": False, "data": overbought, "color": bull_color, "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "Oversold", "title": False, "data": oversold, "color": bear_color, "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "Upper Zone", "title": False, "data": upper_fill, "type": "cloud", "overlay": False},
        {"name": "Lower Zone", "title": False, "data": lower_fill, "type": "cloud", "overlay": False},
        {"name": "Different RSI", "data": line_points(different_rsi, trend_colors), "color": neutral_color, "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "MA", "data": line_points(rsi_average), "color": "#FFEB3B", "type": "line", "lineWidth": 1, "overlay": False},
    ],
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": trend_colors,
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "differentRsi": [float(value) if np.isfinite(value) else None for value in different_rsi],
        "rsiAverage": [float(value) if np.isfinite(value) else None for value in rsi_average],
        "gainEnergy": [float(value) if np.isfinite(value) else None for value in gain_energy],
        "lossEnergy": [float(value) if np.isfinite(value) else None for value in loss_energy],
        "trendState": [int(value) for value in trend_state],
    },
}
