# ============================================================
# QuantDinger Gaussian Channel
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Gaussian Channel"
my_indicator_description = (
    "A configurable multi-pole Gaussian filter with filtered true-range "
    "bands, optional lag reduction, fast response, and directional candle colors."
)

# @param source str hlc3 Source values=open,high,low,close,hl2,hlc3,ohlc4
# @param poles int 4 Gaussian poles range=1:9:1
# @param period int 144 Sampling period range=2:1000:1
# @param multiplier float 1.414 Filtered true-range multiplier range=0.0:20.0:0.001
# @param reduced_lag bool false Reduced lag mode
# @param fast_response bool false Fast response mode


def clamp(value, lower, upper):
    return min(upper, max(lower, value))


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def binomial(number, selected):
    selected = min(selected, number - selected)
    result = 1
    for step in range(1, selected + 1):
        result = result * (number - selected + step) // step
    return result


def gaussian_filter(values, alpha, poles):
    source_values = np.asarray(values, dtype=float)
    result = np.zeros(len(source_values), dtype=float)
    complement = 1.0 - alpha
    source_weight = alpha ** poles
    for index, value in enumerate(source_values):
        current = source_weight * (float(value) if np.isfinite(value) else 0.0)
        for lag_number in range(1, poles + 1):
            history = result[index - lag_number] if index >= lag_number else 0.0
            sign = 1.0 if lag_number % 2 == 1 else -1.0
            current += sign * binomial(poles, lag_number) * (complement ** lag_number) * history
        result[index] = current
    return result


def rgba(color, opacity):
    value = str(color).strip().lstrip("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    try:
        red, green, blue = int(value[:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except (ValueError, IndexError):
        red, green, blue = 204, 204, 204
    return f"rgba({red},{green},{blue},{clamp(float(opacity), 0.0, 1.0):.3f})"


source_name = str(params.get("source", "hlc3")).lower()
poles = min(9, max(1, int(params.get("poles", 4))))
period = min(1000, max(2, int(params.get("period", 144))))
multiplier = clamp(float(params.get("multiplier", 1.414)), 0.0, 20.0)
reduced_lag = bool(params.get("reduced_lag", False))
fast_response = bool(params.get("fast_response", False))

df = df.copy()
open_values = pd.to_numeric(df["open"], errors="coerce").to_numpy(dtype=float)
high_values = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype=float)
low_values = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype=float)
close_values = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
n = len(df)
sources = {
    "open": open_values,
    "high": high_values,
    "low": low_values,
    "close": close_values,
    "hl2": (high_values + low_values) * 0.5,
    "hlc3": (high_values + low_values + close_values) / 3.0,
    "ohlc4": (open_values + high_values + low_values + close_values) * 0.25,
}
source = sources.get(source_name, sources["hlc3"])

true_range = np.full(n, np.nan, dtype=float)
if n:
    true_range[0] = high_values[0] - low_values[0]
for index in range(1, n):
    true_range[index] = max(
        high_values[index] - low_values[index],
        abs(high_values[index] - close_values[index - 1]),
        abs(low_values[index] - close_values[index - 1]),
    )

beta = (1.0 - np.cos(4.0 * np.arcsin(1.0) / period)) / (1.414 ** (2.0 / poles) - 1.0)
alpha = -beta + np.sqrt(beta * beta + 2.0 * beta)
lag = int((period - 1) / (2 * poles))

source_data = source.copy()
range_data = true_range.copy()
if reduced_lag and lag > 0:
    delayed_source = np.full(n, np.nan, dtype=float)
    delayed_range = np.full(n, np.nan, dtype=float)
    if lag < n:
        delayed_source[lag:] = source[:-lag]
        delayed_range[lag:] = true_range[:-lag]
    source_data = source + (source - delayed_source)
    range_data = true_range + (true_range - delayed_range)

filter_n = gaussian_filter(source_data, alpha, poles)
filter_one = gaussian_filter(source_data, alpha, 1)
range_filter_n = gaussian_filter(range_data, alpha, poles)
range_filter_one = gaussian_filter(range_data, alpha, 1)

filtered = (filter_n + filter_one) * 0.5 if fast_response else filter_n
filtered_range = (range_filter_n + range_filter_one) * 0.5 if fast_response else range_filter_n
upper_band = filtered + filtered_range * multiplier
lower_band = filtered - filtered_range * multiplier

up_color = "#0AFF68"
up_extreme_color = "#0AFF1B"
up_below_color = "#00752D"
down_color = "#FF0A5A"
down_extreme_color = "#FF0A11"
down_above_color = "#990032"
flat_color = "#CCCCCC"

filter_colors = []
bar_colors = []
for index in range(n):
    previous_filter = filtered[index - 1] if index > 0 else np.nan
    if np.isfinite(previous_filter) and filtered[index] > previous_filter:
        filter_color = up_color
    elif np.isfinite(previous_filter) and filtered[index] < previous_filter:
        filter_color = down_color
    else:
        filter_color = flat_color
    filter_colors.append(filter_color)

    previous_source = source[index - 1] if index > 0 else np.nan
    if not np.isfinite(previous_source):
        bar_colors.append(flat_color)
    elif source[index] > previous_source and source[index] > filtered[index] and source[index] < upper_band[index]:
        bar_colors.append(up_color)
    elif source[index] > previous_source and source[index] >= upper_band[index]:
        bar_colors.append(up_extreme_color)
    elif source[index] <= previous_source and source[index] > filtered[index]:
        bar_colors.append(up_below_color)
    elif source[index] < previous_source and source[index] < filtered[index] and source[index] > lower_band[index]:
        bar_colors.append(down_color)
    elif source[index] < previous_source and source[index] <= lower_band[index]:
        bar_colors.append(down_extreme_color)
    elif source[index] >= previous_source and source[index] < filtered[index]:
        bar_colors.append(down_above_color)
    else:
        bar_colors.append(flat_color)

filter_plot = [
    {"value": float(filtered[index]), "color": filter_colors[index]}
    for index in range(n)
]
upper_plot = [
    {"value": float(upper_band[index]), "color": filter_colors[index]}
    for index in range(n)
]
lower_plot = [
    {"value": float(lower_band[index]), "color": filter_colors[index]}
    for index in range(n)
]
channel = [
    {
        "upper": float(max(upper_band[index], lower_band[index])),
        "lower": float(min(upper_band[index], lower_band[index])),
        "color": rgba(filter_colors[index], 0.20),
    }
    for index in range(n)
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Filter", "data": filter_plot, "color": flat_color, "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "Filtered True Range High Band", "data": upper_plot, "color": flat_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Filtered True Range Low Band", "data": lower_plot, "color": flat_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Channel Fill", "title": False, "data": channel, "type": "cloud", "overlay": True},
    ],
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": bar_colors,
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "source": finite(source), "trueRange": finite(true_range),
        "beta": float(beta), "alpha": float(alpha), "lag": int(lag),
        "sourceData": finite(source_data), "rangeData": finite(range_data),
        "filterN": finite(filter_n), "filterOne": finite(filter_one),
        "rangeFilterN": finite(range_filter_n), "rangeFilterOne": finite(range_filter_one),
        "filter": finite(filtered), "filteredRange": finite(filtered_range),
        "upperBand": finite(upper_band), "lowerBand": finite(lower_band),
        "filterColors": filter_colors,
    },
}
