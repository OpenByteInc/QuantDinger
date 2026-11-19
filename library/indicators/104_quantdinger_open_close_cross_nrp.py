# ============================================================
# QuantDinger Open-Close Cross NRP
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Open-Close Cross NRP"
my_indicator_description = (
    "Compares independently smoothed open and close series, highlights their "
    "normalized spread, and optionally marks regular or hidden pivot divergence."
)

# @param use_alt_length bool true Use multiplied smoothing length
# @param alt_multiplier int 6 Alternate length multiplier range=1:20:1
# @param average_type str ZEMA Moving average type values=SMA,EMA,DEMA,TEMA,WMA,VWMA,SMMA,HullMA,ZEMA,SSMA,TMA
# @param average_length int 8 Moving average period range=1:100:1
# @param show_divergence bool false Show divergence pivots
# @param show_regular bool false Show regular divergence
# @param show_hidden bool false Show hidden divergence
# @param divergence_width float 0.5 Divergence channel width range=0.0:3.0:0.1

use_alt_length = bool(params.get("use_alt_length", True))
alt_multiplier = max(1, int(params.get("alt_multiplier", 6)))
average_type = str(params.get("average_type", "ZEMA"))
average_length = max(1, int(params.get("average_length", 8)))
show_divergence = bool(params.get("show_divergence", False))
show_regular = bool(params.get("show_regular", False))
show_hidden = bool(params.get("show_hidden", False))
divergence_width = min(3.0, max(0.0, float(params.get("divergence_width", 0.5))))
show_divergence = show_divergence and (show_regular or show_hidden)

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)


def rolling_sma(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(period - 1, len(source)):
        window = source[index - period + 1 : index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def recursive_ema(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def pine_rma(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    seed = []
    previous = np.nan
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed.append(float(value))
            if len(seed) < period:
                continue
            previous = float(np.mean(seed[-period:]))
        else:
            previous = (previous * (period - 1) + float(value)) / period
        result[index] = previous
    return result


def rolling_wma(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    weights = np.arange(1, period + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(period - 1, len(source)):
        window = source[index - period + 1 : index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / denominator)
    return result


def volume_weighted_average(values, period):
    numerator = rolling_sma(np.asarray(values, dtype=float) * volume, period)
    denominator = rolling_sma(volume, period)
    result = np.full(n, np.nan, dtype=float)
    valid = np.isfinite(numerator) & np.isfinite(denominator) & (np.abs(denominator) > 1e-12)
    result[valid] = numerator[valid] / denominator[valid]
    return result


def super_smoother(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    a1 = float(np.exp(-1.414 * 3.14159 / period))
    c2 = 2.0 * a1 * float(np.cos(1.414 * 3.14159 / period))
    c3 = -(a1 * a1)
    c1 = 1.0 - c2 - c3
    for index, value in enumerate(source):
        if not np.isfinite(value):
            continue
        previous_source = source[index - 1] if index > 0 and np.isfinite(source[index - 1]) else 0.0
        previous_one = result[index - 1] if index > 0 and np.isfinite(result[index - 1]) else 0.0
        previous_two = result[index - 2] if index > 1 and np.isfinite(result[index - 2]) else 0.0
        result[index] = c1 * (float(value) + float(previous_source)) / 2.0 + c2 * previous_one + c3 * previous_two
    return result


def moving_average(values, period, kind):
    source = np.asarray(values, dtype=float)
    if kind == "EMA":
        return recursive_ema(source, period)
    if kind == "DEMA":
        first = recursive_ema(source, period)
        return 2.0 * first - recursive_ema(first, period)
    if kind == "TEMA":
        first = recursive_ema(source, period)
        second = recursive_ema(first, period)
        third = recursive_ema(second, period)
        return 3.0 * first - 3.0 * second + third
    if kind == "WMA":
        return rolling_wma(source, period)
    if kind == "VWMA":
        return volume_weighted_average(source, period)
    if kind == "SMMA":
        return pine_rma(source, period)
    if kind == "HullMA":
        half = max(1, int(round(period / 2.0)))
        root = max(1, int(round(period ** 0.5)))
        return rolling_wma(2.0 * rolling_wma(source, half) - rolling_wma(source, period), root)
    if kind == "ZEMA":
        lag = max(0, int((period - 1) / 2))
        adjusted = np.full(len(source), np.nan, dtype=float)
        for index in range(lag, len(source)):
            adjusted[index] = 2.0 * source[index] - source[index - lag]
        return recursive_ema(adjusted, period)
    if kind == "SSMA":
        return super_smoother(source, period)
    if kind == "TMA":
        return rolling_sma(rolling_sma(source, period), period)
    return rolling_sma(source, period)


base_close = moving_average(close, average_length, average_type)
base_open = moving_average(open_price, average_length, average_type)
base_average = (base_close + base_open) / 2.0
effective_length = average_length * (alt_multiplier if use_alt_length else 1)
alternate_close = moving_average(close, effective_length, average_type)
alternate_open = moving_average(open_price, effective_length, average_type)

factor = np.full(n, np.nan, dtype=float)
valid_factor = (
    np.isfinite(alternate_close)
    & np.isfinite(alternate_open)
    & np.isfinite(base_average)
    & (np.abs(base_average) > 1e-12)
)
factor[valid_factor] = 50000.0 * (alternate_close[valid_factor] - alternate_open[valid_factor]) / base_average[valid_factor]

long_cross = np.zeros(n, dtype=bool)
short_cross = np.zeros(n, dtype=bool)
for index in range(1, n):
    if not (
        np.isfinite(alternate_close[index])
        and np.isfinite(alternate_open[index])
        and np.isfinite(alternate_close[index - 1])
        and np.isfinite(alternate_open[index - 1])
    ):
        continue
    long_cross[index] = alternate_close[index] > alternate_open[index] and alternate_close[index - 1] <= alternate_open[index - 1]
    short_cross[index] = alternate_close[index] < alternate_open[index] and alternate_close[index - 1] >= alternate_open[index - 1]


def rolling_std(values, period):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    for index in range(period - 1, len(source)):
        window = source[index - period + 1 : index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.std(window, ddof=0))
    return result


deviation = rolling_std(factor, 20)
top_pivots = np.zeros(n, dtype=bool)
bottom_pivots = np.zeros(n, dtype=bool)
regular_bear = np.zeros(n, dtype=bool)
hidden_bear = np.zeros(n, dtype=bool)
regular_bull = np.zeros(n, dtype=bool)
hidden_bull = np.zeros(n, dtype=bool)
top_level = [None for _ in range(n)]
bottom_level = [None for _ in range(n)]
previous_top = -1
previous_bottom = -1

for confirmation in range(4, n):
    pivot = confirmation - 2
    if not np.all(np.isfinite(factor[confirmation - 4 : confirmation + 1])):
        continue
    is_top = factor[confirmation - 4] < factor[pivot] and factor[confirmation - 3] < factor[pivot] and factor[pivot] > factor[confirmation - 1] and factor[pivot] > factor[confirmation]
    is_bottom = factor[confirmation - 4] > factor[pivot] and factor[confirmation - 3] > factor[pivot] and factor[pivot] < factor[confirmation - 1] and factor[pivot] < factor[confirmation]
    offset = divergence_width * deviation[confirmation] if np.isfinite(deviation[confirmation]) else 0.0
    if is_top:
        top_pivots[pivot] = True
        if show_divergence:
            top_level[pivot] = float(factor[pivot] + offset)
        if previous_top >= 0:
            regular_bear[pivot] = show_divergence and show_regular and high[pivot] > high[previous_top] and factor[pivot] < factor[previous_top] and factor[confirmation] > 0.0
            hidden_bear[pivot] = show_divergence and show_hidden and high[pivot] < high[previous_top] and factor[pivot] > factor[previous_top] and factor[confirmation] > 0.0
        previous_top = pivot
    elif is_bottom:
        bottom_pivots[pivot] = True
        if show_divergence:
            bottom_level[pivot] = float(factor[pivot] - offset)
        if previous_bottom >= 0:
            regular_bull[pivot] = show_divergence and show_regular and low[pivot] < low[previous_bottom] and factor[pivot] > factor[previous_bottom] and factor[confirmation] < 0.0
            hidden_bull[pivot] = show_divergence and show_hidden and low[pivot] > low[previous_bottom] and factor[pivot] < factor[previous_bottom] and factor[confirmation] < 0.0
        previous_bottom = pivot

finite_factor = factor[np.isfinite(factor)]
if finite_factor.size:
    factor_low = float(np.min(finite_factor))
    factor_high = float(np.max(finite_factor))
    alert_level = factor_low - max(1.0, (factor_high - factor_low) * 0.08)
else:
    alert_level = -1.0

factor_plot = []
factor_fill = []
for index, value in enumerate(factor):
    if not np.isfinite(value):
        factor_plot.append(None)
        factor_fill.append(None)
        continue
    color = "#008000" if alternate_close[index] > alternate_open[index] else "#FF0000"
    factor_plot.append({"value": float(value), "color": color})
    factor_fill.append({"upper": max(float(value), 0.0), "lower": min(float(value), 0.0), "color": "rgba(0, 128, 0, 0.18)" if value >= 0.0 else "rgba(255, 0, 0, 0.18)"})

regular_bear_points = [top_level[index] if regular_bear[index] else None for index in range(n)]
hidden_bear_points = [top_level[index] if hidden_bear[index] else None for index in range(n)]
regular_bull_points = [bottom_level[index] if regular_bull[index] else None for index in range(n)]
hidden_bull_points = [bottom_level[index] if hidden_bull[index] else None for index in range(n)]

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "OCC Difference Factor", "data": factor_plot, "color": "#008000", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "OCC Regime Fill", "title": False, "data": factor_fill, "color": "#008000", "type": "cloud", "opacity": 0.18, "overlay": False},
        {"name": "Zero Guide", "data": [0.0 for _ in range(n)], "color": "rgba(128, 128, 128, 0.65)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Long Alert", "data": [alert_level if long_cross[index] else None for index in range(n)], "color": "#00FF00", "type": "circle", "size": 5, "overlay": False},
        {"name": "Short Alert", "data": [alert_level if short_cross[index] else None for index in range(n)], "color": "#FF0000", "type": "circle", "size": 5, "overlay": False},
        {"name": "Top Fractal", "data": top_level, "color": "#FF0000", "type": "circle", "size": 3, "overlay": False},
        {"name": "Bottom Fractal", "data": bottom_level, "color": "#008000", "type": "circle", "size": 3, "overlay": False},
        {"name": "Regular Bearish Divergence", "data": regular_bear_points, "color": "#800000", "type": "circle", "size": 6, "overlay": False},
        {"name": "Hidden Bearish Divergence", "data": hidden_bear_points, "color": "#B22222", "type": "circle", "size": 6, "overlay": False},
        {"name": "Regular Bullish Divergence", "data": regular_bull_points, "color": "#008000", "type": "circle", "size": 6, "overlay": False},
        {"name": "Hidden Bullish Divergence", "data": hidden_bull_points, "color": "#00A878", "type": "circle", "size": 6, "overlay": False},
    ],
    "signals": [],
    "layers": [],
}
