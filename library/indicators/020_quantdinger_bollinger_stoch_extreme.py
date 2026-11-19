# ============================================================
# QuantDinger Bollinger Stochastic Extreme
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Bollinger Stochastic Extreme"
my_indicator_description = (
    "Bollinger Bands combined with Stochastic RSI extremes to identify price "
    "re-entry signals after excursions outside the envelope."
)

# @param source str Close Price source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param offset int 0 Plot offset range=-500:500:1
# @param band_period int 20 Bollinger period range=1:500:1
# @param band_deviation float 2.0 Standard deviation multiplier range=0.001:50.0:0.1
# @param rsi_period int 14 RSI period range=1:500:1
# @param stochastic_period int 14 Stochastic period range=1:500:1
# @param smooth_k int 3 K smoothing range=1:100:1
# @param smooth_d int 3 D smoothing range=1:100:1
# @param upper_limit float 90.0 Upper extreme range=0.01:100.0:1.0
# @param lower_limit float 10.0 Lower extreme range=0.01:100.0:1.0
# @param basis_color str #872323 Basis color
# @param band_color str #008080 Band color
# @param bullish_color str #089981 Bullish marker color
# @param bearish_color str #F23645 Bearish marker color
# @param cloud_opacity float 0.05 Band cloud opacity range=0.01:0.80:0.01

source_name = str(params.get("source", "Close")).upper()
if source_name not in ("CLOSE", "OPEN", "HIGH", "LOW", "HL2", "HLC3", "OHLC4"):
    source_name = "CLOSE"
offset = min(500, max(-500, int(params.get("offset", 0))))
band_period = min(500, max(1, int(params.get("band_period", 20))))
band_deviation = min(50.0, max(0.001, float(params.get("band_deviation", 2.0))))
rsi_period = min(500, max(1, int(params.get("rsi_period", 14))))
stochastic_period = min(500, max(1, int(params.get("stochastic_period", 14))))
smooth_k = min(100, max(1, int(params.get("smooth_k", 3))))
smooth_d = min(100, max(1, int(params.get("smooth_d", 3))))
upper_limit = min(100.0, max(0.01, float(params.get("upper_limit", 90.0))))
lower_limit = min(100.0, max(0.01, float(params.get("lower_limit", 10.0))))
basis_color = str(params.get("basis_color", "#872323"))
band_color = str(params.get("band_color", "#008080"))
bullish_color = str(params.get("bullish_color", "#089981"))
bearish_color = str(params.get("bearish_color", "#F23645"))
cloud_opacity = min(0.80, max(0.01, float(params.get("cloud_opacity", 0.05))))

df = df.copy()
open_values = df["open"].astype(float).to_numpy(dtype=float)
high_values = df["high"].astype(float).to_numpy(dtype=float)
low_values = df["low"].astype(float).to_numpy(dtype=float)
close_values = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)
render_offset_limit = max(0, n - 3)
render_offset = min(render_offset_limit, max(-render_offset_limit, offset))

if source_name == "OPEN":
    source_values = open_values.copy()
elif source_name == "HIGH":
    source_values = high_values.copy()
elif source_name == "LOW":
    source_values = low_values.copy()
elif source_name == "HL2":
    source_values = (high_values + low_values) / 2.0
elif source_name == "HLC3":
    source_values = (high_values + low_values + close_values) / 3.0
elif source_name == "OHLC4":
    source_values = (open_values + high_values + low_values + close_values) / 4.0
else:
    source_values = close_values.copy()


def rolling_average(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def rolling_deviation(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.std(window, ddof=0))
    return result


def wilder_rsi(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) <= period:
        return result
    changes = np.diff(values)
    gains = np.maximum(changes, 0.0)
    losses = np.maximum(-changes, 0.0)
    average_gain = float(np.mean(gains[:period]))
    average_loss = float(np.mean(losses[:period]))
    for index in range(period, len(values)):
        if index > period:
            average_gain = (average_gain * (period - 1) + gains[index - 1]) / period
            average_loss = (average_loss * (period - 1) + losses[index - 1]) / period
        if average_loss <= 1e-14:
            result[index] = 100.0 if average_gain > 1e-14 else 0.0
        else:
            result[index] = 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return result


def stochastic(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if not np.all(np.isfinite(window)):
            continue
        lowest = float(np.min(window))
        highest = float(np.max(window))
        spread = highest - lowest
        result[index] = 0.0 if spread <= 1e-14 else 100.0 * (values[index] - lowest) / spread
    return result


def shifted(values, bars):
    result = np.full(len(values), np.nan, dtype=float)
    if bars >= 0:
        if bars < len(values):
            result[bars:] = values[:len(values) - bars] if bars else values
    elif -bars < len(values):
        result[:bars] = values[-bars:]
    return result


def finite_list(values):
    return [float(value) if np.isfinite(value) else None for value in values]


basis = rolling_average(source_values, band_period)
deviation = rolling_deviation(source_values, band_period)
upper = basis + band_deviation * deviation
lower = basis - band_deviation * deviation
rsi = wilder_rsi(source_values, rsi_period)
raw_stochastic = stochastic(rsi, stochastic_period)
k_line = rolling_average(raw_stochastic, smooth_k)
d_line = rolling_average(k_line, smooth_d)

bearish = np.full(n, False, dtype=bool)
bullish = np.full(n, False, dtype=bool)
for index in range(1, n):
    required = (
        close_values[index - 1],
        close_values[index],
        upper[index - 1],
        upper[index],
        lower[index - 1],
        lower[index],
        k_line[index - 1],
        d_line[index - 1],
    )
    if not all(np.isfinite(value) for value in required):
        continue
    bearish[index] = (
        close_values[index - 1] > upper[index - 1]
        and close_values[index] < upper[index]
        and k_line[index - 1] > upper_limit
        and d_line[index - 1] > upper_limit
    )
    bullish[index] = (
        close_values[index - 1] < lower[index - 1]
        and close_values[index] > lower[index]
        and k_line[index - 1] < lower_limit
        and d_line[index - 1] < lower_limit
    )

basis_plot = finite_list(shifted(basis, render_offset))
upper_plot = finite_list(shifted(upper, render_offset))
lower_plot = finite_list(shifted(lower, render_offset))
band_cloud = [
    {"upper": upper_plot[index], "lower": lower_plot[index]}
    if upper_plot[index] is not None and lower_plot[index] is not None
    else None
    for index in range(n)
]
buy_marks = [float(low_values[index]) if bullish[index] else None for index in range(n)]
sell_marks = [float(high_values[index]) if bearish[index] else None for index in range(n)]


def latest_finite(values):
    for value in values[::-1]:
        if np.isfinite(value):
            return float(value)
    return None


last_index = n - 1

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Band Basis", "data": basis_plot, "color": basis_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Upper Band", "data": upper_plot, "color": band_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Lower Band", "data": lower_plot, "color": band_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Band Cloud", "title": False, "data": band_cloud, "color": band_color, "type": "cloud", "opacity": cloud_opacity, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "", "name": "Bullish Extreme", "data": buy_marks, "color": bullish_color, "renderMode": "events"},
        {"type": "sell", "text": "", "name": "Bearish Extreme", "data": sell_marks, "color": bearish_color, "renderMode": "events"},
    ],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "layers": [],
    "calculatedVars": {
        "source": source_name,
        "requestedOffset": offset,
        "effectiveOffset": render_offset,
        "rsi": latest_finite(rsi),
        "stochasticK": latest_finite(k_line),
        "stochasticD": latest_finite(d_line),
        "bullishExtreme": bool(bullish[last_index]) if n else False,
        "bearishExtreme": bool(bearish[last_index]) if n else False,
    },
}
