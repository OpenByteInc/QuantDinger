# ============================================================
# QuantDinger FVG Positioning Average
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger FVG Positioning Average"
my_indicator_description = (
    "Detects ATR-filtered fair value gaps, maintains bar-count or gap-count "
    "lookbacks, and plots the active bullish and bearish positioning averages."
)

# @param lookback int 30 FVG lookback range=1:500:1
# @param lookback_type str Bar_Count Lookback method values=Bar_Count,FVG_Count
# @param atr_multiplier float 0.25 Minimum gap ATR multiplier range=0.0:10.0:0.25
# @param show_fvgs bool true Show detected FVG boxes
# @param bull_color str #089981 Bullish color
# @param bear_color str #F23645 Bearish color
# @param show_gradient bool true Show positioning gradient areas


def clamp(value, lower, upper):
    return min(upper, max(lower, value))


def rgba(color, opacity):
    value = str(color).strip().lstrip("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    try:
        red, green, blue = int(value[:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except (ValueError, IndexError):
        red, green, blue = 128, 128, 128
    return f"rgba({red},{green},{blue},{clamp(float(opacity), 0.0, 1.0):.3f})"


def pine_sma_ignore_na(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    finite_window = []
    for index, value in enumerate(values):
        if np.isfinite(value):
            finite_window.append(float(value))
            if len(finite_window) > length:
                finite_window.pop(0)
            if len(finite_window) == length:
                result[index] = float(np.mean(finite_window))
    return result

lookback = min(500, max(1, int(params.get("lookback", 30))))
lookback_type = str(params.get("lookback_type", "Bar_Count")).replace("_", " ")
atr_multiplier = min(10.0, max(0.0, float(params.get("atr_multiplier", 0.25))))
show_fvgs = bool(params.get("show_fvgs", True))
bull_color = str(params.get("bull_color", "#089981"))
bear_color = str(params.get("bear_color", "#F23645"))
show_gradient = bool(params.get("show_gradient", True))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

true_range = np.zeros(n, dtype=float)
if n > 0:
    true_range[0] = high[0] - low[0]
for index in range(1, n):
    true_range[index] = max(
        high[index] - low[index],
        abs(high[index] - close[index - 1]),
        abs(low[index] - close[index - 1]),
    )

atr_200 = np.full(n, np.nan, dtype=float)
if n >= 200:
    atr_200[199] = float(np.mean(true_range[:200]))
    for index in range(200, n):
        atr_200[index] = (atr_200[index - 1] * 199.0 + true_range[index]) / 200.0

cumulative_range = np.cumsum(high - low) / np.arange(1, n + 1, dtype=float) if n else np.asarray([], dtype=float)
atr_threshold = np.where(np.isfinite(atr_200), atr_200 * atr_multiplier, cumulative_range)

bullish_gap = np.zeros(n, dtype=bool)
bearish_gap = np.zeros(n, dtype=bool)
bullish_top = np.full(n, np.nan, dtype=float)
bullish_bottom = np.full(n, np.nan, dtype=float)
bearish_top = np.full(n, np.nan, dtype=float)
bearish_bottom = np.full(n, np.nan, dtype=float)

for index in range(2, n):
    bullish_gap[index] = (
        low[index] > high[index - 2]
        and close[index - 1] > high[index - 2]
        and low[index] - high[index - 2] > atr_threshold[index]
    )
    bearish_gap[index] = (
        high[index] < low[index - 2]
        and close[index - 1] < low[index - 2]
        and low[index - 2] - high[index] > atr_threshold[index]
    )
    if bullish_gap[index]:
        bullish_top[index] = low[index]
        bullish_bottom[index] = high[index - 2]
    if bearish_gap[index]:
        bearish_top[index] = low[index - 2]
        bearish_bottom[index] = high[index]

up_average = np.full(n, np.nan, dtype=float)
down_average = np.full(n, np.nan, dtype=float)
up_active_count = np.zeros(n, dtype=int)
down_active_count = np.zeros(n, dtype=int)
active_up = []
active_down = []

for index in range(n):
    if bullish_gap[index]:
        active_up.append(
            {
                "left": index - 2,
                "bottom": float(bullish_bottom[index]),
            }
        )
    if bearish_gap[index]:
        active_down.append(
            {
                "left": index - 2,
                "top": float(bearish_top[index]),
            }
        )

    if lookback_type == "FVG Count":
        while len(active_up) > lookback:
            active_up.pop(0)
        while len(active_down) > lookback:
            active_down.pop(0)
    else:
        cutoff = index - lookback
        active_up = [gap for gap in active_up if gap["left"] >= cutoff]
        active_down = [gap for gap in active_down if gap["left"] >= cutoff]

    if active_up:
        up_average[index] = float(np.mean([gap["bottom"] for gap in active_up]))
    if active_down:
        down_average[index] = float(np.mean([gap["top"] for gap in active_down]))
    up_active_count[index] = len(active_up)
    down_active_count[index] = len(active_down)

highest_five = pd.Series(high, dtype=float).rolling(5, min_periods=1).max().to_numpy(dtype=float)
lowest_five = pd.Series(low, dtype=float).rolling(5, min_periods=1).min().to_numpy(dtype=float)
mid_body = (open_price + close) / 2.0

up_line = []
down_line = []
for index in range(n):
    if np.isfinite(up_average[index]):
        up_line.append(
            {
                "value": float(up_average[index]),
                "color": rgba(bull_color, 0.0) if highest_five[index] < up_average[index] else bull_color,
            }
        )
    else:
        up_line.append(None)
    if np.isfinite(down_average[index]):
        down_line.append(
            {
                "value": float(down_average[index]),
                "color": rgba(bear_color, 0.0) if lowest_five[index] > down_average[index] else bear_color,
            }
        )
    else:
        down_line.append(None)

up_sma = pine_sma_ignore_na(up_average, 10)
down_sma = pine_sma_ignore_na(down_average, 10)
up_gradient = []
down_gradient = []
for index in range(n):
    if (
        show_gradient
        and np.isfinite(up_average[index])
        and np.isfinite(up_sma[index])
        and mid_body[index] > up_average[index]
    ):
        upper = max(mid_body[index], up_average[index])
        lower = min(up_sma[index], up_average[index])
        up_gradient.append(
            {
                "upper": float(max(upper, lower)),
                "lower": float(min(upper, lower)),
                "color": rgba(bull_color, 0.16),
            }
        )
    else:
        up_gradient.append(None)

    if (
        show_gradient
        and np.isfinite(down_average[index])
        and np.isfinite(down_sma[index])
        and mid_body[index] < down_average[index]
    ):
        upper = max(down_sma[index], down_average[index])
        lower = min(mid_body[index], down_average[index])
        down_gradient.append(
            {
                "upper": float(max(upper, lower)),
                "lower": float(min(upper, lower)),
                "color": rgba(bear_color, 0.16),
            }
        )
    else:
        down_gradient.append(None)

bullish_slots = [[None for _ in range(n)] for _ in range(3)]
bearish_slots = [[None for _ in range(n)] for _ in range(3)]
if show_fvgs:
    for detection_index in range(2, n):
        if bullish_gap[detection_index]:
            slot = detection_index % 3
            for index in range(detection_index - 2, detection_index + 1):
                bullish_slots[slot][index] = {
                    "upper": float(bullish_top[detection_index]),
                    "lower": float(bullish_bottom[detection_index]),
                    "color": rgba(bull_color, 0.50),
                }
        if bearish_gap[detection_index]:
            slot = detection_index % 3
            for index in range(detection_index - 2, detection_index + 1):
                bearish_slots[slot][index] = {
                    "upper": float(bearish_top[detection_index]),
                    "lower": float(bearish_bottom[detection_index]),
                    "color": rgba(bear_color, 0.50),
                }

plots = [
    {"name": "Bull Average", "data": up_line, "color": bull_color, "type": "line", "style": "linebr", "connectNulls": False, "lineWidth": 2, "overlay": True},
    {"name": "Bear Average", "data": down_line, "color": bear_color, "type": "line", "style": "linebr", "connectNulls": False, "lineWidth": 2, "overlay": True},
    {"name": "Bull Position Gradient", "title": False, "data": up_gradient, "type": "cloud", "overlay": True},
    {"name": "Bear Position Gradient", "title": False, "data": down_gradient, "type": "cloud", "overlay": True},
]

for slot in range(3):
    plots.extend(
        [
            {"name": f"Bull FVG {slot + 1}", "title": False, "data": bullish_slots[slot], "type": "cloud", "overlay": True},
            {"name": f"Bear FVG {slot + 1}", "title": False, "data": bearish_slots[slot], "type": "cloud", "overlay": True},
        ]
    )

plots = [
    plot
    for plot in plots
    if any(item is not None for item in plot.get("data", []))
]
plots.append(
    {
        "name": "Price Reference",
        "title": False,
        "data": [float(value) for value in close],
        "color": "rgba(255, 255, 255, 0.01)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    }
)

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
        "trueRange": [float(value) if np.isfinite(value) else None for value in true_range],
        "atr200": [float(value) if np.isfinite(value) else None for value in atr_200],
        "cumulativeRange": [float(value) if np.isfinite(value) else None for value in cumulative_range],
        "atrThreshold": [float(value) if np.isfinite(value) else None for value in atr_threshold],
        "bullishGap": bullish_gap.tolist(), "bearishGap": bearish_gap.tolist(),
        "bullishTop": [float(value) if np.isfinite(value) else None for value in bullish_top],
        "bullishBottom": [float(value) if np.isfinite(value) else None for value in bullish_bottom],
        "bearishTop": [float(value) if np.isfinite(value) else None for value in bearish_top],
        "bearishBottom": [float(value) if np.isfinite(value) else None for value in bearish_bottom],
        "upAverage": [float(value) if np.isfinite(value) else None for value in up_average],
        "downAverage": [float(value) if np.isfinite(value) else None for value in down_average],
        "upActiveCount": up_active_count.tolist(), "downActiveCount": down_active_count.tolist(),
        "highestFive": [float(value) if np.isfinite(value) else None for value in highest_five],
        "lowestFive": [float(value) if np.isfinite(value) else None for value in lowest_five],
        "upSma": [float(value) if np.isfinite(value) else None for value in up_sma],
        "downSma": [float(value) if np.isfinite(value) else None for value in down_sma],
    },
}
