# ============================================================
# QuantDinger Trader XO Macro Trend Scanner
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Trader XO Macro Trend Scanner"
my_indicator_description = (
    "Combines an EMA trend regime with Stochastic RSI cross alerts and an optional "
    "long-term moving-average filter. The source uses only the current symbol."
)

# @param source str Close Price source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param fast_ema int 12 Fast EMA range=1:500:1
# @param slow_ema int 25 Slow EMA range=1:500:1
# @param bias_ema int 25 Consolidated EMA range=1:500:1
# @param show_both_emas bool true Show fast and slow EMAs
# @param smooth_k int 3 Stochastic K smoothing range=1:100:1
# @param smooth_d int 3 Stochastic D smoothing range=1:100:1
# @param rsi_length int 14 RSI length range=1:500:1
# @param stochastic_length int 14 Stochastic length range=1:500:1
# @param upper_band float 80.0 Upper band range=0.0:100.0:1.0
# @param middle_band float 50.0 Middle band range=0.0:100.0:1.0
# @param lower_band float 20.0 Lower band range=0.0:100.0:1.0
# @param middle_cross_background bool false Highlight middle-level crosses
# @param extreme_cross_background bool false Highlight overbought/oversold crosses
# @param upper_exit_background bool false Highlight exits below the upper band
# @param lower_exit_background bool false Highlight exits above the lower band
# @param ma_type str EMA Long-term MA type values=EMA,WMA,SMA,None
# @param ma_source str Close Long-term MA source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param ma_length int 200 Long-term MA length range=1:1000:1
# @param show_signal_labels bool true Show Bull and Bear labels
# @param max_signal_labels int 4 Recent signal labels range=1:50:1
# @param bull_color str #00C853 Bull color
# @param bear_color str #FF1744 Bear color
# @param neutral_color str #FF6D00 Neutral color

source_name = str(params.get("source", "Close"))
fast_length = min(500, max(1, int(params.get("fast_ema", 12))))
slow_length = min(500, max(1, int(params.get("slow_ema", 25))))
bias_length = min(500, max(1, int(params.get("bias_ema", 25))))
show_both_emas = bool(params.get("show_both_emas", True))
smooth_k = min(100, max(1, int(params.get("smooth_k", 3))))
smooth_d = min(100, max(1, int(params.get("smooth_d", 3))))
rsi_length = min(500, max(1, int(params.get("rsi_length", 14))))
stochastic_length = min(500, max(1, int(params.get("stochastic_length", 14))))
upper_band = min(100.0, max(0.0, float(params.get("upper_band", 80.0))))
middle_band = min(100.0, max(0.0, float(params.get("middle_band", 50.0))))
lower_band = min(100.0, max(0.0, float(params.get("lower_band", 20.0))))
middle_cross_background = bool(params.get("middle_cross_background", False))
extreme_cross_background = bool(params.get("extreme_cross_background", False))
upper_exit_background = bool(params.get("upper_exit_background", False))
lower_exit_background = bool(params.get("lower_exit_background", False))
ma_type = str(params.get("ma_type", "EMA")).upper()
if ma_type not in ("EMA", "WMA", "SMA", "NONE"):
    ma_type = "EMA"
ma_source_name = str(params.get("ma_source", "Close"))
ma_length = min(1000, max(1, int(params.get("ma_length", 200))))
show_signal_labels = bool(params.get("show_signal_labels", True))
max_signal_labels = min(50, max(1, int(params.get("max_signal_labels", 4))))
bull_color = str(params.get("bull_color", "#00C853"))
bear_color = str(params.get("bear_color", "#FF1744"))
neutral_color = str(params.get("neutral_color", "#FF6D00"))

frame = df.copy()
frame = frame.reset_index(drop=True)
n = len(frame)
if "time" not in frame.columns:
    frame["time"] = np.arange(n, dtype=float) * 3600.0
open_values = pd.to_numeric(frame["open"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
high_values = pd.to_numeric(frame["high"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
low_values = pd.to_numeric(frame["low"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
close_values = pd.to_numeric(frame["close"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
time_values = pd.to_numeric(frame["time"], errors="coerce").fillna(0.0).to_numpy(dtype=float)


def price_source(name):
    key = str(name).strip().lower()
    if key == "open":
        return open_values.copy()
    if key == "high":
        return high_values.copy()
    if key == "low":
        return low_values.copy()
    if key == "hl2":
        return (high_values + low_values) / 2.0
    if key == "hlc3":
        return (high_values + low_values + close_values) / 3.0
    if key == "ohlc4":
        return (open_values + high_values + low_values + close_values) / 4.0
    return close_values.copy()


def ema(values, length):
    result = np.full(n, np.nan, dtype=float)
    if not n:
        return result
    alpha = 2.0 / (float(length) + 1.0)
    result[0] = float(values[0])
    for index in range(1, n):
        result[index] = alpha * float(values[index]) + (1.0 - alpha) * result[index - 1]
    return result


def sma(values, length):
    return pd.Series(values, dtype=float).rolling(length, min_periods=length).mean().to_numpy(dtype=float)


def wma(values, length):
    weights = np.arange(1.0, float(length) + 1.0)
    denominator = float(np.sum(weights))
    return pd.Series(values, dtype=float).rolling(length, min_periods=length).apply(
        lambda window: float(np.dot(window, weights) / denominator), raw=True
    ).to_numpy(dtype=float)


def rma(values, length):
    result = np.full(n, np.nan, dtype=float)
    if n < length:
        return result
    seed = float(np.mean(values[:length]))
    result[length - 1] = seed
    alpha = 1.0 / float(length)
    for index in range(length, n):
        result[index] = alpha * float(values[index]) + (1.0 - alpha) * result[index - 1]
    return result


def rsi(values, length):
    change = np.diff(values, prepend=values[0])
    gain = np.maximum(change, 0.0)
    loss = np.maximum(-change, 0.0)
    average_gain = rma(gain, length)
    average_loss = rma(loss, length)
    result = np.full(n, np.nan, dtype=float)
    valid = np.isfinite(average_gain) & np.isfinite(average_loss)
    both_zero = valid & (average_gain == 0.0) & (average_loss == 0.0)
    only_loss_zero = valid & (average_loss == 0.0) & (average_gain > 0.0)
    normal = valid & (average_loss > 0.0)
    result[both_zero] = 50.0
    result[only_loss_zero] = 100.0
    result[normal] = 100.0 - 100.0 / (1.0 + average_gain[normal] / average_loss[normal])
    return result


source = price_source(source_name)
ma_source_values = price_source(ma_source_name)
fast_ema = ema(source, fast_length)
slow_ema = ema(source, slow_length)
bias_ema = ema(source, bias_length)
trend = np.array(np.where(fast_ema > slow_ema, 1, np.where(fast_ema < slow_ema, -1, 0)), dtype=int)

trend_age = np.zeros(n, dtype=int)
for index in range(n):
    if trend[index] == 0:
        trend_age[index] = 0
    elif index and trend[index] == trend[index - 1]:
        trend_age[index] = trend_age[index - 1] + 1
    else:
        trend_age[index] = 1

bull_signal = np.zeros(n, dtype=bool)
bear_signal = np.zeros(n, dtype=bool)
for index in range(n):
    bull_signal[index] = trend[index] == 1 and (index == 0 or trend[index - 1] != 1)
    bear_signal[index] = trend[index] == -1 and (index == 0 or trend[index - 1] != -1)

rsi_values = rsi(source, rsi_length)
rsi_series = pd.Series(rsi_values, dtype=float)
rsi_low = rsi_series.rolling(stochastic_length, min_periods=stochastic_length).min().to_numpy(dtype=float)
rsi_high = rsi_series.rolling(stochastic_length, min_periods=stochastic_length).max().to_numpy(dtype=float)
stoch_raw = np.full(n, np.nan, dtype=float)
stoch_range = rsi_high - rsi_low
valid_stoch = np.isfinite(rsi_values) & np.isfinite(stoch_range)
flat_stoch = valid_stoch & (stoch_range == 0.0)
normal_stoch = valid_stoch & (stoch_range > 0.0)
stoch_raw[flat_stoch] = 0.0
stoch_raw[normal_stoch] = 100.0 * (rsi_values[normal_stoch] - rsi_low[normal_stoch]) / stoch_range[normal_stoch]
k_values = sma(stoch_raw, smooth_k)
d_values = sma(k_values, smooth_d)

if ma_type == "EMA":
    filter_ma = ema(ma_source_values, ma_length)
elif ma_type == "WMA":
    filter_ma = wma(ma_source_values, ma_length)
elif ma_type == "SMA":
    filter_ma = sma(ma_source_values, ma_length)
else:
    filter_ma = np.zeros(n, dtype=float)

cross_up = np.zeros(n, dtype=bool)
cross_down = np.zeros(n, dtype=bool)
upper_exit = np.zeros(n, dtype=bool)
lower_exit = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.isfinite(k_values[index]) and np.isfinite(d_values[index]) and np.isfinite(k_values[index - 1]) and np.isfinite(d_values[index - 1]):
        cross_up[index] = k_values[index] > d_values[index] and k_values[index - 1] <= d_values[index - 1]
        cross_down[index] = k_values[index] < d_values[index] and k_values[index - 1] >= d_values[index - 1]
    if np.isfinite(k_values[index]) and np.isfinite(k_values[index - 1]):
        upper_exit[index] = k_values[index] < upper_band and k_values[index - 1] >= upper_band
        lower_exit[index] = k_values[index] > lower_band and k_values[index - 1] <= lower_band

if ma_type == "NONE":
    long_filter = np.ones(n, dtype=bool)
    short_filter = np.ones(n, dtype=bool)
else:
    long_filter = np.isfinite(filter_ma) & (open_values > filter_ma)
    short_filter = np.isfinite(filter_ma) & (open_values < filter_ma)

middle_bull_alert = long_filter & cross_up & ((k_values < middle_band) | (d_values < middle_band))
middle_bear_alert = short_filter & cross_down & ((k_values > middle_band) | (d_values > middle_band))
extreme_bull_alert = long_filter & cross_up & ((k_values < lower_band) | (d_values < lower_band))
extreme_bear_alert = short_filter & cross_down & ((k_values > upper_band) | (d_values > upper_band))

background_colors = [None] * n
for index in range(n):
    if middle_cross_background and middle_bull_alert[index]:
        background_colors[index] = "rgba(76,175,80,0.14)"
    elif middle_cross_background and middle_bear_alert[index]:
        background_colors[index] = "rgba(255,23,68,0.14)"
    elif extreme_cross_background and extreme_bull_alert[index]:
        background_colors[index] = "rgba(251,192,45,0.16)"
    elif extreme_cross_background and extreme_bear_alert[index]:
        background_colors[index] = "rgba(0,0,0,0.18)"
    elif upper_exit_background and upper_exit[index]:
        background_colors[index] = "rgba(255,0,20,0.14)"
    elif lower_exit_background and lower_exit[index]:
        background_colors[index] = "rgba(76,175,80,0.14)"

trend_colors = [bull_color if value > 0 else bear_color if value < 0 else neutral_color for value in trend]


def colored_line(values, colors):
    points = []
    for index, value in enumerate(values):
        if np.isfinite(value):
            points.append({"value": float(value), "color": colors[index]})
        else:
            points.append(None)
    return points


plots = []
if show_both_emas:
    plots.extend([
        {"name": "Fast EMA", "data": colored_line(fast_ema, trend_colors), "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Slow EMA", "data": colored_line(slow_ema, trend_colors), "type": "line", "lineWidth": 1, "overlay": True},
    ])
else:
    plots.append({"name": "Consolidated EMA", "data": colored_line(bias_ema, trend_colors), "type": "line", "lineWidth": 3, "overlay": True})

if ma_type != "NONE":
    plots.append({"name": f"{ma_type} Filter", "data": [float(value) if np.isfinite(value) else None for value in filter_ma], "type": "line", "color": "#90A4AE", "lineWidth": 1, "overlay": True})

layers = []
if show_signal_labels:
    signal_indices = [index for index in range(n) if bull_signal[index] or bear_signal[index]][-max_signal_labels:]
    for index in signal_indices:
        bullish = bool(bull_signal[index])
        candle_range = max(float(high_values[index] - low_values[index]), abs(float(close_values[index])) * 0.001, 1e-12)
        layers.append({
            "type": "label",
            "timestamp": float(time_values[index]),
            "price": float(low_values[index] - candle_range * 0.15 if bullish else high_values[index] + candle_range * 0.15),
            "text": "Bull" if bullish else "Bear",
            "color": bull_color if bullish else bear_color,
            "fillColor": bull_color if bullish else bear_color,
            "borderColor": bull_color if bullish else bear_color,
            "textColor": "#07100A" if bullish else "#FFFFFF",
            "fontSize": 8,
            "side": "below" if bullish else "above",
        })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": layers,
    "tables": [],
    "bar_colors": trend_colors,
    "background_colors": background_colors,
    "calculatedVars": {
        "fastEma": [float(value) if np.isfinite(value) else None for value in fast_ema],
        "slowEma": [float(value) if np.isfinite(value) else None for value in slow_ema],
        "biasEma": [float(value) if np.isfinite(value) else None for value in bias_ema],
        "trend": [int(value) for value in trend],
        "trendAge": [int(value) for value in trend_age],
        "bullSignal": [bool(value) for value in bull_signal],
        "bearSignal": [bool(value) for value in bear_signal],
        "rsi": [float(value) if np.isfinite(value) else None for value in rsi_values],
        "stochK": [float(value) if np.isfinite(value) else None for value in k_values],
        "stochD": [float(value) if np.isfinite(value) else None for value in d_values],
        "filterMa": [float(value) if np.isfinite(value) else None for value in filter_ma],
        "middleBullAlert": [bool(value) for value in middle_bull_alert],
        "middleBearAlert": [bool(value) for value in middle_bear_alert],
        "extremeBullAlert": [bool(value) for value in extreme_bull_alert],
        "extremeBearAlert": [bool(value) for value in extreme_bear_alert],
    },
}
