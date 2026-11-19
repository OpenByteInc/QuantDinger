# ============================================================
# QuantDinger Optimized Trend Tracker Oscillator
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Optimized Trend Tracker Oscillator"
my_indicator_description = "Compares a corrected multi-scale VIDYA ratio with a delayed optimized trend tracker."

# @param source str Close Price source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param length int 2 OTT period range=1:500:1
# @param percent float 0.6 OTT coefficient range=0:100:0.1
# @param fast_vidya_length int 10 Fast VIDYA length range=1:100:1
# @param slow_vidya_length int 25 Slow VIDYA length range=1:500:1
# @param correcting_constant int 100000 Correcting constant range=1:10000000:1
# @param show_signals bool true Show crossing signals
# @param highlighting bool true Highlight oscillator regime
# @param average_type str VAR Average type values=SMA,EMA,WMA,DEMA,TMA,VAR,WWMA,ZLEMA,TSF,HULL

source_name = str(params.get("source", "Close")).upper()
if source_name not in ("CLOSE", "OPEN", "HIGH", "LOW", "HL2", "HLC3", "OHLC4"):
    source_name = "CLOSE"
length = min(500, max(1, int(params.get("length", 2))))
percent = min(100.0, max(0.0, float(params.get("percent", 0.6))))
fast_vidya_length = min(100, max(1, int(params.get("fast_vidya_length", 10))))
slow_vidya_length = min(500, max(1, int(params.get("slow_vidya_length", 25))))
correcting_constant = min(10_000_000, max(1, int(params.get("correcting_constant", 100000))))
show_signals = bool(params.get("show_signals", True))
highlighting = bool(params.get("highlighting", True))
average_type = str(params.get("average_type", "VAR")).upper()
if average_type not in ("SMA", "EMA", "WMA", "DEMA", "TMA", "VAR", "WWMA", "ZLEMA", "TSF", "HULL"):
    average_type = "VAR"


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
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


def _var(values, period):
    result = np.zeros(len(values), dtype=float)
    upward = np.zeros(len(values), dtype=float)
    downward = np.zeros(len(values), dtype=float)
    alpha = 2.0 / (float(period) + 1.0)
    for index in range(1, len(values)):
        upward[index] = max(values[index] - values[index - 1], 0.0)
        downward[index] = max(values[index - 1] - values[index], 0.0)
    for index in range(len(values)):
        start = max(0, index - 8)
        up_sum = float(np.sum(upward[start:index + 1]))
        down_sum = float(np.sum(downward[start:index + 1]))
        cmo = (up_sum - down_sum) / (up_sum + down_sum) if up_sum + down_sum > 1e-12 else 0.0
        factor = alpha * abs(cmo)
        previous = result[index - 1] if index >= 1 else 0.0
        result[index] = factor * values[index] + (1.0 - factor) * previous
    return result


def _wwma(values, period):
    result = np.zeros(len(values), dtype=float)
    alpha = 1.0 / period
    for index, value in enumerate(values):
        previous = result[index - 1] if index >= 1 else 0.0
        result[index] = alpha * value + (1.0 - alpha) * previous
    return result


def _zlema(values, period):
    lag = max(0, int(period / 2))
    adjusted = values.copy() if lag == 0 else np.full(len(values), np.nan, dtype=float)
    if lag:
        adjusted[lag:] = values[lag:] + values[lag:] - values[:-lag]
    return _ema(adjusted, period)


def _tsf(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    x_values = np.arange(period, dtype=float)
    sum_x = float(np.sum(x_values))
    sum_x_square = float(np.sum(x_values * x_values))
    denominator = period * sum_x_square - sum_x * sum_x
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if not np.all(np.isfinite(window)):
            continue
        if period == 1 or abs(denominator) <= 1e-12:
            result[index] = window[-1]
        else:
            slope = (period * float(np.sum(window * x_values)) - sum_x * float(np.sum(window))) / denominator
            intercept = float(np.mean(window)) - slope * sum_x / period
            result[index] = intercept + slope * period
    return result


def _hma(values, period):
    half = max(1, int(period / 2))
    root = max(1, int(math.floor(math.sqrt(period) + 0.5)))
    return _wma(2.0 * _wma(values, half) - _wma(values, period), root)


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
source_one = {"OPEN": opened, "HIGH": high, "LOW": low, "HL2": (high + low) / 2.0, "HLC3": (high + low + close) / 3.0, "OHLC4": (opened + high + low + close) / 4.0}.get(source_name, close)

vidya_fast = _var(source_one, slow_vidya_length / 2.0)
vidya_slow = _var(source_one, slow_vidya_length)
vidya_long = _var(source_one, slow_vidya_length * fast_vidya_length)
denominator = vidya_slow - vidya_long + correcting_constant
ratio_source = np.full(n, np.nan, dtype=float)
valid_denominator = np.isfinite(denominator) & (np.abs(denominator) > 1e-12)
ratio_source[valid_denominator] = vidya_fast[valid_denominator] / denominator[valid_denominator]

if average_type == "SMA":
    support = _sma(ratio_source, length)
elif average_type == "EMA":
    support = _ema(ratio_source, length)
elif average_type == "WMA":
    support = _wma(ratio_source, length)
elif average_type == "DEMA":
    first = _ema(ratio_source, length)
    support = 2.0 * first - _ema(first, length)
elif average_type == "TMA":
    support = _sma(_sma(ratio_source, int(math.ceil(length / 2.0))), int(math.floor(length / 2.0)) + 1)
elif average_type == "WWMA":
    support = _wwma(ratio_source, length)
elif average_type == "ZLEMA":
    support = _zlema(ratio_source, length)
elif average_type == "TSF":
    support = _tsf(ratio_source, length)
elif average_type == "HULL":
    support = _hma(ratio_source, length)
else:
    support = _var(ratio_source, length)

long_stop = np.full(n, np.nan, dtype=float)
short_stop = np.full(n, np.nan, dtype=float)
direction = np.ones(n, dtype=int)
hott = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not np.isfinite(support[index]):
        direction[index] = direction[index - 1] if index >= 1 else 1
        continue
    difference = support[index] * percent * 0.01
    raw_long, raw_short = support[index] - difference, support[index] + difference
    previous_long = long_stop[index - 1] if index >= 1 and np.isfinite(long_stop[index - 1]) else raw_long
    previous_short = short_stop[index - 1] if index >= 1 and np.isfinite(short_stop[index - 1]) else raw_short
    long_stop[index] = max(raw_long, previous_long) if support[index] > previous_long else raw_long
    short_stop[index] = min(raw_short, previous_short) if support[index] < previous_short else raw_short
    previous_direction = direction[index - 1] if index >= 1 else 1
    direction[index] = 1 if previous_direction == -1 and support[index] > previous_short else -1 if previous_direction == 1 and support[index] < previous_long else previous_direction
    tracker = long_stop[index] if direction[index] == 1 else short_stop[index]
    hott[index] = tracker * (200.0 + percent) / 200.0 if support[index] > tracker else tracker * (200.0 - percent) / 200.0

delayed_hott = np.zeros(n, dtype=float)
for index in range(2, n):
    delayed_hott[index] = hott[index - 2] if np.isfinite(hott[index - 2]) else 0.0
buy_signal = np.zeros(n, dtype=bool)
sell_signal = np.zeros(n, dtype=bool)
for index in range(1, n):
    buy_signal[index] = delayed_hott[index] < ratio_source[index] and delayed_hott[index - 1] >= ratio_source[index - 1]
    sell_signal[index] = delayed_hott[index] > ratio_source[index] and delayed_hott[index - 1] <= ratio_source[index - 1]

fill_data = []
for index in range(n):
    if np.all(np.isfinite([delayed_hott[index], ratio_source[index]])):
        fill_data.append({"upper": float(max(delayed_hott[index], ratio_source[index])), "lower": float(min(delayed_hott[index], ratio_source[index])), "color": "rgba(255,0,0,0.80)" if highlighting and ratio_source[index] < delayed_hott[index] else "rgba(0,128,0,0.80)"})
    else:
        fill_data.append(None)
buy_points = [{"value": float(delayed_hott[index] * 0.9995), "color": "#008000", "size": 5} if show_signals and buy_signal[index] else None for index in range(n)]
sell_points = [{"value": float(ratio_source[index] * 1.0005), "color": "#FF0000", "size": 5} if show_signals and sell_signal[index] else None for index in range(n)]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "HOTT", "data": _finite(delayed_hott), "color": "#FF0000", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "LOTT", "data": _finite(ratio_source), "color": "#0000FF", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "OTTO Fill", "title": False, "data": fill_data, "color": "#008000", "type": "cloud", "overlay": False},
        {"name": "Buy", "data": buy_points, "color": "#008000", "type": "circle", "size": 5, "overlay": False},
        {"name": "Sell", "data": sell_points, "color": "#FF0000", "type": "circle", "size": 5, "overlay": False},
    ],
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "vidyaFast": _finite(vidya_fast), "vidyaSlow": _finite(vidya_slow), "vidyaLong": _finite(vidya_long), "ratioSource": _finite(ratio_source),
        "support": _finite(support), "longStop": _finite(long_stop), "shortStop": _finite(short_stop), "direction": direction.tolist(),
        "hott": _finite(hott), "delayedHott": _finite(delayed_hott), "buySignal": buy_signal.tolist(), "sellSignal": sell_signal.tolist(),
        "averageType": average_type, "source": source_name,
    },
}
