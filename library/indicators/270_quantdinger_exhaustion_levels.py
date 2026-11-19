# ============================================================
# QuantDinger Exhaustion Levels
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Exhaustion Levels"
my_indicator_description = (
    "Counts persistent four-bar directional closes and marks exhaustion when "
    "a reversal candle reaches a rolling price extreme."
)

# @param show_arrows bool true Show exhaustion markers
# @param show_levels bool true Show exhaustion levels
# @param swing_length int 40 Exhaustion swing length range=1:500:1
# @param exhaustion_count int 10 Exhaustion bar count range=1:500:1
# @param line_width int 2 Level thickness range=1:5:1

show_arrows = bool(params.get("show_arrows", True))
show_levels = bool(params.get("show_levels", True))
swing_length = min(500, max(1, int(params.get("swing_length", 40))))
exhaustion_count = min(500, max(1, int(params.get("exhaustion_count", 10))))
line_width = min(5, max(1, int(params.get("line_width", 2))))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

buy_index = np.zeros(n, dtype=int)
sell_index = np.zeros(n, dtype=int)
exhaustion = np.zeros(n, dtype=int)
rolling_high = pd.Series(high).rolling(swing_length, min_periods=swing_length).max().to_numpy(dtype=float)
rolling_low = pd.Series(low).rolling(swing_length, min_periods=swing_length).min().to_numpy(dtype=float)
buy_state = 0
sell_state = 0

for index in range(n):
    if index >= 4 and close[index] > close[index - 4]:
        buy_state += 1
    if index >= 4 and close[index] < close[index - 4]:
        sell_state += 1
    event = 0
    if buy_state > exhaustion_count and close[index] < open_price[index] and np.isfinite(rolling_high[index]) and high[index] >= rolling_high[index]:
        buy_state = 0
        event = -1
    elif sell_state > exhaustion_count and close[index] > open_price[index] and np.isfinite(rolling_low[index]) and low[index] <= rolling_low[index]:
        sell_state = 0
        event = 1
    buy_index[index] = buy_state
    sell_index[index] = sell_state
    exhaustion[index] = event

resistance = np.full(n, np.nan, dtype=float)
support = np.full(n, np.nan, dtype=float)
resistance_state = np.nan
support_state = np.nan
for index in range(n):
    if exhaustion[index] == -1 and close[index] < open_price[index]:
        resistance_state = float(high[index])
    if exhaustion[index] == 1 and close[index] > open_price[index]:
        support_state = float(low[index])
    resistance[index] = resistance_state
    support[index] = support_state

resistance_plot = []
support_plot = []
for index in range(n):
    resistance_changed = index == 0 or not np.isfinite(resistance[index - 1]) or resistance[index] != resistance[index - 1]
    support_changed = index == 0 or not np.isfinite(support[index - 1]) or support[index] != support[index - 1]
    resistance_plot.append(float(resistance[index]) if show_levels and np.isfinite(resistance[index]) and not resistance_changed else None)
    support_plot.append(float(support[index]) if show_levels and np.isfinite(support[index]) and not support_changed else None)

up_marks = [float(low[index]) if exhaustion[index] == 1 else None for index in range(n)]
down_marks = [float(high[index]) if exhaustion[index] == -1 else None for index in range(n)]


def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Resistance Level", "data": resistance_plot, "color": "rgba(255,0,0,0.99)", "type": "line", "lineWidth": line_width, "overlay": True},
        {"name": "Support Level", "data": support_plot, "color": "rgba(19,132,132,0.99)", "type": "line", "lineWidth": line_width, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite_or_none(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "name": "Up Exhaustion", "text": "▲", "data": up_marks, "color": "#00FF00", "renderMode": "events" if show_arrows else "hidden"},
        {"type": "sell", "name": "Down Exhaustion", "text": "▼", "data": down_marks, "color": "#FF0000", "renderMode": "events" if show_arrows else "hidden"},
    ],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "rollingHigh": _finite_or_none(rolling_high), "rollingLow": _finite_or_none(rolling_low),
        "buyIndex": buy_index.tolist(), "sellIndex": sell_index.tolist(),
        "exhaustion": exhaustion.tolist(),
        "resistance": _finite_or_none(resistance), "support": _finite_or_none(support),
    },
}
