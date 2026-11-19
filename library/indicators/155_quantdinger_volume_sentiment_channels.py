# ============================================================
# QuantDinger Volume Sentiment Breakout Channels
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; breakout signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Volume Sentiment Breakout Channels"
my_indicator_description = (
    "Detects volatility-compression channels, maps signed volume sentiment across 20 price bins, "
    "and follows confirmed channel breaks with adaptive bullish or bearish trend guides."
)

# @param normalization_length int 100 Normalization length range=1:500:1
# @param detection_length int 14 Box detection length range=1:200:1
# @param strong_closes bool true Require strong closes
# @param show_bin_labels bool false Show per-bin sentiment labels
# @param bullish_color str #00FFBB Bullish color
# @param bearish_color str #FF1100 Bearish color

normalization_length = min(500, max(1, int(params.get("normalization_length", 100))))
detection_length = min(200, max(1, int(params.get("detection_length", 14))))
strong_closes = bool(params.get("strong_closes", True))
show_bin_labels = bool(params.get("show_bin_labels", False))
bullish_color = str(params.get("bullish_color", "#00FFBB"))
bearish_color = str(params.get("bearish_color", "#FF1100"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)

def wma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, length + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / denominator)
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

rolling_low = pd.Series(low).rolling(normalization_length, min_periods=normalization_length).min().to_numpy(dtype=float)
rolling_high = pd.Series(high).rolling(normalization_length, min_periods=normalization_length).max().to_numpy(dtype=float)
normalized_price = np.divide(
    close - rolling_low,
    rolling_high - rolling_low,
    out=np.full(n, np.nan, dtype=float),
    where=np.isfinite(rolling_high - rolling_low) & ((rolling_high - rolling_low) != 0.0),
)
volatility = pd.Series(normalized_price).rolling(14, min_periods=14).std(ddof=0).to_numpy(dtype=float)
upper_state = volatility
lower_state = wma(volatility, detection_length)

true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
atr = wilder(true_range, detection_length)

def crossed_above(first, second, index):
    return (
        index > 0
        and np.isfinite(first[index])
        and np.isfinite(second[index])
        and np.isfinite(first[index - 1])
        and np.isfinite(second[index - 1])
        and first[index] > second[index]
        and first[index - 1] <= second[index - 1]
    )

def profile(start_index, end_index, top, bottom, resolution=20):
    if start_index < 0 or end_index < start_index or not np.isfinite(top) or not np.isfinite(bottom) or top <= bottom:
        return []
    step = (top - bottom) / resolution
    bins = []
    for bin_index in range(resolution):
        bin_bottom = bottom + step * bin_index
        bin_top = bin_bottom + step
        total = 0.0
        signed = 0.0
        for bar_index in range(start_index, end_index + 1):
            if low[bar_index] <= bin_top and high[bar_index] >= bin_bottom:
                value = float(volume[bar_index])
                total += value
                signed += value if close[bar_index] > open_price[bar_index] else -value
        bins.append({"bottom": bin_bottom, "top": bin_top, "volume": total, "signed": signed})
    return bins

channels = []
last_lower_cross = None
bull_breaks = [None for _ in range(n)]
bear_breaks = [None for _ in range(n)]
last_bull_break = None
last_bear_break = None
bull_trailing = [None for _ in range(n)]
bear_trailing = [None for _ in range(n)]
bull_active = False
bear_active = False
previous_bull_value = np.nan
previous_bear_value = np.nan

for index in range(n):
    if crossed_above(lower_state, upper_state, index):
        last_lower_cross = index
    duration = max(index - last_lower_cross, 1) if last_lower_cross is not None else 1

    if crossed_above(upper_state, lower_state, index) and duration > 10:
        window_start = max(0, index - duration + 1)
        channel_top = float(np.max(high[window_start:index + 1]))
        channel_bottom = float(np.min(low[window_start:index + 1]))
        can_create = True
        for item in channels:
            if item["active"] and channel_top > item["bottom"] and channel_bottom < item["top"]:
                can_create = False
                break
        if can_create:
            left = max(0, index - duration)
            half_atr = float(atr[index] / 2.0) if np.isfinite(atr[index]) else 0.0
            channels.append(
                {
                    "left": left,
                    "right": index,
                    "top": channel_top,
                    "bottom": channel_bottom,
                    "edge": half_atr,
                    "active": True,
                    "break": 0,
                    "bins": profile(left, index, channel_top, channel_bottom),
                    "current_text": "",
                    "overall_text": "",
                    "text_is_upper": True,
                }
            )

    bull_break_now = False
    bear_break_now = False
    compare_price = (close[index] + open_price[index]) / 2.0 if strong_closes else close[index]
    for channel in channels:
        if not channel["active"]:
            continue
        if compare_price > channel["top"]:
            channel["active"] = False
            channel["right"] = index
            channel["break"] = 1
            bull_break_now = True
        elif compare_price < channel["bottom"]:
            channel["active"] = False
            channel["right"] = index
            channel["break"] = -1
            bear_break_now = True
        else:
            channel["right"] = index
            channel["bins"] = profile(channel["left"], index, channel["top"], channel["bottom"])
            if channel["bins"]:
                matching = next((item for item in channel["bins"] if close[index] >= item["bottom"] and close[index] <= item["top"]), None)
                max_signed = max(abs(item["signed"]) for item in channel["bins"])
                if matching is not None:
                    sentiment = matching["signed"] / max_signed * 100.0 if max_signed > 0.0 else 0.0
                    channel["current_text"] = f"Current Sentiment: {sentiment:.1f}%"
                total_signed = sum(item["signed"] for item in channel["bins"])
                channel["overall_text"] = "Overall Sentiment: Bullish" if total_signed > 0.0 else "Overall Sentiment: Bearish"
                channel["text_is_upper"] = close[index] <= (channel["top"] + channel["bottom"]) / 2.0

    if bull_break_now:
        last_bull_break = index
        bull_active = True
        minimum = float(min(low[max(0, index - 2):index + 1]))
        bull_breaks[index] = minimum
    if bear_break_now:
        last_bear_break = index
        bear_active = True
        maximum = float(max(high[max(0, index - 2):index + 1]))
        bear_breaks[index] = maximum

    if bull_active and last_bull_break is not None:
        dynamic_length = max(index - last_bull_break + 1, 1)
        start = max(0, index - dynamic_length + 1)
        value = float(np.mean(low[start:index + 1]))
        bull_trailing[index] = value
        if index > last_bull_break and np.isfinite(previous_bull_value) and close[index] < value and close[index - 1] >= previous_bull_value:
            bull_active = False
            bull_trailing[index] = None
        previous_bull_value = value
    if bear_active and last_bear_break is not None:
        dynamic_length = max(index - last_bear_break + 1, 1)
        start = max(0, index - dynamic_length + 1)
        value = float(np.mean(high[start:index + 1]))
        bear_trailing[index] = value
        if index > last_bear_break and np.isfinite(previous_bear_value) and close[index] > value and close[index - 1] <= previous_bear_value:
            bear_active = False
            bear_trailing[index] = None
        previous_bear_value = value

layers = []
for channel in channels:
    center = (channel["top"] + channel["bottom"]) / 2.0
    upper_bottom = max(channel["bottom"], channel["top"] - channel["edge"])
    lower_top = min(channel["top"], channel["bottom"] + channel["edge"])
    upper_text = channel["current_text"] if channel["text_is_upper"] else channel["overall_text"]
    lower_text = channel["overall_text"] if channel["text_is_upper"] else channel["current_text"]
    layers.extend(
        [
            {
                "type": "zone",
                "startIndex": int(channel["left"]),
                "endIndex": int(channel["right"]),
                "top": float(channel["top"]),
                "bottom": float(channel["bottom"]),
                "fillColor": "rgba(160,160,160,0.10)",
                "borderColor": "rgba(0,0,0,0)",
                "borderSize": 0,
            },
            {
                "type": "zone",
                "startIndex": int(channel["left"]),
                "endIndex": int(channel["right"]),
                "top": float(channel["top"]),
                "bottom": float(upper_bottom),
                "fillColor": rgba(bearish_color, 0.30),
                "borderColor": "rgba(0,0,0,0)",
                "borderSize": 0,
                "text": upper_text,
                "textColor": "#D0D0D0",
            },
            {
                "type": "zone",
                "startIndex": int(channel["left"]),
                "endIndex": int(channel["right"]),
                "top": float(lower_top),
                "bottom": float(channel["bottom"]),
                "fillColor": rgba(bullish_color, 0.30),
                "borderColor": "rgba(0,0,0,0)",
                "borderSize": 0,
                "text": lower_text,
                "textColor": "#D0D0D0",
            },
        ]
    )
    sentiment_width = max(1, int(round((channel["right"] - channel["left"]) * 0.15)))
    max_abs_signed = max((abs(item["signed"]) for item in channel["bins"]), default=0.0)
    max_volume = max((item["volume"] for item in channel["bins"]), default=0.0)
    for item in channel["bins"]:
        ratio = abs(item["signed"]) / max_abs_signed if max_abs_signed > 0.0 else 0.0
        base_color = bullish_color if item["signed"] > 0.0 else bearish_color
        layers.append(
            {
                "type": "zone",
                "startIndex": int(max(0, channel["left"] - sentiment_width)),
                "endIndex": int(channel["left"]),
                "top": float(item["top"]),
                "bottom": float(item["bottom"]),
                "fillColor": rgba(base_color, 0.10 + ratio * 0.90),
                "borderColor": rgba(base_color, 0.10 + ratio * 0.90),
                "borderSize": 1,
                "text": f"{ratio * 100.0:.0f}%" if show_bin_labels else "",
                "textColor": "#D0D0D0",
            }
        )
        if channel["active"] and max_volume > 0.0:
            profile_width = int(round(item["volume"] / max_volume * 30.0))
            layers.append(
                {
                    "type": "zone",
                    "startIndex": int(channel["right"] + 7 + 30 - profile_width),
                    "endIndex": int(channel["right"] + 7 + 30),
                    "top": float(item["top"]),
                    "bottom": float(item["bottom"]),
                    "fillColor": rgba(base_color, 0.10 + (item["volume"] / max_volume) * 0.90),
                    "borderColor": rgba(base_color, 0.25),
                    "borderSize": 1,
                }
            )
    line_start = max(0, channel["left"] - sentiment_width)
    for price, color, width, dashed in (
        (center, "rgba(200,200,200,0.50)", 1, True),
        (channel["top"], bearish_color, 2, False),
        (upper_bottom, rgba(bearish_color, 0.80), 1, True),
        (lower_top, rgba(bullish_color, 0.80), 1, True),
        (channel["bottom"], bullish_color, 2, False),
    ):
        layers.append(
            {
                "type": "line",
                "startIndex": int(line_start),
                "endIndex": int(channel["right"]),
                "y1": float(price),
                "y2": float(price),
                "color": color,
                "lineWidth": width,
                "dashed": dashed,
            }
        )

price_guide = [float(value) if np.isfinite(value) else None for value in close]
plots = [
    {"name": "Price Reference", "title": False, "data": price_guide, "color": "rgba(255,255,255,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Bullish Trend", "data": bull_trailing, "color": bullish_color, "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Bearish Trend", "data": bear_trailing, "color": bearish_color, "type": "line", "lineWidth": 1, "overlay": True},
]
plots = [plot for plot in plots if any(item is not None for item in plot["data"])]
signals = []
if any(item is not None for item in bull_breaks):
    signals.append({"type": "buy", "text": "▲", "name": "Bullish Breakout", "data": bull_breaks, "color": bullish_color, "renderMode": "events"})
if any(item is not None for item in bear_breaks):
    signals.append({"type": "sell", "text": "▼", "name": "Bearish Breakout", "data": bear_breaks, "color": bearish_color, "renderMode": "events"})

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers[-500:]}
