# ============================================================
# QuantDinger Optimized Trend Tracker Bands
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Optimized Trend Tracker Bands"
my_indicator_description = "Builds asymmetric delayed OTT bands around a selectable adaptive support average."

# @param source str Close Price source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param length int 2 Moving average length range=1:500:1
# @param percent float 15.0 OTT coefficient range=0:100:0.1
# @param upper_coefficient float 0.1 Upper band coefficient range=0:2:0.001
# @param lower_coefficient float 0.077 Lower band coefficient range=0:2:0.001
# @param fibonacci_levels bool false Show Fibonacci levels
# @param show_support bool false Show support line
# @param highlight_support bool false Color support by slope
# @param average_type str VAR Average type values=SMA,EMA,WMA,TMA,VAR,WWMA,ZLEMA,TSF

source_name = str(params.get("source", "Close")).upper()
if source_name not in ("CLOSE", "OPEN", "HIGH", "LOW", "HL2", "HLC3", "OHLC4"):
    source_name = "CLOSE"
length = min(500, max(1, int(params.get("length", 2))))
percent = min(100.0, max(0.0, float(params.get("percent", 15.0))))
upper_coefficient = min(2.0, max(0.0, float(params.get("upper_coefficient", 0.1))))
lower_coefficient = min(2.0, max(0.0, float(params.get("lower_coefficient", 0.077))))
fibonacci_levels = bool(params.get("fibonacci_levels", False))
show_support = bool(params.get("show_support", False))
highlight_support = bool(params.get("highlight_support", False))
average_type = str(params.get("average_type", "VAR")).upper()
if average_type not in ("SMA", "EMA", "WMA", "TMA", "VAR", "WWMA", "ZLEMA", "TSF"):
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
    alpha = 2.0 / (period + 1.0)
    for index in range(1, len(values)):
        upward[index] = max(values[index] - values[index - 1], 0.0)
        downward[index] = max(values[index - 1] - values[index], 0.0)
    for index in range(len(values)):
        start = max(0, index - 8)
        up_sum = float(np.sum(upward[start:index + 1]))
        down_sum = float(np.sum(downward[start:index + 1]))
        cmo = (up_sum - down_sum) / (up_sum + down_sum) if up_sum + down_sum > 1e-12 else 0.0
        previous = result[index - 1] if index >= 1 else 0.0
        factor = alpha * abs(cmo)
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
    adjusted = np.full(len(values), np.nan, dtype=float)
    if lag == 0:
        adjusted = values.copy()
    else:
        for index in range(lag, len(values)):
            adjusted[index] = values[index] + values[index] - values[index - lag]
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

if average_type == "SMA":
    support = _sma(source, length)
elif average_type == "EMA":
    support = _ema(source, length)
elif average_type == "WMA":
    support = _wma(source, length)
elif average_type == "TMA":
    support = _sma(_sma(source, int(math.ceil(length / 2.0))), int(math.floor(length / 2.0)) + 1)
elif average_type == "WWMA":
    support = _wwma(source, length)
elif average_type == "ZLEMA":
    support = _zlema(source, length)
elif average_type == "TSF":
    support = _tsf(source, length)
else:
    support = _var(source, length)

long_stop = np.full(n, np.nan, dtype=float)
short_stop = np.full(n, np.nan, dtype=float)
direction = np.ones(n, dtype=int)
ott = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not np.isfinite(support[index]):
        direction[index] = direction[index - 1] if index >= 1 else 1
        continue
    difference = support[index] * percent * 0.01
    raw_long = support[index] - difference
    raw_short = support[index] + difference
    previous_long = long_stop[index - 1] if index >= 1 and np.isfinite(long_stop[index - 1]) else raw_long
    previous_short = short_stop[index - 1] if index >= 1 and np.isfinite(short_stop[index - 1]) else raw_short
    long_stop[index] = max(raw_long, previous_long) if support[index] > previous_long else raw_long
    short_stop[index] = min(raw_short, previous_short) if support[index] < previous_short else raw_short
    previous_direction = direction[index - 1] if index >= 1 else 1
    if previous_direction == -1 and support[index] > previous_short:
        direction[index] = 1
    elif previous_direction == 1 and support[index] < previous_long:
        direction[index] = -1
    else:
        direction[index] = previous_direction
    tracker = long_stop[index] if direction[index] == 1 else short_stop[index]
    ott[index] = tracker * (200.0 + percent) / 200.0 if support[index] > tracker else tracker * (200.0 - percent) / 200.0

delayed_ott = np.zeros(n, dtype=float)
for index in range(2, n):
    delayed_ott[index] = ott[index - 2] if np.isfinite(ott[index - 2]) else 0.0
upper = delayed_ott * (1.0 + upper_coefficient)
upper_mid = delayed_ott * (1.0 + upper_coefficient * (0.618 if fibonacci_levels else 0.5))
upper_fibonacci = delayed_ott * (1.0 + upper_coefficient * 0.382)
lower = delayed_ott * (1.0 - lower_coefficient)
lower_mid = delayed_ott * (1.0 - lower_coefficient * (0.618 if fibonacci_levels else 0.5))
lower_fibonacci = delayed_ott * (1.0 - lower_coefficient * 0.382)

support_cross_up = np.zeros(n, dtype=bool)
support_cross_down = np.zeros(n, dtype=bool)
price_cross_up = np.zeros(n, dtype=bool)
price_cross_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.all(np.isfinite([support[index], support[index - 1]])):
        support_cross_up[index] = support[index] > delayed_ott[index] and support[index - 1] <= delayed_ott[index - 1]
        support_cross_down[index] = support[index] < delayed_ott[index] and support[index - 1] >= delayed_ott[index - 1]
    price_cross_up[index] = source[index] > delayed_ott[index] and source[index - 1] <= delayed_ott[index - 1]
    price_cross_down[index] = source[index] < delayed_ott[index] and source[index - 1] >= delayed_ott[index - 1]

support_colors = ["#0585E1" if not highlight_support else "#008000" if index >= 1 and np.isfinite(support[index]) and np.isfinite(support[index - 1]) and support[index] > support[index - 1] else "#FF0000" for index in range(n)]


def _cloud(first, second, color, visible=True):
    data = []
    for index in range(n):
        if visible and np.all(np.isfinite([first[index], second[index]])):
            data.append({"upper": float(max(first[index], second[index])), "lower": float(min(first[index], second[index])), "color": color})
        else:
            data.append(None)
    return data


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "OTT", "data": _finite(delayed_ott), "color": "#B800D9", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Support Line", "data": _finite(support) if show_support else [None] * n, "color": support_colors, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "OTT Upper", "data": _finite(upper), "color": "#4500C4", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "OTT Upper Mid", "data": _finite(upper_mid), "color": "#8400FF", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "OTT Upper Fibonacci", "data": _finite(upper_fibonacci) if fibonacci_levels else [None] * n, "color": "#C830FF", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "OTT Lower", "data": _finite(lower), "color": "#00A6FF", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "OTT Lower Mid", "data": _finite(lower_mid), "color": "#007BBD", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "OTT Lower Fibonacci", "data": _finite(lower_fibonacci) if fibonacci_levels else [None] * n, "color": "#024DE3", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Upper Outer Fill", "title": False, "data": _cloud(upper, upper_mid, "rgba(69,0,196,0.12)"), "color": "#4500C4", "type": "cloud", "overlay": True},
        {"name": "Upper Inner Fill", "title": False, "data": _cloud(upper_mid, delayed_ott, "rgba(200,16,255,0.12)"), "color": "#C810FF", "type": "cloud", "overlay": True},
        {"name": "Lower Outer Fill", "title": False, "data": _cloud(lower, lower_mid, "rgba(0,166,255,0.12)"), "color": "#00A6FF", "type": "cloud", "overlay": True},
        {"name": "Lower Inner Fill", "title": False, "data": _cloud(lower_mid, delayed_ott, "rgba(2,77,227,0.12)"), "color": "#024DE3", "type": "cloud", "overlay": True},
    ],
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "support": _finite(support), "longStop": _finite(long_stop), "shortStop": _finite(short_stop), "direction": direction.tolist(),
        "ott": _finite(ott), "delayedOtt": _finite(delayed_ott), "upper": _finite(upper), "upperMid": _finite(upper_mid), "upperFibonacci": _finite(upper_fibonacci),
        "lower": _finite(lower), "lowerMid": _finite(lower_mid), "lowerFibonacci": _finite(lower_fibonacci),
        "supportCrossUp": support_cross_up.tolist(), "supportCrossDown": support_cross_down.tolist(), "priceCrossUp": price_cross_up.tolist(), "priceCrossDown": price_cross_down.tolist(),
        "averageType": average_type, "source": source_name,
    },
}
