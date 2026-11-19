# ============================================================
# QuantDinger Reversal
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Reversal"
my_indicator_description = (
    "A range-displacement reversal state machine that activates an extreme "
    "candle and confirms a directional break within a limited number of bars."
)

# @param lookback int 20 Candle lookback range=2:200:1
# @param confirmation int 3 Confirmation window range=1:30:1
# @param non_repaint bool false Delay confirmation by one bar

lookback = max(2, int(params.get("lookback", 20)))
confirmation = max(1, int(params.get("confirmation", 3)))
non_repaint = bool(params.get("non_repaint", False))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

bull_active = False
bull_low = 0.0
bull_high = 0.0
bull_signal_active = False
bull_count = 0
bear_active = False
bear_low = 0.0
bear_high = 0.0
bear_signal_active = False
bear_count = 0
buy_event = np.zeros(n, dtype=bool)
sell_event = np.zeros(n, dtype=bool)

previous_bull_active = False
previous_bull_high = 0.0
previous_bull_signal_active = False
previous_bull_count = 0
previous_bear_active = False
previous_bear_low = 0.0
previous_bear_signal_active = False
previous_bear_count = 0

for index in range(n):
    bull_candle = 0
    bear_candle = 0
    for offset in range(lookback):
        historical = index - offset
        if historical < 0:
            continue
        if close[index] < low[historical]:
            bull_candle += 1
        if close[index] > high[historical]:
            bear_candle += 1

    if bull_candle == lookback - 1:
        bull_active = True
        bull_low = low[index]
        bull_high = high[index]
        bull_signal_active = False
        bull_count = 0
    if bull_active:
        bull_count += 1

    if bear_candle == lookback - 1:
        bear_active = True
        bear_low = low[index]
        bear_high = high[index]
        bear_signal_active = False
        bear_count = 0
    if bear_active:
        bear_count += 1

    if close[index] < bull_low:
        bull_active = False
    if close[index] > bear_high:
        bear_active = False

    if non_repaint:
        if (
            index > 0
            and previous_bull_active
            and close[index - 1] > previous_bull_high
            and not previous_bull_signal_active
            and previous_bull_count <= confirmation + 1
        ):
            bull_signal_active = True
            buy_event[index] = True
        if (
            index > 0
            and previous_bear_active
            and close[index - 1] < previous_bear_low
            and not previous_bear_signal_active
            and previous_bear_count <= confirmation + 1
        ):
            bear_signal_active = True
            sell_event[index] = True
    else:
        if bull_active and close[index] > bull_high and not bull_signal_active and bull_count <= confirmation + 1:
            bull_signal_active = True
            buy_event[index] = True
        if bear_active and close[index] < bear_low and not bear_signal_active and bear_count <= confirmation + 1:
            bear_signal_active = True
            sell_event[index] = True

    previous_bull_active = bull_active
    previous_bull_high = bull_high
    previous_bull_signal_active = bull_signal_active
    previous_bull_count = bull_count
    previous_bear_active = bear_active
    previous_bear_low = bear_low
    previous_bear_signal_active = bear_signal_active
    previous_bear_count = bear_count

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Event Anchor",
            "title": False,
            "data": [float(value) for value in close],
            "color": "rgba(0, 0, 0, 0)",
            "type": "line",
            "lineWidth": 1,
            "overlay": True,
        }
    ],
    "signals": [
        {
            "type": "buy",
            "text": "REV UP",
            "name": "Bullish Reversal",
            "data": [float(low[index]) if buy_event[index] else None for index in range(n)],
            "color": "#00E676",
            "renderMode": "events",
        },
        {
            "type": "sell",
            "text": "REV DOWN",
            "name": "Bearish Reversal",
            "data": [float(high[index]) if sell_event[index] else None for index in range(n)],
            "color": "#EF4444",
            "renderMode": "events",
        },
    ],
    "layers": [],
}
