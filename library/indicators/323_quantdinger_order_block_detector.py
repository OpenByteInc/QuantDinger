# ============================================================
# QuantDinger Order Block Detector
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Order Block Detector"
my_indicator_description = "Detects volume-pivot order blocks, extends the newest active zones, and removes them after wick- or close-based mitigation."

# @param pivot_length int 5 Symmetric volume-pivot length range=1:100:1
# @param bullish_blocks int 3 Bullish blocks to display range=1:100:1
# @param bearish_blocks int 3 Bearish blocks to display range=1:100:1
# @param mitigation_method str Wick Mitigation source values=Wick,Close
# @param average_line_style str Solid Average-line style values=Solid,Dashed,Dotted
# @param average_line_width int 1 Average-line width range=1:5:1
# @param show_formation_markers bool false Show order-block formation markers

pivot_length = min(100, max(1, int(params.get("pivot_length", 5))))
bullish_blocks = min(100, max(1, int(params.get("bullish_blocks", 3))))
bearish_blocks = min(100, max(1, int(params.get("bearish_blocks", 3))))
mitigation_method = str(params.get("mitigation_method", "Wick")).title()
if mitigation_method not in ("Wick", "Close"):
    mitigation_method = "Wick"
average_line_style = str(params.get("average_line_style", "Solid")).title()
if average_line_style not in ("Solid", "Dashed", "Dotted"):
    average_line_style = "Solid"
average_line_width = min(5, max(1, int(params.get("average_line_width", 1))))
show_formation_markers = bool(params.get("show_formation_markers", False))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close", "volume"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].to_numpy(dtype=float)
n = len(df)

oscillator_state = np.zeros(n, dtype=int)
volume_pivot = np.zeros(n, dtype=bool)
bull_formed = np.zeros(n, dtype=bool)
bear_formed = np.zeros(n, dtype=bool)
bull_mitigated = np.zeros(n, dtype=bool)
bear_mitigated = np.zeros(n, dtype=bool)
bull_plot = np.full(n, np.nan, dtype=float)
bear_plot = np.full(n, np.nan, dtype=float)
active_bull = []
active_bear = []
created_blocks = []
mitigated_blocks = []
state = 0

for index in range(n):
    start = max(0, index - pivot_length + 1)
    upper = float(np.max(high[start:index + 1]))
    lower = float(np.min(low[start:index + 1]))
    if index >= pivot_length:
        center = index - pivot_length
        if high[center] > upper:
            state = 0
        elif low[center] < lower:
            state = 1
    oscillator_state[index] = state

    center = index - pivot_length
    if index >= pivot_length * 2:
        window = volume[center - pivot_length:center + pivot_length + 1]
        volume_pivot[index] = bool(np.isfinite(volume[center]) and volume[center] == np.max(window))

    if volume_pivot[index] and volume[center] != 0.0:
        if state == 1:
            block = {
                "side": "bull", "top": float((high[center] + low[center]) / 2.0), "bottom": float(low[center]),
                "average": float((high[center] + 3.0 * low[center]) / 4.0), "leftIndex": int(center),
                "formedIndex": int(index), "mitigatedIndex": None,
            }
            active_bull.insert(0, block)
            created_blocks.append(block.copy())
            bull_formed[index] = True
            bull_plot[center] = float(low[center])
        elif state == 0:
            block = {
                "side": "bear", "top": float(high[center]), "bottom": float((high[center] + low[center]) / 2.0),
                "average": float((3.0 * high[center] + low[center]) / 4.0), "leftIndex": int(center),
                "formedIndex": int(index), "mitigatedIndex": None,
            }
            active_bear.insert(0, block)
            created_blocks.append(block.copy())
            bear_formed[index] = True
            bear_plot[center] = float(high[center])

    target_bull = float(np.min((close if mitigation_method == "Close" else low)[start:index + 1]))
    target_bear = float(np.max((close if mitigation_method == "Close" else high)[start:index + 1]))
    remaining_bull = []
    for block in active_bull:
        if target_bull < block["bottom"]:
            item = block.copy()
            item["mitigatedIndex"] = int(index)
            mitigated_blocks.append(item)
            bull_mitigated[index] = True
        else:
            remaining_bull.append(block)
    active_bull = remaining_bull
    remaining_bear = []
    for block in active_bear:
        if target_bear > block["top"]:
            item = block.copy()
            item["mitigatedIndex"] = int(index)
            mitigated_blocks.append(item)
            bear_mitigated[index] = True
        else:
            remaining_bear.append(block)
    active_bear = remaining_bear

layers = []
style = average_line_style.lower()
for block in active_bull[:bullish_blocks] + active_bear[:bearish_blocks]:
    bullish = block["side"] == "bull"
    fill = "rgba(22,148,0,0.20)" if bullish else "rgba(255,17,0,0.20)"
    border = "rgba(22,148,0,0.30)" if bullish else "rgba(255,17,0,0.30)"
    layers.append({
        "type": "zone", "role": "Bullish Order Block" if bullish else "Bearish Order Block",
        "startIndex": block["leftIndex"], "endIndex": max(block["leftIndex"], n - 1),
        "top": block["top"], "bottom": block["bottom"], "fillColor": fill, "borderColor": border, "borderSize": 1,
    })
    layers.append({
        "type": "line", "role": "Bullish OB Average" if bullish else "Bearish OB Average",
        "startIndex": block["leftIndex"], "endIndex": max(block["leftIndex"], n - 1),
        "y1": block["average"], "y2": block["average"], "color": "rgba(149,152,161,0.63)",
        "lineWidth": average_line_width, "dashed": style == "dashed", "dotted": style == "dotted",
    })


def _formation_points(events, bullish):
    if not show_formation_markers:
        return [None] * n
    result = [None] * n
    for confirmation in np.flatnonzero(events):
        center = confirmation - pivot_length
        result[center] = {
            "active": True, "price": float(low[center] if bullish else high[center]),
            "text": "Bull OB" if bullish else "Bear OB", "color": "#169400" if bullish else "#FF1100",
        }
    return result


signals = [
    {"type": "buy", "name": "Bullish Order Block Formed", "data": _formation_points(bull_formed, True), "color": "#169400", "renderMode": "events"},
    {"type": "sell", "name": "Bearish Order Block Formed", "data": _formation_points(bear_formed, False), "color": "#FF1100", "renderMode": "events"},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Bull OB Source", "title": False, "data": _finite(bull_plot), "color": "rgba(22,148,0,0.01)", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Bear OB Source", "title": False, "data": _finite(bear_plot), "color": "rgba(255,17,0,0.01)", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": signals,
    "layers": layers,
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "oscillatorState": oscillator_state.tolist(), "volumePivot": volume_pivot.tolist(),
        "bullFormed": bull_formed.tolist(), "bearFormed": bear_formed.tolist(),
        "bullMitigated": bull_mitigated.tolist(), "bearMitigated": bear_mitigated.tolist(),
        "bullPlot": _finite(bull_plot), "bearPlot": _finite(bear_plot),
        "activeBull": active_bull, "activeBear": active_bear,
        "createdBlocks": created_blocks, "mitigatedBlocks": mitigated_blocks,
        "pivotLength": pivot_length, "mitigationMethod": mitigation_method,
    },
}
