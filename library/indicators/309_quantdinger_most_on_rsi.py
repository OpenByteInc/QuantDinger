# ============================================================
# QuantDinger MOST on RSI
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger MOST on RSI"
my_indicator_description = "RSI with seven smoothing choices, MOST trailing state, optional Bollinger envelope and confirmed regular divergences."

# @param rsi_length int 14 RSI length range=1:500:1
# @param source str Close RSI source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param average_type str VAR RSI average type values=SMA,Bollinger Bands,EMA,SMMA (RMA),WMA,VWMA,VAR
# @param average_length int 5 RSI average length range=1:500:1
# @param stop_percent float 9.0 MOST stop percent range=0:100:0.1
# @param bollinger_multiplier float 2.0 Bollinger deviation range=0.001:50:0.001
# @param show_divergence bool true Show regular divergences
# @param show_signals bool false Show MOST signals

rsi_length = min(500, max(1, int(params.get("rsi_length", 14))))
source_name = str(params.get("source", "Close")).upper()
if source_name not in ("CLOSE", "OPEN", "HIGH", "LOW", "HL2", "HLC3", "OHLC4"):
    source_name = "CLOSE"
average_type = str(params.get("average_type", "VAR"))
if average_type not in ("SMA", "Bollinger Bands", "EMA", "SMMA (RMA)", "WMA", "VWMA", "VAR"):
    average_type = "VAR"
average_length = min(500, max(1, int(params.get("average_length", 5))))
stop_percent = min(100.0, max(0.0, float(params.get("stop_percent", 9.0))))
bollinger_multiplier = min(50.0, max(0.001, float(params.get("bollinger_multiplier", 2.0))))
show_divergence = bool(params.get("show_divergence", True))
show_signals = bool(params.get("show_signals", False))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _sma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def _wma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, period + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.sum(window * weights) / denominator)
    return result


def _vwma(values, volume_values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        source_window = values[index - period + 1:index + 1]
        volume_window = volume_values[index - period + 1:index + 1]
        denominator = float(np.sum(volume_window))
        if np.all(np.isfinite(source_window)) and denominator != 0.0:
            result[index] = float(np.sum(source_window * volume_window) / denominator)
    return result


def _rma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    finite_indices = np.flatnonzero(np.isfinite(values))
    if len(finite_indices) < period:
        return result
    seed_indices = finite_indices[:period]
    seed_index = int(seed_indices[-1])
    previous = float(np.mean(values[seed_indices]))
    result[seed_index] = previous
    for index in range(seed_index + 1, len(values)):
        if np.isfinite(values[index]):
            previous = (previous * (period - 1) + float(values[index])) / period
            result[index] = previous
    return result


def _rsi(values, period):
    gains = np.full(len(values), np.nan, dtype=float)
    losses = np.full(len(values), np.nan, dtype=float)
    for index in range(1, len(values)):
        change = values[index] - values[index - 1]
        gains[index], losses[index] = max(change, 0.0), max(-change, 0.0)
    average_gain, average_loss = _rma(gains, period), _rma(losses, period)
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(len(values)):
        if not np.all(np.isfinite([average_gain[index], average_loss[index]])):
            continue
        result[index] = 100.0 if average_loss[index] == 0.0 else 0.0 if average_gain[index] == 0.0 else 100.0 - 100.0 / (1.0 + average_gain[index] / average_loss[index])
    return result


def _var(values, period):
    result = np.zeros(len(values), dtype=float)
    upward = np.zeros(len(values), dtype=float)
    downward = np.zeros(len(values), dtype=float)
    alpha = 2.0 / (period + 1.0)
    for index in range(1, len(values)):
        upward[index] = max(values[index] - values[index - 1], 0.0)
        downward[index] = max(values[index - 1] - values[index], 0.0)
    for index in range(len(values)):
        if index >= 8:
            sum_up = float(np.sum(upward[index - 8:index + 1]))
            sum_down = float(np.sum(downward[index - 8:index + 1]))
            cmo = (sum_up - sum_down) / (sum_up + sum_down) if sum_up + sum_down != 0.0 else 0.0
        else:
            cmo = 0.0
        previous = result[index - 1] if index else 0.0
        if np.isfinite(values[index]):
            weight = alpha * abs(cmo)
            result[index] = weight * values[index] + (1.0 - weight) * previous
        else:
            result[index] = previous
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close", "volume"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].to_numpy(dtype=float)
n = len(df)
source = {"OPEN": opened, "HIGH": high, "LOW": low, "HL2": (high + low) / 2.0, "HLC3": (high + low + close) / 3.0, "OHLC4": (opened + high + low + close) / 4.0}.get(source_name, close)
rsi = _rsi(source, rsi_length)
if average_type in ("SMA", "Bollinger Bands"):
    rsi_average = _sma(rsi, average_length)
elif average_type == "EMA":
    rsi_average = _ema(rsi, average_length)
elif average_type == "SMMA (RMA)":
    rsi_average = _rma(rsi, average_length)
elif average_type == "WMA":
    rsi_average = _wma(rsi, average_length)
elif average_type == "VWMA":
    rsi_average = _vwma(rsi, volume, average_length)
else:
    rsi_average = _var(rsi, average_length)

long_stop = np.full(n, np.nan, dtype=float)
short_stop = np.full(n, np.nan, dtype=float)
most_direction = np.ones(n, dtype=int)
most = np.full(n, np.nan, dtype=float)
buy = np.zeros(n, dtype=bool)
sell = np.zeros(n, dtype=bool)
signal_direction = np.zeros(n, dtype=int)
for index in range(n):
    value = rsi_average[index]
    if not np.isfinite(value):
        most_direction[index] = most_direction[index - 1] if index else 1
        signal_direction[index] = signal_direction[index - 1] if index else 0
        continue
    difference = value * stop_percent * 0.01
    raw_long, raw_short = value - difference, value + difference
    previous_long = long_stop[index - 1] if index >= 1 and np.isfinite(long_stop[index - 1]) else raw_long
    previous_short = short_stop[index - 1] if index >= 1 and np.isfinite(short_stop[index - 1]) else raw_short
    long_stop[index] = max(raw_long, previous_long) if value > previous_long else raw_long
    short_stop[index] = min(raw_short, previous_short) if value < previous_short else raw_short
    previous_direction = most_direction[index - 1] if index else 1
    most_direction[index] = 1 if previous_direction == -1 and value > previous_short else -1 if previous_direction == 1 and value < previous_long else previous_direction
    most[index] = long_stop[index] if most_direction[index] == 1 else short_stop[index]
    if index >= 1 and np.all(np.isfinite([rsi_average[index - 1], most[index - 1]])):
        buy[index] = value > most[index] and rsi_average[index - 1] <= most[index - 1]
        sell[index] = value < most[index] and rsi_average[index - 1] >= most[index - 1]
    signal_direction[index] = 1 if buy[index] else -1 if sell[index] else signal_direction[index - 1] if index else 0

bb_upper = np.full(n, np.nan, dtype=float)
bb_lower = np.full(n, np.nan, dtype=float)
if average_type == "Bollinger Bands":
    for index in range(average_length - 1, n):
        window = rsi[index - average_length + 1:index + 1]
        if np.all(np.isfinite(window)) and np.isfinite(rsi_average[index]):
            deviation = float(np.std(window, ddof=0)) * bollinger_multiplier
            bb_upper[index], bb_lower[index] = rsi_average[index] + deviation, rsi_average[index] - deviation

lookback_left = lookback_right = 5
pivot_low_found = np.zeros(n, dtype=bool)
pivot_high_found = np.zeros(n, dtype=bool)
pivot_low_value = np.full(n, np.nan, dtype=float)
pivot_high_value = np.full(n, np.nan, dtype=float)
for index in range(lookback_left + lookback_right, n):
    pivot_index = index - lookback_right
    window = rsi[pivot_index - lookback_left:pivot_index + lookback_right + 1]
    if np.all(np.isfinite(window)):
        pivot_low_found[index] = rsi[pivot_index] == np.min(window) and np.count_nonzero(window == rsi[pivot_index]) == 1
        pivot_high_found[index] = rsi[pivot_index] == np.max(window) and np.count_nonzero(window == rsi[pivot_index]) == 1
        if pivot_low_found[index]:
            pivot_low_value[index] = rsi[pivot_index]
        if pivot_high_found[index]:
            pivot_high_value[index] = rsi[pivot_index]

bullish_divergence = np.zeros(n, dtype=bool)
bearish_divergence = np.zeros(n, dtype=bool)
bullish_points = np.full(n, np.nan, dtype=float)
bearish_points = np.full(n, np.nan, dtype=float)
last_low_confirmation = previous_low_confirmation = -1
last_low_rsi = last_low_price = np.nan
last_high_confirmation = previous_high_confirmation = -1
last_high_rsi = last_high_price = np.nan
for index in range(n):
    pivot_index = index - lookback_right
    if pivot_low_found[index]:
        if last_low_confirmation >= 0:
            bars = index - (last_low_confirmation + 1)
            bullish_divergence[index] = 5 <= bars <= 60 and rsi[pivot_index] > last_low_rsi and low[pivot_index] < last_low_price
        previous_low_confirmation = last_low_confirmation
        last_low_confirmation = index
        last_low_rsi, last_low_price = rsi[pivot_index], low[pivot_index]
        if bullish_divergence[index] and show_divergence:
            bullish_points[pivot_index] = rsi[pivot_index]
    if pivot_high_found[index]:
        if last_high_confirmation >= 0:
            bars = index - (last_high_confirmation + 1)
            bearish_divergence[index] = 5 <= bars <= 60 and rsi[pivot_index] < last_high_rsi and high[pivot_index] > last_high_price
        previous_high_confirmation = last_high_confirmation
        last_high_confirmation = index
        last_high_rsi, last_high_price = rsi[pivot_index], high[pivot_index]
        if bearish_divergence[index] and show_divergence:
            bearish_points[pivot_index] = rsi[pivot_index]

rsi_zone = []
bb_cloud = []
for index in range(n):
    rsi_zone.append({"upper": 70.0, "lower": 30.0, "color": "rgba(126,87,194,0.10)"})
    bb_cloud.append({"upper": float(bb_upper[index]), "lower": float(bb_lower[index]), "color": "rgba(0,128,0,0.10)"} if np.all(np.isfinite([bb_upper[index], bb_lower[index]])) else None)

signals = []
if show_signals:
    signals.extend([
        {"type": "buy", "name": "MOST Buy", "text": "BUY", "data": [float(rsi_average[index]) if buy[index] else None for index in range(n)], "color": "#0F18BF", "renderMode": "events"},
        {"type": "sell", "name": "MOST Sell", "text": "SELL", "data": [float(rsi_average[index]) if sell[index] else None for index in range(n)], "color": "#0F18BF", "renderMode": "events"},
    ])
if show_divergence:
    signals.extend([
        {"type": "buy", "name": "Regular Bullish Divergence", "text": "Bull", "data": _finite(bullish_points), "color": "#008000", "renderMode": "events"},
        {"type": "sell", "name": "Regular Bearish Divergence", "text": "Bear", "data": _finite(bearish_points), "color": "#FF0000", "renderMode": "events"},
    ])

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "MOST", "data": _finite(most), "color": "#800000", "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "RSI", "data": _finite(rsi), "color": "#7E57C2", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "RSI Average", "data": _finite(rsi_average), "color": "#FFFF00", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "RSI Upper", "data": [70.0] * n, "color": "#787B86", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "RSI Middle", "data": [50.0] * n, "color": "rgba(120,123,134,0.50)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "RSI Lower", "data": [30.0] * n, "color": "#787B86", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "RSI Zone", "title": False, "data": rsi_zone, "color": "#7E57C2", "type": "cloud", "overlay": False},
        {"name": "Bollinger Upper", "data": _finite(bb_upper), "color": "#008000", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Bollinger Lower", "data": _finite(bb_lower), "color": "#008000", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Bollinger Fill", "title": False, "data": bb_cloud, "color": "#008000", "type": "cloud", "overlay": False},
        {"name": "Bullish Divergence", "data": _finite(bullish_points), "color": "#008000", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Bearish Divergence", "data": _finite(bearish_points), "color": "#FF0000", "type": "line", "lineWidth": 2, "overlay": False},
    ],
    "signals": signals,
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "rsi": _finite(rsi), "rsiAverage": _finite(rsi_average), "longStop": _finite(long_stop), "shortStop": _finite(short_stop), "mostDirection": most_direction.tolist(), "most": _finite(most),
        "buy": buy.tolist(), "sell": sell.tolist(), "signalDirection": signal_direction.tolist(), "bollingerUpper": _finite(bb_upper), "bollingerLower": _finite(bb_lower),
        "pivotLowFound": pivot_low_found.tolist(), "pivotHighFound": pivot_high_found.tolist(), "pivotLowValue": _finite(pivot_low_value), "pivotHighValue": _finite(pivot_high_value),
        "bullishDivergence": bullish_divergence.tolist(), "bearishDivergence": bearish_divergence.tolist(), "bullishPoints": _finite(bullish_points), "bearishPoints": _finite(bearish_points),
        "source": source_name, "averageType": average_type,
    },
}
