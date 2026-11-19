# ============================================================
# QuantDinger Order Block Finder
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Order Block Finder"
my_indicator_description = (
    "Finds the last opposite candle before a configurable uninterrupted move, "
    "marks its range, and keeps the newest bullish and bearish reference channels."
)

# @param periods int 7 Consecutive move candles range=1:30:1
# @param minimum_move float 0.0 Minimum close-to-close move percent range=0.0:25.0:0.1
# @param bullish_channels int 2 Bullish channels to retain range=0:20:1
# @param bearish_channels int 2 Bearish channels to retain range=0:20:1
# @param color_scheme str Light Color scheme values=Light,Dark

periods = min(30, max(1, int(params.get("periods", 7))))
minimum_move = min(25.0, max(0.0, float(params.get("minimum_move", 0.0))))
bullish_channels = min(20, max(0, int(params.get("bullish_channels", 2))))
bearish_channels = min(20, max(0, int(params.get("bearish_channels", 2))))
color_scheme = str(params.get("color_scheme", "Light"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

bull_color = "#FFFFFF" if color_scheme == "Dark" else "#008000"
bear_color = "#2962FF" if color_scheme == "Dark" else "#FF0000"
ob_period = periods + 1

bull_events = []
bear_events = []
for confirmation_index in range(ob_period, n):
    origin = confirmation_index - ob_period
    sequence_start = origin + 1
    sequence_end = confirmation_index
    base_close = close[origin]
    if not np.isfinite(base_close) or base_close == 0.0:
        continue
    absolute_move = abs(base_close - close[confirmation_index - 1]) / abs(base_close) * 100.0
    if not np.isfinite(absolute_move) or absolute_move < minimum_move:
        continue

    all_up = bool(np.all(close[sequence_start:sequence_end] > open_price[sequence_start:sequence_end]))
    all_down = bool(np.all(close[sequence_start:sequence_end] < open_price[sequence_start:sequence_end]))
    top = float(high[origin])
    bottom = float(low[origin])
    event = {
        "origin": int(origin),
        "confirmed": int(confirmation_index),
        "top": top,
        "bottom": bottom,
        "average": float((top + bottom) / 2.0),
    }
    if close[origin] < open_price[origin] and all_up:
        bull_events.append(event)
    if close[origin] > open_price[origin] and all_down:
        bear_events.append(event)

bull_high = [None for _ in range(n)]
bull_low = [None for _ in range(n)]
bull_average = [None for _ in range(n)]
bear_high = [None for _ in range(n)]
bear_low = [None for _ in range(n)]
bear_average = [None for _ in range(n)]
bull_fill = [None for _ in range(n)]
bear_fill = [None for _ in range(n)]
bull_marks = [None for _ in range(n)]
bear_marks = [None for _ in range(n)]

for event in bull_events:
    index = event["origin"]
    bull_high[index] = event["top"]
    bull_low[index] = event["bottom"]
    bull_average[index] = event["average"]
    bull_fill[index] = {
        "upper": event["top"],
        "lower": event["bottom"],
        "color": "rgba(0,128,0,0.50)" if color_scheme != "Dark" else "rgba(255,255,255,0.32)",
    }
    bull_marks[index] = {"value": event["bottom"], "text": "Bull", "color": bull_color}

for event in bear_events:
    index = event["origin"]
    bear_high[index] = event["top"]
    bear_low[index] = event["bottom"]
    bear_average[index] = event["average"]
    bear_fill[index] = {
        "upper": event["top"],
        "lower": event["bottom"],
        "color": "rgba(255,0,0,0.50)" if color_scheme != "Dark" else "rgba(41,98,255,0.32)",
    }
    bear_marks[index] = {"value": event["top"], "text": "Bear", "color": bear_color}

def channel_line(event, price, color, dashed):
    return {
        "type": "line",
        "startIndex": int(event["origin"]),
        "endIndex": int(n - 1),
        "y1": float(price),
        "y2": float(price),
        "color": color,
        "lineWidth": 1,
        "dashed": bool(dashed),
    }

layers = []
for event in bull_events[-bullish_channels:] if bullish_channels else []:
    layers.append(
        {
            "type": "zone",
            "startIndex": int(event["origin"]),
            "endIndex": int(n - 1),
            "top": float(event["top"]),
            "bottom": float(event["bottom"]),
            "fillColor": "rgba(0,128,0,0.12)" if color_scheme != "Dark" else "rgba(255,255,255,0.10)",
            "borderColor": bull_color,
            "borderSize": 1,
        }
    )
    layers.append(channel_line(event, event["top"], bull_color, True))
    layers.append(channel_line(event, event["average"], bull_color, False))
    layers.append(channel_line(event, event["bottom"], bull_color, True))
for event in bear_events[-bearish_channels:] if bearish_channels else []:
    layers.append(
        {
            "type": "zone",
            "startIndex": int(event["origin"]),
            "endIndex": int(n - 1),
            "top": float(event["top"]),
            "bottom": float(event["bottom"]),
            "fillColor": "rgba(255,0,0,0.12)" if color_scheme != "Dark" else "rgba(41,98,255,0.10)",
            "borderColor": bear_color,
            "borderSize": 1,
        }
    )
    layers.append(channel_line(event, event["top"], bear_color, True))
    layers.append(channel_line(event, event["average"], bear_color, False))
    layers.append(channel_line(event, event["bottom"], bear_color, True))

price_guide = [float(value) if np.isfinite(value) else None for value in close]
plots = [
    {"name": "Price Reference", "title": False, "data": price_guide, "color": "rgba(255,255,255,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
]
signals = [
    {"type": "buy", "text": "Bull", "name": "Bullish OB", "data": bull_marks, "color": bull_color, "renderMode": "events"},
    {"type": "sell", "text": "Bear", "name": "Bearish OB", "data": bear_marks, "color": bear_color, "renderMode": "events"},
]

signals = [signal for signal in signals if any(item is not None for item in signal["data"])]

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers}
