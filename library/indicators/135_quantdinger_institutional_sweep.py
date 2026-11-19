# ============================================================
# QuantDinger Institutional Sweep
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Institutional Sweep"
my_indicator_description = (
    "Finds confirmed swing-point liquidity sweeps, applies a three-bar hold "
    "confirmation and cooldown, and keeps the latest swing reference levels."
)

# @param cooldown_period int 10 Cooldown period range=0:250:1
# @param show_swings bool true Show swing levels
# @param bullish_width int 1 Bullish sweep width range=1:5:1
# @param bullish_style str Dashed Bullish sweep style values=Solid,Dotted,Dashed
# @param bearish_width int 1 Bearish sweep width range=1:5:1
# @param bearish_style str Dashed Bearish sweep style values=Solid,Dotted,Dashed

cooldown_period = min(250, max(0, int(params.get("cooldown_period", 10))))
show_swings = bool(params.get("show_swings", True))
bullish_width = min(5, max(1, int(params.get("bullish_width", 1))))
bullish_style = str(params.get("bullish_style", "Dashed"))
bearish_width = min(5, max(1, int(params.get("bearish_width", 1))))
bearish_style = str(params.get("bearish_style", "Dashed"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

left_bars = 20
right_bars = 20
pivot_high = np.full(n, np.nan, dtype=float)
pivot_low = np.full(n, np.nan, dtype=float)
pivot_high_index = np.full(n, -1, dtype=int)
pivot_low_index = np.full(n, -1, dtype=int)
for confirmation_index in range(left_bars + right_bars, n):
    candidate_index = confirmation_index - right_bars
    first_index = candidate_index - left_bars
    last_index = candidate_index + right_bars + 1
    if high[candidate_index] >= float(np.max(high[first_index:last_index])):
        pivot_high[confirmation_index] = float(high[candidate_index])
        pivot_high_index[confirmation_index] = candidate_index
    if low[candidate_index] <= float(np.min(low[first_index:last_index])):
        pivot_low[confirmation_index] = float(low[candidate_index])
        pivot_low_index[confirmation_index] = candidate_index

held_high = np.full(n, np.nan, dtype=float)
held_low = np.full(n, np.nan, dtype=float)
held_high_index = np.full(n, -1, dtype=int)
held_low_index = np.full(n, -1, dtype=int)
last_high = np.nan
last_low = np.nan
last_high_index = -1
last_low_index = -1
for index in range(n):
    if np.isfinite(pivot_high[index]):
        last_high = float(pivot_high[index])
        last_high_index = int(pivot_high_index[index])
    if np.isfinite(pivot_low[index]):
        last_low = float(pivot_low[index])
        last_low_index = int(pivot_low_index[index])
    held_high[index] = last_high
    held_low[index] = last_low
    held_high_index[index] = last_high_index
    held_low_index[index] = last_low_index

bullish_sweep = np.zeros(n, dtype=bool)
bearish_sweep = np.zeros(n, dtype=bool)
for index in range(left_bars - 1, n):
    first_index = index - left_bars + 1
    if np.isfinite(held_low[index]):
        rolling_low = float(np.min(low[first_index:index + 1]))
        rolling_close_low = float(np.min(close[first_index:index + 1]))
        bullish_sweep[index] = (
            low[index] < held_low[index]
            and close[index] > held_low[index]
            and open_price[index] > held_low[index]
            and low[index] <= rolling_low
            and rolling_close_low >= held_low[index]
        )
    if np.isfinite(held_high[index]):
        rolling_high = float(np.max(high[first_index:index + 1]))
        rolling_close_high = float(np.max(close[first_index:index + 1]))
        bearish_sweep[index] = (
            high[index] > held_high[index]
            and close[index] < held_high[index]
            and open_price[index] < held_high[index]
            and high[index] >= rolling_high
            and rolling_close_high <= held_high[index]
        )

bullish_confirmed = np.zeros(n, dtype=bool)
bearish_confirmed = np.zeros(n, dtype=bool)
last_bullish_signal = 0
last_bearish_signal = 0
for index in range(3, n):
    bullish_confirmed[index] = (
        bullish_sweep[index - 3]
        and np.isfinite(held_low[index])
        and np.isfinite(held_low[index - 1])
        and np.isfinite(held_low[index - 2])
        and close[index] > held_low[index]
        and close[index - 1] > held_low[index - 1]
        and close[index - 2] > held_low[index - 2]
        and index >= last_bullish_signal + cooldown_period
    )
    if bullish_confirmed[index]:
        last_bullish_signal = index

    bearish_confirmed[index] = (
        bearish_sweep[index - 3]
        and np.isfinite(held_high[index])
        and np.isfinite(held_high[index - 1])
        and np.isfinite(held_high[index - 2])
        and close[index] < held_high[index]
        and close[index - 1] < held_high[index - 1]
        and close[index - 2] < held_high[index - 2]
        and index >= last_bearish_signal + cooldown_period
    )
    if bearish_confirmed[index]:
        last_bearish_signal = index

sweep_layers = []

def append_styled_line(target, start_index, end_index, price, color, width, style):
    first = max(0, int(start_index))
    last = max(first, int(end_index))
    if style != "Dotted":
        target.append(
            {
                "type": "line",
                "startIndex": first,
                "endIndex": last,
                "y1": float(price),
                "y2": float(price),
                "color": color,
                "lineWidth": int(width),
                "dashed": style == "Dashed",
            }
        )
        return
    dot_index = first
    while dot_index <= last:
        target.append(
            {
                "type": "line",
                "startIndex": int(dot_index),
                "endIndex": int(min(dot_index + 1, last)),
                "y1": float(price),
                "y2": float(price),
                "color": color,
                "lineWidth": int(width),
                "dashed": False,
            }
        )
        dot_index += 3

for index in range(n):
    if bullish_confirmed[index] and held_low_index[index] >= 0:
        append_styled_line(
            sweep_layers,
            held_low_index[index],
            index - 3,
            held_low[index],
            "#089981",
            bullish_width,
            bullish_style,
        )
    if bearish_confirmed[index] and held_high_index[index] >= 0:
        append_styled_line(
            sweep_layers,
            held_high_index[index],
            index - 3,
            held_high[index],
            "#7A1D2D",
            bearish_width,
            bearish_style,
        )

swing_high = None
swing_low = None
swing_high_labels = []
swing_low_labels = []
swing_high_value = np.nan
swing_low_value = np.nan
swing_high_counter = 0
swing_low_counter = 0
checking_high = False
checking_low = False

for index in range(n):
    if np.isfinite(swing_low_value) and checking_low and high[index] < swing_low_value:
        swing_low_counter += 1
    if np.isfinite(swing_high_value) and checking_high and low[index] > swing_high_value:
        swing_high_counter += 1

    if index > 0 and np.isfinite(swing_low_value) and not checking_low:
        crossed_under = close[index] < swing_low_value and close[index - 1] >= swing_low_value
        if crossed_under:
            checking_low = True
            swing_low_counter = 1
    if index > 0 and np.isfinite(swing_high_value) and not checking_high:
        crossed_over = close[index] > swing_high_value and close[index - 1] <= swing_high_value
        if crossed_over:
            checking_high = True
            swing_high_counter = 1

    if swing_low_counter == 5 and checking_low:
        checking_low = False
        if swing_low is not None:
            swing_low["endIndex"] = max(swing_low["startIndex"], index - 4)
            swing_low["extendBars"] = 0
    if swing_high_counter == 5 and checking_high:
        checking_high = False
        if swing_high is not None:
            swing_high["endIndex"] = max(swing_high["startIndex"], index - 4)
            swing_high["extendBars"] = 0

    high_pivot_bar = int(pivot_high_index[index])
    if show_swings and high_pivot_bar >= 0 and not bearish_sweep[high_pivot_bar]:
        swing_high_value = float(high[high_pivot_bar])
        swing_high = {
            "type": "line",
            "startIndex": high_pivot_bar,
            "endIndex": max(high_pivot_bar, n - 1),
            "extendBars": 5,
            "y1": swing_high_value,
            "y2": swing_high_value,
            "color": "#94A3B8",
            "lineWidth": 2,
            "dashed": False,
        }
        swing_high_labels.append((high_pivot_bar, swing_high_value))
        swing_high_labels = swing_high_labels[-3:]

    low_pivot_bar = int(pivot_low_index[index])
    if show_swings and low_pivot_bar >= 0 and not bullish_sweep[low_pivot_bar]:
        swing_low_value = float(low[low_pivot_bar])
        swing_low = {
            "type": "line",
            "startIndex": low_pivot_bar,
            "endIndex": max(low_pivot_bar, n - 1),
            "extendBars": 5,
            "y1": swing_low_value,
            "y2": swing_low_value,
            "color": "#94A3B8",
            "lineWidth": 2,
            "dashed": False,
        }
        swing_low_labels.append((low_pivot_bar, swing_low_value))
        swing_low_labels = swing_low_labels[-3:]

layers = sweep_layers[-500:]
if show_swings:
    if swing_high is not None:
        layers.append(swing_high)
    if swing_low is not None:
        layers.append(swing_low)
    for label_index, label_price in swing_high_labels:
        layers.append(
            {
                "type": "label",
                "index": int(label_index),
                "price": float(label_price),
                "text": "Swing H",
                "color": "rgba(0, 0, 0, 0.0)",
                "textColor": "#94A3B8",
                "side": "above",
            }
        )
    for label_index, label_price in swing_low_labels:
        layers.append(
            {
                "type": "label",
                "index": int(label_index),
                "price": float(label_price),
                "text": "Swing L",
                "color": "rgba(0, 0, 0, 0.0)",
                "textColor": "#94A3B8",
                "side": "below",
            }
        )

bullish_data = [None for _ in range(n)]
bearish_data = [None for _ in range(n)]
for index in range(3, n):
    sweep_index = index - 3
    if bullish_confirmed[index]:
        bullish_data[sweep_index] = {
            "value": float(low[sweep_index]),
            "text": "Sweep",
            "color": "#089981",
        }
    if bearish_confirmed[index]:
        bearish_data[sweep_index] = {
            "value": float(high[sweep_index]),
            "text": "Sweep",
            "color": "#7A1D2D",
        }

plots = [
    {
        "name": "Price Reference",
        "title": False,
        "data": [float(value) for value in close],
        "color": "rgba(255, 255, 255, 0.01)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    }
]

signals = [
    {"type": "buy", "text": "Sweep", "name": "Bullish Sweep", "data": bullish_data, "color": "#089981", "renderMode": "events"},
    {"type": "sell", "text": "Sweep", "name": "Bearish Sweep", "data": bearish_data, "color": "#7A1D2D", "renderMode": "events"},
]

signals = [signal for signal in signals if any(item is not None for item in signal["data"])]

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers}
