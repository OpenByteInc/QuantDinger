# ============================================================
# QuantDinger Bollinger Breakout Oscillator
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: analytical oscillator only; no orders are submitted.
# ============================================================

my_indicator_name = "QuantDinger Bollinger Breakout Oscillator"
my_indicator_description = (
    "Measures bullish and bearish excursions beyond an EMA-centered Bollinger "
    "envelope and normalizes each side into a 0-100 breakout intensity."
)

# @param length int 14 Lookback length range=2:300:1
# @param multiplier float 1.0 Deviation multiplier range=0.1:10.0:0.1
# @param source str Close Price source values=Close,HL2,HLC3,OHLC4
# @param bullish_color str #089981 Bullish color
# @param bearish_color str #F23645 Bearish color

length = min(300, max(2, int(params.get("length", 14))))
multiplier = min(10.0, max(0.1, float(params.get("multiplier", 1.0))))
source_name = str(params.get("source", "Close")).upper()
if source_name not in ("CLOSE", "HL2", "HLC3", "OHLC4"):
    source_name = "CLOSE"
bullish_color = str(params.get("bullish_color", "#089981"))
bearish_color = str(params.get("bearish_color", "#F23645"))

df = df.copy()
open_values = df["open"].astype(float).to_numpy(dtype=float)
high_values = df["high"].astype(float).to_numpy(dtype=float)
low_values = df["low"].astype(float).to_numpy(dtype=float)
close_values = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

if source_name == "HL2":
    source_values = (high_values + low_values) / 2.0
elif source_name == "HLC3":
    source_values = (high_values + low_values + close_values) / 3.0
elif source_name == "OHLC4":
    source_values = (open_values + high_values + low_values + close_values) / 4.0
else:
    source_values = close_values.copy()


def exponential_average(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    if not len(values):
        return result
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def rolling_deviation(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if all(np.isfinite(float(value)) for value in window):
            mean_value = sum(float(value) for value in window) / period
            variance = sum((float(value) - mean_value) ** 2 for value in window) / period
            result[index] = variance ** 0.5
    return result


basis = exponential_average(source_values, length)
deviation = rolling_deviation(source_values, length) * multiplier
upper = basis + deviation
lower = basis - deviation

bullish = np.zeros(n, dtype=float)
bearish = np.zeros(n, dtype=float)
window_size = max(1, length - 1)
for index in range(n):
    if index < length - 1:
        continue
    left = max(0, index - window_size + 1)
    bullish_numerator = 0.0
    bearish_numerator = 0.0
    bullish_denominator = 0.0
    bearish_denominator = 0.0
    valid = False
    for cursor in range(left, index + 1):
        if not all(np.isfinite(value) for value in (source_values[cursor], upper[cursor], lower[cursor])):
            continue
        valid = True
        upper_distance = float(source_values[cursor] - upper[cursor])
        lower_distance = float(lower[cursor] - source_values[cursor])
        bullish_numerator += max(upper_distance, 0.0)
        bearish_numerator += max(lower_distance, 0.0)
        bullish_denominator += abs(upper_distance)
        bearish_denominator += abs(lower_distance)
    if valid and bullish_denominator > 1e-12:
        bullish[index] = min(100.0, max(0.0, bullish_numerator / bullish_denominator * 100.0))
    if valid and bearish_denominator > 1e-12:
        bearish[index] = min(100.0, max(0.0, bearish_numerator / bearish_denominator * 100.0))


def line_values(values, hide_zero=False):
    result = []
    for value in values:
        if not np.isfinite(value) or (hide_zero and abs(float(value)) <= 1e-12):
            result.append(None)
        else:
            result.append(float(value))
    return result


def intensity_cloud(values, lower_bound, upper_bound):
    result = []
    for value in values:
        if np.isfinite(value) and float(value) > 0.0:
            intensity = min(100.0, max(0.0, float(value)))
            in_bucket = intensity > lower_bound and (intensity <= upper_bound or upper_bound >= 100.0)
            result.append({"upper": intensity, "lower": 0.0} if in_bucket else None)
        else:
            result.append(None)
    return result


plots = [
    {"name": "Bullish Breakout", "data": line_values(bullish, True), "color": bullish_color, "type": "line", "lineWidth": 2, "overlay": False},
    {"name": "Bearish Breakout", "data": line_values(bearish, True), "color": bearish_color, "type": "line", "lineWidth": 2, "overlay": False},
    {"name": "Midline", "title": False, "data": [50.0] * n, "color": "#64748B", "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": False},
]

bucket_specs = (
    (0.0, 20.0, 0.10),
    (20.0, 40.0, 0.16),
    (40.0, 60.0, 0.22),
    (60.0, 80.0, 0.30),
    (80.0, 100.0, 0.40),
)
for index, (lower_bound, upper_bound, opacity) in enumerate(bucket_specs):
    plots.append({
        "name": f"Bullish Intensity {index + 1}",
        "title": False,
        "data": intensity_cloud(bullish, lower_bound, upper_bound),
        "color": bullish_color,
        "type": "cloud",
        "opacity": opacity,
        "overlay": False,
    })
    plots.append({
        "name": f"Bearish Intensity {index + 1}",
        "title": False,
        "data": intensity_cloud(bearish, lower_bound, upper_bound),
        "color": bearish_color,
        "type": "cloud",
        "opacity": opacity,
        "overlay": False,
    })

last_index = n - 1
output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "layers": [],
    "calculatedVars": {
        "bullish": float(bullish[last_index]) if n else None,
        "bearish": float(bearish[last_index]) if n else None,
        "dominant": "bullish" if n and bullish[last_index] > bearish[last_index] else "bearish" if n and bearish[last_index] > bullish[last_index] else "neutral",
    },
}
