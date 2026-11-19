# ============================================================
# QuantDinger Optimized Trend Tracker
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Optimized Trend Tracker"
my_indicator_description = (
    "An adaptive moving-average support line and delayed optimized trail with "
    "support, price, and trail-direction crossover events."
)

# @param length int 2 Tracker period range=1:100:1
# @param percent float 1.4 Tracker percent range=0.0:20.0:0.1
# @param ma_type int 4 Moving average type range=0:7:1
# @param show_support bool true Show support average
# @param show_support_signals bool true Show support crossing signals
# @param show_price_signals bool false Show price crossing signals
# @param show_direction_signals bool false Show trail direction signals
# @param color_by_direction bool false Color trail by direction
# @param highlight_state bool true Highlight price state

length = max(1, int(params.get("length", 2)))
percent = max(0.0, float(params.get("percent", 1.4)))
ma_type = max(0, min(7, int(params.get("ma_type", 4))))
show_support = bool(params.get("show_support", True))
show_support_signals = bool(params.get("show_support_signals", True))
show_price_signals = bool(params.get("show_price_signals", False))
show_direction_signals = bool(params.get("show_direction_signals", False))
color_by_direction = bool(params.get("color_by_direction", False))
highlight_state = bool(params.get("highlight_state", True))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
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


def adaptive_average(values, period):
    up = np.zeros(n, dtype=float)
    down = np.zeros(n, dtype=float)
    for index in range(1, n):
        change = values[index] - values[index - 1]
        up[index] = max(change, 0.0)
        down[index] = max(-change, 0.0)
    up_sum = pd.Series(up).rolling(9, min_periods=1).sum().to_numpy(dtype=float)
    down_sum = pd.Series(down).rolling(9, min_periods=1).sum().to_numpy(dtype=float)
    result = np.zeros(n, dtype=float)
    alpha = 2.0 / (period + 1.0)
    for index in range(n):
        denominator = up_sum[index] + down_sum[index]
        cmo = (up_sum[index] - down_sum[index]) / denominator if denominator != 0.0 else 0.0
        previous = result[index - 1] if index > 0 else 0.0
        result[index] = alpha * abs(cmo) * values[index] + (1.0 - alpha * abs(cmo)) * previous
    return result


def linear_regression(values, period):
    result = np.full(n, np.nan, dtype=float)
    x_sum = float(period * (period - 1)) / 2.0
    x2_sum = float(period * (period - 1) * (2 * period - 1)) / 6.0
    denominator = period * x2_sum - x_sum * x_sum
    for index in range(period - 1, n):
        y_sum = 0.0
        xy_sum = 0.0
        for offset in range(period):
            value = values[index - period + 1 + offset]
            y_sum += value
            xy_sum += float(offset) * value
        slope = (period * xy_sum - x_sum * y_sum) / denominator if denominator != 0.0 else 0.0
        intercept = (y_sum - slope * x_sum) / period
        result[index] = intercept + slope * float(period - 1)
    return result


if ma_type == 0:
    average = sma(close, length)
elif ma_type == 1:
    average = ema(close, length)
elif ma_type == 2:
    average = wma(close, length)
elif ma_type == 3:
    average = sma(sma(close, max(1, int(np.ceil(length / 2.0)))), max(1, int(np.floor(length / 2.0)) + 1))
elif ma_type == 4:
    average = adaptive_average(close, length)
elif ma_type == 5:
    average = np.zeros(n, dtype=float)
    alpha = 1.0 / length
    for index, value in enumerate(close):
        average[index] = alpha * value + (1.0 - alpha) * (average[index - 1] if index > 0 else 0.0)
elif ma_type == 6:
    lag = max(1, int((length - 1) / 2))
    adjusted = np.full(n, np.nan, dtype=float)
    for index in range(lag, n):
        adjusted[index] = close[index] + close[index] - close[index - lag]
    average = ema(adjusted, length)
else:
    regression = linear_regression(close, length)
    previous_regression = np.concatenate(([np.nan], regression[:-1]))
    average = regression + (regression - previous_regression)

long_stop = np.zeros(n, dtype=float)
short_stop = np.zeros(n, dtype=float)
direction = np.ones(n, dtype=int)
for index in range(n):
    current = average[index] if np.isfinite(average[index]) else 0.0
    distance = current * percent / 100.0
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
    direction[index] = 1 if previous_direction == -1 and current > previous_short else -1 if previous_direction == 1 and current < previous_long else previous_direction

market_trail = np.where(direction == 1, long_stop, short_stop)
tracker = np.where(average > market_trail, market_trail * (200.0 + percent) / 200.0, market_trail * (200.0 - percent) / 200.0)
delayed = np.concatenate((np.full(2, np.nan), tracker[:-2])) if n > 2 else np.full(n, np.nan)
support_up = np.concatenate(([False], (average[1:] > delayed[1:]) & (average[:-1] <= delayed[:-1])))
support_down = np.concatenate(([False], (average[1:] < delayed[1:]) & (average[:-1] >= delayed[:-1])))
price_up = np.concatenate(([False], (close[1:] > delayed[1:]) & (close[:-1] <= delayed[:-1])))
price_down = np.concatenate(([False], (close[1:] < delayed[1:]) & (close[:-1] >= delayed[:-1])))
trail_up = np.concatenate(([False], (delayed[1:] > delayed[:-1]) & np.isfinite(delayed[:-1])))
trail_down = np.concatenate(([False], (delayed[1:] < delayed[:-1]) & np.isfinite(delayed[:-1])))
tracker_plot = []
state_cloud = []
mid_price = (open_price + high + low + close) / 4.0
for index in range(n):
    if np.isfinite(delayed[index]):
        color = "#00C853" if color_by_direction and index > 0 and delayed[index] > delayed[index - 1] else "#FF1744" if color_by_direction else "#B800D9"
        tracker_plot.append({"value": float(delayed[index]), "color": color})
        if highlight_state:
            state_cloud.append({"upper": float(max(mid_price[index], delayed[index])), "lower": float(min(mid_price[index], delayed[index])), "color": "#00C853" if average[index] > delayed[index] else "#FF1744"})
        else:
            state_cloud.append(None)
    else:
        tracker_plot.append(None)
        state_cloud.append(None)

buy_events = (show_support_signals & support_up) | (show_price_signals & price_up) | (show_direction_signals & trail_up)
sell_events = (show_support_signals & support_down) | (show_price_signals & price_down) | (show_direction_signals & trail_down)

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Support Average", "data": [float(value) if show_support and np.isfinite(value) else None for value in average], "color": "#0585E1", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Optimized Trail", "data": tracker_plot, "color": "#B800D9", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Tracker State", "title": False, "data": state_cloud, "color": "#00C853", "type": "cloud", "opacity": 0.16, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "Tracker Buy", "data": [float(delayed[i]) if buy_events[i] and np.isfinite(delayed[i]) else None for i in range(n)], "color": "#00C853", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "Tracker Sell", "data": [float(delayed[i]) if sell_events[i] and np.isfinite(delayed[i]) else None for i in range(n)], "color": "#FF1744", "renderMode": "events"},
    ],
    "layers": [],
}
