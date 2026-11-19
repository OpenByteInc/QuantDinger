# ============================================================
# QuantDinger CBC Flip
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger CBC Flip"
my_indicator_description = (
    "A two-state close breakout controller that flips bullish above the prior "
    "high and bearish below the prior low."
)

# @param show_flip_highlight bool true Highlight flip bars

show_flip_highlight = bool(params.get("show_flip_highlight", True))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

state = np.zeros(n, dtype=bool)
for index in range(1, n):
    current = bool(state[index - 1])
    if current and close[index] < low[index - 1]:
        current = False
    if not current and close[index] > high[index - 1]:
        current = True
    state[index] = current

bull_flip = np.concatenate(([False], state[1:] & ~state[:-1]))
bear_flip = np.concatenate(([False], ~state[1:] & state[:-1]))
bull_marks = [float(low[index] * 0.995) if bull_flip[index] else None for index in range(n)]
bear_marks = [float(high[index] * 1.005) if bear_flip[index] else None for index in range(n)]
flip_cloud = []
for index in range(n):
    if not show_flip_highlight or not (bull_flip[index] or bear_flip[index]):
        flip_cloud.append(None)
    else:
        flip_cloud.append(
            {
                "upper": float(high[index]),
                "lower": float(low[index]),
                "color": "#FFEB3B" if bull_flip[index] else "#05B9F0",
            }
        )

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "State Anchor", "title": False, "data": [float(value) for value in close], "color": "rgba(0, 0, 0, 0)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Flip Highlight", "title": False, "data": flip_cloud, "color": "#FFEB3B", "type": "cloud", "opacity": 0.28, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "BULL", "name": "Bulls", "data": bull_marks, "color": "#78CEB0", "renderMode": "events"},
        {"type": "sell", "text": "BEAR", "name": "Bears", "data": bear_marks, "color": "#E46666", "renderMode": "events"},
    ],
    "layers": [],
}
