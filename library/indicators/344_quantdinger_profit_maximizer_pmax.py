# ============================================================
# QuantDinger Profit Maximizer PMax
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Profit Maximizer PMax"
my_indicator_description = "Builds a selectable support average and ATR-based ratcheting PMax trail with support and price crossover signals."

# @param source str HL2 Price source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param atr_length int 10 ATR length range=1:500:1
# @param atr_multiplier float 3.0 ATR multiplier range=-100:100:0.1
# @param average_type str EMA Moving average type values=SMA,EMA,WMA,TMA,VAR,WWMA,ZLEMA,TSF
# @param average_length int 10 Moving average length range=1:500:1
# @param use_wilder_atr bool true Use Wilder ATR instead of simple ATR
# @param normalize_atr bool false Divide ATR distance by close
# @param show_average bool true Show moving average
# @param show_average_signals bool true Show average/PMax crossing signals
# @param show_price_signals bool false Show price/PMax crossing signals
# @param highlighting bool true Highlight price regime

source_name = str(params.get("source", "HL2")).upper()
if source_name not in ("CLOSE", "OPEN", "HIGH", "LOW", "HL2", "HLC3", "OHLC4"):
    source_name = "HL2"
atr_length = min(500, max(1, int(params.get("atr_length", 10))))
atr_multiplier = min(100.0, max(-100.0, float(params.get("atr_multiplier", 3.0))))
average_type = str(params.get("average_type", "EMA")).upper()
if average_type not in ("SMA", "EMA", "WMA", "TMA", "VAR", "WWMA", "ZLEMA", "TSF"):
    average_type = "EMA"
average_length = min(500, max(1, int(params.get("average_length", 10))))
use_wilder_atr = bool(params.get("use_wilder_atr", True))
normalize_atr = bool(params.get("normalize_atr", False))
show_average = bool(params.get("show_average", True))
show_average_signals = bool(params.get("show_average_signals", True))
show_price_signals = bool(params.get("show_price_signals", False))
highlighting = bool(params.get("highlighting", True))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


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
                previous = sum(seed) / length
                result[index] = previous
        else:
            previous = (previous * (length - 1.0) + float(value)) / length
            result[index] = previous
    return result


def _wma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, length + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / denominator)
    return result


def _var(values, length):
    result = np.zeros(len(values), dtype=float)
    up = np.zeros(len(values), dtype=float)
    down = np.zeros(len(values), dtype=float)
    alpha = 2.0 / (length + 1.0)
    for index in range(1, len(values)):
        up[index] = max(values[index] - values[index - 1], 0.0)
        down[index] = max(values[index - 1] - values[index], 0.0)
    for index in range(len(values)):
        left = max(0, index - 8)
        upward = float(np.sum(up[left:index + 1]))
        downward = float(np.sum(down[left:index + 1]))
        cmo = (upward - downward) / (upward + downward) if upward + downward > 0.0 else 0.0
        factor = alpha * abs(cmo)
        result[index] = factor * values[index] + (1.0 - factor) * (result[index - 1] if index else 0.0)
    return result


def _wwma(values, length):
    result = np.zeros(len(values), dtype=float)
    alpha = 1.0 / length
    for index, value in enumerate(values):
        result[index] = alpha * value + (1.0 - alpha) * (result[index - 1] if index else 0.0)
    return result


def _zlema(values, length):
    lag = int(length / 2)
    adjusted = values.copy() if lag == 0 else np.full(len(values), np.nan, dtype=float)
    if lag:
        adjusted[lag:] = values[lag:] + values[lag:] - values[:-lag]
    return _ema(adjusted, length)


def _linreg(values, length, offset):
    result = np.full(len(values), np.nan, dtype=float)
    x = np.arange(length, dtype=float)
    x_mean = float(np.mean(x))
    denominator = float(np.sum((x - x_mean) ** 2))
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            y_mean = float(np.mean(window))
            slope = float(np.sum((x - x_mean) * (window - y_mean)) / denominator) if denominator > 0.0 else 0.0
            intercept = y_mean - slope * x_mean
            result[index] = intercept + slope * (length - 1 - offset)
    return result


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
source = {"OPEN": opened, "HIGH": high, "LOW": low, "HL2": (high + low) / 2.0, "HLC3": (high + low + close) / 3.0, "OHLC4": (opened + high + low + close) / 4.0}.get(source_name, close)

true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    true_range[index] = high[index] - low[index] if index == 0 else max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
simple_atr = _sma(true_range, atr_length)
wilder_atr = _rma(true_range, atr_length)
atr = wilder_atr if use_wilder_atr else simple_atr

if average_type == "SMA":
    average = _sma(source, average_length)
elif average_type == "EMA":
    average = _ema(source, average_length)
elif average_type == "WMA":
    average = _wma(source, average_length)
elif average_type == "TMA":
    average = _sma(_sma(source, int(np.ceil(average_length / 2.0))), int(np.floor(average_length / 2.0)) + 1)
elif average_type == "VAR":
    average = _var(source, average_length)
elif average_type == "WWMA":
    average = _wwma(source, average_length)
elif average_type == "ZLEMA":
    average = _zlema(source, average_length)
else:
    line_now = _linreg(source, average_length, 0)
    average = line_now + (line_now - _linreg(source, average_length, 1))

long_stop = np.full(n, np.nan, dtype=float)
short_stop = np.full(n, np.nan, dtype=float)
direction = np.ones(n, dtype=int)
pmax = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not np.all(np.isfinite([average[index], atr[index]])):
        direction[index] = direction[index - 1] if index else 1
        continue
    distance = atr_multiplier * atr[index] / close[index] if normalize_atr and close[index] != 0.0 else atr_multiplier * atr[index]
    raw_long = average[index] - distance
    raw_short = average[index] + distance
    previous_long = long_stop[index - 1] if index and np.isfinite(long_stop[index - 1]) else raw_long
    previous_short = short_stop[index - 1] if index and np.isfinite(short_stop[index - 1]) else raw_short
    long_stop[index] = max(raw_long, previous_long) if average[index] > previous_long else raw_long
    short_stop[index] = min(raw_short, previous_short) if average[index] < previous_short else raw_short
    previous_direction = direction[index - 1] if index else 1
    direction[index] = 1 if previous_direction == -1 and average[index] > previous_short else -1 if previous_direction == 1 and average[index] < previous_long else previous_direction
    pmax[index] = long_stop[index] if direction[index] == 1 else short_stop[index]


def _cross(first, second):
    upward = np.zeros(n, dtype=bool)
    downward = np.zeros(n, dtype=bool)
    for index in range(1, n):
        if np.all(np.isfinite([first[index], first[index - 1], second[index], second[index - 1]])):
            upward[index] = first[index] > second[index] and first[index - 1] <= second[index - 1]
            downward[index] = first[index] < second[index] and first[index - 1] >= second[index - 1]
    return upward, downward


average_cross_up, average_cross_down = _cross(average, pmax)
price_cross_up, price_cross_down = _cross(source, pmax)
ohlc4 = (opened + high + low + close) / 4.0
regime_cloud = []
for index in range(n):
    if highlighting and np.all(np.isfinite([ohlc4[index], pmax[index], average[index]])):
        regime_cloud.append({"upper": float(max(ohlc4[index], pmax[index])), "lower": float(min(ohlc4[index], pmax[index])), "color": "rgba(0,128,0,0.25)" if average[index] > pmax[index] else "rgba(255,0,0,0.25)" if average[index] < pmax[index] else "rgba(0,0,0,0)"})
    else:
        regime_cloud.append(None)


def _signal(mask, price_values, text, color):
    return [{"active": True, "price": float(price_values[index]), "text": text, "color": color} if mask[index] and np.isfinite(price_values[index]) else None for index in range(n)]


output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "Moving Average", "data": _finite(average) if show_average else [None] * n, "color": "#0585E1", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "PMax", "data": _finite(pmax), "color": "#FF0000", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "PMax Regime", "title": False, "data": regime_cloud, "color": "#008000", "type": "cloud", "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "name": "Average Buy", "data": _signal(average_cross_up & show_average_signals, pmax * 0.995, "Buy", "#008000"), "color": "#008000", "renderMode": "events"},
        {"type": "sell", "name": "Average Sell", "data": _signal(average_cross_down & show_average_signals, pmax * 1.005, "Sell", "#FF0000"), "color": "#FF0000", "renderMode": "events"},
        {"type": "buy", "name": "Price Buy", "data": _signal(price_cross_up & show_price_signals, pmax * 0.995, "Buy", "#0F18BF"), "color": "#0F18BF", "renderMode": "events"},
        {"type": "sell", "name": "Price Sell", "data": _signal(price_cross_down & show_price_signals, pmax * 1.005, "Sell", "#0F18BF"), "color": "#0F18BF", "renderMode": "events"},
    ],
    "layers": [], "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "source": _finite(source), "trueRange": _finite(true_range), "simpleAtr": _finite(simple_atr), "wilderAtr": _finite(wilder_atr), "atr": _finite(atr),
        "average": _finite(average), "longStop": _finite(long_stop), "shortStop": _finite(short_stop), "direction": [int(value) for value in direction], "pmax": _finite(pmax),
        "averageCrossUp": average_cross_up.tolist(), "averageCrossDown": average_cross_down.tolist(), "priceCrossUp": price_cross_up.tolist(), "priceCrossDown": price_cross_down.tolist(),
        "averageType": average_type, "sourceName": source_name, "normalizedAtr": normalize_atr, "wilderAtrSelected": use_wilder_atr,
    },
}
