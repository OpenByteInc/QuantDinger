# ============================================================
# QuantDinger Cup Finder
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Cup Finder"
my_indicator_description = (
    "Searches completed breakouts for rounded bullish or bearish cup shapes, "
    "scores candle containment inside the fitted channel, and marks valid cups."
)

# @param lookback int 150 Bars searched for a completed cup range=30:250:1
# @param width_ratio float 5.0 Cup channel width ratio range=1:20:0.5
# @param breakout_source str Close Breakout source values=Close,High/Low
# @param include_rate float 75.0 Minimum contained-bar percent range=10:100:5
# @param show_channel bool false Show fitted cup channel boundaries
# @param show_labels bool true Show cup breakout labels
# @param remove_old_cups bool false Keep only the latest detected cup
# @param latest_label_only bool false Keep only the latest cup label

lookback = min(250, max(30, int(params.get("lookback", 150))))
width_ratio = min(20.0, max(1.0, float(params.get("width_ratio", 5.0))))
breakout_source = str(params.get("breakout_source", "Close"))
include_rate = min(100.0, max(10.0, float(params.get("include_rate", 75.0)))) / 100.0
show_channel = bool(params.get("show_channel", False))
show_labels = bool(params.get("show_labels", True))
remove_old_cups = bool(params.get("remove_old_cups", False))
latest_label_only = bool(params.get("latest_label_only", False))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

highest_bar_30 = np.zeros(n, dtype=bool)
lowest_bar_30 = np.zeros(n, dtype=bool)
for index in range(n):
    start = max(0, index - 29)
    highest_bar_30[index] = high[index] >= float(np.max(high[start:index + 1]))
    lowest_bar_30[index] = low[index] <= float(np.min(low[start:index + 1]))

detected_cups = []
included_percent = np.full(n, np.nan, dtype=float)
bullish_cup = np.zeros(n, dtype=bool)
bearish_cup = np.zeros(n, dtype=bool)

for index in range(1, n):
    max_distance = min(lookback, index)
    if max_distance < 20:
        continue

    rolling_start = max(0, index - 291)
    width = (
        float(np.max(high[rolling_start:index + 1]))
        - float(np.min(low[rolling_start:index + 1]))
    ) / 100.0
    if width <= 0.0:
        continue
    channel_width = width * width_ratio

    bullish_source = float(close[index]) if breakout_source == "Close" else float(high[index])
    bearish_source = float(close[index]) if breakout_source == "Close" else float(low[index])

    selected_distance = 0
    selected_level = 0.0
    bullish = False
    latest_high = 0.0

    for distance in range(1, max_distance + 1):
        past_index = index - distance
        latest_high = max(latest_high, float(high[past_index]))
        if bullish_source <= latest_high:
            break
        if (
            distance >= 20
            and bullish_source > high[past_index]
            and close[index - 1] < high[past_index]
            and high[past_index] >= latest_high
            and highest_bar_30[past_index]
        ):
            selected_level = float(high[past_index])
            selected_distance = distance
            bullish = True

    if selected_distance == 0:
        latest_low = float(np.max(high[rolling_start:index + 1]))
        for distance in range(1, max_distance + 1):
            past_index = index - distance
            latest_low = min(latest_low, float(low[past_index]))
            if bearish_source >= latest_low:
                break
            if (
                distance >= 20
                and bearish_source < low[past_index]
                and close[index - 1] > low[past_index]
                and low[past_index] <= latest_low
                and lowest_bar_30[past_index]
            ):
                selected_level = float(low[past_index])
                selected_distance = distance
                bullish = False

    if selected_distance == 0:
        continue

    radius = selected_distance / 2.0
    squared_radius = radius * radius
    half_count = (selected_distance - (selected_distance % 2)) // 2
    levels = []

    if selected_distance % 2 == 0:
        center_offset = np.sqrt(max(squared_radius, 0.0)) * width
        levels.append(
            selected_level - center_offset
            if bullish
            else selected_level + center_offset
        )

    for offset in range(1, half_count + 1):
        vertical = np.sqrt(max(squared_radius - offset * offset, 0.0)) * width
        value = selected_level - vertical if bullish else selected_level + vertical
        levels.append(float(value))
        levels.insert(0, float(value))

    if selected_distance % 2 == 1:
        levels.append(float(selected_level))
        levels.insert(0, float(selected_level))

    if len(levels) != selected_distance + 1:
        continue

    contained = 0
    for offset, level in enumerate(levels):
        candle_index = index - offset
        overlap = min(level + channel_width, float(high[candle_index])) - max(
            level - channel_width,
            float(low[candle_index]),
        )
        if overlap > 0.0:
            contained += 1

    containment = contained / float(selected_distance + 1)
    if containment < include_rate:
        continue

    included_percent[index] = float(containment * 100.0)
    bullish_cup[index] = bool(bullish)
    bearish_cup[index] = bool(not bullish)
    detected_cups.append({
        "start": index - selected_distance,
        "end": index,
        "levels": [float(value) for value in levels],
        "channel_width": float(channel_width),
        "bullish": bullish,
        "containment": float(containment),
        "radius_value": float(width * selected_distance / 2.0),
    })

if remove_old_cups and detected_cups:
    display_cups = [detected_cups[-1]]
else:
    display_cups = detected_cups[-12:]

plots = [
    {
        "name": "Price Reference",
        "title": False,
        "data": [float(value) for value in close],
        "color": "rgba(255, 255, 255, 0.01)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    },
]

signals = []
for number, cup in enumerate(display_cups, start=1):
    curve = [None for _ in range(n)]
    rim = [None for _ in range(n)]
    upper_channel = [None for _ in range(n)]
    lower_channel = [None for _ in range(n)]

    for offset, level in enumerate(cup["levels"]):
        item = cup["start"] + offset
        curve[item] = float(level)
        if show_channel:
            upper_channel[item] = float(level + cup["channel_width"])
            lower_channel[item] = float(level - cup["channel_width"])

    rim_value = float(cup["levels"][0])
    for item in range(cup["start"] + 1, cup["end"] + 1):
        rim[item] = rim_value

    color = "#2ECC71" if cup["bullish"] else "#EF5350"
    plots.append({
        "name": f"Cup {number}",
        "title": False,
        "data": curve,
        "color": color,
        "type": "line",
        "lineWidth": 3,
        "overlay": True,
    })
    plots.append({
        "name": f"Cup Rim {number}",
        "title": False,
        "data": rim,
        "color": color,
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    })

    if show_channel:
        plots.append({
            "name": f"Cup Upper {number}",
            "title": False,
            "data": upper_channel,
            "color": "rgba(46, 204, 113, 0.48)" if cup["bullish"] else "rgba(239, 83, 80, 0.48)",
            "type": "line",
            "lineWidth": 1,
            "overlay": True,
        })
        plots.append({
            "name": f"Cup Lower {number}",
            "title": False,
            "data": lower_channel,
            "color": "rgba(46, 204, 113, 0.48)" if cup["bullish"] else "rgba(239, 83, 80, 0.48)",
            "type": "line",
            "lineWidth": 1,
            "overlay": True,
        })

    if show_labels and (not latest_label_only or number == len(display_cups)):
        marker = [None for _ in range(n)]
        marker[cup["end"]] = float(cup["levels"][-1])
        label_text = "Cup+" if cup["bullish"] else "Cup-"
        signals.append({
            "type": "buy" if cup["bullish"] else "sell",
            "text": label_text,
            "name": (
                f"{label_text} {cup['containment'] * 100.0:.0f}% "
                f"R={cup['radius_value']:.2f}"
            ),
            "data": marker,
            "color": color,
            "renderMode": "events",
        })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "includedPercent": [float(value) if np.isfinite(value) else None for value in included_percent],
        "bullishCup": bullish_cup.tolist(),
        "bearishCup": bearish_cup.tolist(),
        "detectedCupCount": int(len(detected_cups)),
    },
}
