# ============================================================
# QuantDinger Experimental Order Blocks
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Experimental Order Blocks"
my_indicator_description = (
    "Identifies the last opposite candle before a directional sequence, marks its "
    "selected body-or-wick range, and retains the newest bullish and bearish channels."
)

# @param color_scheme str Dark Color scheme values=Dark,Bright
# @param periods int 5 Consecutive confirmation candles range=1:30:1
# @param minimum_move float 0.0 Minimum move percent range=0.0:25.0:0.1
# @param use_wicks bool false Use full high-low order-block range
# @param show_bullish_channel bool true Show latest bullish channel
# @param show_bearish_channel bool true Show latest bearish channel
# @param show_info_panel bool false Show latest order-block panel

color_scheme = str(params.get("color_scheme", "Dark"))
periods = min(30, max(1, int(params.get("periods", 5))))
minimum_move = min(25.0, max(0.0, float(params.get("minimum_move", 0.0))))
use_wicks = bool(params.get("use_wicks", False))
show_bullish_channel = bool(params.get("show_bullish_channel", True))
show_bearish_channel = bool(params.get("show_bearish_channel", True))
show_info_panel = bool(params.get("show_info_panel", False))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
times = df["time"].to_numpy(dtype=float)
n = len(df)

if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0
interval = float(np.median(np.diff(times))) if n > 1 else 3600000.0
interval = max(interval, 1.0)

bull_color = "#00C853" if color_scheme == "Dark" else "#008000"
bear_color = "#2962FF" if color_scheme == "Dark" else "#FF0000"
confirmation_span = periods + 1
bull_events = []
bear_events = []

for confirmation_index in range(confirmation_span, n):
    origin = confirmation_index - confirmation_span
    base_close = close[origin]
    if not np.isfinite(base_close) or base_close == 0.0:
        continue
    move_percent = abs(base_close - close[confirmation_index - 1]) / abs(base_close) * 100.0
    if move_percent < minimum_move:
        continue
    sequence = slice(origin + 1, confirmation_index)
    all_up = bool(np.all(close[sequence] > open_price[sequence]))
    all_down = bool(np.all(close[sequence] < open_price[sequence]))
    if close[origin] < open_price[origin] and all_up:
        top = float(high[origin] if use_wicks else open_price[origin])
        bottom = float(low[origin])
        bull_events.append(
            {
                "origin": origin,
                "confirmed": confirmation_index - 1,
                "top": top,
                "bottom": bottom,
                "average": (top + bottom) / 2.0,
            }
        )
    if close[origin] > open_price[origin] and all_down:
        top = float(high[origin])
        bottom = float(low[origin] if use_wicks else open_price[origin])
        bear_events.append(
            {
                "origin": origin,
                "confirmed": confirmation_index - 1,
                "top": top,
                "bottom": bottom,
                "average": (top + bottom) / 2.0,
            }
        )

bull_marks = [None for _ in range(n)]
bear_marks = [None for _ in range(n)]

for event in bull_events:
    index = event["origin"]
    bull_marks[index] = {"value": float(event["bottom"]), "text": "Bullish OB", "color": bull_color}

for event in bear_events:
    index = event["origin"]
    bear_marks[index] = {"value": float(event["top"]), "text": "Bearish OB", "color": bear_color}

def channel_line(event, price, color, dashed):
    return {
        "type": "line",
        "startIndex": int(event["origin"]),
        "endIndex": max(0, n - 1),
        "y1": float(price),
        "y2": float(price),
        "color": color,
        "lineWidth": 1,
        "dashed": bool(dashed),
    }

layers = []
for event in bull_events[-20:]:
    layers.append(
        {
            "type": "zone",
            "startIndex": int(event["origin"]),
            "endIndex": int(event["confirmed"]),
            "top": float(event["top"]),
            "bottom": float(event["bottom"]),
            "fillColor": "rgba(0,128,0,0.14)" if color_scheme == "Bright" else "rgba(0,200,83,0.12)",
            "borderColor": bull_color,
            "borderSize": 1,
        }
    )
for event in bear_events[-20:]:
    layers.append(
        {
            "type": "zone",
            "startIndex": int(event["origin"]),
            "endIndex": int(event["confirmed"]),
            "top": float(event["top"]),
            "bottom": float(event["bottom"]),
            "fillColor": "rgba(255,0,0,0.12)" if color_scheme == "Bright" else "rgba(41,98,255,0.12)",
            "borderColor": bear_color,
            "borderSize": 1,
        }
    )
latest_bull = bull_events[-1] if bull_events else None
latest_bear = bear_events[-1] if bear_events else None
if show_bullish_channel and latest_bull is not None:
    layers.extend(
        [
            channel_line(latest_bull, latest_bull["average"], bull_color, False),
            channel_line(latest_bull, latest_bull["top"], bull_color, True),
            channel_line(latest_bull, latest_bull["bottom"], bull_color, True),
        ]
    )
if show_bearish_channel and latest_bear is not None:
    layers.extend(
        [
            channel_line(latest_bear, latest_bear["average"], bear_color, False),
            channel_line(latest_bear, latest_bear["top"], bear_color, True),
            channel_line(latest_bear, latest_bear["bottom"], bear_color, True),
        ]
    )

if show_info_panel and n:
    bull_values = latest_bull or {"top": 0.0, "average": 0.0, "bottom": 0.0}
    bear_values = latest_bear or {"top": 0.0, "average": 0.0, "bottom": 0.0}
    panel_text = (
        "LATEST ORDER BLOCKS\n"
        f"Bullish · High {bull_values['top']:.2f} · Avg {bull_values['average']:.2f} · Low {bull_values['bottom']:.2f}\n"
        f"Bearish · High {bear_values['top']:.2f} · Avg {bear_values['average']:.2f} · Low {bear_values['bottom']:.2f}"
    )
    layers.append(
        {
            "type": "label",
            "timestamp": float(times[-1] + interval * 100.0),
            "price": float(close[-1]),
            "text": panel_text,
            "color": "rgba(56,56,56,0.95)",
            "textColor": "#FFFFFF",
            "side": "right",
        }
    )

plots = [
    {"name": "Price Reference", "title": False, "data": [float(value) if np.isfinite(value) else None for value in close], "color": "rgba(255,255,255,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
]
signals = []
if any(item is not None for item in bull_marks):
    signals.append({"type": "buy", "text": "Bullish OB", "name": "Bullish Order Block", "data": bull_marks, "color": bull_color, "renderMode": "events"})
if any(item is not None for item in bear_marks):
    signals.append({"type": "sell", "text": "Bearish OB", "name": "Bearish Order Block", "data": bear_marks, "color": bear_color, "renderMode": "events"})
output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers}
