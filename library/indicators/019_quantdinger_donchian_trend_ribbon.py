# ============================================================
# QuantDinger Donchian Trend Ribbon
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Donchian Trend Ribbon"
my_indicator_description = (
    "Ten stacked Donchian breakout states that show agreement with the main "
    "channel trend as a compact red/green ribbon."
)

# @param channel_period int 20 Donchian period range=10:300:1

channel_period = max(10, int(params.get("channel_period", 20)))

df = df.copy()
high = df["high"].astype(float)
low = df["low"].astype(float)
close_values = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

def channel_state(length):
    rolling_high = high.rolling(window=length, min_periods=length).max().shift(1).to_numpy(dtype=float)
    rolling_low = low.rolling(window=length, min_periods=length).min().shift(1).to_numpy(dtype=float)
    state = np.zeros(n, dtype=int)
    for index in range(n):
        previous = state[index - 1] if index > 0 else 0
        if np.isfinite(rolling_high[index]) and close_values[index] > rolling_high[index]:
            state[index] = 1
        elif np.isfinite(rolling_low[index]) and close_values[index] < rolling_low[index]:
            state[index] = -1
        else:
            state[index] = previous
    return state

main_state = channel_state(channel_period)
plots = []
for ribbon_index in range(10):
    alternate_state = channel_state(max(1, channel_period - ribbon_index))
    center_value = float(ribbon_index * 5 + 2.5)
    ribbon_data = []
    for index in range(n):
        if main_state[index] == 1:
            color = "#00FF00" if alternate_state[index] == 1 else "rgba(0,255,0,0.62)"
        elif main_state[index] == -1:
            color = "#FF0000" if alternate_state[index] == -1 else "rgba(255,0,0,0.62)"
        else:
            color = "#808080"
        ribbon_data.append({"value": center_value, "color": color})
    plots.append({"name": f"Ribbon {ribbon_index + 1}", "title": False, "data": ribbon_data, "color": "#808080", "type": "line", "lineWidth": 6, "overlay": False})

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None for _ in range(n)],
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "mainState": [int(value) for value in main_state],
        "ribbonStates": [
            [int(value) for value in channel_state(max(1, channel_period - ribbon_index))]
            for ribbon_index in range(10)
        ],
    },
}
