# ============================================================
# QuantDinger Consolidation Range Signals
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Consolidation Range Signals"
my_indicator_description = (
    "Detects consolidation through ADX or volatility compression, displays a stepped "
    "range filter with internal bands, and projects cooldown-controlled entry, stop, and targets."
)

# @param detection_method str ADX Range detection method values=ADX,Volatility
# @param range_period int 10 Range period range=2:200:1
# @param range_multiplier float 1.8 Range multiplier range=0.1:10.0:0.1
# @param show_targets bool true Show SL and TP levels
# @param cooldown int 20 Entry cooldown bars range=1:200:1
# @param stop_multiplier float 0.4 Stop multiplier range=0.1:10.0:0.1
# @param show_stop bool true Show stop level
# @param tp1_multiplier float 0.5 TP1 multiplier range=0.1:10.0:0.1
# @param show_tp1 bool true Show TP1 level
# @param tp2_multiplier float 1.0 TP2 multiplier range=0.1:10.0:0.1
# @param show_tp2 bool false Show TP2 level
# @param tp3_multiplier float 2.0 TP3 multiplier range=0.1:10.0:0.1
# @param show_tp3 bool false Show TP3 level
# @param adx_threshold int 17 ADX range threshold range=0:100:1
# @param adx_smoothing int 10 ADX smoothing range=1:100:1
# @param std_multiplier float 0.8 Standard deviation compression range=0.1:2.0:0.1
# @param variance_multiplier float 0.8 Variance compression range=0.1:2.0:0.1
# @param atr_multiplier float 0.9 ATR compression range=0.1:2.0:0.1

detection_method = str(params.get("detection_method", "ADX"))
range_period = min(200, max(2, int(params.get("range_period", 10))))
range_multiplier = min(10.0, max(0.1, float(params.get("range_multiplier", 1.8))))
show_targets = bool(params.get("show_targets", True))
cooldown = min(200, max(1, int(params.get("cooldown", 20))))
stop_multiplier = min(10.0, max(0.1, float(params.get("stop_multiplier", 0.4))))
show_stop = bool(params.get("show_stop", True))
tp1_multiplier = min(10.0, max(0.1, float(params.get("tp1_multiplier", 0.5))))
show_tp1 = bool(params.get("show_tp1", True))
tp2_multiplier = min(10.0, max(0.1, float(params.get("tp2_multiplier", 1.0))))
show_tp2 = bool(params.get("show_tp2", False))
tp3_multiplier = min(10.0, max(0.1, float(params.get("tp3_multiplier", 2.0))))
show_tp3 = bool(params.get("show_tp3", False))
adx_threshold = min(100, max(0, int(params.get("adx_threshold", 17))))
adx_smoothing = min(100, max(1, int(params.get("adx_smoothing", 10))))
std_multiplier = min(2.0, max(0.1, float(params.get("std_multiplier", 0.8))))
variance_multiplier = min(2.0, max(0.1, float(params.get("variance_multiplier", 0.8))))
atr_multiplier = min(2.0, max(0.1, float(params.get("atr_multiplier", 0.9))))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
times = df["time"].to_numpy(dtype=float)
n = len(df)
if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0
interval = float(np.median(np.diff(times))) if n > 1 else 3600000.0
interval = max(interval, 1.0)

true_range = np.full(n, np.nan, dtype=float)
plus_dm = np.zeros(n, dtype=float)
minus_dm = np.zeros(n, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
        upward = high[index] - high[index - 1]
        downward = low[index - 1] - low[index]
        plus_dm[index] = upward if upward > downward and upward > 0.0 else 0.0
        minus_dm[index] = downward if downward > upward and downward > 0.0 else 0.0

def wilder(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < period:
        return result
    result[period - 1] = float(np.mean(values[:period]))
    for item_index in range(period, len(values)):
        result[item_index] = (result[item_index - 1] * (period - 1) + values[item_index]) / period
    return result

atr_range = wilder(true_range, range_period)
plus_smoothed = wilder(plus_dm, range_period)
minus_smoothed = wilder(minus_dm, range_period)
plus_di = np.divide(100.0 * plus_smoothed, atr_range, out=np.full(n, np.nan), where=np.isfinite(atr_range) & (atr_range != 0.0))
minus_di = np.divide(100.0 * minus_smoothed, atr_range, out=np.full(n, np.nan), where=np.isfinite(atr_range) & (atr_range != 0.0))
di_total = plus_di + minus_di
dx = np.divide(100.0 * np.abs(plus_di - minus_di), di_total, out=np.full(n, np.nan), where=np.isfinite(di_total) & (di_total != 0.0))

adx = np.full(n, np.nan, dtype=float)
valid_dx = np.where(np.isfinite(dx), dx, 0.0)
first_dx = np.flatnonzero(np.isfinite(dx))
if len(first_dx):
    start = int(first_dx[0])
    if n - start >= adx_smoothing:
        first_adx = start + adx_smoothing - 1
        adx[first_adx] = float(np.mean(valid_dx[start:first_adx + 1]))
        for index in range(first_adx + 1, n):
            adx[index] = (adx[index - 1] * (adx_smoothing - 1) + valid_dx[index]) / adx_smoothing
is_adx = np.isfinite(adx) & (adx < adx_threshold)

log_return = np.full(n, np.nan, dtype=float)
for index in range(1, n):
    if close[index - 1] > 0.0 and close[index] > 0.0:
        log_return[index] = np.log(close[index] / close[index - 1])
std_now = pd.Series(log_return).rolling(range_period, min_periods=range_period).std(ddof=0).to_numpy(dtype=float)
std_average = pd.Series(std_now).rolling(range_period, min_periods=range_period).mean().to_numpy(dtype=float)
variance_now = pd.Series(log_return).rolling(range_period, min_periods=range_period).var(ddof=0).to_numpy(dtype=float)
variance_average = pd.Series(variance_now).rolling(range_period, min_periods=range_period).mean().to_numpy(dtype=float)
atr_average = pd.Series(atr_range).rolling(range_period, min_periods=range_period).mean().to_numpy(dtype=float)
is_volatility = (
    np.isfinite(std_now)
    & np.isfinite(std_average)
    & np.isfinite(variance_now)
    & np.isfinite(variance_average)
    & np.isfinite(atr_range)
    & np.isfinite(atr_average)
    & (std_now < std_average * std_multiplier)
    & (variance_now < variance_average * variance_multiplier)
    & (atr_range < atr_average * atr_multiplier)
)
method_detected = is_adx if detection_method == "ADX" else is_volatility

range_distance_source = np.full(n, np.nan, dtype=float)
for index in range(1, n):
    range_distance_source[index] = 2.618 * abs(high[index] - low[index - 1])
distance_window = min(2000, max(20, n))
distance_min_periods = min(distance_window, max(10, min(200, distance_window // 4)))
distance = pd.Series(range_distance_source).rolling(distance_window, min_periods=distance_min_periods).mean().to_numpy(dtype=float) * range_multiplier

range_filter = np.full(n, np.nan, dtype=float)
upper_band = np.full(n, np.nan, dtype=float)
lower_band = np.full(n, np.nan, dtype=float)
mid_1 = np.full(n, np.nan, dtype=float)
mid_2 = np.full(n, np.nan, dtype=float)
mid_3 = np.full(n, np.nan, dtype=float)
mid_4 = np.full(n, np.nan, dtype=float)
range_visible = np.zeros(n, dtype=bool)
step_up = np.zeros(n, dtype=bool)
step_down = np.zeros(n, dtype=bool)

current_filter = float(close[0]) if n else np.nan
visible = False
previous_high = np.nan
previous_low = np.nan
range_start = None
for index in range(n):
    if method_detected[index] and range_start is None:
        range_start = index
    elif not method_detected[index]:
        range_start = None
    if method_detected[index]:
        visible = True

    previous_filter = current_filter
    current_distance = distance[index]
    if np.isfinite(current_distance) and current_distance > 0.0:
        if close[index] > previous_filter:
            current_filter = previous_filter if close[index] - current_distance < previous_filter else close[index] - current_distance
        else:
            current_filter = previous_filter if close[index] + current_distance > previous_filter else close[index] + current_distance
        range_filter[index] = current_filter
        upper_band[index] = current_filter + current_distance
        lower_band[index] = current_filter - current_distance
        third = current_distance / 3.0
        mid_1[index] = current_filter + third
        mid_2[index] = current_filter + third * 2.0
        mid_3[index] = current_filter - third
        mid_4[index] = current_filter - third * 2.0
        step_up[index] = current_filter > previous_filter
        step_down[index] = current_filter < previous_filter

    if method_detected[index] and np.isfinite(upper_band[index]):
        previous_high = upper_band[index]
        previous_low = lower_band[index]
    if not method_detected[index] and index > 0 and np.isfinite(previous_high) and (close[index - 1] > previous_high or close[index - 1] < previous_low):
        visible = False
    range_visible[index] = visible and np.isfinite(range_filter[index])

layers = []
last_breakout = None
long_entries = [None for _ in range(n)]
short_entries = [None for _ in range(n)]

def time_for_index(index):
    if n == 0:
        return 0.0
    if index < n:
        return float(times[max(0, index)])
    return float(times[-1] + (index - (n - 1)) * interval)

def add_level(start_index, price, text_value, color):
    end_index = start_index + 20
    layers.append(
        {
            "type": "line",
            "startIndex": int(start_index),
            "end": time_for_index(end_index),
            "y1": float(price),
            "y2": float(price),
            "color": color,
            "lineWidth": 2,
        }
    )
    layers.append(
        {
            "type": "label",
            "timestamp": time_for_index(end_index),
            "price": float(price),
            "text": text_value,
            "color": color,
            "textColor": "#FFFFFF",
            "side": "right",
        }
    )

for index in range(n):
    if not range_visible[index] or not np.isfinite(range_filter[index]) or not np.isfinite(distance[index]):
        continue
    can_trigger = last_breakout is None or index - last_breakout >= cooldown
    direction = 1 if step_up[index] and can_trigger else -1 if step_down[index] and can_trigger else 0
    if direction == 0:
        continue
    base = float(range_filter[index])
    entry = float(close[index])
    stop = base - distance[index] * stop_multiplier if direction == 1 else base + distance[index] * stop_multiplier
    tp1 = base + distance[index] * tp1_multiplier if direction == 1 else base - distance[index] * tp1_multiplier
    tp2 = base + distance[index] * tp2_multiplier if direction == 1 else base - distance[index] * tp2_multiplier
    tp3 = base + distance[index] * tp3_multiplier if direction == 1 else base - distance[index] * tp3_multiplier
    if show_targets:
        add_level(index, entry, "Entry", "#2962FF")
        if show_stop:
            add_level(index, stop, "SL", "#FF0000")
        target_color = "#09C10F" if direction == 1 else "#FF69CB"
        if show_tp1:
            add_level(index, tp1, "TP 1", target_color)
        if show_tp2:
            add_level(index, tp2, "TP 2", target_color)
        if show_tp3:
            add_level(index, tp3, "TP 3", target_color)
    if direction == 1:
        long_entries[index] = entry
    else:
        short_entries[index] = entry
    last_breakout = index

trend_data = []
mid_data = [[], [], [], []]
trend_color = "#0060E6"
for index in range(n):
    if range_visible[index]:
        if step_up[index]:
            trend_color = "#0060E6"
        elif step_down[index]:
            trend_color = "#29FAFA"
        trend_data.append({"value": float(range_filter[index]), "color": trend_color})
        values = [mid_1[index], mid_2[index], mid_3[index], mid_4[index]]
        for item_index, value in enumerate(values):
            mid_data[item_index].append(float(value))
    else:
        trend_data.append(None)
        for item_data in mid_data:
            item_data.append(None)

plots = [
    {"name": "Price Reference", "title": False, "data": [float(value) if np.isfinite(value) else None for value in close], "color": "rgba(255,255,255,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Range Trend", "data": trend_data, "color": "#0060E6", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Upper Mid One", "data": mid_data[0], "color": "rgba(0,96,230,0.50)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Upper Mid Two", "data": mid_data[1], "color": "rgba(0,96,230,0.50)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Lower Mid One", "data": mid_data[2], "color": "rgba(41,250,250,0.50)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Lower Mid Two", "data": mid_data[3], "color": "rgba(41,250,250,0.50)", "type": "line", "lineWidth": 1, "overlay": True},
]
plots = [plot for plot in plots if any(item is not None for item in plot["data"])]
signals = []
if any(item is not None for item in long_entries):
    signals.append({"type": "buy", "text": "Entry", "name": "Range Long", "data": long_entries, "color": "#09C10F", "renderMode": "events"})
if any(item is not None for item in short_entries):
    signals.append({"type": "sell", "text": "Entry", "name": "Range Short", "data": short_entries, "color": "#FF69CB", "renderMode": "events"})
output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers[-500:]}
