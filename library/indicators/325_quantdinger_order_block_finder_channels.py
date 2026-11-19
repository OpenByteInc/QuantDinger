# ============================================================
# QuantDinger Order Block Finder Channels
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Order Block Finder Channels"
my_indicator_description = "Finds full-range order blocks after uninterrupted directional sequences and retains a bounded set of historical channels per side."

# @param color_scheme str Light Channel color scheme values=Dark,Light
# @param relevant_periods int 7 Required consecutive candles range=1:100:1
# @param minimum_move_percent float 0 Minimum absolute move range=0:100:0.1
# @param bullish_channels int 2 Bullish channels to retain range=0:100:1
# @param bearish_channels int 2 Bearish channels to retain range=0:100:1

color_scheme = str(params.get("color_scheme", "Light")).title()
if color_scheme not in ("Dark", "Light"):
    color_scheme = "Light"
relevant_periods = min(100, max(1, int(params.get("relevant_periods", 7))))
minimum_move_percent = min(100.0, max(0.0, float(params.get("minimum_move_percent", 0.0))))
bullish_channels = min(100, max(0, int(params.get("bullish_channels", 2))))
bearish_channels = min(100, max(0, int(params.get("bearish_channels", 2))))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)
candidate_offset = relevant_periods + 1

absolute_move = np.full(n, np.nan, dtype=float)
up_count = np.zeros(n, dtype=int)
down_count = np.zeros(n, dtype=int)
bull_event = np.zeros(n, dtype=bool)
bear_event = np.zeros(n, dtype=bool)
bull_high = np.full(n, np.nan, dtype=float)
bull_low = np.full(n, np.nan, dtype=float)
bull_average = np.full(n, np.nan, dtype=float)
bear_high = np.full(n, np.nan, dtype=float)
bear_low = np.full(n, np.nan, dtype=float)
bear_average = np.full(n, np.nan, dtype=float)
all_blocks = []
active_bull_channels = []
active_bear_channels = []

for index in range(n):
    source_index = index - candidate_offset
    if source_index < 0:
        continue
    denominator = abs(close[source_index])
    absolute_move[index] = abs(close[source_index] - close[index - 1]) / denominator * 100.0 if denominator > 1e-12 else np.nan
    sequence = slice(index - relevant_periods, index)
    up_count[index] = int(np.count_nonzero(close[sequence] > opened[sequence]))
    down_count[index] = int(np.count_nonzero(close[sequence] < opened[sequence]))
    valid_move = np.isfinite(absolute_move[index]) and absolute_move[index] >= minimum_move_percent

    if close[source_index] < opened[source_index] and up_count[index] == relevant_periods and valid_move:
        top, bottom = float(high[source_index]), float(low[source_index])
        average = (top + bottom) / 2.0
        block = {"side": "bull", "high": top, "average": average, "low": bottom, "formedIndex": int(index), "sourceIndex": int(source_index), "movePercent": float(absolute_move[index])}
        bull_event[index] = True
        bull_high[source_index], bull_low[source_index], bull_average[source_index] = top, bottom, average
        all_blocks.append(block)
        if bullish_channels > 0:
            active_bull_channels.append(block)
            if len(active_bull_channels) > bullish_channels:
                active_bull_channels.pop(0)

    if close[source_index] > opened[source_index] and down_count[index] == relevant_periods and valid_move:
        top, bottom = float(high[source_index]), float(low[source_index])
        average = (top + bottom) / 2.0
        block = {"side": "bear", "high": top, "average": average, "low": bottom, "formedIndex": int(index), "sourceIndex": int(source_index), "movePercent": float(absolute_move[index])}
        bear_event[index] = True
        bear_high[source_index], bear_low[source_index], bear_average[source_index] = top, bottom, average
        all_blocks.append(block)
        if bearish_channels > 0:
            active_bear_channels.append(block)
            if len(active_bear_channels) > bearish_channels:
                active_bear_channels.pop(0)

bull_color = "#FFFFFF" if color_scheme == "Dark" else "#008000"
bear_color = "#0000FF" if color_scheme == "Dark" else "#FF0000"
layers = []
for side, channels, color in (("Bullish", active_bull_channels, bull_color), ("Bearish", active_bear_channels, bear_color)):
    for block in channels:
        for role, price, dashed in (("Average", block["average"], False), ("High", block["high"], True), ("Low", block["low"], True)):
            layers.append({
                "type": "line", "role": f"{side} OB {role}", "startIndex": 0, "endIndex": block["formedIndex"],
                "y1": price, "y2": price, "color": color, "lineWidth": 1, "dashed": dashed,
            })


def _markers(events, bullish):
    result = [None] * n
    for confirmation in np.flatnonzero(events):
        source_index = confirmation - candidate_offset
        result[source_index] = {
            "active": True, "price": float(low[source_index] if bullish else high[source_index]),
            "text": "Bull" if bullish else "Bear", "color": bull_color if bullish else bear_color,
        }
    return result


bull_fill = [
    {"upper": float(bull_high[index]), "lower": float(bull_low[index]), "color": "rgba(0,128,0,0.50)" if color_scheme == "Light" else "rgba(255,255,255,0.50)"}
    if np.all(np.isfinite([bull_high[index], bull_low[index]])) else None for index in range(n)
]
bear_fill = [
    {"upper": float(bear_high[index]), "lower": float(bear_low[index]), "color": "rgba(255,0,0,0.50)" if color_scheme == "Light" else "rgba(0,0,255,0.50)"}
    if np.all(np.isfinite([bear_high[index], bear_low[index]])) else None for index in range(n)
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Bullish OB High", "data": _finite(bull_high), "color": bull_color, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Bullish OB Low", "data": _finite(bull_low), "color": bull_color, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Bullish OB Fill", "title": False, "data": bull_fill, "color": bull_color, "type": "cloud", "overlay": True},
        {"name": "Bearish OB High", "data": _finite(bear_high), "color": bear_color, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Bearish OB Low", "data": _finite(bear_low), "color": bear_color, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Bearish OB Fill", "title": False, "data": bear_fill, "color": bear_color, "type": "cloud", "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "name": "Bullish Order Block", "data": _markers(bull_event, True), "color": bull_color, "renderMode": "events"},
        {"type": "sell", "name": "Bearish Order Block", "data": _markers(bear_event, False), "color": bear_color, "renderMode": "events"},
    ],
    "layers": layers,
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "absoluteMovePercent": _finite(absolute_move), "upCount": up_count.tolist(), "downCount": down_count.tolist(),
        "bullEvent": bull_event.tolist(), "bearEvent": bear_event.tolist(),
        "bullHigh": _finite(bull_high), "bullLow": _finite(bull_low), "bullAverage": _finite(bull_average),
        "bearHigh": _finite(bear_high), "bearLow": _finite(bear_low), "bearAverage": _finite(bear_average),
        "allBlocks": all_blocks, "activeBullChannels": active_bull_channels, "activeBearChannels": active_bear_channels,
        "candidateOffset": candidate_offset, "relevantPeriods": relevant_periods,
    },
}
