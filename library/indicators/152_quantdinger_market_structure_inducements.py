# ============================================================
# QuantDinger Market Structure with Inducements and Sweeps
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; structural annotations do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Market Structure with Inducements and Sweeps"
my_indicator_description = (
    "Tracks long and short swing state to annotate CHoCH, BOS, inducement, and liquidity-sweep "
    "levels while extending the latest active market-structure references."
)

# @param structure_period int 50 CHoCH detection period range=2:200:1
# @param inducement_period int 3 Inducement detection period range=2:50:1
# @param show_choch bool true Show CHoCH
# @param show_bos bool true Show BOS
# @param show_inducements bool true Show inducements
# @param show_sweeps bool true Show sweeps
# @param show_swings bool true Show swing circles
# @param bullish_color str #089981 Bullish color
# @param bearish_color str #FF5252 Bearish color
# @param inducement_color str #808080 Inducement color
# @param sweep_color str #808080 Sweep color

structure_period = min(200, max(2, int(params.get("structure_period", 50))))
inducement_period = min(50, max(2, int(params.get("inducement_period", 3))))
show_choch = bool(params.get("show_choch", True))
show_bos = bool(params.get("show_bos", True))
show_inducements = bool(params.get("show_inducements", True))
show_sweeps = bool(params.get("show_sweeps", True))
show_swings = bool(params.get("show_swings", True))
bullish_color = str(params.get("bullish_color", "#089981"))
bearish_color = str(params.get("bearish_color", "#FF5252"))
inducement_color = str(params.get("inducement_color", "#808080"))
sweep_color = str(params.get("sweep_color", "#808080"))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

def swing_events(period):
    rolling_high = pd.Series(high).rolling(period, min_periods=period).max().to_numpy(dtype=float)
    rolling_low = pd.Series(low).rolling(period, min_periods=period).min().to_numpy(dtype=float)
    side = np.zeros(n, dtype=int)
    tops = np.full(n, np.nan, dtype=float)
    bottoms = np.full(n, np.nan, dtype=float)
    top_indexes = np.full(n, -1, dtype=int)
    bottom_indexes = np.full(n, -1, dtype=int)
    last_top_index = -1
    last_bottom_index = -1
    for index in range(n):
        previous_side = side[index - 1] if index else 0
        current_side = previous_side
        source_index = index - period
        if source_index >= 0 and np.isfinite(rolling_high[index]) and high[source_index] > rolling_high[index]:
            current_side = 0
        elif source_index >= 0 and np.isfinite(rolling_low[index]) and low[source_index] < rolling_low[index]:
            current_side = 1
        side[index] = current_side
        if source_index >= 0 and current_side == 0 and previous_side != 0:
            tops[index] = float(high[source_index])
            last_top_index = int(source_index)
        if source_index >= 0 and current_side == 1 and previous_side != 1:
            bottoms[index] = float(low[source_index])
            last_bottom_index = int(source_index)
        top_indexes[index] = last_top_index
        bottom_indexes[index] = last_bottom_index
    return tops, top_indexes, bottoms, bottom_indexes

major_top, major_top_index, major_bottom, major_bottom_index = swing_events(structure_period)
minor_top, minor_top_index, minor_bottom, minor_bottom_index = swing_events(inducement_period)

swing_high_data = [None for _ in range(n)]
swing_low_data = [None for _ in range(n)]
layers = []

def add_line_label(start_index, end_index, price, text, color, dashed=False, side="up"):
    if start_index < 0 or end_index < 0 or not np.isfinite(price):
        return
    layers.append(
        {
            "type": "line",
            "startIndex": int(start_index),
            "endIndex": int(end_index),
            "y1": float(price),
            "y2": float(price),
            "color": color,
            "lineWidth": 1,
            "dashed": bool(dashed),
        }
    )
    layers.append(
        {
            "type": "label",
            "index": int(round((start_index + end_index) / 2.0)),
            "price": float(price),
            "text": text,
            "color": "rgba(0,0,0,0)",
            "textColor": color,
            "side": side,
        }
    )

structure_side = 0
top_crossed = False
bottom_crossed = False
minor_top_crossed = False
minor_bottom_crossed = False
top_price = np.nan
bottom_price = np.nan
top_index = -1
bottom_index = -1
minor_top_price = np.nan
minor_bottom_price = np.nan
minor_top_x = -1
minor_bottom_x = -1
trailing_max = np.nan
trailing_min = np.nan
trailing_max_index = -1
trailing_min_index = -1

for index in range(n):
    if np.isfinite(major_top[index]):
        top_price = float(major_top[index])
        top_index = int(major_top_index[index])
        top_crossed = False
        if show_swings and 0 <= top_index < n:
            swing_high_data[top_index] = float(top_price)
    if np.isfinite(major_bottom[index]):
        bottom_price = float(major_bottom[index])
        bottom_index = int(major_bottom_index[index])
        bottom_crossed = False
        if show_swings and 0 <= bottom_index < n:
            swing_low_data[bottom_index] = float(bottom_price)
    if np.isfinite(minor_top[index]):
        minor_top_price = float(minor_top[index])
        minor_top_x = int(minor_top_index[index])
    if np.isfinite(minor_bottom[index]):
        minor_bottom_price = float(minor_bottom[index])
        minor_bottom_x = int(minor_bottom_index[index])

    previous_structure_side = structure_side
    if np.isfinite(top_price) and close[index] > top_price and not top_crossed:
        structure_side = 1
        top_crossed = True
    if np.isfinite(bottom_price) and close[index] < bottom_price and not bottom_crossed:
        structure_side = 0
        bottom_crossed = True

    if structure_side != previous_structure_side:
        trailing_max = float(high[index])
        trailing_min = float(low[index])
        trailing_max_index = index
        trailing_min_index = index
        minor_top_crossed = False
        minor_bottom_crossed = False
        if structure_side == 1 and show_choch:
            add_line_label(top_index, index, top_price, "CHoCH", bullish_color, True, "down")
        elif structure_side == 0 and show_choch:
            add_line_label(bottom_index, index, bottom_price, "CHoCH", bearish_color, True, "up")

    if (
        np.isfinite(minor_bottom_price)
        and low[index] < minor_bottom_price
        and not minor_bottom_crossed
        and structure_side == 1
        and (not np.isfinite(bottom_price) or minor_bottom_price != bottom_price)
    ):
        if show_inducements:
            add_line_label(minor_bottom_x, index, minor_bottom_price, "IDM", inducement_color, True, "up")
        minor_bottom_crossed = True

    if (
        np.isfinite(trailing_max)
        and close[index] > trailing_max
        and minor_bottom_crossed
        and structure_side == 1
    ):
        if show_bos:
            add_line_label(trailing_max_index, index, trailing_max, "BOS", bullish_color, False, "down")
        minor_bottom_crossed = False

    if (
        np.isfinite(minor_top_price)
        and high[index] > minor_top_price
        and not minor_top_crossed
        and structure_side == 0
        and (not np.isfinite(top_price) or minor_top_price != top_price)
    ):
        if show_inducements:
            add_line_label(minor_top_x, index, minor_top_price, "IDM", inducement_color, True, "down")
        minor_top_crossed = True

    if (
        np.isfinite(trailing_min)
        and close[index] < trailing_min
        and minor_top_crossed
        and structure_side == 0
    ):
        if show_bos:
            add_line_label(trailing_min_index, index, trailing_min, "BOS", bearish_color, False, "up")
        minor_top_crossed = False

    if (
        show_sweeps
        and structure_side == 1
        and np.isfinite(trailing_max)
        and high[index] > trailing_max
        and close[index] < trailing_max
        and index - trailing_max_index > 1
    ):
        add_line_label(trailing_max_index, index, trailing_max, "x", sweep_color, True, "down")
    if (
        show_sweeps
        and structure_side == 0
        and np.isfinite(trailing_min)
        and low[index] < trailing_min
        and close[index] > trailing_min
        and index - trailing_min_index > 1
    ):
        add_line_label(trailing_min_index, index, trailing_min, "x", sweep_color, True, "up")

    if np.isfinite(trailing_max) and high[index] > trailing_max:
        trailing_max = float(high[index])
        trailing_max_index = index
    if np.isfinite(trailing_min) and low[index] < trailing_min:
        trailing_min = float(low[index])
        trailing_min_index = index

if show_swings:
    for index, price in enumerate(swing_high_data):
        if price is not None:
            layers.append(
                {
                    "type": "label",
                    "index": int(index),
                    "price": float(price),
                    "text": "●",
                    "color": "rgba(0,0,0,0)",
                    "textColor": bearish_color,
                    "side": "center",
                }
            )
    for index, price in enumerate(swing_low_data):
        if price is not None:
            layers.append(
                {
                    "type": "label",
                    "index": int(index),
                    "price": float(price),
                    "text": "●",
                    "color": "rgba(0,0,0,0)",
                    "textColor": bullish_color,
                    "side": "center",
                }
            )

def add_extension(start_index, end_index, price, text, color, dashed, side):
    if start_index < 0 or end_index < 0 or not np.isfinite(price):
        return
    layers.append(
        {
            "type": "line",
            "startIndex": int(start_index),
            "endIndex": int(end_index),
            "y1": float(price),
            "y2": float(price),
            "color": color,
            "lineWidth": 1,
            "dashed": bool(dashed),
        }
    )
    layers.append(
        {
            "type": "label",
            "index": int(end_index),
            "price": float(price),
            "text": text,
            "color": "rgba(0,0,0,0)",
            "textColor": color,
            "side": side,
        }
    )

last_index = max(0, n - 1)
if structure_side == 1:
    if show_choch:
        add_extension(bottom_index, last_index, bottom_price, "CHoCH", bearish_color, True, "up")
    if show_bos:
        add_extension(trailing_max_index, last_index, trailing_max, "BOS", bullish_color, False, "down")
    if show_inducements and not minor_bottom_crossed:
        add_extension(minor_bottom_x, last_index, minor_bottom_price, "IDM", inducement_color, True, "up")
else:
    if show_choch:
        add_extension(top_index, last_index, top_price, "CHoCH", bullish_color, True, "down")
    if show_bos:
        add_extension(trailing_min_index, last_index, trailing_min, "BOS", bearish_color, False, "up")
    if show_inducements and not minor_top_crossed:
        add_extension(minor_top_x, last_index, minor_top_price, "IDM", inducement_color, True, "down")

price_guide = [float(value) if np.isfinite(value) else None for value in close]
plots = [
    {"name": "Price Reference", "title": False, "data": price_guide, "color": "rgba(255,255,255,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
]

output = {"name": my_indicator_name, "plots": plots, "signals": [], "layers": layers[-500:]}
