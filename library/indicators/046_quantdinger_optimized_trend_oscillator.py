# ============================================================
# QuantDinger Optimized Trend Oscillator
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Optimized Trend Oscillator"
my_indicator_description = (
    "A corrected multi-speed adaptive average transformed into an optimized "
    "trend tracker oscillator with crossover events."
)

# @param tracker_length int 2 Tracker length range=1:100:1
# @param optimization_percent float 0.6 Optimization coefficient range=0.0:20.0:0.1
# @param fast_vidya_length int 10 Fast adaptive length range=1:100:1
# @param slow_vidya_length int 25 Slow adaptive length range=2:200:1
# @param correcting_constant float 100000.0 Correcting constant range=1.0:1000000.0:1000.0
# @param ma_type int 5 Moving average type range=0:9:1
# @param show_signals bool true Show crossover events
# @param highlight_state bool true Highlight oscillator state

tracker_length = max(1, int(params.get("tracker_length", 2)))
optimization_percent = max(0.0, float(params.get("optimization_percent", 0.6)))
fast_vidya_length = max(1, int(params.get("fast_vidya_length", 10)))
slow_vidya_length = max(2, int(params.get("slow_vidya_length", 25)))
correcting_constant = max(1.0, float(params.get("correcting_constant", 100000.0)))
ma_type = max(0, min(9, int(params.get("ma_type", 5))))
show_signals = bool(params.get("show_signals", True))
highlight_state = bool(params.get("highlight_state", True))

df = df.copy()
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def sma(values, period):
    return pd.Series(values).rolling(period, min_periods=period).mean().to_numpy(dtype=float)


def ema(values, period):
    result = np.full(n, np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        previous = value if not np.isfinite(previous) else alpha * value + (1.0 - alpha) * previous
        result[index] = previous
    return result


def wma(values, period):
    result = np.full(n, np.nan, dtype=float)
    denominator = float(period * (period + 1)) / 2.0
    for index in range(period - 1, n):
        total = 0.0
        valid = True
        for offset in range(period):
            value = values[index - period + 1 + offset]
            if not np.isfinite(value):
                valid = False
                break
            total += value * float(offset + 1)
        if valid:
            result[index] = total / denominator
    return result


def vidya(values, period):
    source = np.asarray(values, dtype=float)
    up = np.zeros(n, dtype=float)
    down = np.zeros(n, dtype=float)
    for index in range(1, n):
        change = source[index] - source[index - 1] if np.isfinite(source[index]) and np.isfinite(source[index - 1]) else 0.0
        up[index] = max(change, 0.0)
        down[index] = max(-change, 0.0)
    up_sum = pd.Series(up).rolling(9, min_periods=1).sum().to_numpy(dtype=float)
    down_sum = pd.Series(down).rolling(9, min_periods=1).sum().to_numpy(dtype=float)
    result = np.zeros(n, dtype=float)
    alpha = 2.0 / (period + 1.0)
    for index in range(n):
        denominator = up_sum[index] + down_sum[index]
        cmo = (up_sum[index] - down_sum[index]) / denominator if denominator != 0.0 else 0.0
        value = source[index] if np.isfinite(source[index]) else 0.0
        previous = result[index - 1] if index > 0 else 0.0
        result[index] = alpha * abs(cmo) * value + (1.0 - alpha * abs(cmo)) * previous
    return result


def wwma(values, period):
    result = np.zeros(n, dtype=float)
    alpha = 1.0 / period
    for index, value in enumerate(values):
        previous = result[index - 1] if index > 0 else 0.0
        current = value if np.isfinite(value) else 0.0
        result[index] = alpha * current + (1.0 - alpha) * previous
    return result


def linear_regression(values, period):
    result = np.full(n, np.nan, dtype=float)
    x_sum = float(period * (period - 1)) / 2.0
    x2_sum = float(period * (period - 1) * (2 * period - 1)) / 6.0
    denominator = period * x2_sum - x_sum * x_sum
    for index in range(period - 1, n):
        y_sum = 0.0
        xy_sum = 0.0
        valid = True
        for offset in range(period):
            value = values[index - period + 1 + offset]
            if not np.isfinite(value):
                valid = False
                break
            y_sum += value
            xy_sum += float(offset) * value
        if valid:
            slope = (period * xy_sum - x_sum * y_sum) / denominator if denominator != 0.0 else 0.0
            intercept = (y_sum - slope * x_sum) / period
            result[index] = intercept + slope * float(period - 1)
    return result


def choose_average(values, period):
    if ma_type == 0:
        return sma(values, period)
    if ma_type == 1:
        return ema(values, period)
    if ma_type == 2:
        return wma(values, period)
    if ma_type == 3:
        first = ema(values, period)
        return 2.0 * first - ema(first, period)
    if ma_type == 4:
        return sma(sma(values, max(1, int(np.ceil(period / 2.0)))), max(1, int(np.floor(period / 2.0)) + 1))
    if ma_type == 5:
        return vidya(values, period)
    if ma_type == 6:
        return wwma(values, period)
    if ma_type == 7:
        lag = max(1, int((period - 1) / 2))
        adjusted = np.full(n, np.nan, dtype=float)
        for index in range(lag, n):
            adjusted[index] = values[index] + values[index] - values[index - lag]
        return ema(adjusted, period)
    if ma_type == 8:
        regression = linear_regression(values, period)
        previous = np.concatenate(([np.nan], regression[:-1]))
        return regression + (regression - previous)
    half = max(1, int(period / 2))
    return wma(2.0 * wma(values, half) - wma(values, period), max(1, int(round(np.sqrt(period)))))


move_fast = vidya(close, max(1, int(slow_vidya_length / 2)))
move_slow = vidya(close, slow_vidya_length)
move_correction = vidya(close, slow_vidya_length * fast_vidya_length)
denominator = move_slow - move_correction + correcting_constant
source = np.divide(move_fast, denominator, out=np.zeros(n, dtype=float), where=denominator != 0.0)
average = choose_average(source, tracker_length)

long_stop = np.zeros(n, dtype=float)
short_stop = np.zeros(n, dtype=float)
direction = np.ones(n, dtype=int)
for index in range(n):
    current = average[index] if np.isfinite(average[index]) else 0.0
    distance = current * optimization_percent / 100.0
    raw_long = current - distance
    raw_short = current + distance
    if index == 0:
        long_stop[index] = raw_long
        short_stop[index] = raw_short
        continue
    previous_long = long_stop[index - 1]
    previous_short = short_stop[index - 1]
    long_stop[index] = max(raw_long, previous_long) if current > previous_long else raw_long
    short_stop[index] = min(raw_short, previous_short) if current < previous_short else raw_short
    previous_direction = direction[index - 1]
    if previous_direction == -1 and current > previous_short:
        direction[index] = 1
    elif previous_direction == 1 and current < previous_long:
        direction[index] = -1
    else:
        direction[index] = previous_direction

market_trail = np.where(direction == 1, long_stop, short_stop)
tracker = np.where(average > market_trail, market_trail * (200.0 + optimization_percent) / 200.0, market_trail * (200.0 - optimization_percent) / 200.0)
delayed_tracker = np.concatenate((np.full(2, np.nan), tracker[:-2])) if n > 2 else np.full(n, np.nan)
buy_event = np.concatenate(([False], (delayed_tracker[1:] < source[1:]) & (delayed_tracker[:-1] >= source[:-1])))
sell_event = np.concatenate(([False], (delayed_tracker[1:] > source[1:]) & (delayed_tracker[:-1] <= source[:-1])))
state_cloud = []
for index in range(n):
    if highlight_state and np.isfinite(delayed_tracker[index]):
        state_cloud.append({"upper": float(max(source[index], delayed_tracker[index])), "lower": float(min(source[index], delayed_tracker[index])), "color": "#00C853" if source[index] >= delayed_tracker[index] else "#FF1744"})
    else:
        state_cloud.append(None)

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Optimized Tracker", "data": [float(value) if np.isfinite(value) else None for value in delayed_tracker], "color": "#FF1744", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Corrected Adaptive Source", "data": [float(value) if np.isfinite(value) else None for value in source], "color": "#2962FF", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Oscillator State", "title": False, "data": state_cloud, "color": "#00C853", "type": "cloud", "opacity": 0.20, "overlay": False},
    ],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "Tracker Buy", "data": [float(delayed_tracker[i]) if show_signals and buy_event[i] and np.isfinite(delayed_tracker[i]) else None for i in range(n)], "color": "#00C853", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "Tracker Sell", "data": [float(delayed_tracker[i]) if show_signals and sell_event[i] and np.isfinite(delayed_tracker[i]) else None for i in range(n)], "color": "#FF1744", "renderMode": "events"},
    ],
    "layers": [],
}
