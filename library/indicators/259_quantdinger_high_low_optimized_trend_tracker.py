# ============================================================
# QuantDinger High-Low Optimized Trend Tracker
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger High-Low Optimized Trend Tracker"
my_indicator_description = (
    "Builds independent optimized trend trackers from rolling highs and lows, "
    "with ten moving-average engines, state fill, candle colors, and price alerts."
)

# @param length int 2 OTT period range=1:200:1
# @param percent float 0.6 OTT optimization coefficient range=0.0:20.0:0.1
# @param high_low_length int 10 Highest and lowest length range=1:500:1
# @param ma_type str VAR Moving average type values=SMA,EMA,WMA,DEMA,TMA,VAR,WWMA,ZLEMA,TSF,HULL
# @param highlighting bool true Highlight between trackers

length = max(1, int(params.get("length", 2)))
percent = max(0.0, float(params.get("percent", 0.6)))
high_low_length = max(1, int(params.get("high_low_length", 10)))
ma_type = str(params.get("ma_type", "VAR")).upper()
highlighting = bool(params.get("highlighting", True))

frame = df.copy().reset_index(drop=True)
n = len(frame)
high_values = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype=float)
low_values = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype=float)
close_values = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype=float)
highest = pd.Series(high_values).rolling(high_low_length, min_periods=1).max().to_numpy(dtype=float)
lowest = pd.Series(low_values).rolling(high_low_length, min_periods=1).min().to_numpy(dtype=float)


def sma(values, period):
    return pd.Series(values, dtype=float).rolling(period, min_periods=period).mean().to_numpy(dtype=float)


def ema(values, period):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        previous = value if not np.isfinite(previous) else alpha * value + (1.0 - alpha) * previous
        result[index] = previous
    return result


def wma(values, period):
    period = max(1, int(period))
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1.0, period + 1.0)
    denominator = float(weights.sum())
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / denominator)
    return result


def variable_average(values, period):
    values = np.asarray(values, dtype=float)
    up = np.zeros(len(values), dtype=float)
    down = np.zeros(len(values), dtype=float)
    for index in range(1, len(values)):
        change = values[index] - values[index - 1]
        up[index] = max(change, 0.0)
        down[index] = max(-change, 0.0)
    up_sum = pd.Series(up).rolling(9, min_periods=9).sum().to_numpy(dtype=float)
    down_sum = pd.Series(down).rolling(9, min_periods=9).sum().to_numpy(dtype=float)
    result = np.zeros(len(values), dtype=float)
    alpha = 2.0 / (period + 1.0)
    for index in range(len(values)):
        denominator = up_sum[index] + down_sum[index]
        cmo = (up_sum[index] - down_sum[index]) / denominator if np.isfinite(denominator) and denominator != 0.0 else 0.0
        weight = alpha * abs(cmo)
        result[index] = weight * values[index] + (1.0 - weight) * (result[index - 1] if index else 0.0)
    return result


def wwma(values, period):
    result = np.zeros(len(values), dtype=float)
    alpha = 1.0 / period
    for index, value in enumerate(values):
        result[index] = alpha * value + (1.0 - alpha) * (result[index - 1] if index else 0.0)
    return result


def linreg(values, period, offset=0):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    x = np.arange(period, dtype=float)
    x_mean = float(x.mean())
    denominator = float(np.sum((x - x_mean) ** 2))
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if not np.all(np.isfinite(window)):
            continue
        y_mean = float(window.mean())
        slope = float(np.sum((x - x_mean) * (window - y_mean)) / denominator) if denominator else 0.0
        intercept = y_mean - slope * x_mean
        result[index] = intercept + slope * (period - 1 - offset)
    return result


def moving_average(values):
    if ma_type == "SMA":
        return sma(values, length)
    if ma_type == "EMA":
        return ema(values, length)
    if ma_type == "WMA":
        return wma(values, length)
    if ma_type == "DEMA":
        first = ema(values, length)
        return 2.0 * first - ema(first, length)
    if ma_type == "TMA":
        return sma(sma(values, int(np.ceil(length / 2.0))), int(np.floor(length / 2.0)) + 1)
    if ma_type == "WWMA":
        return wwma(values, length)
    if ma_type == "ZLEMA":
        lag = int(np.floor(length / 2.0))
        adjusted = np.full(n, np.nan, dtype=float)
        for index in range(lag, n):
            adjusted[index] = values[index] * 2.0 - values[index - lag]
        return ema(adjusted, length)
    if ma_type == "TSF":
        current = linreg(values, length, 0)
        prior = linreg(values, length, 1)
        return current + (current - prior)
    if ma_type == "HULL":
        half = max(1, int(np.floor(length / 2.0)))
        root = max(1, int(np.floor(np.sqrt(length) + 0.5)))
        return wma(2.0 * wma(values, half) - wma(values, length), root)
    return variable_average(values, length)


def tracker(average):
    long_stop = np.full(n, np.nan, dtype=float)
    short_stop = np.full(n, np.nan, dtype=float)
    direction = np.ones(n, dtype=int)
    market_trail = np.full(n, np.nan, dtype=float)
    ott = np.full(n, np.nan, dtype=float)
    for index, current in enumerate(average):
        if not np.isfinite(current):
            direction[index] = direction[index - 1] if index else 1
            continue
        distance = current * percent * 0.01
        raw_long = current - distance
        raw_short = current + distance
        previous_long = long_stop[index - 1] if index and np.isfinite(long_stop[index - 1]) else raw_long
        previous_short = short_stop[index - 1] if index and np.isfinite(short_stop[index - 1]) else raw_short
        long_stop[index] = max(raw_long, previous_long) if current > previous_long else raw_long
        short_stop[index] = min(raw_short, previous_short) if current < previous_short else raw_short
        previous_direction = direction[index - 1] if index else 1
        direction[index] = 1 if previous_direction == -1 and current > previous_short else -1 if previous_direction == 1 and current < previous_long else previous_direction
        market_trail[index] = long_stop[index] if direction[index] == 1 else short_stop[index]
        factor = (200.0 + percent) / 200.0 if current > market_trail[index] else (200.0 - percent) / 200.0
        ott[index] = market_trail[index] * factor
    return long_stop, short_stop, direction, market_trail, ott


high_average = moving_average(highest)
low_average = moving_average(lowest)
high_long, high_short, high_direction, high_trail, hott = tracker(high_average)
low_long, low_short, low_direction, low_trail, lott = tracker(low_average)
delayed_hott = np.concatenate((np.full(2, np.nan), hott[:-2])) if n > 2 else np.full(n, np.nan)
delayed_lott = np.concatenate((np.full(2, np.nan), lott[:-2])) if n > 2 else np.full(n, np.nan)

buy = np.zeros(n, dtype=bool)
sell = np.zeros(n, dtype=bool)
bar_colors = [None] * n
fill = []
for index in range(n):
    if np.isfinite(delayed_hott[index]) and np.isfinite(delayed_lott[index]):
        if index and np.isfinite(delayed_hott[index - 1]):
            buy[index] = close_values[index] > delayed_hott[index] and close_values[index - 1] <= delayed_hott[index - 1]
        if index and np.isfinite(delayed_lott[index - 1]):
            sell[index] = close_values[index] < delayed_lott[index] and close_values[index - 1] >= delayed_lott[index - 1]
        bar_colors[index] = "#00FFFF" if close_values[index] > delayed_hott[index] else "#FF00FF" if close_values[index] < delayed_lott[index] else None
        fill.append({"upper": float(max(delayed_hott[index], delayed_lott[index])), "lower": float(min(delayed_hott[index], delayed_lott[index]))} if highlighting else None)
    else:
        fill.append(None)


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "HOTT", "data": finite(delayed_hott), "color": "#2962FF", "type": "line", "lineWidth": 2, "overlay": True, "connectNulls": False},
        {"name": "LOTT", "data": finite(delayed_lott), "color": "#F23645", "type": "line", "lineWidth": 2, "overlay": True, "connectNulls": False},
        {"name": "High-Low Tracker Fill", "title": False, "data": fill, "color": "#9915FF", "type": "cloud", "opacity": 0.20, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "Price Over HOTT", "data": [float(delayed_hott[i]) if buy[i] else None for i in range(n)], "color": "#00FFFF", "renderMode": "hidden"},
        {"type": "sell", "text": "SELL", "name": "Price Under LOTT", "data": [float(delayed_lott[i]) if sell[i] else None for i in range(n)], "color": "#FF00FF", "renderMode": "hidden"},
    ],
    "layers": [],
    "tables": [],
    "bar_colors": bar_colors,
    "background_colors": [None] * n,
    "calculatedVars": {
        "highest": finite(highest), "lowest": finite(lowest),
        "highAverage": finite(high_average), "lowAverage": finite(low_average),
        "highLongStop": finite(high_long), "highShortStop": finite(high_short),
        "lowLongStop": finite(low_long), "lowShortStop": finite(low_short),
        "highDirection": high_direction.tolist(), "lowDirection": low_direction.tolist(),
        "highMarketTrail": finite(high_trail), "lowMarketTrail": finite(low_trail),
        "hott": finite(hott), "lott": finite(lott),
        "delayedHott": finite(delayed_hott), "delayedLott": finite(delayed_lott),
        "buySignal": buy.tolist(), "sellSignal": sell.tolist(),
    },
}
