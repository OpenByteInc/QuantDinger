# ============================================================
# QuantDinger Leviathan Liquidity Sniper
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Leviathan Liquidity Sniper"
my_indicator_description = (
    "Tracks confirmed liquidity pivots, RSI-divergent sweeps, broken-level "
    "retests, and a velocity projection of the main EMA."
)

# @param liquidity_length int 29 Liquidity pivot period range=3:200:1
# @param rsi_length int 14 RSI period range=1:200:1
# @param ema_length int 200 Main EMA period range=1:1000:1
# @param projection_bars int 30 EMA future projection bars range=1:500:1
# @param inertia_smoothing int 5 Projection velocity lookback range=1:200:1
# @param ignore_gaps bool true Ignore gap signals
# @param show_simple_sweeps bool false Show sweeps without divergence

liquidity_length = min(200, max(3, int(params.get("liquidity_length", 29))))
rsi_length = min(200, max(1, int(params.get("rsi_length", 14))))
ema_length = min(1000, max(1, int(params.get("ema_length", 200))))
projection_bars = min(500, max(1, int(params.get("projection_bars", 30))))
inertia_smoothing = min(200, max(1, int(params.get("inertia_smoothing", 5))))
ignore_gaps = bool(params.get("ignore_gaps", True))
show_simple_sweeps = bool(params.get("show_simple_sweeps", False))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)
times = df["time"].to_numpy(dtype=float) if "time" in df.columns else np.arange(n, dtype=float) * 3600000.0
if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0
positive_intervals = np.diff(times)
positive_intervals = positive_intervals[np.isfinite(positive_intervals) & (positive_intervals > 0.0)]
bar_interval = float(np.median(positive_intervals)) if len(positive_intervals) else 3600000.0


def _ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    state = np.nan
    for index, value in enumerate(values):
        state = value if not np.isfinite(state) else alpha * value + (1.0 - alpha) * state
        result[index] = state
    return result


def _rsi(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if length == 1:
        for index in range(1, len(values)):
            difference = values[index] - values[index - 1]
            result[index] = 100.0 if difference > 0.0 else 0.0 if difference < 0.0 else 50.0
        return result
    if len(values) <= length:
        return result
    changes = np.diff(values)
    gains = np.maximum(changes, 0.0)
    losses = np.maximum(-changes, 0.0)
    average_gain = float(np.mean(gains[:length]))
    average_loss = float(np.mean(losses[:length]))
    for index in range(length, len(values)):
        if index > length:
            average_gain = (average_gain * (length - 1) + gains[index - 1]) / length
            average_loss = (average_loss * (length - 1) + losses[index - 1]) / length
        if average_gain == 0.0 and average_loss == 0.0:
            result[index] = 50.0
        elif average_loss == 0.0:
            result[index] = 100.0
        elif average_gain == 0.0:
            result[index] = 0.0
        else:
            result[index] = 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return result


ema_main = _ema(close, ema_length)
rsi = _rsi(close, rsi_length)
pivot_high = np.full(n, np.nan, dtype=float)
pivot_low = np.full(n, np.nan, dtype=float)
for confirmation in range(2 * liquidity_length, n):
    center = confirmation - liquidity_length
    left = center - liquidity_length
    right = center + liquidity_length + 1
    if high[center] >= np.max(high[left:right]):
        pivot_high[confirmation] = high[center]
    if low[center] <= np.min(low[left:right]):
        pivot_low[confirmation] = low[center]

top_liquidity = np.full(n, np.nan, dtype=float)
bottom_liquidity = np.full(n, np.nan, dtype=float)
top_rsi = np.full(n, np.nan, dtype=float)
bottom_rsi = np.full(n, np.nan, dtype=float)
broken_support = np.full(n, np.nan, dtype=float)
broken_resistance = np.full(n, np.nan, dtype=float)
sweep_up = np.zeros(n, dtype=bool)
sweep_down = np.zeros(n, dtype=bool)
bear_divergence = np.zeros(n, dtype=bool)
bull_divergence = np.zeros(n, dtype=bool)
short_retest = np.zeros(n, dtype=bool)
long_retest = np.zeros(n, dtype=bool)

state_top = np.nan
state_bottom = np.nan
state_top_rsi = np.nan
state_bottom_rsi = np.nan
state_broken_support = np.nan
state_broken_resistance = np.nan

for index in range(n):
    if np.isfinite(pivot_high[index]):
        center = index - liquidity_length
        state_top = float(high[center])
        state_top_rsi = float(rsi[center]) if np.isfinite(rsi[center]) else np.nan
    if np.isfinite(pivot_low[index]):
        center = index - liquidity_length
        state_bottom = float(low[center])
        state_bottom_rsi = float(rsi[center]) if np.isfinite(rsi[center]) else np.nan

    if index > 0 and np.isfinite(state_bottom) and close[index] < state_bottom and close[index - 1] >= state_bottom:
        state_broken_support = state_bottom
    if index > 0 and np.isfinite(state_top) and close[index] > state_top and close[index - 1] <= state_top:
        state_broken_resistance = state_top

    gap = index > 0 and (low[index] > high[index - 1] or high[index] < low[index - 1])
    gap_allowed = not gap or not ignore_gaps
    sweep_up[index] = np.isfinite(state_top) and high[index] > state_top and close[index] < state_top and gap_allowed
    sweep_down[index] = np.isfinite(state_bottom) and low[index] < state_bottom and close[index] > state_bottom and gap_allowed
    bear_divergence[index] = sweep_up[index] and np.isfinite(state_top_rsi) and np.isfinite(rsi[index]) and rsi[index] < state_top_rsi
    bull_divergence[index] = sweep_down[index] and np.isfinite(state_bottom_rsi) and np.isfinite(rsi[index]) and rsi[index] > state_bottom_rsi
    short_retest[index] = np.isfinite(state_broken_support) and high[index] >= state_broken_support and close[index] < state_broken_support and open_price[index] < state_broken_support
    long_retest[index] = np.isfinite(state_broken_resistance) and low[index] <= state_broken_resistance and close[index] > state_broken_resistance and open_price[index] > state_broken_resistance

    top_liquidity[index] = state_top
    bottom_liquidity[index] = state_bottom
    top_rsi[index] = state_top_rsi
    bottom_rsi[index] = state_bottom_rsi
    broken_support[index] = state_broken_support
    broken_resistance[index] = state_broken_resistance


def _first_event(values):
    result = np.zeros(len(values), dtype=bool)
    for index in range(len(values)):
        result[index] = bool(values[index] and (index == 0 or not values[index - 1]))
    return result


bear_div_event = _first_event(bear_divergence)
bull_div_event = _first_event(bull_divergence)
short_retest_event = _first_event(short_retest)
long_retest_event = _first_event(long_retest)
simple_up_event = _first_event(sweep_up & ~bear_divergence)
simple_down_event = _first_event(sweep_down & ~bull_divergence)

top_plot = [None] * n
bottom_plot = [None] * n
for index in range(liquidity_length, n):
    source_index = index
    target_index = index - liquidity_length
    if np.isfinite(top_liquidity[source_index]):
        top_plot[target_index] = float(top_liquidity[source_index])
    if np.isfinite(bottom_liquidity[source_index]):
        bottom_plot[target_index] = float(bottom_liquidity[source_index])

layers = []
if n and n > inertia_smoothing and np.isfinite(ema_main[-1]) and np.isfinite(ema_main[-1 - inertia_smoothing]):
    velocity = (ema_main[-1] - ema_main[-1 - inertia_smoothing]) / inertia_smoothing
    projected = ema_main[-1] + velocity * projection_bars
    layers.append({
        "type": "line", "role": "EMA Projection",
        "start": float(times[-1]), "end": float(times[-1] + bar_interval * projection_bars),
        "y1": float(ema_main[-1]), "y2": float(projected),
        "color": "rgba(255,235,59,0.60)", "lineWidth": 2, "dashed": True,
    })


def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]


signals = [
    {"type": "sell", "name": "Simple Top Sweep", "text": "SWEEP", "data": [float(high[i]) if show_simple_sweeps and simple_up_event[i] else None for i in range(n)], "color": "#F44336", "renderMode": "events" if show_simple_sweeps else "hidden"},
    {"type": "buy", "name": "Simple Bottom Sweep", "text": "SWEEP", "data": [float(low[i]) if show_simple_sweeps and simple_down_event[i] else None for i in range(n)], "color": "#4CAF50", "renderMode": "events" if show_simple_sweeps else "hidden"},
    {"type": "sell", "name": "Bearish Sweep Divergence", "text": "BEAR DIV", "data": [float(high[i]) if bear_div_event[i] else None for i in range(n)], "color": "#9C27B0", "renderMode": "events"},
    {"type": "buy", "name": "Bullish Sweep Divergence", "text": "BULL DIV", "data": [float(low[i]) if bull_div_event[i] else None for i in range(n)], "color": "#00897B", "renderMode": "events"},
    {"type": "sell", "name": "Short Retest", "text": "RETEST SHORT", "data": [float(high[i]) if short_retest_event[i] else None for i in range(n)], "color": "#FF9800", "renderMode": "events"},
    {"type": "buy", "name": "Long Retest", "text": "RETEST LONG", "data": [float(low[i]) if long_retest_event[i] else None for i in range(n)], "color": "#00FFFF", "renderMode": "events"},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Main EMA", "data": _finite_or_none(ema_main), "color": "#FFEB3B", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Top Liquidity", "data": top_plot, "color": "rgba(244,67,54,0.70)", "type": "circle", "size": 2, "overlay": True},
        {"name": "Bottom Liquidity", "data": bottom_plot, "color": "rgba(76,175,80,0.70)", "type": "circle", "size": 2, "overlay": True},
        {"name": "Broken Support", "data": _finite_or_none(broken_support), "color": "rgba(255,152,0,0.50)", "type": "circle", "size": 2, "overlay": True},
        {"name": "Broken Resistance", "data": _finite_or_none(broken_resistance), "color": "rgba(0,255,255,0.50)", "type": "circle", "size": 2, "overlay": True},
    ],
    "signals": signals,
    "layers": layers,
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "ema": _finite_or_none(ema_main), "rsi": _finite_or_none(rsi),
        "pivotHigh": _finite_or_none(pivot_high), "pivotLow": _finite_or_none(pivot_low),
        "topLiquidity": _finite_or_none(top_liquidity), "bottomLiquidity": _finite_or_none(bottom_liquidity),
        "topRsi": _finite_or_none(top_rsi), "bottomRsi": _finite_or_none(bottom_rsi),
        "brokenSupport": _finite_or_none(broken_support), "brokenResistance": _finite_or_none(broken_resistance),
        "sweepUp": sweep_up.tolist(), "sweepDown": sweep_down.tolist(),
        "bearDivergence": bear_divergence.tolist(), "bullDivergence": bull_divergence.tolist(),
        "bearDivergenceEvent": bear_div_event.tolist(), "bullDivergenceEvent": bull_div_event.tolist(),
        "shortRetest": short_retest.tolist(), "longRetest": long_retest.tolist(),
        "shortRetestEvent": short_retest_event.tolist(), "longRetestEvent": long_retest_event.tolist(),
        "simpleUpEvent": simple_up_event.tolist(), "simpleDownEvent": simple_down_event.tolist(),
        "barInterval": bar_interval,
    },
}
