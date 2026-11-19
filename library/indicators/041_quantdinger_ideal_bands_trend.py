# ============================================================
# QuantDinger Ideal Bands Trend
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Ideal Bands Trend"
my_indicator_description = (
    "A third-generation moving average, session VWAP, Bollinger envelope, "
    "and Kalman-smoothed Hull crossover trend in one overlay."
)

# @param long_length int 120 Primary MA length range=2:500:1
# @param secondary_length int 12 Secondary MA length range=1:200:1
# @param ma_type int 0 MA type range=0:3:1
# @param band_length int 20 Bollinger length range=2:300:1
# @param band_deviation float 2.0 Bollinger deviation range=0.1:10.0:0.1
# @param hull_length int 24 Hull lookback range=4:300:1
# @param kalman_gain float 10000.0 Kalman gain range=1.0:20000.0:100.0
# @param use_kalman bool true Use Kalman smoothing
# @param show_signals bool true Show Hull crossover events

long_length = max(1, int(params.get("long_length", 120)))
secondary_length = max(1, int(params.get("secondary_length", 12)))
ma_type = max(0, min(3, int(params.get("ma_type", 0))))
band_length = max(2, int(params.get("band_length", 20)))
band_deviation = max(0.1, float(params.get("band_deviation", 2.0)))
hull_length = max(4, int(params.get("hull_length", 24)))
kalman_gain = max(1.0, float(params.get("kalman_gain", 10000.0)))
use_kalman = bool(params.get("use_kalman", True))
show_signals = bool(params.get("show_signals", True))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
times = df["time"].to_numpy(dtype=float)
n = len(df)
if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0


def sma(values, period):
    return pd.Series(np.asarray(values, dtype=float)).rolling(period, min_periods=period).mean().to_numpy(dtype=float)


def ema(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        previous = value if not np.isfinite(previous) else alpha * value + (1.0 - alpha) * previous
        result[index] = previous
    return result


def wma(values, period):
    source = pd.Series(np.asarray(values, dtype=float))
    weights = np.arange(1, period + 1, dtype=float)
    weight_total = float(period * (period + 1)) / 2.0
    return source.rolling(period, min_periods=period).apply(lambda window: float(np.dot(window, weights) / weight_total), raw=True).to_numpy(dtype=float)


def vwma(values, period):
    source = np.asarray(values, dtype=float)
    numerator = pd.Series(source * volume).rolling(period, min_periods=period).sum().to_numpy(dtype=float)
    denominator = pd.Series(volume).rolling(period, min_periods=period).sum().to_numpy(dtype=float)
    result = np.full(n, np.nan, dtype=float)
    valid = denominator != 0.0
    result[valid] = numerator[valid] / denominator[valid]
    return result


def moving_average(values, period):
    if ma_type == 1:
        return sma(values, period)
    if ma_type == 2:
        return vwma(values, period)
    if ma_type == 3:
        return wma(values, period)
    return ema(values, period)


def kalman(values):
    source = np.asarray(values, dtype=float)
    result = np.full(n, np.nan, dtype=float)
    state = np.nan
    velocity = 0.0
    gain_ratio = kalman_gain / 10000.0
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        if not np.isfinite(state):
            state = value
            result[index] = state
            continue
        delta = value - state
        smooth = state + delta * np.sqrt(max(gain_ratio * 2.0, 0.0))
        velocity += gain_ratio * delta
        state = smooth + velocity
        result[index] = state
    return result


hl2 = (high + low) / 2.0
primary = moving_average(hl2, long_length)
secondary = moving_average(primary, secondary_length)
ratio = float(long_length) / float(secondary_length)
denominator = float(long_length) - ratio
alpha = ratio * (long_length - 1.0) / denominator if denominator != 0.0 else 0.0
generation_ma = (1.0 + alpha) * primary - alpha * secondary

typical = (high + low + close) / 3.0
session_vwap = np.full(n, np.nan, dtype=float)
weighted_total = 0.0
volume_total = 0.0
previous_session = None
timestamps = pd.to_datetime(times, unit="ms", utc=True) if n else []
for index in range(n):
    current_session = timestamps[index].date()
    if current_session != previous_session:
        weighted_total = 0.0
        volume_total = 0.0
        previous_session = current_session
    if np.isfinite(typical[index]) and np.isfinite(volume[index]):
        weighted_total += typical[index] * volume[index]
        volume_total += volume[index]
    if volume_total != 0.0:
        session_vwap[index] = weighted_total / volume_total
baseline = sma(close, band_length)
deviation = pd.Series(close).rolling(band_length, min_periods=band_length).std(ddof=0).to_numpy(dtype=float) * band_deviation
upper_band = baseline + deviation
lower_band = baseline - deviation

half = max(1, hull_length // 2)
root = max(1, int(round(np.sqrt(hull_length))))
hull_primary = wma(2.0 * wma(hl2, half) - wma(hl2, hull_length), root)
inner = max(1, half)
hull_secondary = wma(3.0 * wma(close, max(1, inner // 3)) - wma(close, max(1, inner // 2)) - wma(close, inner), inner)
trend_fast = kalman(hull_primary) if use_kalman else hull_primary
trend_slow = kalman(hull_secondary) if use_kalman else hull_secondary
trend_valid = np.isfinite(trend_fast) & np.isfinite(trend_slow)
trend_up = trend_valid & (trend_slow > trend_fast)
cross_up = np.zeros(n, dtype=bool)
cross_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    if trend_valid[index] and trend_valid[index - 1]:
        cross_up[index] = trend_slow[index] > trend_fast[index] and trend_slow[index - 1] < trend_fast[index - 1]
        cross_down[index] = trend_fast[index] > trend_slow[index] and trend_fast[index - 1] < trend_slow[index - 1]


def finite_line(values):
    return [float(value) if np.isfinite(value) else None for value in values]


band_cloud = []
hull_cloud = []
fast_plot = []
slow_plot = []
for index in range(n):
    if np.isfinite(upper_band[index]) and np.isfinite(lower_band[index]):
        band_cloud.append({"upper": float(upper_band[index]), "lower": float(lower_band[index]), "color": "#2962FF"})
    else:
        band_cloud.append(None)
    color = "#00C853" if trend_up[index] else "#FF1744"
    fast_plot.append({"value": float(trend_fast[index]), "color": color} if np.isfinite(trend_fast[index]) else None)
    slow_plot.append({"value": float(trend_slow[index]), "color": color} if np.isfinite(trend_slow[index]) else None)
    if np.isfinite(trend_fast[index]) and np.isfinite(trend_slow[index]):
        hull_cloud.append({"upper": float(max(trend_fast[index], trend_slow[index])), "lower": float(min(trend_fast[index], trend_slow[index])), "color": color})
    else:
        hull_cloud.append(None)

buy_marks = [None] * n
sell_marks = [None] * n
if show_signals:
    for index in range(1, n):
        target = index - 1
        if cross_up[index]:
            buy_marks[target] = float(low[target])
        if cross_down[index]:
            sell_marks[target] = float(high[target])

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Third Generation Average", "data": finite_line(generation_ma), "color": "#F5F5F5", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Session VWAP", "data": finite_line(session_vwap), "color": "#E91E63", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Band Baseline", "data": finite_line(baseline), "color": "#FF0000", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Upper Band", "data": finite_line(upper_band), "color": "#2962FF", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Lower Band", "data": finite_line(lower_band), "color": "#5E35B1", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Bollinger Envelope", "title": False, "data": band_cloud, "color": "#2962FF", "type": "cloud", "opacity": 0.12, "overlay": True},
        {"name": "Hull Fast", "data": fast_plot, "color": "#00C853", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Hull Slow", "data": slow_plot, "color": "#00C853", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Hull Trend Fill", "title": False, "data": hull_cloud, "color": "#00C853", "type": "cloud", "opacity": 0.28, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "BUY", "name": "Hull Buy", "data": buy_marks, "color": "#00C853", "renderMode": "events"},
        {"type": "sell", "text": "SELL", "name": "Hull Sell", "data": sell_marks, "color": "#FF1744", "renderMode": "events"},
    ],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "primaryAverage": finite_line(primary),
        "secondaryAverage": finite_line(secondary),
        "thirdGenerationAverage": finite_line(generation_ma),
        "sessionVwap": finite_line(session_vwap),
        "bandBaseline": finite_line(baseline),
        "bandDeviation": finite_line(deviation),
        "upperBand": finite_line(upper_band),
        "lowerBand": finite_line(lower_band),
        "hullPrimary": finite_line(hull_primary),
        "hullSecondary": finite_line(hull_secondary),
        "trendFast": finite_line(trend_fast),
        "trendSlow": finite_line(trend_slow),
        "trendValid": trend_valid.tolist(),
        "trendUp": trend_up.tolist(),
        "crossUp": cross_up.tolist(),
        "crossDown": cross_down.tolist(),
    },
}
