# ============================================================
# QuantDinger Heikin Ashi RSI Oscillator
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Heikin Ashi RSI Oscillator"
my_indicator_description = (
    "Transforms zero-centered RSI values into Heikin Ashi candles with optional "
    "RSI, histogram, stochastic ribbon, and overbought/oversold channels."
)

# @param harsi_length int 14 HARSI RSI length range=1:200:1
# @param open_smoothing int 1 HARSI open smoothing range=1:100:1
# @param up_color color #00897B Rising candle color
# @param down_color color #F23645 Falling candle color
# @param wick_color color #808080 Wick color
# @param rsi_source str OHLC4 RSI source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param rsi_length int 7 RSI plot length range=1:200:1
# @param smoothed_rsi bool true Use HA-style RSI smoothing
# @param show_rsi bool true Show RSI line
# @param show_histogram bool true Show RSI histogram
# @param show_stochastic bool false Show stochastic RSI
# @param stochastic_ribbon bool true Fill between stochastic lines
# @param smooth_k int 3 Stochastic K smoothing range=1:50:1
# @param smooth_d int 3 Stochastic D smoothing range=1:50:1
# @param stochastic_length int 14 Stochastic length range=1:200:1
# @param stochastic_scale int 80 Stochastic scaling percent range=1:100:1
# @param overbought int 20 Overbought boundary range=1:50:1
# @param overbought_extreme int 30 Extreme overbought boundary range=1:50:1
# @param oversold int -20 Oversold boundary range=-50:-1:1
# @param oversold_extreme int -30 Extreme oversold boundary range=-50:-1:1

harsi_length = max(1, int(params.get("harsi_length", 14)))
open_smoothing = min(100, max(1, int(params.get("open_smoothing", 1))))
up_color = str(params.get("up_color", "#00897B"))
down_color = str(params.get("down_color", "#F23645"))
wick_color = str(params.get("wick_color", "#808080"))
rsi_source = str(params.get("rsi_source", "OHLC4")).upper()
rsi_length = max(1, int(params.get("rsi_length", 7)))
smoothed_rsi = bool(params.get("smoothed_rsi", True))
show_rsi = bool(params.get("show_rsi", True))
show_histogram = bool(params.get("show_histogram", True))
show_stochastic = bool(params.get("show_stochastic", False))
stochastic_ribbon = bool(params.get("stochastic_ribbon", True))
smooth_k = max(1, int(params.get("smooth_k", 3)))
smooth_d = max(1, int(params.get("smooth_d", 3)))
stochastic_length = max(1, int(params.get("stochastic_length", 14)))
stochastic_scale = min(100, max(1, int(params.get("stochastic_scale", 80))))
overbought = min(50, max(1, int(params.get("overbought", 20))))
overbought_extreme = min(50, max(1, int(params.get("overbought_extreme", 30))))
oversold = min(-1, max(-50, int(params.get("oversold", -20))))
oversold_extreme = min(-1, max(-50, int(params.get("oversold_extreme", -30))))

frame = df.copy().reset_index(drop=True)
n = len(frame)
open_values = pd.to_numeric(frame["open"], errors="coerce").to_numpy(dtype=float)
high_values = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype=float)
low_values = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype=float)
close_values = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype=float)


def pine_rsi(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) <= length:
        return result
    changes = np.diff(values, prepend=np.nan)
    gains = np.where(np.isfinite(changes), np.maximum(changes, 0.0), np.nan)
    losses = np.where(np.isfinite(changes), np.maximum(-changes, 0.0), np.nan)
    average_gain = float(np.mean(gains[1:length + 1]))
    average_loss = float(np.mean(losses[1:length + 1]))
    for index in range(length, len(values)):
        if index > length:
            average_gain = (average_gain * (length - 1) + gains[index]) / length
            average_loss = (average_loss * (length - 1) + losses[index]) / length
        if average_loss == 0.0:
            result[index] = 100.0 if average_gain > 0.0 else 50.0
        else:
            result[index] = 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return result


def sma(values, length):
    return pd.Series(values, dtype=float).rolling(length, min_periods=length).mean().to_numpy(dtype=float)


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


source_map = {
    "OPEN": open_values,
    "HIGH": high_values,
    "LOW": low_values,
    "HL2": (high_values + low_values) / 2.0,
    "HLC3": (high_values + low_values + close_values) / 3.0,
    "OHLC4": (open_values + high_values + low_values + close_values) / 4.0,
}
source_values = source_map.get(rsi_source, close_values)

zero_rsi = pine_rsi(source_values, rsi_length) - 50.0
smoothed_values = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not np.isfinite(zero_rsi[index]):
        continue
    if index == 0 or not np.isfinite(smoothed_values[index - 1]):
        smoothed_values[index] = zero_rsi[index]
    else:
        smoothed_values[index] = (smoothed_values[index - 1] + zero_rsi[index]) / 2.0
rsi_values = smoothed_values if smoothed_rsi else zero_rsi

stochastic_raw = np.full(n, np.nan, dtype=float)
for index in range(n):
    start = index - stochastic_length + 1
    if start < 0 or not np.all(np.isfinite(rsi_values[start:index + 1])):
        continue
    window = rsi_values[start:index + 1]
    lowest = float(np.min(window))
    span = float(np.max(window) - lowest)
    stochastic_raw[index] = (100.0 * (rsi_values[index] - lowest) / span if span != 0.0 else 0.0) - 50.0
stochastic_k = sma(stochastic_raw, smooth_k) * (stochastic_scale / 100.0)
stochastic_d = sma(stochastic_k, smooth_d)

close_rsi = pine_rsi(close_values, harsi_length) - 50.0
high_rsi_raw = pine_rsi(high_values, harsi_length) - 50.0
low_rsi_raw = pine_rsi(low_values, harsi_length) - 50.0
harsi_open = np.full(n, np.nan, dtype=float)
harsi_high = np.full(n, np.nan, dtype=float)
harsi_low = np.full(n, np.nan, dtype=float)
harsi_close = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not all(np.isfinite(value) for value in (close_rsi[index], high_rsi_raw[index], low_rsi_raw[index])):
        continue
    open_rsi = close_rsi[index - 1] if index > 0 and np.isfinite(close_rsi[index - 1]) else close_rsi[index]
    high_rsi = max(high_rsi_raw[index], low_rsi_raw[index])
    low_rsi = min(high_rsi_raw[index], low_rsi_raw[index])
    harsi_close[index] = (open_rsi + high_rsi + low_rsi + close_rsi[index]) / 4.0
    lagged_open = harsi_open[index - open_smoothing] if index >= open_smoothing else np.nan
    if not np.isfinite(lagged_open):
        harsi_open[index] = (open_rsi + close_rsi[index]) / 2.0
    else:
        harsi_open[index] = (
            harsi_open[index - 1] * open_smoothing + harsi_close[index - 1]
        ) / (open_smoothing + 1.0)
    harsi_high[index] = max(high_rsi, harsi_open[index], harsi_close[index])
    harsi_low[index] = min(low_rsi, harsi_open[index], harsi_close[index])

candles = []
for index in range(n):
    if not all(np.isfinite(value) for value in (harsi_open[index], harsi_high[index], harsi_low[index], harsi_close[index])):
        candles.append(None)
        continue
    color = up_color if harsi_close[index] > harsi_open[index] else down_color
    candles.append({
        "open": float(harsi_open[index]), "high": float(harsi_high[index]),
        "low": float(harsi_low[index]), "close": float(harsi_close[index]),
        "color": color, "fillColor": color, "borderColor": color, "wickColor": wick_color,
    })

constant = lambda value: [float(value) for _ in range(n)]
plots = [
    {"name": "OB Fill", "title": False, "data": [{"upper": float(overbought_extreme), "lower": float(overbought)} for _ in range(n)], "color": "#F23645", "type": "cloud", "opacity": 0.10, "overlay": False},
    {"name": "Channel Fill", "title": False, "data": [{"upper": float(overbought), "lower": float(oversold)} for _ in range(n)], "color": "#2962FF", "type": "cloud", "opacity": 0.10, "overlay": False},
    {"name": "OS Fill", "title": False, "data": [{"upper": float(oversold), "lower": float(oversold_extreme)} for _ in range(n)], "color": "#22AB94", "type": "cloud", "opacity": 0.10, "overlay": False},
    {"name": "OB Extreme", "title": False, "data": constant(overbought_extreme), "color": "rgba(192,192,192,0.40)", "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "OB", "title": False, "data": constant(overbought), "color": "rgba(192,192,192,0.20)", "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Median", "title": False, "data": constant(0.0), "color": "#FF9800", "type": "line", "lineStyle": "dotted", "lineWidth": 1, "overlay": False},
    {"name": "OS", "title": False, "data": constant(oversold), "color": "rgba(192,192,192,0.20)", "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "OS Extreme", "title": False, "data": constant(oversold_extreme), "color": "rgba(192,192,192,0.40)", "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "RSI Histogram", "title": False, "data": finite(rsi_values) if show_histogram else [None] * n, "color": "rgba(192,192,192,0.20)", "type": "histogram", "baseValue": 0.0, "lineWidth": 1, "overlay": False},
    {"name": "HARSI", "data": candles, "type": "candlestick", "overlay": False},
    {"name": "RSI Shadow", "title": False, "data": finite(rsi_values) if show_rsi else [None] * n, "color": "rgba(0,0,0,0.80)", "type": "line", "lineWidth": 3, "overlay": False},
    {"name": "RSI", "data": finite(rsi_values) if show_rsi else [None] * n, "color": "#FAC832", "type": "line", "lineWidth": 1, "overlay": False},
]

if show_stochastic:
    if stochastic_ribbon:
        ribbon = []
        for index in range(n):
            if np.isfinite(stochastic_k[index]) and np.isfinite(stochastic_d[index]):
                ribbon.append({
                    "upper": float(max(stochastic_k[index], stochastic_d[index])),
                    "lower": float(min(stochastic_k[index], stochastic_d[index])),
                    "color": "rgba(0,148,255,0.50)" if stochastic_k[index] >= stochastic_d[index] else "rgba(255,106,0,0.50)",
                })
            else:
                ribbon.append(None)
        plots.append({"name": "Stochastic Ribbon", "title": False, "data": ribbon, "color": "#0094FF", "type": "cloud", "opacity": 0.50, "overlay": False})
    else:
        plots.extend([
            {"name": "Stochastic K", "data": finite(stochastic_k), "color": "#0094FF", "type": "line", "lineWidth": 1, "overlay": False},
            {"name": "Stochastic D", "data": finite(stochastic_d), "color": "#FF6A00", "type": "line", "lineWidth": 1, "overlay": False},
        ])

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "zeroRsi": finite(zero_rsi),
        "smoothedRsi": finite(smoothed_values),
        "rsi": finite(rsi_values),
        "stochasticRaw": finite(stochastic_raw),
        "stochasticK": finite(stochastic_k),
        "stochasticD": finite(stochastic_d),
        "harsiOpen": finite(harsi_open),
        "harsiHigh": finite(harsi_high),
        "harsiLow": finite(harsi_low),
        "harsiClose": finite(harsi_close),
    },
}
