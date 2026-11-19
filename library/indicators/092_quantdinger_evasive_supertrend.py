# ============================================================
# QuantDinger Evasive SuperTrend
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Evasive SuperTrend"
my_indicator_description = (
    "Expands the active SuperTrend band away from nearby price noise while "
    "retaining trend flips, noise-state line styling, and directional shading."
)

# @param atr_length int 10 ATR length range=1:200:1
# @param multiplier float 3.0 Base multiplier range=0.1:10.0:0.1
# @param noise_threshold float 1.0 Noise threshold range=0.1:5.0:0.1
# @param expansion_alpha float 0.5 Expansion alpha range=0.0:5.0:0.1
# @param show_labels bool true Show trend labels
# @param show_fills bool true Show directional gradient fills

atr_length = min(500, max(1, int(params.get("atr_length", 10))))
multiplier = min(20.0, max(0.1, float(params.get("multiplier", 3.0))))
noise_threshold = min(20.0, max(0.1, float(params.get("noise_threshold", 1.0))))
expansion_alpha = min(20.0, max(0.0, float(params.get("expansion_alpha", 0.5))))
show_labels = bool(params.get("show_labels", True))
show_fills = bool(params.get("show_fills", True))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


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


true_range = np.full(n, np.nan, dtype=float)
if n:
    true_range[0] = high[0] - low[0]
for index in range(1, n):
    true_range[index] = max(
        high[index] - low[index],
        abs(high[index] - close[index - 1]),
        abs(low[index] - close[index - 1]),
    )
atr = pine_rma(true_range, atr_length)

source = (high + low) / 2.0
band = np.full(n, np.nan, dtype=float)
trend = np.ones(n, dtype=int)
noisy = np.zeros(n, dtype=bool)
trend_changed = np.zeros(n, dtype=bool)

current_trend = 1
for index in range(n):
    if not np.isfinite(atr[index]):
        trend[index] = current_trend
        continue

    upper_base = source[index] + multiplier * atr[index]
    lower_base = source[index] - multiplier * atr[index]
    previous_band = band[index - 1] if index > 0 and np.isfinite(band[index - 1]) else (lower_base if current_trend == 1 else upper_base)
    noisy[index] = abs(close[index] - previous_band) < atr[index] * noise_threshold

    previous_trend = current_trend
    if current_trend == 1:
        next_band = previous_band - atr[index] * expansion_alpha if noisy[index] else max(lower_base, previous_band)
        if close[index] < next_band:
            current_trend = -1
            next_band = upper_base
    else:
        next_band = previous_band + atr[index] * expansion_alpha if noisy[index] else min(upper_base, previous_band)
        if close[index] > next_band:
            current_trend = 1
            next_band = lower_base

    band[index] = float(next_band)
    trend[index] = current_trend
    trend_changed[index] = index > 0 and current_trend != previous_trend

bull_color = "#089981"
bear_color = "#F23645"

solid_band = []
dotted_band = []
switch_points = []
for index in range(n):
    if not np.isfinite(band[index]):
        solid_band.append(None)
        dotted_band.append(None)
        switch_points.append(None)
        continue
    color = bull_color if trend[index] == 1 else bear_color
    point = {"value": float(band[index]), "color": color}
    solid_band.append(None if noisy[index] else point)
    dotted_band.append(point if noisy[index] else None)
    switch_points.append(point if trend_changed[index] else None)

slice_count = 6
fill_slices = [[None for _ in range(n)] for _ in range(slice_count)]
if show_fills:
    for index in range(n):
        if not np.isfinite(band[index]):
            continue
        top = float(max(source[index], band[index]))
        bottom = float(min(source[index], band[index]))
        span = top - bottom
        if span <= 0.0:
            continue
        for slice_index in range(slice_count):
            upper = top - span * slice_index / slice_count
            lower = top - span * (slice_index + 1) / slice_count
            near_price_ratio = (
                1.0 - (slice_index + 0.5) / slice_count
                if trend[index] == 1
                else (slice_index + 0.5) / slice_count
            )
            alpha = 0.50 * near_price_ratio
            if trend[index] == 1:
                color = f"rgba(8, 153, 129, {alpha:.3f})"
            else:
                color = f"rgba(242, 54, 69, {alpha:.3f})"
            fill_slices[slice_index][index] = {
                "upper": float(upper),
                "lower": float(lower),
                "color": color,
            }

plots = [
    {"name": "Solid Band", "data": solid_band, "color": bull_color, "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Dotted Band", "data": dotted_band, "color": bull_color, "type": "line", "lineWidth": 2, "lineStyle": "dashed", "overlay": True},
    {"name": "Switch Point", "data": switch_points, "color": bull_color, "type": "circle", "size": 5, "overlay": True},
]
for slice_index, slice_data in enumerate(fill_slices):
    plots.append(
        {
            "name": f"Fill Slice {slice_index + 1}",
            "title": False,
            "data": slice_data,
            "color": bull_color,
            "type": "cloud",
            "opacity": 1.0,
            "overlay": True,
        }
    )

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [
        {
            "type": "buy",
            "text": "BULL",
            "name": "Bullish Flip",
            "data": [float(band[index]) if show_labels and trend_changed[index] and trend[index] == 1 else None for index in range(n)],
            "color": bull_color,
            "renderMode": "events",
        },
        {
            "type": "sell",
            "text": "BEAR",
            "name": "Bearish Flip",
            "data": [float(band[index]) if show_labels and trend_changed[index] and trend[index] == -1 else None for index in range(n)],
            "color": bear_color,
            "renderMode": "events",
        },
    ],
    "layers": [],
    "tables": [],
    "bar_colors": [None for _ in range(n)],
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "trueRange": [float(value) if np.isfinite(value) else None for value in true_range],
        "atr": [float(value) if np.isfinite(value) else None for value in atr],
        "source": [float(value) if np.isfinite(value) else None for value in source],
        "upperBase": [float(source[index] + multiplier * atr[index]) if np.isfinite(atr[index]) else None for index in range(n)],
        "lowerBase": [float(source[index] - multiplier * atr[index]) if np.isfinite(atr[index]) else None for index in range(n)],
        "band": [float(value) if np.isfinite(value) else None for value in band],
        "trend": trend.tolist(),
        "noisy": noisy.tolist(),
        "trendChanged": trend_changed.tolist(),
    },
}
