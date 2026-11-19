# ============================================================
# QuantDinger Fractal FVG Order Blocks
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; alerts do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Fractal FVG Order Blocks"
my_indicator_description = (
    "Detects fractal breaks, searches the preceding move for the defining opposite candle, "
    "optionally requires a nearby fair-value gap, and draws the order-block boundaries."
)

# @param show_fractals bool false Show confirmed fractals
# @param fractal_filter str 3-bar Fractal filter values=3-bar,5-bar
# @param break_source str Close Break confirmation values=Close,High/Low
# @param require_fvg bool true Require nearby fair-value gap
# @param fvg_distance int 3 Maximum bars between block and gap range=1:6:1
# @param boundary_mode str Body Boundary mode values=Body,Wick
# @param delete_filled bool true Delete filled blocks
# @param line_style str Solid Line style values=Solid,Dashed,Dotted
# @param line_length int 5 Line length range=1:100:1
# @param line_width int 2 Line width range=1:4:1
# @param bearish_color str #F23645 Bearish block color
# @param bullish_color str #2962FF Bullish block color

show_fractals = bool(params.get("show_fractals", False))
fractal_filter = str(params.get("fractal_filter", "3-bar"))
break_source = str(params.get("break_source", "Close"))
require_fvg = bool(params.get("require_fvg", True))
fvg_distance = min(6, max(1, int(params.get("fvg_distance", 3))))
boundary_mode = str(params.get("boundary_mode", "Body"))
delete_filled = bool(params.get("delete_filled", True))
line_style = str(params.get("line_style", "Solid"))
line_length = min(100, max(1, int(params.get("line_length", 5))))
line_width = min(4, max(1, int(params.get("line_width", 2))))
bearish_color = str(params.get("bearish_color", "#F23645"))
bullish_color = str(params.get("bullish_color", "#2962FF"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

def regular_high(index):
    if index < 3:
        return False
    return bool(
        high[index] < high[index - 1]
        and (
            high[index - 2] < high[index - 1]
            or (high[index - 2] == high[index - 1] and high[index - 3] < high[index - 2])
        )
    )

def regular_low(index):
    if index < 3:
        return False
    return bool(
        low[index] > low[index - 1]
        and (
            low[index - 2] > low[index - 1]
            or (low[index - 2] == low[index - 1] and low[index - 3] > low[index - 2])
        )
    )

def five_bar_high(index):
    if index < 4:
        return False
    center = high[index - 2]
    return bool(
        high[index] < center
        and high[index - 1] < center
        and high[index - 3] < center
        and high[index - 4] < center
    )

def five_bar_low(index):
    if index < 4:
        return False
    center = low[index - 2]
    return bool(
        low[index] > center
        and low[index - 1] > center
        and low[index - 3] > center
        and low[index - 4] > center
    )

pending_highs = []
pending_lows = []
blocks = []
block_keys = set()
fractal_high_events = [None for _ in range(n)]
fractal_low_events = [None for _ in range(n)]

def bearish_gap(index):
    return bool(
        index >= 2
        and close[index - 1] < low[index - 2]
        and high[index] < low[index - 2]
    )

def bullish_gap(index):
    return bool(
        index >= 2
        and close[index - 1] > high[index - 2]
        and low[index] > high[index - 2]
    )

def find_bearish_block(current_index, pivot_index):
    selected = None
    selected_high = float(low[current_index])
    oldest = max(pivot_index, current_index - 500)
    for candidate in range(current_index, oldest - 1, -1):
        if close[candidate] > open_price[candidate] and high[candidate] > selected_high:
            selected = candidate
            selected_high = float(high[candidate])
    if selected is None:
        return None
    accepted = not require_fvg
    if require_fvg:
        latest_gap_end = min(current_index, selected + fvg_distance + 2)
        accepted = any(bearish_gap(gap_end) for gap_end in range(selected + 2, latest_gap_end + 1))
    return selected if accepted else None

def find_bullish_block(current_index, pivot_index):
    selected = None
    selected_low = float(low[current_index])
    oldest = max(pivot_index, current_index - 500)
    for candidate in range(current_index, oldest - 1, -1):
        if close[candidate] < open_price[candidate] and low[candidate] < selected_low:
            selected = candidate
            selected_low = float(low[candidate])
    if selected is None:
        return None
    accepted = not require_fvg
    if require_fvg:
        latest_gap_end = min(current_index, selected + fvg_distance + 2)
        accepted = any(bullish_gap(gap_end) for gap_end in range(selected + 2, latest_gap_end + 1))
    return selected if accepted else None

for index in range(n):
    if fractal_filter == "5-bar":
        new_high = five_bar_high(index)
        new_low = five_bar_low(index)
        pivot_index = index - 2
    else:
        new_high = regular_high(index)
        new_low = regular_low(index)
        pivot_index = index - 1

    if new_high:
        pending_highs.append({"index": pivot_index, "price": float(high[pivot_index])})
        fractal_high_events[pivot_index] = float(high[pivot_index])
    if new_low:
        pending_lows.append({"index": pivot_index, "price": float(low[pivot_index])})
        fractal_low_events[pivot_index] = float(low[pivot_index])

    low_break_value = close[index] if break_source == "Close" else low[index]
    for pivot in list(reversed(pending_lows)):
        if low_break_value < pivot["price"]:
            origin = find_bearish_block(index, pivot["index"])
            key = ("bearish", origin)
            if origin is not None and key not in block_keys:
                lower = float(open_price[origin] if boundary_mode == "Body" else low[origin])
                blocks.append(
                    {
                        "kind": "bearish",
                        "origin": origin,
                        "created": index,
                        "upper": float(high[origin]),
                        "lower": lower,
                        "visible": True,
                        "tracking": True,
                    }
                )
                block_keys.add(key)
            pending_lows.remove(pivot)

    high_break_value = close[index] if break_source == "Close" else high[index]
    for pivot in list(reversed(pending_highs)):
        if high_break_value > pivot["price"]:
            origin = find_bullish_block(index, pivot["index"])
            key = ("bullish", origin)
            if origin is not None and key not in block_keys:
                upper = float(open_price[origin] if boundary_mode == "Body" else high[origin])
                blocks.append(
                    {
                        "kind": "bullish",
                        "origin": origin,
                        "created": index,
                        "upper": upper,
                        "lower": float(low[origin]),
                        "visible": True,
                        "tracking": True,
                    }
                )
                block_keys.add(key)
            pending_highs.remove(pivot)

    for block in blocks:
        if not block["tracking"] or index <= block["origin"]:
            continue
        filled = high[index] >= block["upper"] if block["kind"] == "bearish" else low[index] <= block["lower"]
        if filled:
            block["tracking"] = False
            if delete_filled:
                block["visible"] = False

    if len(blocks) > 200:
        blocks = blocks[-200:]

layers = []
dashed = line_style != "Solid"
for block in blocks[-50:]:
    if not block["visible"]:
        continue
    end_index = min(n - 1, block["created"] + line_length)
    color = bearish_color if block["kind"] == "bearish" else bullish_color
    for price in (block["upper"], block["lower"]):
        layers.append(
            {
                "type": "line",
                "startIndex": int(block["origin"]),
                "endIndex": int(end_index),
                "y1": float(price),
                "y2": float(price),
                "color": color,
                "lineWidth": int(line_width),
                "dashed": dashed,
                "extendRight": False,
            }
        )

plots = [
    {
        "name": "Price Guide",
        "title": False,
        "data": [float(value) if np.isfinite(value) else None for value in close],
        "color": "rgba(0,0,0,0)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    }
]

signals = []
if show_fractals:
    signals.extend(
        [
            {
                "type": "sell",
                "text": "▼",
                "name": "Fractal High",
                "data": fractal_high_events,
                "color": bearish_color,
                "renderMode": "events",
            },
            {
                "type": "buy",
                "text": "▲",
                "name": "Fractal Low",
                "data": fractal_low_events,
                "color": bullish_color,
                "renderMode": "events",
            },
        ]
    )

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers[-100:]}
