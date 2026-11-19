# ============================================================
# QuantDinger Order Block Finder Experimental
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Order Block Finder Experimental"
my_indicator_description = "Finds the final opposite candle before a configurable uninterrupted directional move and records its order-block range."

# @param color_scheme str Dark Marker color scheme values=Dark,Bright
# @param relevant_periods int 5 Required consecutive candles range=1:100:1
# @param minimum_move_percent float 0 Minimum absolute move range=0:100:0.1
# @param use_whole_range bool false Use candle high and low for block bounds
# @param show_latest_bullish bool true Show latest bullish channel
# @param show_latest_bearish bool true Show latest bearish channel
# @param show_latest_panel bool false Show latest order-block values

color_scheme = str(params.get("color_scheme", "Dark")).title()
if color_scheme not in ("Dark", "Bright"):
    color_scheme = "Dark"
relevant_periods = min(100, max(1, int(params.get("relevant_periods", 5))))
minimum_move_percent = min(100.0, max(0.0, float(params.get("minimum_move_percent", 0.0))))
use_whole_range = bool(params.get("use_whole_range", False))
show_latest_bullish = bool(params.get("show_latest_bullish", True))
show_latest_bearish = bool(params.get("show_latest_bearish", True))
show_latest_panel = bool(params.get("show_latest_panel", False))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


df = df.copy().reset_index(drop=True)
if "time" not in df.columns:
    df["time"] = np.arange(len(df), dtype=float) * 60.0
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
blocks = []
latest_bull = {"high": 0.0, "average": 0.0, "low": 0.0, "formedIndex": None, "sourceIndex": None}
latest_bear = {"high": 0.0, "average": 0.0, "low": 0.0, "formedIndex": None, "sourceIndex": None}

for index in range(n):
    candidate = index - candidate_offset
    if candidate < 0 or index < 1:
        continue
    denominator = abs(close[candidate])
    absolute_move[index] = abs(close[candidate] - close[index - 1]) / denominator * 100.0 if denominator > 1e-12 else np.nan
    up_count[index] = int(np.count_nonzero(close[index - relevant_periods:index] > opened[index - relevant_periods:index]))
    down_count[index] = int(np.count_nonzero(close[index - relevant_periods:index] < opened[index - relevant_periods:index]))
    relevant_move = np.isfinite(absolute_move[index]) and absolute_move[index] >= minimum_move_percent

    if close[candidate] < opened[candidate] and up_count[index] == relevant_periods and relevant_move:
        top = float(high[candidate] if use_whole_range else opened[candidate])
        bottom = float(low[candidate])
        average = (top + bottom) / 2.0
        bull_event[index] = True
        bull_high[candidate], bull_low[candidate], bull_average[candidate] = top, bottom, average
        latest_bull = {"high": top, "average": average, "low": bottom, "formedIndex": int(index), "sourceIndex": int(candidate)}
        blocks.append({"side": "bull", "high": top, "average": average, "low": bottom, "formedIndex": int(index), "sourceIndex": int(candidate), "movePercent": float(absolute_move[index])})

    if close[candidate] > opened[candidate] and down_count[index] == relevant_periods and relevant_move:
        top = float(high[candidate])
        bottom = float(low[candidate] if use_whole_range else opened[candidate])
        average = (top + bottom) / 2.0
        bear_event[index] = True
        bear_high[candidate], bear_low[candidate], bear_average[candidate] = top, bottom, average
        latest_bear = {"high": top, "average": average, "low": bottom, "formedIndex": int(index), "sourceIndex": int(candidate)}
        blocks.append({"side": "bear", "high": top, "average": average, "low": bottom, "formedIndex": int(index), "sourceIndex": int(candidate), "movePercent": float(absolute_move[index])})

bull_color = "#16A34A" if color_scheme == "Dark" else "#22C55E"
bear_color = "#DC2626" if color_scheme == "Dark" else "#EF4444"
layers = []
for side, latest, enabled, color in (
    ("Bullish", latest_bull, show_latest_bullish, bull_color),
    ("Bearish", latest_bear, show_latest_bearish, bear_color),
):
    if not enabled or latest["formedIndex"] is None:
        continue
    for role, price, dashed in (
        ("Average", latest["average"], False),
        ("High", latest["high"], True),
        ("Low", latest["low"], True),
    ):
        layers.append({
            "type": "line", "role": f"Latest {side} OB {role}", "startIndex": 0, "endIndex": latest["formedIndex"],
            "y1": price, "y2": price, "color": color, "lineWidth": 1, "dashed": dashed,
        })


def _marker(events, bullish):
    result = [None] * n
    for confirmation in np.flatnonzero(events):
        source_index = confirmation - candidate_offset
        result[source_index] = {
            "active": True, "price": float(low[source_index] if bullish else high[source_index]),
            "text": "Bullish OB" if bullish else "Bearish OB", "color": bull_color if bullish else bear_color,
        }
    return result


bull_fill = [
    {"upper": float(bull_high[index]), "lower": float(bull_low[index]), "color": bull_color}
    if np.all(np.isfinite([bull_high[index], bull_low[index]])) else None for index in range(n)
]
bear_fill = [
    {"upper": float(bear_high[index]), "lower": float(bear_low[index]), "color": bear_color}
    if np.all(np.isfinite([bear_high[index], bear_low[index]])) else None for index in range(n)
]

tables = []
if show_latest_panel:
    tables.append({
        "title": "Latest Order Blocks", "position": "right-center", "compact": True,
        "columns": [{"text": "Side"}, {"text": "High"}, {"text": "Average"}, {"text": "Low"}],
        "rows": [
            [{"text": "Bullish", "color": bull_color}, {"text": f"{latest_bull['high']:.6g}"}, {"text": f"{latest_bull['average']:.6g}"}, {"text": f"{latest_bull['low']:.6g}"}],
            [{"text": "Bearish", "color": bear_color}, {"text": f"{latest_bear['high']:.6g}"}, {"text": f"{latest_bear['average']:.6g}"}, {"text": f"{latest_bear['low']:.6g}"}],
        ],
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Bullish OB High", "data": _finite(bull_high), "color": bull_color, "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "Bullish OB Low", "data": _finite(bull_low), "color": bull_color, "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "Bullish OB Fill", "title": False, "data": bull_fill, "color": bull_color, "type": "cloud", "overlay": True},
        {"name": "Bearish OB High", "data": _finite(bear_high), "color": bear_color, "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "Bearish OB Low", "data": _finite(bear_low), "color": bear_color, "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "Bearish OB Fill", "title": False, "data": bear_fill, "color": bear_color, "type": "cloud", "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "name": "Bullish Order Block", "data": _marker(bull_event, True), "color": bull_color, "renderMode": "events"},
        {"type": "sell", "name": "Bearish Order Block", "data": _marker(bear_event, False), "color": bear_color, "renderMode": "events"},
    ],
    "layers": layers,
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "absoluteMovePercent": _finite(absolute_move), "upCount": up_count.tolist(), "downCount": down_count.tolist(),
        "bullEvent": bull_event.tolist(), "bearEvent": bear_event.tolist(),
        "bullHigh": _finite(bull_high), "bullLow": _finite(bull_low), "bullAverage": _finite(bull_average),
        "bearHigh": _finite(bear_high), "bearLow": _finite(bear_low), "bearAverage": _finite(bear_average),
        "latestBull": latest_bull, "latestBear": latest_bear, "blocks": blocks,
        "candidateOffset": candidate_offset, "relevantPeriods": relevant_periods,
    },
}
