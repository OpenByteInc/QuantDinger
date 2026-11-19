# ============================================================
# QuantDinger EMA 9/21 Target Price
# Independent Python implementation for the QuantDinger runtime.
# ============================================================

my_indicator_name = "QuantDinger EMA 9/21 Target Price"
my_indicator_description = (
    "Tracks EMA 9/21 regime changes and projects two persistent targets from "
    "recent crossover averages and above/below-regime extremes."
)

# @param lookback int 500 Target statistics lookback range=1:5000:1
# @param show_target_labels bool true Show latest target labels
# @param show_regime_cloud bool true Fill the EMA regime cloud
# @param bullish_color str #00E676 Bullish color
# @param bearish_color str #F23645 Bearish color

lookback = min(5000, max(1, int(float(params.get("lookback", 500)))))
show_target_labels = bool(params.get("show_target_labels", True))
show_regime_cloud = bool(params.get("show_regime_cloud", True))
bullish_color = str(params.get("bullish_color", "#00E676"))
bearish_color = str(params.get("bearish_color", "#F23645"))

df = df.copy()
open_values = pd.to_numeric(df["open"], errors="coerce").to_numpy(dtype=float)
high_values = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype=float)
low_values = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype=float)
close_values = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
n = len(df)


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def rgba(color, alpha):
    value = str(color).strip().lstrip("#")
    if len(value) == 3:
        value = "".join(character * 2 for character in value)
    try:
        red, green, blue = int(value[:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except (ValueError, IndexError):
        red, green, blue = 128, 128, 128
    return f"rgba({red},{green},{blue},{min(1.0, max(0.0, float(alpha))):.3f})"


def ema(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if not len(source):
        return result
    alpha = 2.0 / (float(length) + 1.0)
    state = float(source[0])
    result[0] = state
    for index in range(1, len(source)):
        state = alpha * float(source[index]) + (1.0 - alpha) * state
        result[index] = state
    return result


ema_fast = ema(close_values, 9)
ema_slow = ema(close_values, 21)
bull_cross = np.zeros(n, dtype=bool)
bear_cross = np.zeros(n, dtype=bool)
above_both = (close_values >= ema_fast) & (close_values >= ema_slow)
below_both = (close_values < ema_fast) & (close_values <= ema_slow)
bearish_palette = (close_values <= ema_fast) & (close_values <= ema_slow)
for index in range(1, n):
    bull_cross[index] = ema_fast[index] > ema_slow[index] and ema_fast[index - 1] <= ema_slow[index - 1]
    bear_cross[index] = ema_slow[index] > ema_fast[index] and ema_slow[index - 1] <= ema_fast[index - 1]

ema_colors = []
regime_colors = []
for index in range(n):
    if above_both[index]:
        palette = rgba(bullish_color, 0.50)
    elif bearish_palette[index]:
        palette = rgba(bearish_color, 0.50)
    else:
        palette = "rgba(128,128,128,0.50)"
    regime_colors.append(palette)
    ema_colors.append("rgba(128,0,128,0.25)" if bull_cross[index] else "rgba(255,165,0,0.25)" if bear_cross[index] else palette)

bull_target_one_at_cross = np.full(n, np.nan, dtype=float)
bull_target_two_at_cross = np.full(n, np.nan, dtype=float)
bear_target_one_at_cross = np.full(n, np.nan, dtype=float)
bear_target_two_at_cross = np.full(n, np.nan, dtype=float)
maximum_above = np.full(n, np.nan, dtype=float)
minimum_below = np.full(n, np.nan, dtype=float)
bull_cross_average = np.full(n, np.nan, dtype=float)
bear_cross_average = np.full(n, np.nan, dtype=float)
bear_anchor_open = np.full(n, np.nan, dtype=float)

latest_bull_index = -1
latest_bear_index = -1
latest_bull_targets = None
latest_bear_targets = None
for index in range(n):
    start = max(0, index - lookback)
    if bull_cross[index]:
        bullish_values = close_values[start:index + 1][above_both[start:index + 1]]
        crossover_values = close_values[start:index + 1][bull_cross[start:index + 1]]
        if len(bullish_values) and len(crossover_values):
            maximum_above[index] = float(np.max(bullish_values))
            bull_cross_average[index] = float(np.mean(crossover_values))
            distance = maximum_above[index] - bull_cross_average[index]
            first = float(open_values[index] + distance * 0.5)
            second = float(open_values[index] + distance)
            bull_target_one_at_cross[index] = first
            bull_target_two_at_cross[index] = second
            latest_bull_index = index
            latest_bull_targets = (first, second)

    if bear_cross[index]:
        bearish_values = close_values[start:index + 1][below_both[start:index + 1]]
        crossunder_values = close_values[start:index + 1][bear_cross[start:index + 1]]
        crossover_indices = np.flatnonzero(bull_cross[start:index + 1]) + start
        if len(bearish_values) and len(crossunder_values):
            minimum_below[index] = float(np.min(bearish_values))
            bear_cross_average[index] = float(np.mean(crossunder_values))
            anchor = float(open_values[crossover_indices[-1]]) if len(crossover_indices) else 0.0
            bear_anchor_open[index] = anchor
            distance = bear_cross_average[index] - minimum_below[index]
            first = float(anchor - distance * 0.5)
            second = float(anchor - distance)
            bear_target_one_at_cross[index] = first
            bear_target_two_at_cross[index] = second
            latest_bear_index = index
            latest_bear_targets = (first, second)

bull_target_one = np.full(n, np.nan, dtype=float)
bull_target_two = np.full(n, np.nan, dtype=float)
bear_target_one = np.full(n, np.nan, dtype=float)
bear_target_two = np.full(n, np.nan, dtype=float)
active_bull_targets = latest_bull_targets
active_bear_targets = latest_bear_targets
if active_bull_targets is not None:
    bull_completed = bool(np.any(high_values[latest_bull_index:] >= active_bull_targets[1]))
    if latest_bear_index > latest_bull_index or bull_completed:
        active_bull_targets = None
if active_bear_targets is not None:
    bear_completed = bool(np.any(low_values[latest_bear_index:] <= active_bear_targets[1]))
    if latest_bull_index > latest_bear_index or bear_completed:
        active_bear_targets = None

if active_bull_targets is not None:
    start = max(0, latest_bull_index - 1)
    bull_target_one[start:] = active_bull_targets[0]
    bull_target_two[start:] = active_bull_targets[1]
if active_bear_targets is not None:
    start = max(0, latest_bear_index - 1)
    bear_target_one[start:] = active_bear_targets[0]
    bear_target_two[start:] = active_bear_targets[1]

fast_points = [
    {"value": float(ema_fast[index]), "color": ema_colors[index]}
    if np.isfinite(ema_fast[index]) else None for index in range(n)
]
slow_points = [
    {"value": float(ema_slow[index]), "color": ema_colors[index]}
    if np.isfinite(ema_slow[index]) else None for index in range(n)
]
regime_cloud = [
    {
        "upper": float(max(ema_fast[index], ema_slow[index])),
        "lower": float(min(ema_fast[index], ema_slow[index])),
        "color": regime_colors[index],
    }
    if show_regime_cloud else None for index in range(n)
]

signals = [
    {"type": "buy", "text": "EMA+", "name": "Bullish EMA Cross", "color": bullish_color, "renderMode": "events", "data": [float(low_values[index]) if bull_cross[index] else None for index in range(n)]},
    {"type": "sell", "text": "EMA-", "name": "Bearish EMA Cross", "color": bearish_color, "renderMode": "events", "data": [float(high_values[index]) if bear_cross[index] else None for index in range(n)]},
]

layers = []
if show_target_labels and active_bull_targets is not None:
    anchor = max(0, latest_bull_index - 1)
    for number, value in enumerate(active_bull_targets, start=1):
        layers.append({
            "type": "label", "index": anchor, "price": float(value),
            "text": f"Bull Target {number}\n{value:.2f}", "color": rgba(bullish_color, 0.50),
            "textColor": "#FFFFFF", "side": "center",
        })
if show_target_labels and active_bear_targets is not None:
    anchor = max(0, latest_bear_index - 1)
    for number, value in enumerate(active_bear_targets, start=1):
        layers.append({
            "type": "label", "index": anchor, "price": float(value),
            "text": f"Bear Target {number}\n{value:.2f}", "color": rgba(bearish_color, 0.50),
            "textColor": "#FFFFFF", "side": "center",
        })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "EMA 9", "data": fast_points, "color": bullish_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "EMA 21", "data": slow_points, "color": bearish_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "EMA Regime", "title": False, "data": regime_cloud, "type": "cloud", "overlay": True},
        {"name": "Bull Target 1", "title": False, "data": finite(bull_target_one), "color": rgba(bullish_color, 0.50), "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "Bull Target 2", "title": False, "data": finite(bull_target_two), "color": rgba(bullish_color, 0.50), "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "Bear Target 1", "title": False, "data": finite(bear_target_one), "color": rgba(bearish_color, 0.50), "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "Bear Target 2", "title": False, "data": finite(bear_target_two), "color": rgba(bearish_color, 0.50), "type": "line", "lineWidth": 3, "overlay": True},
    ],
    "signals": signals,
    "layers": layers,
    "tables": [],
    "bar_colors": [None for _ in range(n)],
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "ema9": finite(ema_fast), "ema21": finite(ema_slow),
        "bullCross": bull_cross.tolist(), "bearCross": bear_cross.tolist(),
        "aboveBoth": above_both.tolist(), "belowBoth": below_both.tolist(),
        "bearishPalette": bearish_palette.tolist(), "emaColors": ema_colors, "regimeColors": regime_colors,
        "maximumAbove": finite(maximum_above), "minimumBelow": finite(minimum_below),
        "bullCrossAverage": finite(bull_cross_average), "bearCrossAverage": finite(bear_cross_average),
        "bearAnchorOpen": finite(bear_anchor_open),
        "bullTargetOneAtCross": finite(bull_target_one_at_cross), "bullTargetTwoAtCross": finite(bull_target_two_at_cross),
        "bearTargetOneAtCross": finite(bear_target_one_at_cross), "bearTargetTwoAtCross": finite(bear_target_two_at_cross),
        "latestBullIndex": int(latest_bull_index), "latestBearIndex": int(latest_bear_index),
        "bullTargetOne": finite(bull_target_one), "bullTargetTwo": finite(bull_target_two),
        "bearTargetOne": finite(bear_target_one), "bearTargetTwo": finite(bear_target_two),
    },
}
