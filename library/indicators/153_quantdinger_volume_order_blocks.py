# ============================================================
# QuantDinger Volume Order Blocks
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; order-block annotations do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Volume Order Blocks"
my_indicator_description = (
    "Builds bullish and bearish order blocks from fast/slow EMA transitions, removes crossed "
    "or overlapping levels, and labels each surviving block by its relative captured volume."
)

# @param sensitivity int 5 Detection sensitivity range=1:100:1
# @param show_midline bool true Show block midline
# @param trend_shadow bool true Show EMA trend shadow
# @param bullish_color str #26BA9F Bullish block color
# @param bearish_color str #6626BA Bearish block color

sensitivity = min(100, max(1, int(params.get("sensitivity", 5))))
slow_length = sensitivity + 13
show_midline = bool(params.get("show_midline", True))
trend_shadow = bool(params.get("trend_shadow", True))
bullish_color = str(params.get("bullish_color", "#26BA9F"))
bearish_color = str(params.get("bearish_color", "#6626BA"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)

def pine_ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    result[length - 1] = float(np.mean(values[:length]))
    alpha = 2.0 / (length + 1.0)
    for index in range(length, len(values)):
        result[index] = alpha * values[index] + (1.0 - alpha) * result[index - 1]
    return result

def wilder(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    result[length - 1] = float(np.mean(values[:length]))
    for index in range(length, len(values)):
        result[index] = (result[index - 1] * (length - 1) + values[index]) / length
    return result

def rgba(color, opacity):
    value = str(color).strip()
    if value.startswith("#") and len(value) in (4, 7):
        if len(value) == 4:
            red = int(value[1] * 2, 16)
            green = int(value[2] * 2, 16)
            blue = int(value[3] * 2, 16)
        else:
            red = int(value[1:3], 16)
            green = int(value[3:5], 16)
            blue = int(value[5:7], 16)
        return f"rgba({red},{green},{blue},{min(1.0, max(0.0, opacity)):.3f})"
    return value

fast_ema = pine_ema(close, sensitivity)
slow_ema = pine_ema(close, slow_length)
ema_difference = slow_ema - fast_ema

true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
atr_200 = wilder(true_range, 200)
atr_high = pd.Series(atr_200).rolling(200, min_periods=200).max().to_numpy(dtype=float)
overlap_distance = atr_high * 3.0
minimum_height = atr_high

rolling_low = pd.Series(low).rolling(slow_length, min_periods=slow_length).min().to_numpy(dtype=float)
rolling_high = pd.Series(high).rolling(slow_length, min_periods=slow_length).max().to_numpy(dtype=float)

bullish_levels = []
bearish_levels = []

def clean_levels(levels, current_close, distance, bullish):
    if levels and np.isfinite(distance):
        for item_index in range(1, len(levels)):
            current = levels[item_index]
            previous = levels[item_index - 1]
            if current["active"] and previous["active"] and abs(current["mid"] - previous["mid"]) < distance:
                previous["active"] = False
        for current in levels:
            if current["active"]:
                crossed = current_close < current["lower"] if bullish else current_close > current["upper"]
                if crossed:
                    current["active"] = False
    if len(levels) > 15:
        levels.pop(0)

for index in range(1, n):
    cross_up = (
        np.isfinite(fast_ema[index])
        and np.isfinite(slow_ema[index])
        and np.isfinite(fast_ema[index - 1])
        and np.isfinite(slow_ema[index - 1])
        and fast_ema[index] > slow_ema[index]
        and fast_ema[index - 1] <= slow_ema[index - 1]
    )
    cross_down = (
        np.isfinite(fast_ema[index])
        and np.isfinite(slow_ema[index])
        and np.isfinite(fast_ema[index - 1])
        and np.isfinite(slow_ema[index - 1])
        and fast_ema[index] < slow_ema[index]
        and fast_ema[index - 1] >= slow_ema[index - 1]
    )

    if cross_up and np.isfinite(rolling_low[index]) and np.isfinite(minimum_height[index]):
        for offset in range(1, slow_length + 1):
            origin = index - offset
            if origin < 0:
                break
            if low[origin] == rolling_low[index]:
                captured_volume = float(np.sum(volume[origin:index + 1]))
                upper = float(min(open_price[origin], close[origin]))
                upper = max(upper, float(rolling_low[index] + minimum_height[index] * 0.5))
                lower = float(rolling_low[index])
                bullish_levels.append(
                    {"origin": origin, "upper": upper, "lower": lower, "mid": (upper + lower) / 2.0, "volume": captured_volume, "active": True}
                )

    if cross_down and np.isfinite(rolling_high[index]) and np.isfinite(minimum_height[index]):
        for offset in range(1, slow_length + 1):
            origin = index - offset
            if origin < 0:
                break
            if high[origin] == rolling_high[index]:
                captured_volume = float(np.sum(volume[origin:index + 1]))
                lower = float(max(open_price[origin], close[origin]))
                lower = min(lower, float(rolling_high[index] - minimum_height[index] * 0.5))
                upper = float(rolling_high[index])
                bearish_levels.append(
                    {"origin": origin, "upper": upper, "lower": lower, "mid": (upper + lower) / 2.0, "volume": captured_volume, "active": True}
                )

    clean_levels(bullish_levels, close[index], overlap_distance[index], True)
    clean_levels(bearish_levels, close[index], overlap_distance[index], False)

bullish_active = [item for item in bullish_levels if item["active"] and np.isfinite(item["mid"])]
bearish_active = [item for item in bearish_levels if item["active"] and np.isfinite(item["mid"])]
bullish_volume_total = sum(item["volume"] for item in bullish_active)
bearish_volume_total = sum(item["volume"] for item in bearish_active)

layers = []

def add_block(block, color, total_volume):
    end_index = max(0, n - 1)
    layers.append(
        {
            "type": "zone",
            "startIndex": int(block["origin"]),
            "endIndex": end_index,
            "top": float(block["upper"]),
            "bottom": float(block["lower"]),
            "fillColor": rgba(color, 0.10),
            "borderColor": "rgba(0,0,0,0)",
            "borderSize": 0,
        }
    )
    for price in (block["upper"], block["lower"]):
        layers.append(
            {
                "type": "line",
                "startIndex": int(block["origin"]),
                "endIndex": end_index,
                "y1": float(price),
                "y2": float(price),
                "color": color,
                "lineWidth": 2,
            }
        )
    if show_midline:
        layers.append(
            {
                "type": "line",
                "startIndex": int(block["origin"]),
                "endIndex": end_index,
                "y1": float(block["mid"]),
                "y2": float(block["mid"]),
                "color": color,
                "lineWidth": 1,
                "dashed": True,
            }
        )
    share = block["volume"] / total_volume * 100.0 if total_volume > 0.0 else 0.0
    layers.append(
        {
            "type": "label",
            "index": end_index,
            "price": float(block["mid"]),
            "text": f"⮜ {share:.1f}% ({block['volume']:.0f})",
            "color": "rgba(0,0,0,0)",
            "textColor": "#D0D0D0",
            "side": "left",
        }
    )

for block in bullish_active:
    add_block(block, bullish_color, bullish_volume_total)
for block in bearish_active:
    add_block(block, bearish_color, bearish_volume_total)

positive_max = pd.Series(ema_difference).rolling(100, min_periods=1).max().to_numpy(dtype=float)
negative_min = pd.Series(ema_difference).rolling(100, min_periods=1).min().to_numpy(dtype=float)
fast_shadow = []
slow_shadow = []
for index in range(n):
    if not trend_shadow or not np.isfinite(fast_ema[index]) or not np.isfinite(slow_ema[index]) or not np.isfinite(ema_difference[index]):
        fast_shadow.append(None)
        slow_shadow.append(None)
        continue
    if ema_difference[index] > 0.0:
        scale = positive_max[index]
        intensity = ema_difference[index] / scale if np.isfinite(scale) and scale > 0.0 else 0.0
        color = rgba(bearish_color, 0.15 + 0.70 * min(1.0, max(0.0, intensity)))
    else:
        scale = abs(negative_min[index])
        intensity = abs(ema_difference[index]) / scale if np.isfinite(scale) and scale > 0.0 else 0.0
        color = rgba(bullish_color, 0.15 + 0.70 * min(1.0, max(0.0, intensity)))
    fast_shadow.append({"value": float(fast_ema[index]), "color": color})
    slow_shadow.append({"value": float(slow_ema[index]), "color": color})

price_guide = [float(value) if np.isfinite(value) else None for value in close]
plots = [
    {"name": "Price Reference", "title": False, "data": price_guide, "color": "rgba(255,255,255,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Fast EMA Shadow", "title": False, "data": fast_shadow, "color": bullish_color, "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Slow EMA Shadow", "title": False, "data": slow_shadow, "color": bearish_color, "type": "line", "lineWidth": 1, "overlay": True},
]
plots = [plot for plot in plots if any(item is not None for item in plot["data"])]

output = {"name": my_indicator_name, "plots": plots, "signals": [], "layers": layers[-500:]}
