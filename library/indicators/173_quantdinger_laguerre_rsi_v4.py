# ============================================================
# QuantDinger Laguerre RSI v4
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Laguerre RSI v4"
my_indicator_description = (
    "Applies a four-stage Laguerre filter to closing prices, converts directional movement into "
    "a bounded momentum ratio, and colors rising, falling, and neutral phases."
)

# @param gamma float 0.75 Laguerre damping factor range=0.0:0.99:0.01
# @param overbought float 0.8 Overbought level range=0.0:1.0:0.01
# @param oversold float 0.2 Oversold level range=0.0:1.0:0.01
# @param smoothing int 1 EMA smoothing, one means off range=1:100:1
# @param three_color_mode bool true Use momentum-only three-color rules
# @param show_levels bool true Show overbought and oversold levels
# @param rising_color str #00FF7B Rising momentum color
# @param falling_color str #FF3571 Falling momentum color
# @param neutral_color str #ECA700 Neutral momentum color
# @param level_color str #64748B Threshold color

gamma = min(0.99, max(0.0, float(params.get("gamma", 0.75))))
overbought = min(1.0, max(0.0, float(params.get("overbought", 0.8))))
oversold = min(1.0, max(0.0, float(params.get("oversold", 0.2))))
smoothing = min(100, max(1, int(params.get("smoothing", 1))))
three_color_mode = bool(params.get("three_color_mode", True))
show_levels = bool(params.get("show_levels", True))
rising_color = str(params.get("rising_color", "#00FF7B"))
falling_color = str(params.get("falling_color", "#FF3571"))
neutral_color = str(params.get("neutral_color", "#ECA700"))
level_color = str(params.get("level_color", "#64748B"))

df = df.copy()
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

raw_lrsi = np.zeros(n, dtype=float)
previous_l0 = 0.0
previous_l1 = 0.0
previous_l2 = 0.0
previous_l3 = 0.0
for index in range(n):
    current_l0 = (1.0 - gamma) * float(close[index]) + gamma * previous_l0
    current_l1 = -gamma * current_l0 + previous_l0 + gamma * previous_l1
    current_l2 = -gamma * current_l1 + previous_l1 + gamma * previous_l2
    current_l3 = -gamma * current_l2 + previous_l2 + gamma * previous_l3

    upward = (
        max(current_l0 - current_l1, 0.0)
        + max(current_l1 - current_l2, 0.0)
        + max(current_l2 - current_l3, 0.0)
    )
    downward = (
        max(current_l1 - current_l0, 0.0)
        + max(current_l2 - current_l1, 0.0)
        + max(current_l3 - current_l2, 0.0)
    )
    denominator = upward + downward
    raw_lrsi[index] = upward / denominator if denominator > 0.0 else 0.0

    previous_l0 = current_l0
    previous_l1 = current_l1
    previous_l2 = current_l2
    previous_l3 = current_l3

lrsi = np.zeros(n, dtype=float)
if n:
    lrsi[0] = raw_lrsi[0]
    alpha = 2.0 / (smoothing + 1.0)
    for index in range(1, n):
        lrsi[index] = raw_lrsi[index] if smoothing == 1 else alpha * raw_lrsi[index] + (1.0 - alpha) * lrsi[index - 1]

states = ["neutral" for _ in range(n)]
for index in range(1, n):
    rising = lrsi[index] > lrsi[index - 1] and lrsi[index] > oversold
    falling = lrsi[index] < lrsi[index - 1] and lrsi[index] < overbought
    if rising:
        states[index] = "rising"
    elif falling:
        states[index] = "falling"
    elif not three_color_mode and lrsi[index] < oversold:
        states[index] = "falling"
    elif not three_color_mode and lrsi[index] > overbought:
        states[index] = "rising"

rising_data = [None for _ in range(n)]
falling_data = [None for _ in range(n)]
neutral_data = [None for _ in range(n)]
state_data = {"rising": rising_data, "falling": falling_data, "neutral": neutral_data}
for index in range(n):
    state_data[states[index]][index] = float(lrsi[index])
    if index > 0 and states[index] != states[index - 1]:
        state_data[states[index]][index - 1] = float(lrsi[index - 1])
        state_data[states[index - 1]][index] = float(lrsi[index])

plots = [
    {"name": "Laguerre RSI Rising", "data": rising_data, "color": rising_color, "type": "line", "lineWidth": 2, "overlay": False},
    {"name": "Laguerre RSI Falling", "data": falling_data, "color": falling_color, "type": "line", "lineWidth": 2, "overlay": False},
    {"name": "Laguerre RSI Neutral", "data": neutral_data, "color": neutral_color, "type": "line", "lineWidth": 2, "overlay": False},
]
if show_levels:
    plots.extend(
        [
            {"name": "Overbought", "data": [float(overbought) for _ in range(n)], "color": level_color, "type": "line", "lineWidth": 1, "dashed": True, "overlay": False},
            {"name": "Oversold", "data": [float(oversold) for _ in range(n)], "color": level_color, "type": "line", "lineWidth": 1, "dashed": True, "overlay": False},
        ]
    )

output = {"name": my_indicator_name, "plots": plots, "signals": []}
