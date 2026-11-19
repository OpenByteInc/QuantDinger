# ============================================================
# QuantDinger Cipher A Signals
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Cipher A Signals"
my_indicator_description = (
    "Combines an eight-EMA ribbon, WaveTrend crosses, RSI and candle flow "
    "into the original Cipher A family of chart markers."
)

# @param wt_channel_length int 9 WaveTrend channel length range=1:100:1
# @param wt_average_length int 13 WaveTrend average length range=1:100:1
# @param wt_ma_length int 3 WaveTrend signal length range=1:50:1
# @param wt_source str hlc3 WaveTrend source values=hlc3,close,open,high,low
# @param overbought_level float 53.0 WaveTrend overbought range=1.0:150.0:1.0
# @param oversold_level float -53.0 WaveTrend oversold range=-150.0:-1.0:1.0
# @param extreme_oversold float -80.0 Extreme oversold floor range=-200.0:-1.0:1.0
# @param show_ribbon bool true Show all eight EMA ribbon lines
# @param ema_1 int 5 EMA 1 length range=1:200:1
# @param ema_2 int 11 EMA 2 length range=1:200:1
# @param ema_3 int 15 EMA 3 length range=1:200:1
# @param ema_4 int 18 EMA 4 length range=1:200:1
# @param ema_5 int 21 EMA 5 length range=1:200:1
# @param ema_6 int 24 EMA 6 length range=1:200:1
# @param ema_7 int 28 EMA 7 length range=1:200:1
# @param ema_8 int 34 EMA 8 length range=1:200:1
# @param rsi_length int 14 RSI length range=1:100:1
# @param flow_period int 60 Candle flow period range=1:300:1
# @param flow_multiplier float 150.0 Candle flow multiplier range=1.0:500.0:1.0

wt_channel_length = min(100, max(1, int(params.get("wt_channel_length", 9))))
wt_average_length = min(100, max(1, int(params.get("wt_average_length", 13))))
wt_ma_length = min(50, max(1, int(params.get("wt_ma_length", 3))))
wt_source_name = str(params.get("wt_source", "hlc3")).lower()
overbought_level = min(150.0, max(1.0, float(params.get("overbought_level", 53.0))))
oversold_level = min(-1.0, max(-150.0, float(params.get("oversold_level", -53.0))))
extreme_oversold = min(-1.0, max(-200.0, float(params.get("extreme_oversold", -80.0))))
show_ribbon = bool(params.get("show_ribbon", True))
ema_lengths = [
    min(200, max(1, int(params.get("ema_1", 5)))),
    min(200, max(1, int(params.get("ema_2", 11)))),
    min(200, max(1, int(params.get("ema_3", 15)))),
    min(200, max(1, int(params.get("ema_4", 18)))),
    min(200, max(1, int(params.get("ema_5", 21)))),
    min(200, max(1, int(params.get("ema_6", 24)))),
    min(200, max(1, int(params.get("ema_7", 28)))),
    min(200, max(1, int(params.get("ema_8", 34)))),
]
rsi_length = min(100, max(1, int(params.get("rsi_length", 14))))
flow_period = min(300, max(1, int(params.get("flow_period", 60))))
flow_multiplier = min(500.0, max(1.0, float(params.get("flow_multiplier", 150.0))))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

def ema(values, length):
    result = np.full(n, np.nan, dtype=float)
    if n == 0:
        return result
    alpha = 2.0 / (float(length) + 1.0)
    result[0] = float(values[0])
    for index in range(1, n):
        result[index] = alpha * float(values[index]) + (1.0 - alpha) * result[index - 1]
    return result

def rma(values, length):
    result = np.full(n, np.nan, dtype=float)
    if n == 0:
        return result
    alpha = 1.0 / float(length)
    result[0] = float(values[0])
    for index in range(1, n):
        result[index] = alpha * float(values[index]) + (1.0 - alpha) * result[index - 1]
    return result

def rolling_mean(values, length):
    return pd.Series(values, dtype=float).rolling(length, min_periods=1).mean().to_numpy(dtype=float)

def rsi(values, length):
    changes = np.zeros(n, dtype=float)
    if n > 1:
        changes[1:] = np.diff(values)
    average_gain = rma(np.maximum(changes, 0.0), length)
    average_loss = rma(np.maximum(-changes, 0.0), length)
    result = np.full(n, 50.0, dtype=float)
    for index in range(n):
        if average_loss[index] == 0.0:
            result[index] = 100.0 if average_gain[index] > 0.0 else 50.0
        else:
            ratio = average_gain[index] / average_loss[index]
            result[index] = 100.0 - 100.0 / (1.0 + ratio)
    return result

sources = {
    "open": open_price,
    "high": high,
    "low": low,
    "close": close,
    "hlc3": (high + low + close) / 3.0,
}
wt_source = sources.get(wt_source_name, sources["hlc3"])

ema_ribbon = [ema(close, length) for length in ema_lengths]
ema1, ema2, ema3, ema4, ema5, ema6, ema7, ema8 = ema_ribbon

esa = ema(wt_source, wt_channel_length)
deviation = ema(np.abs(wt_source - esa), wt_channel_length)
channel_index = np.zeros(n, dtype=float)
for index in range(n):
    channel_index[index] = (wt_source[index] - esa[index]) / (0.015 * deviation[index]) if deviation[index] > 0.0 else 0.0
wt1 = ema(channel_index, wt_average_length)
wt2 = rolling_mean(wt1, wt_ma_length)
rsi_values = rsi(close, rsi_length)

spread = high - low
flow_input = np.zeros(n, dtype=float)
valid_spread = spread != 0.0
flow_input[valid_spread] = ((close[valid_spread] - open_price[valid_spread]) / spread[valid_spread]) * flow_multiplier
flow = rolling_mean(flow_input, flow_period)

long_ema = np.zeros(n, dtype=bool)
short_ema = np.zeros(n, dtype=bool)
red_cross = np.zeros(n, dtype=bool)
blue_triangle = np.zeros(n, dtype=bool)
red_diamond = np.zeros(n, dtype=bool)
yellow_cross = np.zeros(n, dtype=bool)
blood_diamond = np.zeros(n, dtype=bool)
bull_candle = np.zeros(n, dtype=bool)

for index in range(1, n):
    long_ema[index] = ema2[index] > ema8[index] and ema2[index - 1] <= ema8[index - 1]
    short_ema[index] = ema8[index] > ema2[index] and ema8[index - 1] <= ema2[index - 1]
    red_cross[index] = ema1[index] < ema2[index] and ema1[index - 1] >= ema2[index - 1]
    blue_triangle[index] = ema2[index] > ema3[index] and ema2[index - 1] <= ema3[index - 1]
    wt_cross = (
        (wt1[index] > wt2[index] and wt1[index - 1] <= wt2[index - 1])
        or (wt1[index] < wt2[index] and wt1[index - 1] >= wt2[index - 1])
    )
    wt_cross_down = wt2[index] - wt1[index] >= 0.0
    red_diamond[index] = wt_cross and wt_cross_down
    yellow_cross[index] = (
        red_diamond[index]
        and wt2[index] < 45.0
        and wt2[index] > extreme_oversold
        and rsi_values[index] < 30.0
        and rsi_values[index] > 15.0
        and flow[index] < -5.0
    )
    blood_diamond[index] = red_diamond[index] and red_cross[index]
    bull_candle[index] = (
        open_price[index] > ema2[index]
        and open_price[index] > ema8[index]
        and close[index - 1] > open_price[index - 1]
        and close[index] > open_price[index]
        and not red_diamond[index]
        and not red_cross[index]
    )

ribbon_colors = ("#1573D4", "#3096FF", "#57ABFF", "#85C2FF", "#9BCDFF", "#B3D9FF", "#C9E5FF", "#DFECFB")
plots = []
for ribbon_index, values in enumerate(ema_ribbon):
    data = []
    for index, value in enumerate(values):
        bullish = ema8[index] < ema2[index]
        if show_ribbon:
            color = ribbon_colors[ribbon_index] if bullish else "#808080"
            data.append({"value": float(value), "color": color})
        elif ribbon_index == 7:
            data.append({"value": float(value), "color": "#008000" if bullish else "#FF0000"})
        else:
            data.append(None)
    plots.append(
        {
            "name": f"EMA Ribbon {ribbon_index + 1}",
            "data": data,
            "color": ribbon_colors[ribbon_index],
            "type": "line",
            "lineWidth": 2,
            "overlay": True,
        }
    )

primary_fill = []
ribbon_fill = []
for index in range(n):
    if show_ribbon:
        primary_fill.append({"upper": float(max(ema1[index], ema2[index])), "lower": float(min(ema1[index], ema2[index])), "color": "rgba(21, 115, 212, 0.15)"})
        ribbon_fill.append({"upper": float(max(ema2[index], ema8[index])), "lower": float(min(ema2[index], ema8[index])), "color": "rgba(54, 58, 69, 0.15)"})
    else:
        primary_fill.append(None)
        ribbon_fill.append(None)
plots.append({"name": "Fast Ribbon Fill", "title": False, "data": primary_fill, "color": "#1573D4", "type": "cloud", "opacity": 0.15, "overlay": True})
plots.append({"name": "Ribbon Range Fill", "title": False, "data": ribbon_fill, "color": "#363A45", "type": "cloud", "opacity": 0.15, "overlay": True})

def markers(mask, text, color):
    result = [None for _ in range(n)]
    for index in range(n):
        if mask[index]:
            result[index] = {"value": float(high[index]), "text": text, "color": color}
    return result

signals = [
    {"type": "buy", "text": "Long EMA", "name": "Long EMA Signal", "data": markers(long_ema, "●", "#00FF00"), "color": "#00FF00", "renderMode": "events"},
    {"type": "sell", "text": "Short EMA", "name": "Short EMA Signal", "data": markers(short_ema, "●", "#FF0000"), "color": "#FF0000", "renderMode": "events"},
    {"type": "sell", "text": "Red Cross", "name": "Red Cross", "data": markers(red_cross, "×", "#FF0000"), "color": "#FF0000", "renderMode": "events"},
    {"type": "buy", "text": "Blue Triangle", "name": "Blue Triangle", "data": markers(blue_triangle, "▲", "#0064FF"), "color": "#0064FF", "renderMode": "events"},
    {"type": "sell", "text": "Red Diamond", "name": "Red Diamond", "data": markers(red_diamond, "◆", "#FF0000"), "color": "#FF0000", "renderMode": "events"},
    {"type": "buy", "text": "Bull Candle", "name": "Bull Candle", "data": markers(bull_candle, "◆", "#FACC15"), "color": "#FACC15", "renderMode": "events"},
    {"type": "sell", "text": "Blood Diamond", "name": "Blood Diamond", "data": markers(blood_diamond, "◆", "#B91C1C"), "color": "#B91C1C", "renderMode": "events"},
    {"type": "sell", "text": "Yellow Cross", "name": "Yellow Cross", "data": markers(yellow_cross, "×", "#FACC15"), "color": "#FACC15", "renderMode": "events"},
]

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": []}
