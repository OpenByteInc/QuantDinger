# ============================================================
# QuantDinger CCI Stochastic
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger CCI Stochastic"
my_indicator_description = (
    "Applies a stochastic transform to CCI, smooths selectable K or D output, "
    "and marks entries, exits, and optional center-line crosses."
)

# @param source str Close Price source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param cci_period int 28 CCI period range=1:500:1
# @param stochastic_period int 28 Stochastic period range=1:500:1
# @param smooth_k int 3 K smoothing range=1:100:1
# @param smooth_d int 3 D smoothing range=1:100:1
# @param active_line str D Active line values=D,K
# @param overbought float 80.0 Overbought level range=50.0:100.0:1.0
# @param oversold float 20.0 Oversold level range=0.0:50.0:1.0
# @param show_arrows bool true Show event arrows
# @param show_enter bool true Show zone-entry events
# @param show_center bool false Show center-line events
# @param show_exit bool true Show zone-exit events
# @param max_visible_signals int 24 Maximum visible event labels range=1:100:1

source_name = str(params.get("source", "Close"))
cci_period = min(500, max(1, int(params.get("cci_period", 28))))
stochastic_period = min(500, max(1, int(params.get("stochastic_period", 28))))
smooth_k = min(100, max(1, int(params.get("smooth_k", 3))))
smooth_d = min(100, max(1, int(params.get("smooth_d", 3))))
active_line = str(params.get("active_line", "D")).upper()
overbought = min(100.0, max(50.0, float(params.get("overbought", 80.0))))
oversold = min(50.0, max(0.0, float(params.get("oversold", 20.0))))
show_arrows = bool(params.get("show_arrows", True))
show_enter = bool(params.get("show_enter", True))
show_center = bool(params.get("show_center", False))
show_exit = bool(params.get("show_exit", True))
max_visible_signals = min(100, max(1, int(params.get("max_visible_signals", 24))))

frame = df.copy()
frame = frame.reset_index(drop=True)
n = len(frame)
open_values = pd.to_numeric(frame["open"], errors="coerce").ffill().fillna(0.0)
high_values = pd.to_numeric(frame["high"], errors="coerce").ffill().fillna(0.0)
low_values = pd.to_numeric(frame["low"], errors="coerce").ffill().fillna(0.0)
close_values = pd.to_numeric(frame["close"], errors="coerce").ffill().fillna(0.0)

source_key = source_name.replace("_", "").replace(" ", "").upper()
source_map = {
    "OPEN": open_values,
    "HIGH": high_values,
    "LOW": low_values,
    "HL2": (high_values + low_values) / 2.0,
    "HLC3": (high_values + low_values + close_values) / 3.0,
    "OHLC4": (open_values + high_values + low_values + close_values) / 4.0,
}
source = source_map.get(source_key, close_values).astype(float)

cci_basis = source.rolling(window=cci_period, min_periods=cci_period).mean()
cci_deviation = source.rolling(window=cci_period, min_periods=cci_period).apply(
    lambda values: float(np.mean(np.abs(values - np.mean(values)))),
    raw=True,
)
cci_denominator = 0.015 * cci_deviation
cci = (source - cci_basis) / cci_denominator.where(cci_denominator != 0.0, np.nan)

cci_low = cci.rolling(window=stochastic_period, min_periods=stochastic_period).min()
cci_high = cci.rolling(window=stochastic_period, min_periods=stochastic_period).max()
cci_range = cci_high - cci_low
raw_k = 100.0 * (cci - cci_low) / cci_range.where(cci_range != 0.0, np.nan)
k_line = raw_k.rolling(window=smooth_k, min_periods=smooth_k).mean()
d_line = k_line.rolling(window=smooth_d, min_periods=smooth_d).mean()
active = d_line if active_line == "D" else k_line


def crossover(values, level):
    return (values > level) & (values.shift(1) <= level)


def crossunder(values, level):
    return (values < level) & (values.shift(1) >= level)


enter_buy = crossunder(active, oversold).fillna(False)
enter_sell = crossover(active, overbought).fillna(False)
exit_buy = crossover(active, oversold).fillna(False)
exit_sell = crossunder(active, overbought).fillna(False)
center_buy = crossover(active, 50.0).fillna(False)
center_sell = crossunder(active, 50.0).fillna(False)

enabled_events = []
if show_enter:
    enabled_events.extend([enter_buy, enter_sell])
if show_center:
    enabled_events.extend([center_buy, center_sell])
if show_exit:
    enabled_events.extend([exit_buy, exit_sell])

visible_indexes = set()
if show_arrows and enabled_events:
    combined = np.zeros(n, dtype=bool)
    for event in enabled_events:
        combined |= event.to_numpy(dtype=bool)
    visible_indexes = set(np.flatnonzero(combined)[-max_visible_signals:].tolist())


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def constant_line(value):
    return [float(value)] * n


def marker_points(events, level, color, size=6.0):
    mask = events.to_numpy(dtype=bool)
    return [
        {"value": float(level), "color": color, "size": float(size)}
        if index in visible_indexes and mask[index]
        else None
        for index in range(n)
    ]


active_array = active.to_numpy(dtype=float)
active_plot = [
    {
        "value": float(value),
        "color": "#EF4444" if value > overbought else "#22C55E" if value < oversold else "#94A3B8",
    }
    if np.isfinite(value)
    else None
    for value in active_array
]
zone_cloud = []
for value in active_array:
    if np.isfinite(value) and value > overbought:
        zone_cloud.append({"upper": 100.0, "lower": overbought, "color": "rgba(239,68,68,0.18)"})
    elif np.isfinite(value) and value < oversold:
        zone_cloud.append({"upper": oversold, "lower": 0.0, "color": "rgba(34,197,94,0.18)"})
    else:
        zone_cloud.append(None)

event_plots = []
if show_arrows and show_enter:
    event_plots.extend([
        {"name": "Enter Oversold", "title": False, "data": marker_points(enter_buy, 0.0, "#22C55E", 7.0), "color": "#22C55E", "type": "scatter", "overlay": False},
        {"name": "Enter Overbought", "title": False, "data": marker_points(enter_sell, 100.0, "#EF4444", 7.0), "color": "#EF4444", "type": "scatter", "overlay": False},
    ])
if show_arrows and show_center:
    event_plots.extend([
        {"name": "Center Up", "title": False, "data": marker_points(center_buy, 35.0, "#22D3EE"), "color": "#22D3EE", "type": "scatter", "overlay": False},
        {"name": "Center Down", "title": False, "data": marker_points(center_sell, 65.0, "#E879F9"), "color": "#E879F9", "type": "scatter", "overlay": False},
    ])
if show_arrows and show_exit:
    event_plots.extend([
        {"name": "Exit Oversold", "title": False, "data": marker_points(exit_buy, 0.0, "#F59E0B", 7.0), "color": "#F59E0B", "type": "scatter", "overlay": False},
        {"name": "Exit Overbought", "title": False, "data": marker_points(exit_sell, 100.0, "#B91C1C", 7.0), "color": "#B91C1C", "type": "scatter", "overlay": False},
    ])

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Extreme Zone", "title": False, "data": zone_cloud, "color": "#64748B", "type": "cloud", "opacity": 0.18, "overlay": False},
        {"name": "CCI Stochastic", "data": active_plot, "color": "#94A3B8", "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "Maximum", "title": False, "data": constant_line(100.0), "color": "rgba(148,163,184,0.10)", "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": False},
        {"name": "Overbought", "data": constant_line(overbought), "color": "#EF4444", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Center", "data": constant_line(50.0), "color": "#64748B", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Oversold", "data": constant_line(oversold), "color": "#22C55E", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Minimum", "title": False, "data": constant_line(0.0), "color": "rgba(148,163,184,0.10)", "type": "line", "lineWidth": 1, "lineStyle": "dotted", "overlay": False},
    ] + event_plots,
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "source": finite(source.to_numpy(dtype=float)),
        "cci": finite(cci.to_numpy(dtype=float)),
        "rawK": finite(raw_k.to_numpy(dtype=float)),
        "k": finite(k_line.to_numpy(dtype=float)),
        "d": finite(d_line.to_numpy(dtype=float)),
        "active": finite(active_array),
        "enterBuy": [bool(value) for value in enter_buy],
        "enterSell": [bool(value) for value in enter_sell],
        "exitBuy": [bool(value) for value in exit_buy],
        "exitSell": [bool(value) for value in exit_sell],
        "centerBuy": [bool(value) for value in center_buy],
        "centerSell": [bool(value) for value in center_sell],
    },
}
