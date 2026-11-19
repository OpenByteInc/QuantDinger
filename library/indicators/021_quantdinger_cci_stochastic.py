# ============================================================
# QuantDinger CCI Stochastic
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger CCI Stochastic"
my_indicator_description = (
    "A stochastic transform of CCI with configurable smoothing, zone coloring, "
    "and entry, center, and exit crossing events."
)

# @param cci_period int 28 CCI period range=1:300:1
# @param stochastic_period int 28 Stochastic period range=1:300:1
# @param smooth_k int 3 K smoothing range=1:100:1
# @param smooth_d int 3 D smoothing range=1:100:1
# @param active_line str D Active line values=D,K
# @param overbought float 80.0 Overbought level range=50.0:100.0:1.0
# @param oversold float 20.0 Oversold level range=0.0:50.0:1.0
# @param show_enter bool true Show zone-entry events
# @param show_center bool false Show center events
# @param show_exit bool true Show zone-exit events

cci_period = max(1, int(params.get("cci_period", 28)))
stochastic_period = max(1, int(params.get("stochastic_period", 28)))
smooth_k = max(1, int(params.get("smooth_k", 3)))
smooth_d = max(1, int(params.get("smooth_d", 3)))
active_line = str(params.get("active_line", "D")).upper()
overbought = float(params.get("overbought", 80.0))
oversold = float(params.get("oversold", 20.0))
show_enter = bool(params.get("show_enter", True))
show_center = bool(params.get("show_center", False))
show_exit = bool(params.get("show_exit", True))

df = df.copy()
close = df["close"].astype(float)
n = len(df)
mean = close.rolling(window=cci_period, min_periods=cci_period).mean()
mean_deviation = close.rolling(window=cci_period, min_periods=cci_period).apply(
    lambda values: float(np.mean(np.abs(values - np.mean(values)))), raw=True
)
cci = (close - mean) / (0.015 * mean_deviation.replace(0.0, np.nan))
cci_low = cci.rolling(window=stochastic_period, min_periods=stochastic_period).min()
cci_high = cci.rolling(window=stochastic_period, min_periods=stochastic_period).max()
raw_k = 100.0 * (cci - cci_low) / (cci_high - cci_low).replace(0.0, np.nan)
k_line = raw_k.rolling(window=smooth_k, min_periods=smooth_k).mean()
d_line = k_line.rolling(window=smooth_d, min_periods=smooth_d).mean()
active = d_line if active_line == "D" else k_line

cross_above_os = (active > oversold) & (active.shift(1) <= oversold)
cross_below_os = (active < oversold) & (active.shift(1) >= oversold)
cross_above_ob = (active > overbought) & (active.shift(1) <= overbought)
cross_below_ob = (active < overbought) & (active.shift(1) >= overbought)
cross_above_mid = (active > 50.0) & (active.shift(1) <= 50.0)
cross_below_mid = (active < 50.0) & (active.shift(1) >= 50.0)

active_values = active.to_numpy(dtype=float)
active_plot = [
    {
        "value": float(value),
        "color": "#FF0000" if value > overbought else "#008000" if value < oversold else "#808080",
    }
    if np.isfinite(value)
    else None
    for value in active_values
]

def constant_line(value):
    return [float(value) for _ in range(n)]

def event_values(mask, value):
    return [float(value) if bool(mask.iloc[index]) else None for index in range(n)]

signals = []
if show_enter:
    signals.extend([
        {"type": "buy", "text": "ENTER", "name": "Enter Oversold", "data": event_values(cross_below_os, 0.0), "color": "#008000", "renderMode": "events"},
        {"type": "sell", "text": "ENTER", "name": "Enter Overbought", "data": event_values(cross_above_ob, 100.0), "color": "#FF0000", "renderMode": "events"},
    ])
if show_center:
    signals.extend([
        {"type": "buy", "text": "MID", "name": "Center Up", "data": event_values(cross_above_mid, 35.0), "color": "#00FFFF", "renderMode": "events"},
        {"type": "sell", "text": "MID", "name": "Center Down", "data": event_values(cross_below_mid, 65.0), "color": "#FF00FF", "renderMode": "events"},
    ])
if show_exit:
    signals.extend([
        {"type": "buy", "text": "EXIT", "name": "Exit Oversold", "data": event_values(cross_above_os, 0.0), "color": "#FFA500", "renderMode": "events"},
        {"type": "sell", "text": "EXIT", "name": "Exit Overbought", "data": event_values(cross_below_ob, 100.0), "color": "#800000", "renderMode": "events"},
    ])

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "CCI Stochastic", "data": active_plot, "color": "#808080", "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "Overbought", "data": constant_line(overbought), "color": "#FF0000", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Center", "data": constant_line(50.0), "color": "#808080", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Oversold", "data": constant_line(oversold), "color": "#00FF00", "type": "line", "lineWidth": 1, "overlay": False},
    ],
    "signals": signals,
    "layers": [],
}
