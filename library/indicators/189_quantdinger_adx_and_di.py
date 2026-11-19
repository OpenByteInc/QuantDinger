# ============================================================
# QuantDinger ADX and DI
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger ADX and DI"
my_indicator_description = (
    "Calculates recursively smoothed positive and negative directional movement, "
    "their directional indexes, and a moving-average ADX strength line."
)

# @param length int 14 Directional smoothing length range=2:500:1
# @param threshold float 20.0 Trend-strength threshold range=0.0:100.0:1.0
# @param plus_color str #22C55E DI+ color
# @param minus_color str #EF4444 DI- color
# @param adx_color str #4F6BED ADX color
# @param threshold_color str #8B93A1 Threshold color

length = min(500, max(2, int(params.get("length", 14))))
threshold = min(100.0, max(0.0, float(params.get("threshold", 20.0))))
plus_color = str(params.get("plus_color", "#22C55E"))
minus_color = str(params.get("minus_color", "#EF4444"))
adx_color = str(params.get("adx_color", "#4F6BED"))
threshold_color = str(params.get("threshold_color", "#8B93A1"))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

true_range = np.zeros(n, dtype=float)
directional_plus = np.zeros(n, dtype=float)
directional_minus = np.zeros(n, dtype=float)
for index in range(n):
    prior_close = close[index - 1] if index > 0 else 0.0
    prior_high = high[index - 1] if index > 0 else 0.0
    prior_low = low[index - 1] if index > 0 else 0.0
    upward_move = high[index] - prior_high
    downward_move = prior_low - low[index]
    true_range[index] = max(
        high[index] - low[index],
        abs(high[index] - prior_close),
        abs(low[index] - prior_close),
    )
    directional_plus[index] = max(upward_move, 0.0) if upward_move > downward_move else 0.0
    directional_minus[index] = max(downward_move, 0.0) if downward_move > upward_move else 0.0

smoothed_range = np.zeros(n, dtype=float)
smoothed_plus = np.zeros(n, dtype=float)
smoothed_minus = np.zeros(n, dtype=float)
for index in range(n):
    if index == 0:
        smoothed_range[index] = true_range[index]
        smoothed_plus[index] = directional_plus[index]
        smoothed_minus[index] = directional_minus[index]
    else:
        smoothed_range[index] = smoothed_range[index - 1] - smoothed_range[index - 1] / length + true_range[index]
        smoothed_plus[index] = smoothed_plus[index - 1] - smoothed_plus[index - 1] / length + directional_plus[index]
        smoothed_minus[index] = smoothed_minus[index - 1] - smoothed_minus[index - 1] / length + directional_minus[index]

di_plus = np.divide(
    smoothed_plus * 100.0,
    smoothed_range,
    out=np.zeros(n, dtype=float),
    where=np.abs(smoothed_range) > 1e-12,
)
di_minus = np.divide(
    smoothed_minus * 100.0,
    smoothed_range,
    out=np.zeros(n, dtype=float),
    where=np.abs(smoothed_range) > 1e-12,
)
directional_sum = di_plus + di_minus
dx = np.divide(
    np.abs(di_plus - di_minus) * 100.0,
    directional_sum,
    out=np.zeros(n, dtype=float),
    where=np.abs(directional_sum) > 1e-12,
)

adx = np.full(n, np.nan, dtype=float)
for index in range(length - 1, n):
    adx[index] = float(np.mean(dx[index - length + 1:index + 1]))

def points(values, color):
    return [
        {"value": float(value), "color": color} if np.isfinite(value) else None
        for value in values
    ]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Positive DI", "data": points(di_plus, plus_color), "color": plus_color, "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Negative DI", "data": points(di_minus, minus_color), "color": minus_color, "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "ADX", "data": points(adx, adx_color), "color": adx_color, "type": "line", "lineWidth": 2, "overlay": False},
        {
            "name": "Strength Threshold",
            "title": False,
            "data": points(np.full(n, threshold, dtype=float), threshold_color),
            "color": threshold_color,
            "type": "line",
            "lineWidth": 1,
            "lineStyle": "dashed",
            "overlay": False,
        },
    ],
    "signals": [],
    "tables": [],
}
