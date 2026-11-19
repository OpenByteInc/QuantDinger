# ============================================================
# QuantDinger High-Volume Support Resistance
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger High-Volume Support Resistance"
my_indicator_description = (
    "Creates ATR-width support and resistance boxes from volume-filtered price "
    "pivots, tracks breaks and role reversals, and marks holds and first breaks."
)

# @param pivot_lookback int 20 Pivot lookback range=1:100:1
# @param volume_filter_length int 2 Delta-volume filter length range=1:100:1
# @param box_width float 1.0 ATR box width multiplier range=0.0:1000.0:0.1

pivot_lookback = min(100, max(1, int(params.get("pivot_lookback", 20))))
volume_filter_length = min(100, max(1, int(params.get("volume_filter_length", 2))))
box_width = min(1000.0, max(0.0, float(params.get("box_width", 1.0))))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
time_values = df["time"].astype(float).to_numpy(dtype=float)
n = len(df)

signed_volume = np.zeros(n, dtype=float)
buy_volume = True
for index in range(n):
    if close[index] > open_price[index]:
        buy_volume = True
    elif close[index] < open_price[index]:
        buy_volume = False
    signed_volume[index] = float(volume[index] if buy_volume else -volume[index])

volume_high = (
    pd.Series(signed_volume / 2.5, dtype=float)
    .rolling(volume_filter_length, min_periods=volume_filter_length)
    .max()
    .to_numpy(dtype=float)
)
volume_low = (
    pd.Series(signed_volume / 2.5, dtype=float)
    .rolling(volume_filter_length, min_periods=volume_filter_length)
    .min()
    .to_numpy(dtype=float)
)
positive_peak = (
    pd.Series(np.maximum(signed_volume, 0.0), dtype=float)
    .rolling(25, min_periods=1)
    .max()
    .to_numpy(dtype=float)
)
negative_peak = (
    pd.Series(np.minimum(signed_volume, 0.0), dtype=float)
    .rolling(25, min_periods=1)
    .min()
    .to_numpy(dtype=float)
)

true_range = np.zeros(n, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(
            high[index] - low[index],
            abs(high[index] - close[index - 1]),
            abs(low[index] - close[index - 1]),
        )
atr = np.full(n, np.nan, dtype=float)
atr_length = 200
if n >= atr_length:
    atr[atr_length - 1] = float(np.mean(true_range[:atr_length]))
    for index in range(atr_length, n):
        atr[index] = ((atr[index - 1] * (atr_length - 1)) + true_range[index]) / atr_length

pivot_high = np.full(n, np.nan, dtype=float)
pivot_low = np.full(n, np.nan, dtype=float)
for confirmation_index in range(2 * pivot_lookback, n):
    pivot_index = confirmation_index - pivot_lookback
    first_index = pivot_index - pivot_lookback
    last_index = pivot_index + pivot_lookback + 1
    window = close[first_index:last_index]
    if close[pivot_index] >= float(np.max(window)):
        pivot_high[confirmation_index] = float(close[pivot_index])
    if close[pivot_index] <= float(np.min(window)):
        pivot_low[confirmation_index] = float(close[pivot_index])

support_blocks = []
resistance_blocks = []
active_support = None
active_resistance = None
support_level = np.nan
support_bottom = np.nan
resistance_level = np.nan
resistance_top = np.nan
support_series = np.full(n, np.nan, dtype=float)
resistance_series = np.full(n, np.nan, dtype=float)

resistance_holds = np.zeros(n, dtype=bool)
support_holds = np.zeros(n, dtype=bool)
breakout_resistance = np.zeros(n, dtype=bool)
breakout_support = np.zeros(n, dtype=bool)
resistance_as_support = np.zeros(n, dtype=bool)
support_as_resistance = np.zeros(n, dtype=bool)
break_labels = []
resistance_is_support = None
support_is_resistance = None
creation_order = 0

for index in range(n):
    previous_support = support_series[index - 1] if index > 0 else np.nan
    previous_resistance = resistance_series[index - 1] if index > 0 else np.nan
    previous_support_bottom = (
        active_support["bottom"] if active_support is not None else np.nan
    )
    previous_resistance_top = (
        active_resistance["top"] if active_resistance is not None else np.nan
    )

    pivot_index = index - pivot_lookback
    if (
        pivot_index >= 0
        and np.isfinite(pivot_low[index])
        and np.isfinite(atr[index])
        and np.isfinite(volume_high[index])
        and signed_volume[index] > volume_high[index]
    ):
        support_level = float(pivot_low[index])
        support_bottom = float(support_level - atr[index] * box_width)
        opacity = (
            0.70 * min(max(signed_volume[index] / positive_peak[index], 0.0), 1.0)
            if positive_peak[index] > 0.0
            else 0.0
        )
        active_support = {
            "start": int(pivot_index),
            "end": int(index),
            "top": support_level,
            "bottom": support_bottom,
            "text": f"Vol: {signed_volume[index]:.2f}",
            "fill": f"rgba(32, 202, 38, {opacity:.3f})",
            "border": "#20CA26",
            "dashed": False,
            "created": creation_order,
        }
        creation_order += 1
        support_blocks.append(active_support)

    if (
        pivot_index >= 0
        and np.isfinite(pivot_high[index])
        and np.isfinite(atr[index])
        and np.isfinite(volume_low[index])
        and signed_volume[index] < volume_low[index]
    ):
        resistance_level = float(pivot_high[index])
        resistance_top = float(resistance_level + atr[index] * box_width)
        opacity = (
            0.70 * min(max(signed_volume[index] / negative_peak[index], 0.0), 1.0)
            if negative_peak[index] < 0.0
            else 0.0
        )
        active_resistance = {
            "start": int(pivot_index),
            "end": int(index),
            "top": resistance_top,
            "bottom": resistance_level,
            "text": f"Vol: {signed_volume[index]:.2f}",
            "fill": f"rgba(233, 41, 41, {opacity:.3f})",
            "border": "#E92929",
            "dashed": False,
            "created": creation_order,
        }
        creation_order += 1
        resistance_blocks.append(active_resistance)

    if active_support is not None:
        active_support["end"] = int(index + 1)
        support_bottom = float(active_support["bottom"])
    if active_resistance is not None:
        active_resistance["end"] = int(index + 1)
        resistance_top = float(active_resistance["top"])

    support_series[index] = support_level
    resistance_series[index] = resistance_level

    if index > 0 and np.isfinite(resistance_level) and np.isfinite(previous_resistance_top):
        breakout_resistance[index] = (
            low[index] > resistance_top and low[index - 1] <= previous_resistance_top
        )
    if index > 0 and np.isfinite(resistance_level) and np.isfinite(previous_resistance):
        resistance_holds[index] = (
            high[index] < resistance_level and high[index - 1] >= previous_resistance
        )
    if index > 0 and np.isfinite(support_level) and np.isfinite(previous_support):
        support_holds[index] = (
            low[index] > support_level and low[index - 1] <= previous_support
        )
    if index > 0 and np.isfinite(support_bottom) and np.isfinite(previous_support_bottom):
        breakout_support[index] = (
            high[index] < support_bottom and high[index - 1] >= previous_support_bottom
        )

    previous_resistance_role = resistance_is_support
    previous_support_role = support_is_resistance
    resistance_as_support[index] = breakout_resistance[index] and previous_resistance_role is True
    support_as_resistance[index] = breakout_support[index] and previous_support_role is True

    if breakout_resistance[index]:
        resistance_is_support = True
        if active_resistance is not None:
            active_resistance["fill"] = "rgba(32, 202, 38, 0.20)"
            active_resistance["border"] = "#20CA26"
            active_resistance["dashed"] = True
    elif resistance_holds[index]:
        resistance_is_support = False
        if active_resistance is not None:
            active_resistance["border"] = "#E92929"
            active_resistance["dashed"] = False

    if breakout_support[index]:
        support_is_resistance = True
        if active_support is not None:
            active_support["fill"] = "rgba(233, 41, 41, 0.20)"
            active_support["border"] = "#E92929"
            active_support["dashed"] = True
    elif support_holds[index]:
        support_is_resistance = False
        if active_support is not None:
            active_support["border"] = "#20CA26"
            active_support["dashed"] = False

    marker_index = max(0, index - 1)
    if breakout_support[index] and previous_support_role is not True and index > 0:
        break_labels.append((marker_index, float(previous_support), "Break Sup", "#7E1E1E", "above"))
    if breakout_resistance[index] and previous_resistance_role is not True and index > 0:
        break_labels.append((marker_index, float(previous_resistance), "Break Res", "#2B6D2D", "below"))

positive_intervals = np.diff(time_values)
positive_intervals = positive_intervals[positive_intervals > 0.0]
interval = float(np.median(positive_intervals)) if positive_intervals.size else 0.0
all_blocks = sorted(support_blocks + resistance_blocks, key=lambda item: item["created"])[-50:]
layers = []
for block in all_blocks:
    layer = {
        "type": "zone",
        "startIndex": int(block["start"]),
        "top": float(block["top"]),
        "bottom": float(block["bottom"]),
        "fillColor": block["fill"],
        "borderColor": block["border"],
        "borderSize": 1,
        "dashed": bool(block["dashed"]),
        "text": block["text"],
        "textColor": "#D9D9D9",
    }
    if block["end"] >= n:
        layer["end"] = float(time_values[-1] + interval * (block["end"] - (n - 1)))
    else:
        layer["endIndex"] = int(block["end"])
    layers.append(layer)

for label_index, label_price, label_text, label_color, side in break_labels[-100:]:
    if np.isfinite(label_price):
        layers.append(
            {
                "type": "label",
                "index": int(label_index),
                "price": float(label_price),
                "text": label_text,
                "color": label_color,
                "textColor": "#FFFFFF",
                "side": side,
            }
        )

buy_data = [None for _ in range(n)]
sell_data = [None for _ in range(n)]
for index in range(1, n):
    marker_index = index - 1
    if support_holds[index] or resistance_as_support[index]:
        buy_data[marker_index] = {"value": float(low[marker_index]), "text": "◆", "color": "#20CA26"}
    if resistance_holds[index] or support_as_resistance[index]:
        sell_data[marker_index] = {"value": float(high[marker_index]), "text": "◆", "color": "#E92929"}

plots = [
    {"name": "Price Reference", "title": False, "data": [float(value) for value in close], "color": "rgba(255, 255, 255, 0.01)", "type": "line", "lineWidth": 1, "overlay": True}
]
signals = [
    {"type": "buy", "text": "◆", "name": "Support Hold", "data": buy_data, "color": "#20CA26", "renderMode": "events"},
    {"type": "sell", "text": "◆", "name": "Resistance Hold", "data": sell_data, "color": "#E92929", "renderMode": "events"},
]

signals = [signal for signal in signals if any(item is not None for item in signal["data"])]

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers}
