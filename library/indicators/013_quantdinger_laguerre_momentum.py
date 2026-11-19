# ============================================================
# QuantDinger Laguerre Momentum
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Laguerre Momentum"
my_indicator_description = (
    "A four-stage Laguerre-filtered momentum oscillator with optional "
    "directional coloring and 20/80 guide levels."
)

# @param alpha float 0.2 Filter alpha range=0.0:1.0:0.05
# @param color_change bool false Color by one-bar direction
# @param source str close Price source values=close,open,high,low,hl2,hlc3,ohlc4

alpha = float(params.get("alpha", 0.2))
alpha = min(1.0, max(0.0, alpha))
color_change = bool(params.get("color_change", False))
source = str(params.get("source", "close"))
if source not in ("close", "open", "high", "low", "hl2", "hlc3", "ohlc4"):
    source = "close"

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
source_values = {
    "close": close, "open": open_price, "high": high, "low": low,
    "hl2": (high + low) * 0.5,
    "hlc3": (high + low + close) / 3.0,
    "ohlc4": (open_price + high + low + close) * 0.25,
}[source]
n = len(df)
gamma = 1.0 - alpha
l0_values = np.zeros(n, dtype=float)
l1_values = np.zeros(n, dtype=float)
l2_values = np.zeros(n, dtype=float)
l3_values = np.zeros(n, dtype=float)
oscillator = np.zeros(n, dtype=float)

for index in range(n):
    previous_l0 = l0_values[index - 1] if index > 0 else 0.0
    previous_l1 = l1_values[index - 1] if index > 0 else 0.0
    previous_l2 = l2_values[index - 1] if index > 0 else 0.0
    previous_l3 = l3_values[index - 1] if index > 0 else 0.0

    l0_values[index] = alpha * source_values[index] + gamma * previous_l0
    l1_values[index] = (
        -gamma * l0_values[index] + previous_l0 + gamma * previous_l1
    )
    l2_values[index] = (
        -gamma * l1_values[index] + previous_l1 + gamma * previous_l2
    )
    l3_values[index] = (
        -gamma * l2_values[index] + previous_l2 + gamma * previous_l3
    )

    cu = 0.0
    cd = 0.0
    stages = (
        (l0_values[index], l1_values[index]),
        (l1_values[index], l2_values[index]),
        (l2_values[index], l3_values[index]),
    )
    for upper_stage, lower_stage in stages:
        if upper_stage >= lower_stage:
            cu += upper_stage - lower_stage
        else:
            cd += lower_stage - upper_stage
    denominator = cu + cd
    oscillator[index] = 0.0 if denominator == 0.0 else 100.0 * cu / denominator

oscillator_plot = []
for index, value in enumerate(oscillator):
    if color_change:
        previous_value = oscillator[index - 1] if index > 0 else value
        oscillator_plot.append(
            {
                "value": float(value),
                "color": "#00A000" if value > previous_value else "#D00000",
            }
        )
    else:
        oscillator_plot.append(float(value))

cross_over_20 = np.zeros(n, dtype=bool)
cross_under_80 = np.zeros(n, dtype=bool)
for index in range(1, n):
    cross_over_20[index] = oscillator[index] > 20.0 and oscillator[index - 1] <= 20.0
    cross_under_80[index] = oscillator[index] < 80.0 and oscillator[index - 1] >= 80.0

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Laguerre Momentum",
            "data": oscillator_plot,
            "color": "#1565C0",
            "type": "line",
            "lineWidth": 2,
            "overlay": False,
        },
        {
            "name": "Upper Guide",
            "data": [80.0] * n,
            "color": "#800000",
            "type": "line",
            "lineWidth": 1,
            "overlay": False,
        },
        {
            "name": "Lower Guide",
            "data": [20.0] * n,
            "color": "#800000",
            "type": "line",
            "lineWidth": 1,
            "overlay": False,
        },
    ],
    "signals": [
        {"type": "buy", "name": "Laguerre RSI Cross Over 20", "text": "L20", "data": [float(oscillator[i]) if cross_over_20[i] else None for i in range(n)], "color": "#00A000", "renderMode": "hidden"},
        {"type": "sell", "name": "Laguerre RSI Cross Under 80", "text": "L80", "data": [float(oscillator[i]) if cross_under_80[i] else None for i in range(n)], "color": "#D00000", "renderMode": "hidden"},
    ],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "source": [float(value) for value in source_values],
        "l0": [float(value) for value in l0_values], "l1": [float(value) for value in l1_values],
        "l2": [float(value) for value in l2_values], "l3": [float(value) for value in l3_values],
        "oscillator": [float(value) for value in oscillator],
        "crossOver20": cross_over_20.tolist(), "crossUnder80": cross_under_80.tolist(),
    },
}
