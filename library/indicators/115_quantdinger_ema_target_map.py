# ============================================================
# QuantDinger EMA Target Map
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger EMA Target Map"
my_indicator_description = (
    "Tracks EMA 9/21 regime changes and estimates two persistent bullish and "
    "bearish targets from recent crossover behavior and regime extremes."
)

# @param lookback int 500 Target statistics lookback range=20:2000:10
# @param show_target_labels bool true Show target labels at EMA crosses
# @param show_regime_cloud bool true Fill the EMA regime cloud

lookback = min(2000, max(20, int(params.get("lookback", 500))))
show_target_labels = bool(params.get("show_target_labels", True))
show_regime_cloud = bool(params.get("show_regime_cloud", True))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

def ema_series(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) == 0:
        return result
    alpha = 2.0 / (length + 1.0)
    result[0] = float(values[0])
    for index in range(1, len(values)):
        result[index] = alpha * values[index] + (1.0 - alpha) * result[index - 1]
    return result

ema_fast = ema_series(close, 9)
ema_slow = ema_series(close, 21)
bull_cross = np.zeros(n, dtype=bool)
bear_cross = np.zeros(n, dtype=bool)
above_both = np.zeros(n, dtype=bool)
below_both = np.zeros(n, dtype=bool)

for index in range(n):
    above_both[index] = close[index] >= ema_fast[index] and close[index] >= ema_slow[index]
    below_both[index] = close[index] < ema_fast[index] and close[index] <= ema_slow[index]
    if index == 0:
        continue
    bull_cross[index] = (
        ema_fast[index] > ema_slow[index]
        and ema_fast[index - 1] <= ema_slow[index - 1]
    )
    bear_cross[index] = (
        ema_slow[index] > ema_fast[index]
        and ema_slow[index - 1] <= ema_fast[index - 1]
    )

bull_target_one = [None for _ in range(n)]
bull_target_two = [None for _ in range(n)]
bear_target_one = [None for _ in range(n)]
bear_target_two = [None for _ in range(n)]
bull_marks = [None for _ in range(n)]
bear_marks = [None for _ in range(n)]
bull_labels = [None for _ in range(n)]
bear_labels = [None for _ in range(n)]

active_bull_one = None
active_bull_two = None
active_bear_one = None
active_bear_two = None

for index in range(n):
    start = max(0, index - lookback)

    if bull_cross[index]:
        bullish_values = [close[item] for item in range(start, index + 1) if above_both[item]]
        crossover_values = [close[item] for item in range(start, index + 1) if bull_cross[item]]
        if bullish_values and crossover_values:
            maximum_above = float(max(bullish_values))
            crossover_average = float(np.mean(crossover_values))
            distance = maximum_above - crossover_average
            active_bull_one = float(open_price[index] + distance / 2.0)
            active_bull_two = float(open_price[index] + distance)
            for old_index in range(index):
                bull_target_one[old_index] = None
                bull_target_two[old_index] = None
            bull_marks[index] = float(low[index])
            if show_target_labels:
                bull_labels[index] = {
                    "value": active_bull_two,
                    "text": f"Bull targets {active_bull_one:.2f} / {active_bull_two:.2f}",
                    "color": "#4CAF50",
                }

    if bear_cross[index]:
        bearish_values = [close[item] for item in range(start, index + 1) if below_both[item]]
        crossunder_values = [close[item] for item in range(start, index + 1) if bear_cross[item]]
        bullish_cross_indices = [item for item in range(start, index + 1) if bull_cross[item]]
        if bearish_values and crossunder_values:
            minimum_below = float(min(bearish_values))
            crossunder_average = float(np.mean(crossunder_values))
            anchor_open = (
                float(open_price[bullish_cross_indices[-1]])
                if bullish_cross_indices
                else float(open_price[index])
            )
            distance = crossunder_average - minimum_below
            active_bear_one = float(anchor_open - distance / 2.0)
            active_bear_two = float(anchor_open - distance)
            for old_index in range(index):
                bear_target_one[old_index] = None
                bear_target_two[old_index] = None
            bear_marks[index] = float(high[index])
            if show_target_labels:
                bear_labels[index] = {
                    "value": active_bear_two,
                    "text": f"Bear targets {active_bear_one:.2f} / {active_bear_two:.2f}",
                    "color": "#F44336",
                }

    if active_bull_one is not None:
        bull_target_one[index] = active_bull_one
        bull_target_two[index] = active_bull_two
    if active_bear_one is not None:
        bear_target_one[index] = active_bear_one
        bear_target_two[index] = active_bear_two

regime_cloud = []
for index in range(n):
    if not show_regime_cloud:
        regime_cloud.append(None)
    elif above_both[index]:
        regime_cloud.append({
            "upper": float(max(ema_fast[index], ema_slow[index])),
            "lower": float(min(ema_fast[index], ema_slow[index])),
            "color": "rgba(76, 175, 80, 0.22)",
        })
    elif below_both[index]:
        regime_cloud.append({
            "upper": float(max(ema_fast[index], ema_slow[index])),
            "lower": float(min(ema_fast[index], ema_slow[index])),
            "color": "rgba(244, 67, 54, 0.22)",
        })
    else:
        regime_cloud.append({
            "upper": float(max(ema_fast[index], ema_slow[index])),
            "lower": float(min(ema_fast[index], ema_slow[index])),
            "color": "rgba(158, 158, 158, 0.14)",
        })

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "EMA 9", "data": [float(value) for value in ema_fast], "color": "#26A69A", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "EMA 21", "data": [float(value) for value in ema_slow], "color": "#FF9800", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "EMA Regime", "title": False, "data": regime_cloud, "color": "#787B86", "type": "cloud", "opacity": 0.22, "overlay": True},
        {"name": "Bull Target 1", "data": bull_target_one, "color": "rgba(76, 175, 80, 0.68)", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Bull Target 2", "data": bull_target_two, "color": "#4CAF50", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Bear Target 1", "data": bear_target_one, "color": "rgba(244, 67, 54, 0.68)", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Bear Target 2", "data": bear_target_two, "color": "#F44336", "type": "line", "lineWidth": 2, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "EMA+", "name": "Bullish EMA Cross", "data": bull_marks, "color": "#4CAF50", "renderMode": "events"},
        {"type": "sell", "text": "EMA-", "name": "Bearish EMA Cross", "data": bear_marks, "color": "#F44336", "renderMode": "events"},
        {"type": "sell", "text": "Targets", "name": "Bull Target Labels", "data": bull_labels, "color": "#4CAF50", "renderMode": "events"},
        {"type": "buy", "text": "Targets", "name": "Bear Target Labels", "data": bear_labels, "color": "#F44336", "renderMode": "events"},
    ],
    "layers": [],
}
