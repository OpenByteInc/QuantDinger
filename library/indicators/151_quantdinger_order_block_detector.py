# ============================================================
# QuantDinger Order Block Detector
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; alerts do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Order Block Detector"
my_indicator_description = (
    "Detects volume-pivot order blocks, removes blocks after wick or close mitigation, "
    "and extends the newest bullish and bearish zones with configurable average lines."
)

# @param pivot_length int 5 Volume pivot length range=1:100:1
# @param bullish_blocks int 3 Bullish blocks to retain range=1:50:1
# @param bearish_blocks int 3 Bearish blocks to retain range=1:50:1
# @param mitigation_method str Wick Mitigation method values=Wick,Close
# @param average_line_style str Solid Average line style values=Solid,Dashed,Dotted
# @param average_line_width int 1 Average line width range=1:5:1
# @param bullish_fill str rgba(22,148,0,0.20) Bullish fill color
# @param bullish_border str #169400 Bullish border color
# @param bullish_average str rgba(149,152,161,0.63) Bullish average color
# @param bearish_fill str rgba(255,17,0,0.20) Bearish fill color
# @param bearish_border str #FF1100 Bearish border color
# @param bearish_average str rgba(149,152,161,0.63) Bearish average color

pivot_length = min(100, max(1, int(params.get("pivot_length", 5))))
bullish_blocks = min(50, max(1, int(params.get("bullish_blocks", 3))))
bearish_blocks = min(50, max(1, int(params.get("bearish_blocks", 3))))
mitigation_method = str(params.get("mitigation_method", "Wick"))
average_line_style = str(params.get("average_line_style", "Solid"))
average_line_width = min(5, max(1, int(params.get("average_line_width", 1))))
bullish_fill = str(params.get("bullish_fill", "rgba(22,148,0,0.20)"))
bullish_border = str(params.get("bullish_border", "#169400"))
bullish_average = str(params.get("bullish_average", "rgba(149,152,161,0.63)"))
bearish_fill = str(params.get("bearish_fill", "rgba(255,17,0,0.20)"))
bearish_border = str(params.get("bearish_border", "#FF1100"))
bearish_average = str(params.get("bearish_average", "rgba(149,152,161,0.63)"))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)

rolling_high = pd.Series(high).rolling(pivot_length, min_periods=pivot_length).max().to_numpy(dtype=float)
rolling_low = pd.Series(low).rolling(pivot_length, min_periods=pivot_length).min().to_numpy(dtype=float)
rolling_close_high = pd.Series(close).rolling(pivot_length, min_periods=pivot_length).max().to_numpy(dtype=float)
rolling_close_low = pd.Series(close).rolling(pivot_length, min_periods=pivot_length).min().to_numpy(dtype=float)

market_side = np.zeros(n, dtype=int)
bullish_active = []
bearish_active = []
bullish_formed = [None for _ in range(n)]
bearish_formed = [None for _ in range(n)]
bullish_mitigated = [None for _ in range(n)]
bearish_mitigated = [None for _ in range(n)]

def is_volume_pivot(confirmation_index):
    center = confirmation_index - pivot_length
    if center < pivot_length or confirmation_index >= n:
        return False
    candidate = volume[center]
    if not np.isfinite(candidate):
        return False
    window = volume[center - pivot_length:confirmation_index + 1]
    if len(window) != pivot_length * 2 + 1 or not np.all(np.isfinite(window)):
        return False
    other_values = np.concatenate((window[:pivot_length], window[pivot_length + 1:]))
    return bool(np.all(candidate > other_values))

for index in range(n):
    previous_side = market_side[index - 1] if index else 0
    market_side[index] = previous_side
    source_index = index - pivot_length
    if source_index >= 0 and np.isfinite(rolling_high[index]) and high[source_index] > rolling_high[index]:
        market_side[index] = 0
    elif source_index >= 0 and np.isfinite(rolling_low[index]) and low[source_index] < rolling_low[index]:
        market_side[index] = 1

    if is_volume_pivot(index):
        origin = index - pivot_length
        if market_side[index] == 1:
            top = float((high[origin] + low[origin]) / 2.0)
            bottom = float(low[origin])
            bullish_active.insert(
                0,
                {"origin": int(origin), "top": top, "bottom": bottom, "average": float((top + bottom) / 2.0)},
            )
            bullish_formed[origin] = float(bottom)
        else:
            top = float(high[origin])
            bottom = float((high[origin] + low[origin]) / 2.0)
            bearish_active.insert(
                0,
                {"origin": int(origin), "top": top, "bottom": bottom, "average": float((top + bottom) / 2.0)},
            )
            bearish_formed[origin] = float(top)

    bull_target = rolling_close_low[index] if mitigation_method == "Close" else rolling_low[index]
    bear_target = rolling_close_high[index] if mitigation_method == "Close" else rolling_high[index]
    if np.isfinite(bull_target):
        surviving = []
        removed = False
        for block in bullish_active:
            if bull_target < block["bottom"]:
                removed = True
            else:
                surviving.append(block)
        bullish_active = surviving
        if removed:
            bullish_mitigated[index] = float(low[index])
    if np.isfinite(bear_target):
        surviving = []
        removed = False
        for block in bearish_active:
            if bear_target > block["top"]:
                removed = True
            else:
                surviving.append(block)
        bearish_active = surviving
        if removed:
            bearish_mitigated[index] = float(high[index])

layers = []
dashed = average_line_style != "Solid"
for block in bullish_active[:bullish_blocks]:
    layers.append(
        {
            "type": "zone",
            "startIndex": int(block["origin"]),
            "endIndex": max(0, n - 1),
            "top": float(block["top"]),
            "bottom": float(block["bottom"]),
            "fillColor": bullish_fill,
            "borderColor": bullish_border,
            "borderSize": 1,
            "extendRight": True,
        }
    )
    layers.append(
        {
            "type": "line",
            "startIndex": int(block["origin"]),
            "endIndex": max(0, n - 1),
            "y1": float(block["average"]),
            "y2": float(block["average"]),
            "color": bullish_average,
            "lineWidth": average_line_width,
            "dashed": dashed,
            "lineStyle": average_line_style.lower(),
            "extendRight": True,
        }
    )

for block in bearish_active[:bearish_blocks]:
    layers.append(
        {
            "type": "zone",
            "startIndex": int(block["origin"]),
            "endIndex": max(0, n - 1),
            "top": float(block["top"]),
            "bottom": float(block["bottom"]),
            "fillColor": bearish_fill,
            "borderColor": bearish_border,
            "borderSize": 1,
            "extendRight": True,
        }
    )
    layers.append(
        {
            "type": "line",
            "startIndex": int(block["origin"]),
            "endIndex": max(0, n - 1),
            "y1": float(block["average"]),
            "y2": float(block["average"]),
            "color": bearish_average,
            "lineWidth": average_line_width,
            "dashed": dashed,
            "lineStyle": average_line_style.lower(),
            "extendRight": True,
        }
    )

price_guide = [float(value) if np.isfinite(value) else None for value in close]
plots = [
    {
        "name": "Price Reference",
        "title": False,
        "data": price_guide,
        "color": "rgba(255,255,255,0.01)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    }
]

signals = []
if any(item is not None for item in bullish_formed):
    signals.append({"type": "buy", "text": "Bullish OB", "name": "Bullish Block Formed", "data": bullish_formed, "color": bullish_border, "renderMode": "events"})
if any(item is not None for item in bearish_formed):
    signals.append({"type": "sell", "text": "Bearish OB", "name": "Bearish Block Formed", "data": bearish_formed, "color": bearish_border, "renderMode": "events"})
if any(item is not None for item in bullish_mitigated):
    signals.append({"type": "sell", "text": "Bull OB Mitigated", "name": "Bullish Block Mitigated", "data": bullish_mitigated, "color": "#FF9800", "renderMode": "events"})
if any(item is not None for item in bearish_mitigated):
    signals.append({"type": "buy", "text": "Bear OB Mitigated", "name": "Bearish Block Mitigated", "data": bearish_mitigated, "color": "#03A9F4", "renderMode": "events"})

output = {
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": layers,
    "events": {
        "bullishFormed": bullish_formed,
        "bearishFormed": bearish_formed,
        "bullishMitigated": bullish_mitigated,
        "bearishMitigated": bearish_mitigated,
    },
}
