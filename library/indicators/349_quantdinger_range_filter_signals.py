# ============================================================
# QuantDinger Range Filter Signals
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Range Filter Signals"
my_indicator_description = "Smooths price movement into an adaptive range filter and marks confirmed direction switches."

# @param source str Close Price source options=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param sampling_period int 100 Sampling period range=1:10000:1
# @param range_multiplier float 3.0 Range multiplier range=0.1:100:0.1
# @param rising_color str #FFFFFF Rising color
# @param neutral_color str #90BFF9 Neutral color
# @param falling_color str #2962FF Falling color

source_name = str(params.get("source", "Close"))
sampling_period = min(10000, max(1, int(params.get("sampling_period", 100))))
range_multiplier = min(100.0, max(0.1, float(params.get("range_multiplier", 3.0))))
rising_color = str(params.get("rising_color", "#FFFFFF"))
neutral_color = str(params.get("neutral_color", "#90BFF9"))
falling_color = str(params.get("falling_color", "#2962FF"))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def _source(name, opened, high, low, close):
    key = name.strip().upper()
    if key == "OPEN":
        return opened.copy()
    if key == "HIGH":
        return high.copy()
    if key == "LOW":
        return low.copy()
    if key == "HL2":
        return (high + low) / 2.0
    if key == "HLC3":
        return (high + low + close) / 3.0
    if key == "OHLC4":
        return (opened + high + low + close) / 4.0
    return close.copy()


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
source = _source(source_name, opened, high, low, close)
n = len(df)

movement = np.full(n, np.nan, dtype=float)
if n > 1:
    movement[1:] = np.abs(np.diff(source))
average_movement = _ema(movement, sampling_period)
smooth_range = _ema(average_movement, sampling_period * 2 - 1) * range_multiplier

range_filter = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not np.all(np.isfinite([source[index], smooth_range[index]])):
        continue
    prior = range_filter[index - 1] if index > 0 and np.isfinite(range_filter[index - 1]) else 0.0
    if source[index] > prior:
        range_filter[index] = prior if source[index] - smooth_range[index] < prior else source[index] - smooth_range[index]
    else:
        range_filter[index] = prior if source[index] + smooth_range[index] > prior else source[index] + smooth_range[index]

upward = np.zeros(n, dtype=int)
downward = np.zeros(n, dtype=int)
for index in range(1, n):
    if not np.all(np.isfinite([range_filter[index], range_filter[index - 1]])):
        upward[index] = upward[index - 1]
        downward[index] = downward[index - 1]
    elif range_filter[index] > range_filter[index - 1]:
        upward[index] = upward[index - 1] + 1
        downward[index] = 0
    elif range_filter[index] < range_filter[index - 1]:
        upward[index] = 0
        downward[index] = downward[index - 1] + 1
    else:
        upward[index] = upward[index - 1]
        downward[index] = downward[index - 1]

upper_target = range_filter + smooth_range
lower_target = range_filter - smooth_range
raw_long = np.zeros(n, dtype=bool)
raw_short = np.zeros(n, dtype=bool)
buy = np.zeros(n, dtype=bool)
sell = np.zeros(n, dtype=bool)
condition_state = np.zeros(n, dtype=int)
bar_colors = []
filter_colors = []
prior_state = 0
for index in range(n):
    source_changed = index > 0 and source[index] != source[index - 1]
    if np.isfinite(range_filter[index]) and source_changed:
        raw_long[index] = source[index] > range_filter[index] and upward[index] > 0
        raw_short[index] = source[index] < range_filter[index] and downward[index] > 0
    buy[index] = raw_long[index] and prior_state == -1
    sell[index] = raw_short[index] and prior_state == 1
    if raw_long[index]:
        prior_state = 1
    elif raw_short[index]:
        prior_state = -1
    condition_state[index] = prior_state
    filter_colors.append(rising_color if upward[index] > 0 else falling_color if downward[index] > 0 else neutral_color)
    bar_colors.append(rising_color if raw_long[index] else falling_color if raw_short[index] else neutral_color)

upper_cloud = []
lower_cloud = []
for index in range(n):
    if np.all(np.isfinite([upper_target[index], range_filter[index]])):
        upper_cloud.append({"upper": float(upper_target[index]), "lower": float(range_filter[index]), "color": rising_color, "opacity": 0.10})
        lower_cloud.append({"upper": float(range_filter[index]), "lower": float(lower_target[index]), "color": falling_color, "opacity": 0.10})
    else:
        upper_cloud.append(None)
        lower_cloud.append(None)


def _events(mask, prices, text, color, event_type):
    data = []
    for index, active in enumerate(mask):
        data.append({"active": True, "price": float(prices[index]), "text": text, "color": color} if active else None)
    return {"type": event_type, "name": text, "data": data, "color": color, "renderMode": "events"}


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Range Filter", "data": _finite(range_filter), "color": filter_colors, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "High Target", "data": _finite(upper_target), "color": "rgba(255,255,255,0.30)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Low Target", "data": _finite(lower_target), "color": "rgba(41,98,255,0.30)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "High Target Range", "title": False, "data": upper_cloud, "color": rising_color, "type": "cloud", "overlay": True},
        {"name": "Low Target Range", "title": False, "data": lower_cloud, "color": falling_color, "type": "cloud", "overlay": True},
    ],
    "signals": [
        _events(buy, low, "Buy", "#16A34A", "buy"),
        _events(sell, high, "Sell", falling_color, "sell"),
    ],
    "layers": [],
    "tables": [],
    "bar_colors": bar_colors,
    "background_colors": [None] * n,
    "calculatedVars": {
        "source": _finite(source),
        "movement": _finite(movement),
        "averageMovement": _finite(average_movement),
        "smoothRange": _finite(smooth_range),
        "rangeFilter": _finite(range_filter),
        "upward": [int(value) for value in upward],
        "downward": [int(value) for value in downward],
        "upperTarget": _finite(upper_target),
        "lowerTarget": _finite(lower_target),
        "rawLong": raw_long.tolist(),
        "rawShort": raw_short.tolist(),
        "conditionState": [int(value) for value in condition_state],
        "buy": buy.tolist(),
        "sell": sell.tolist(),
    },
}
