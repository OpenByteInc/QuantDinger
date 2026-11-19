# ============================================================
# QuantDinger Bollinger Awesome
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Bollinger Awesome"
my_indicator_description = (
    "Bollinger Bands combined with a fast average, Awesome Oscillator "
    "direction, relative band compression, and filtered entry events."
)

# @param use_ema bool false Use EMA for the Bollinger basis
# @param band_filter bool false Require signals to close inside the bands
# @param squeeze_filter bool false Require signals to pass the squeeze threshold
# @param band_length int 20 Bollinger period range=1:200:1
# @param source str Close Bollinger source values=Close,HL2,HLC3,OHLC4
# @param band_multiplier float 2.0 Bollinger multiplier range=0.5:10.0:0.1
# @param fast_length int 3 Fast EMA period range=2:100:1
# @param awesome_fast int 5 Awesome fast period range=1:100:1
# @param awesome_slow int 34 Awesome slow period range=1:200:1
# @param squeeze_length int 100 Relative squeeze period range=5:300:1
# @param squeeze_threshold float 50.0 Squeeze threshold percent range=0.0:99.0:1.0

use_ema = bool(params.get("use_ema", False))
band_filter = bool(params.get("band_filter", False))
squeeze_filter = bool(params.get("squeeze_filter", False))
band_length = max(1, int(params.get("band_length", 20)))
source_name = str(params.get("source", "Close")).upper()
if source_name not in ("CLOSE", "HL2", "HLC3", "OHLC4"):
    source_name = "CLOSE"
band_multiplier = max(0.5, float(params.get("band_multiplier", 2.0)))
fast_length = max(2, int(params.get("fast_length", 3)))
awesome_fast = max(1, int(params.get("awesome_fast", 5)))
awesome_slow = max(1, int(params.get("awesome_slow", 34)))
squeeze_length = max(5, int(params.get("squeeze_length", 100)))
squeeze_threshold = max(0.0, min(99.0, float(params.get("squeeze_threshold", 50.0))))

df = df.copy()
open_values = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

if source_name == "HL2":
    source_values = (high + low) / 2.0
elif source_name == "HLC3":
    source_values = (high + low + close) / 3.0
elif source_name == "OHLC4":
    source_values = (open_values + high + low + close) / 4.0
else:
    source_values = close.copy()


def exponential_average(values, period):
    result = np.full(n, np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index in range(n):
        value = values[index]
        if np.isfinite(value):
            previous = value if not np.isfinite(previous) else alpha * value + (1.0 - alpha) * previous
            result[index] = previous
    return result


def recursive_average(values, period):
    result = np.full(n, np.nan, dtype=float)
    if n >= period:
        result[period - 1] = float(np.sum(values[:period])) / period
        for index in range(period, n):
            result[index] = (result[index - 1] * (period - 1) + values[index]) / period
    return result


series = pd.Series(source_values)
basis = exponential_average(source_values, band_length) if use_ema else series.rolling(band_length, min_periods=band_length).mean().to_numpy(dtype=float)
fast_average = exponential_average(source_values, fast_length)
deviation = series.rolling(band_length, min_periods=band_length).std(ddof=0).to_numpy(dtype=float)
upper = basis + deviation * band_multiplier
lower = basis - deviation * band_multiplier

middle_price = (high + low) / 2.0
awesome = (
    pd.Series(middle_price).rolling(awesome_fast, min_periods=awesome_fast).mean().to_numpy(dtype=float)
    - pd.Series(middle_price).rolling(awesome_slow, min_periods=awesome_slow).mean().to_numpy(dtype=float)
)
awesome_state = np.zeros(n, dtype=int)
for index in range(n):
    if not np.isfinite(awesome[index]):
        continue
    rising = index > 0 and np.isfinite(awesome[index - 1]) and awesome[index] > awesome[index - 1]
    awesome_state[index] = 1 if awesome[index] >= 0.0 and rising else 2 if awesome[index] >= 0.0 else -1 if rising else -2

spread = upper - lower
average_spread = pd.Series(spread).rolling(squeeze_length, min_periods=squeeze_length).mean().to_numpy(dtype=float)
squeeze_percent = np.divide(spread, average_spread, out=np.full(n, np.nan, dtype=float), where=np.isfinite(average_spread) & (average_spread != 0.0)) * 100.0

previous_close = np.concatenate(([close[0]], close[:-1]))
true_range = np.maximum(high - low, np.maximum(np.abs(high - previous_close), np.abs(low - previous_close)))
atr = recursive_average(true_range, 14)
squeeze_upper = upper + atr * 0.5
squeeze_lower = lower - atr * 0.5

break_up = np.zeros(n, dtype=bool)
break_down = np.zeros(n, dtype=bool)
for index in range(1, n):
    valid = np.isfinite(fast_average[index]) and np.isfinite(basis[index]) and np.isfinite(fast_average[index - 1]) and np.isfinite(basis[index - 1])
    if not valid:
        continue
    crossed_up = fast_average[index] > basis[index] and fast_average[index - 1] <= basis[index - 1]
    crossed_down = fast_average[index] < basis[index] and fast_average[index - 1] >= basis[index - 1]
    squeeze_ok = np.isfinite(squeeze_percent[index]) and squeeze_percent[index] > squeeze_threshold
    break_up[index] = crossed_up and close[index] > basis[index] and abs(awesome_state[index]) == 1 and (not band_filter or close[index] < upper[index]) and (not squeeze_filter or squeeze_ok)
    break_down[index] = crossed_down and close[index] < basis[index] and abs(awesome_state[index]) == 2 and (not band_filter or close[index] > lower[index]) and (not squeeze_filter or squeeze_ok)

center_cloud = []
upper_cloud = []
lower_cloud = []
for index in range(n):
    if np.isfinite(upper[index]) and np.isfinite(lower[index]):
        center_cloud.append({"upper": float(upper[index]), "lower": float(lower[index]), "color": "#94A3B8"})
    else:
        center_cloud.append(None)
    squeeze_color = "#FFFFFF" if np.isfinite(squeeze_percent[index]) and squeeze_percent[index] > squeeze_threshold else "#2563EB"
    upper_cloud.append({"upper": float(squeeze_upper[index]), "lower": float(upper[index]), "color": squeeze_color} if np.isfinite(squeeze_upper[index]) and np.isfinite(upper[index]) else None)
    lower_cloud.append({"upper": float(lower[index]), "lower": float(squeeze_lower[index]), "color": squeeze_color} if np.isfinite(squeeze_lower[index]) and np.isfinite(lower[index]) else None)

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Basis", "data": [float(value) if np.isfinite(value) else None for value in basis], "color": "#EF4444", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Upper Band", "data": [float(value) if np.isfinite(value) else None for value in upper], "color": "#2563EB", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Lower Band", "data": [float(value) if np.isfinite(value) else None for value in lower], "color": "#2563EB", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Center Channel", "title": False, "data": center_cloud, "color": "#94A3B8", "type": "cloud", "opacity": 0.10, "overlay": True},
        {"name": "Upper Squeeze", "title": False, "data": upper_cloud, "color": "#2563EB", "type": "cloud", "opacity": 0.50, "overlay": True},
        {"name": "Lower Squeeze", "title": False, "data": lower_cloud, "color": "#2563EB", "type": "cloud", "opacity": 0.50, "overlay": True},
        {"name": "Fast EMA", "data": [float(value) if np.isfinite(value) else None for value in fast_average], "color": "#E2E8F0", "type": "line", "lineWidth": 2, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "Buy", "name": "Breakout Up", "data": [float(low[i]) if break_up[i] else None for i in range(n)], "color": "#16A34A", "renderMode": "events"},
        {"type": "sell", "text": "Sell", "name": "Breakout Down", "data": [float(high[i]) if break_down[i] else None for i in range(n)], "color": "#EF4444", "renderMode": "events"},
    ],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "layers": [],
}
