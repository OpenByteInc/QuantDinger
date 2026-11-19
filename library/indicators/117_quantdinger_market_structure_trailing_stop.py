# ============================================================
# QuantDinger Market Structure Trailing Stop
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Market Structure Trailing Stop"
my_indicator_description = (
    "Confirms swing structures, resets a directional trailing stop on selected "
    "breakouts, and highlights retracements through the active stop."
)

# @param pivot_lookback int 14 Pivot confirmation lookback range=2:100:1
# @param increment_factor float 100.0 Trailing increment factor percent range=0:500:5
# @param reset_on str CHoCH Reset stop on structure type values=CHoCH,All
# @param show_structures bool true Show confirmed structure segments

pivot_lookback = min(100, max(2, int(params.get("pivot_lookback", 14))))
increment_factor = min(500.0, max(0.0, float(params.get("increment_factor", 100.0))))
reset_on = str(params.get("reset_on", "CHoCH"))
show_structures = bool(params.get("show_structures", True))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

bull_structure = [None for _ in range(n)]
bear_structure = [None for _ in range(n)]
bull_stop = [None for _ in range(n)]
bear_stop = [None for _ in range(n)]
retracement_area = [None for _ in range(n)]
bull_reversal = [None for _ in range(n)]
bear_reversal = [None for _ in range(n)]

last_pivot_high = None
last_pivot_high_index = None
last_pivot_low = None
last_pivot_low_index = None
pivot_high_crossed = False
pivot_low_crossed = False

direction = 0
trailing_stop = None
support_floor = None
resistance_ceiling = None
maximum_close = None
minimum_close = None

for index in range(n):
    center = index - pivot_lookback
    if center >= pivot_lookback:
        start = center - pivot_lookback
        high_window = high[start:index + 1]
        low_window = low[start:index + 1]
        candidate_high = float(high[center])
        candidate_low = float(low[center])
        if candidate_high >= float(np.max(high_window)):
            last_pivot_high = candidate_high
            last_pivot_high_index = center
            pivot_high_crossed = False
        if candidate_low <= float(np.min(low_window)):
            last_pivot_low = candidate_low
            last_pivot_low_index = center
            pivot_low_crossed = False

    market_structure = 0

    if (
        last_pivot_high is not None
        and close[index] > last_pivot_high
        and not pivot_high_crossed
    ):
        market_structure = 1 if reset_on == "All" or direction == -1 else 0
        pivot_high_crossed = True
        if show_structures:
            for item in range(last_pivot_high_index, index + 1):
                bull_structure[item] = last_pivot_high
        direction = 1
        support_floor = float(np.min(low[last_pivot_high_index:index + 1]))
        if market_structure == 1:
            bull_reversal[index] = float(low[index])

    if (
        last_pivot_low is not None
        and close[index] < last_pivot_low
        and not pivot_low_crossed
    ):
        market_structure = -1 if reset_on == "All" or direction == 1 else 0
        pivot_low_crossed = True
        if show_structures:
            for item in range(last_pivot_low_index, index + 1):
                bear_structure[item] = last_pivot_low
        direction = -1
        resistance_ceiling = float(np.max(high[last_pivot_low_index:index + 1]))
        if market_structure == -1:
            bear_reversal[index] = float(high[index])

    previous_maximum = maximum_close
    previous_minimum = minimum_close

    if market_structure == 1:
        maximum_close = float(close[index])
    elif market_structure == -1:
        minimum_close = float(close[index])
    else:
        if maximum_close is not None:
            maximum_close = max(maximum_close, float(close[index]))
        if minimum_close is not None:
            minimum_close = min(minimum_close, float(close[index]))

    if market_structure == 1 and support_floor is not None:
        trailing_stop = support_floor
    elif market_structure == -1 and resistance_ceiling is not None:
        trailing_stop = resistance_ceiling
    elif (
        direction == 1
        and trailing_stop is not None
        and maximum_close is not None
        and previous_maximum is not None
    ):
        trailing_stop += (
            maximum_close - previous_maximum
        ) * increment_factor / 100.0
    elif (
        direction == -1
        and trailing_stop is not None
        and minimum_close is not None
        and previous_minimum is not None
    ):
        trailing_stop += (
            minimum_close - previous_minimum
        ) * increment_factor / 100.0

    if trailing_stop is None or market_structure != 0 or direction == 0:
        continue

    if direction == 1:
        bull_stop[index] = float(trailing_stop)
    else:
        bear_stop[index] = float(trailing_stop)

    is_retracement = (close[index] - trailing_stop) * direction < 0.0
    area_color = (
        "rgba(255, 93, 0, 0.20)"
        if is_retracement
        else "rgba(38, 166, 154, 0.16)"
        if direction == 1
        else "rgba(239, 83, 80, 0.16)"
    )
    retracement_area[index] = {
        "upper": float(max(close[index], trailing_stop)),
        "lower": float(min(close[index], trailing_stop)),
        "color": area_color,
    }

plots = [
    {"name": "Price Reference", "title": False, "data": [float(value) for value in close], "color": "rgba(255, 255, 255, 0.01)", "type": "line", "lineWidth": 1, "overlay": True},
]

if any(value is not None for value in retracement_area):
    plots.append({"name": "Stop Area", "title": False, "data": retracement_area, "color": "#787B86", "type": "cloud", "opacity": 0.18, "overlay": True})
if any(value is not None for value in bull_structure):
    plots.append({"name": "Bullish Structure", "title": False, "data": bull_structure, "color": "rgba(38, 166, 154, 0.72)", "type": "line", "lineWidth": 1, "overlay": True})
if any(value is not None for value in bear_structure):
    plots.append({"name": "Bearish Structure", "title": False, "data": bear_structure, "color": "rgba(239, 83, 80, 0.72)", "type": "line", "lineWidth": 1, "overlay": True})
if any(value is not None for value in bull_stop):
    plots.append({"name": "Bullish Trailing Stop", "data": bull_stop, "color": "#26A69A", "type": "line", "lineWidth": 2, "overlay": True})
if any(value is not None for value in bear_stop):
    plots.append({"name": "Bearish Trailing Stop", "data": bear_stop, "color": "#EF5350", "type": "line", "lineWidth": 2, "overlay": True})

signals = []
if any(value is not None for value in bull_reversal):
    signals.append({"type": "buy", "text": "CHoCH+", "name": "Bullish Structure Reversal", "data": bull_reversal, "color": "#26A69A", "renderMode": "events"})
if any(value is not None for value in bear_reversal):
    signals.append({"type": "sell", "text": "CHoCH-", "name": "Bearish Structure Reversal", "data": bear_reversal, "color": "#EF5350", "renderMode": "events"})

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": []}
