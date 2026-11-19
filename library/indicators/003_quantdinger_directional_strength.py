# ============================================================
# QuantDinger Directional Strength
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Directional Strength"
my_indicator_description = (
    "Directional-movement strength study with positive/negative direction "
    "lines, an ADX trend-strength line, and a configurable reference level."
)

# @param period int 14 Smoothing period range=1:200:1
# @param threshold float 20.0 Trend-strength reference level range=0.0:100.0:1.0

period = max(1, int(params.get("period", 14)))
threshold = float(params.get("threshold", 20.0))

df = df.copy()
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)

high_values = high.to_numpy(dtype=float)
low_values = low.to_numpy(dtype=float)
close_values = close.to_numpy(dtype=float)
n = len(df)

previous_high = np.concatenate(([0.0], high_values[:-1]))
previous_low = np.concatenate(([0.0], low_values[:-1]))
previous_close = np.concatenate(([0.0], close_values[:-1]))

true_range = np.maximum.reduce(
    [
        high_values - low_values,
        np.abs(high_values - previous_close),
        np.abs(low_values - previous_close),
    ]
)

up_move = high_values - previous_high
down_move = previous_low - low_values
directional_plus = np.where(
    up_move > down_move,
    np.maximum(up_move, 0.0),
    0.0,
)
directional_minus = np.where(
    down_move > up_move,
    np.maximum(down_move, 0.0),
    0.0,
)

smoothed_range = np.zeros(n, dtype=float)
smoothed_plus = np.zeros(n, dtype=float)
smoothed_minus = np.zeros(n, dtype=float)

for index in range(n):
    if index == 0:
        previous_range = 0.0
        previous_plus = 0.0
        previous_minus = 0.0
    else:
        previous_range = smoothed_range[index - 1]
        previous_plus = smoothed_plus[index - 1]
        previous_minus = smoothed_minus[index - 1]

    smoothed_range[index] = (
        previous_range - previous_range / period + true_range[index]
    )
    smoothed_plus[index] = (
        previous_plus - previous_plus / period + directional_plus[index]
    )
    smoothed_minus[index] = (
        previous_minus - previous_minus / period + directional_minus[index]
    )

with np.errstate(divide="ignore", invalid="ignore"):
    di_plus = smoothed_plus / smoothed_range * 100.0
    di_minus = smoothed_minus / smoothed_range * 100.0
    dx = np.abs(di_plus - di_minus) / (di_plus + di_minus) * 100.0

adx = (
    pd.Series(dx, index=df.index)
    .rolling(window=period, min_periods=period)
    .mean()
    .to_numpy(dtype=float)
)


def finite_plot(values):
    return [
        float(value) if np.isfinite(value) else None
        for value in values
    ]


output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Positive Direction",
            "data": finite_plot(di_plus),
            "color": "#20A464",
            "type": "line",
            "lineWidth": 2,
            "overlay": False,
        },
        {
            "name": "Negative Direction",
            "data": finite_plot(di_minus),
            "color": "#F23645",
            "type": "line",
            "lineWidth": 2,
            "overlay": False,
        },
        {
            "name": "ADX",
            "data": finite_plot(adx),
            "color": "#243B80",
            "type": "line",
            "lineWidth": 2,
            "overlay": False,
        },
        {
            "name": "Strength Level",
            "data": [threshold for _ in range(n)],
            "color": "#808080",
            "type": "line",
            "lineWidth": 1,
            "overlay": False,
        },
    ],
    "signals": [],
    "layers": [],
}
