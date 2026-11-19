# ============================================================
# QuantDinger RMI Trend Sniper
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger RMI Trend Sniper"
my_indicator_description = "Combines RSI and money-flow momentum with a range-weighted average and delayed adaptive volatility band."

# @param momentum_length int 14 RSI and MFI length range=1:10000:1
# @param positive_threshold float 66 Positive threshold range=0:100:0.1
# @param negative_threshold float 30 Negative threshold range=0:100:0.1
# @param show_range_ma bool true Show the range-weighted trend band
# @param bull_color str #00BCD4 Bullish trend color
# @param bear_color str #FF5252 Bearish trend color

momentum_length = min(10000, max(1, int(params.get("momentum_length", 14))))
positive_threshold = min(100.0, max(0.0, float(params.get("positive_threshold", 66.0))))
negative_threshold = min(100.0, max(0.0, float(params.get("negative_threshold", 30.0))))
show_range_ma = bool(params.get("show_range_ma", True))
bull_color = str(params.get("bull_color", "#00BCD4"))
bear_color = str(params.get("bear_color", "#FF5252"))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
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
                previous = float(np.mean(seed))
                result[index] = previous
        else:
            previous = (previous * (length - 1.0) + float(value)) / length
            result[index] = previous
    return result


def _rolling_sum(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.sum(window))
    return result


def _atr(high_values, low_values, close_values, length):
    true_range = np.full(len(close_values), np.nan, dtype=float)
    for index in range(len(close_values)):
        true_range[index] = high_values[index] - low_values[index] if index == 0 else max(high_values[index] - low_values[index], abs(high_values[index] - close_values[index - 1]), abs(low_values[index] - close_values[index - 1]))
    return _rma(true_range, length)


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = np.nan if column == "volume" else 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].to_numpy(dtype=float)
n = len(df)

change = np.diff(close, prepend=np.nan)
up = _rma(np.where(np.isfinite(change), np.maximum(change, 0.0), np.nan), momentum_length)
down = _rma(np.where(np.isfinite(change), np.maximum(-change, 0.0), np.nan), momentum_length)
rsi = np.full(n, np.nan, dtype=float)
for index in range(n):
    if np.isfinite(up[index]) and np.isfinite(down[index]):
        rsi[index] = 100.0 if down[index] == 0.0 else 0.0 if up[index] == 0.0 else 100.0 - 100.0 / (1.0 + up[index] / down[index])

typical = (high + low + close) / 3.0
money_flow = typical * volume
typical_change = np.diff(typical, prepend=np.nan)
positive_flow = np.where(np.isfinite(money_flow) & (typical_change > 0.0), money_flow, np.where(np.isfinite(money_flow), 0.0, np.nan))
negative_flow = np.where(np.isfinite(money_flow) & (typical_change < 0.0), money_flow, np.where(np.isfinite(money_flow), 0.0, np.nan))
positive_sum = _rolling_sum(positive_flow, momentum_length)
negative_sum = _rolling_sum(negative_flow, momentum_length)
mfi = np.full(n, np.nan, dtype=float)
for index in range(n):
    if np.isfinite(positive_sum[index]) and np.isfinite(negative_sum[index]):
        mfi[index] = 100.0 if negative_sum[index] == 0.0 and positive_sum[index] > 0.0 else 50.0 if negative_sum[index] == 0.0 else 0.0 if positive_sum[index] == 0.0 else 100.0 - 100.0 / (1.0 + positive_sum[index] / negative_sum[index])
rsi_mfi = (rsi + mfi) / 2.0

ema5 = _ema(close, 5)
positive_momentum = np.asarray([
    index > 0 and np.all(np.isfinite([rsi_mfi[index - 1], rsi_mfi[index], ema5[index - 1], ema5[index]]))
    and rsi_mfi[index - 1] < positive_threshold and rsi_mfi[index] > positive_threshold
    and rsi_mfi[index] > negative_threshold and ema5[index] - ema5[index - 1] > 0.0
    for index in range(n)
], dtype=bool)
negative_momentum = np.asarray([
    index > 0 and np.all(np.isfinite([rsi_mfi[index], ema5[index - 1], ema5[index]]))
    and rsi_mfi[index] < negative_threshold and ema5[index] - ema5[index - 1] < 0.0
    for index in range(n)
], dtype=bool)
positive = np.zeros(n, dtype=bool)
negative = np.zeros(n, dtype=bool)
positive_state = negative_state = False
for index in range(n):
    if positive_momentum[index]:
        positive_state, negative_state = True, False
    if negative_momentum[index]:
        positive_state, negative_state = False, True
    positive[index], negative[index] = positive_state, negative_state

bar_range = high - low
range_sum = _rolling_sum(bar_range, 20)
weight = np.divide(bar_range, range_sum, out=np.full(n, np.nan, dtype=float), where=np.isfinite(range_sum) & (range_sum != 0.0))
weighted_price_sum = _rolling_sum(close * weight, 20)
weight_sum = _rolling_sum(weight, 20)
rwma = np.divide(weighted_price_sum, weight_sum, out=np.full(n, np.nan, dtype=float), where=np.isfinite(weighted_price_sum) & np.isfinite(weight_sum) & (weight_sum != 0.0))
atr30 = _atr(high, low, close, 30)
raw_band = np.minimum(atr30 * 0.3, close * 0.003)
band = np.full(n, np.nan, dtype=float)
if n > 20:
    band[20:] = raw_band[:-20] * 4.0
rwma_shifted = np.where(positive, rwma - band, np.where(negative, rwma + band, np.nan))
upper = rwma_shifted + band
lower = rwma_shifted - band
trend_colors = [bull_color if value else bear_color for value in positive]
cloud_upper = [
    {"upper": float(upper[index]), "lower": float(rwma_shifted[index]), "color": trend_colors[index], "opacity": 0.25}
    if show_range_ma and np.all(np.isfinite([upper[index], rwma_shifted[index]])) else None for index in range(n)
]
cloud_lower = [
    {"upper": float(rwma_shifted[index]), "lower": float(lower[index]), "color": trend_colors[index], "opacity": 0.25}
    if show_range_ma and np.all(np.isfinite([rwma_shifted[index], lower[index]])) else None for index in range(n)
]
buy = positive & ~np.r_[False, positive[:-1]]
sell = negative & ~np.r_[False, negative[:-1]]


def _events(mask, prices, text, color, event_type):
    return {"type": event_type, "name": text, "data": [{"active": True, "price": float(prices[index]), "text": text, "color": color} if active and np.isfinite(prices[index]) else None for index, active in enumerate(mask)], "color": color, "renderMode": "events"}


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Range-Weighted Trend", "title": show_range_ma, "data": _finite(rwma_shifted) if show_range_ma else [None] * n, "color": trend_colors, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Upper Trend Fill", "title": False, "data": cloud_upper, "color": bull_color, "type": "cloud", "overlay": True},
        {"name": "Lower Trend Fill", "title": False, "data": cloud_lower, "color": bull_color, "type": "cloud", "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "overlay": True},
    ],
    "signals": [_events(buy, lower - band / 2.0, "Buy", bull_color, "buy"), _events(sell, upper + band / 2.0, "Sell", bear_color, "sell")],
    "layers": [], "tables": [], "bar_colors": ["#008000" if value else "#FF0000" for value in positive], "background_colors": [None] * n,
    "calculatedVars": {
        "up": _finite(up), "down": _finite(down), "rsi": _finite(rsi), "mfi": _finite(mfi), "rsiMfi": _finite(rsi_mfi), "ema5": _finite(ema5),
        "positiveMomentum": positive_momentum.tolist(), "negativeMomentum": negative_momentum.tolist(), "positive": positive.tolist(), "negative": negative.tolist(),
        "rangeSum": _finite(range_sum), "weight": _finite(weight), "weightedPriceSum": _finite(weighted_price_sum), "weightSum": _finite(weight_sum), "rwma": _finite(rwma),
        "atr30": _finite(atr30), "rawBand": _finite(raw_band), "band": _finite(band), "trendMa": _finite(rwma_shifted), "upper": _finite(upper), "lower": _finite(lower),
        "buy": buy.tolist(), "sell": sell.tolist(),
    },
}
