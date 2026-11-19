# ============================================================
# QuantDinger CBC Flip
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger CBC Flip"
my_indicator_description = (
    "Maintains a bullish or bearish control state and flips only when the close "
    "breaks the previous candle high or low."
)

# @param show_background bool true Highlight state changes
# @param bullish_color str #78CEB0 Bullish marker color
# @param bearish_color str #E46666 Bearish marker color
# @param bullish_background str rgba(255,235,59,0.29) Bullish flip background
# @param bearish_background str rgba(5,185,240,0.41) Bearish flip background
# @param max_signals int 14 Maximum visible flip labels range=1:50:1
# @param signal_spacing int 5 Minimum bars between visible labels range=0:20:1
# @param signal_lookback int 180 Label display lookback bars range=20:500:10

show_background = bool(params.get("show_background", True))
bullish_color = str(params.get("bullish_color", "#78CEB0"))
bearish_color = str(params.get("bearish_color", "#E46666"))
bullish_background = str(params.get("bullish_background", "rgba(255,235,59,0.29)"))
bearish_background = str(params.get("bearish_background", "rgba(5,185,240,0.41)"))
max_signals = min(50, max(1, int(params.get("max_signals", 14))))
signal_spacing = min(20, max(0, int(params.get("signal_spacing", 5))))
signal_lookback = min(500, max(20, int(params.get("signal_lookback", 180))))

frame = df.copy()
frame = frame.reset_index(drop=True)
n = len(frame)
high_values = pd.to_numeric(frame["high"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
low_values = pd.to_numeric(frame["low"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
close_values = pd.to_numeric(frame["close"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)

bull_control = np.zeros(n, dtype=bool)
bull_flip = np.zeros(n, dtype=bool)
bear_flip = np.zeros(n, dtype=bool)
state = False
for index in range(1, n):
    previous_state = state
    if state and close_values[index] < low_values[index - 1]:
        state = False
    if not state and close_values[index] > high_values[index - 1]:
        state = True
    bull_control[index] = state
    bull_flip[index] = state and not previous_state
    bear_flip[index] = not state and previous_state

visible_flip_indexes = [
    index
    for index in range(max(0, n - signal_lookback), n)
    if bull_flip[index] or bear_flip[index]
]
if n > 50 and signal_spacing > 0:
    spaced_indexes = []
    for index in visible_flip_indexes:
        if spaced_indexes and index - spaced_indexes[-1] < signal_spacing:
            spaced_indexes[-1] = index
        else:
            spaced_indexes.append(index)
    visible_flip_indexes = spaced_indexes
visible_flip_indexes = set(visible_flip_indexes[-max_signals:])

bull_marks = [
    float(low_values[index]) if bull_flip[index] and index in visible_flip_indexes else None
    for index in range(n)
]
bear_marks = [
    float(high_values[index]) if bear_flip[index] and index in visible_flip_indexes else None
    for index in range(n)
]
background_colors = []
for index in range(n):
    if not show_background:
        background_colors.append(None)
    elif bull_flip[index]:
        background_colors.append(bullish_background)
    elif bear_flip[index]:
        background_colors.append(bearish_background)
    else:
        background_colors.append(None)

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [{
        "name": "CBC Alignment Anchor",
        "title": False,
        "data": [float(value) for value in close_values],
        "color": "rgba(0,0,0,0)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    }],
    "signals": [
        {"type": "buy", "text": "BULL", "name": "Bulls", "data": bull_marks, "color": bullish_color, "renderMode": "events"},
        {"type": "sell", "text": "BEAR", "name": "Bears", "data": bear_marks, "color": bearish_color, "renderMode": "events"},
    ],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": background_colors,
    "calculatedVars": {
        "bullControl": [bool(value) for value in bull_control],
        "bullFlip": [bool(value) for value in bull_flip],
        "bearFlip": [bool(value) for value in bear_flip],
    },
}
