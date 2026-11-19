# ============================================================
# QuantDinger Best SuperTrend CCI
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Best SuperTrend CCI"
my_indicator_description = (
    "A CCI-directed ATR trail that ratchets with the prior CCI regime and "
    "colors the active line by the close-to-trail relationship."
)

# @param source str Close CCI source values=Close,HL2,HLC3,OHLC4
# @param cci_period int 28 CCI period range=1:300:1
# @param cci_midline float 0.0 CCI midline range=-300.0:300.0:1.0
# @param atr_factor float 3.0 ATR factor range=1.0:100.0:0.5
# @param atr_period int 3 ATR period range=1:100:1

source_name = str(params.get("source", "Close"))
cci_period = min(300, max(1, int(params.get("cci_period", 28))))
cci_midline = float(params.get("cci_midline", 0.0))
atr_factor = min(100.0, max(1.0, float(params.get("atr_factor", 3.0))))
atr_period = min(100, max(1, int(params.get("atr_period", 3))))

df = df.copy()
open_price = df["open"].astype(float)
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)
n = len(df)

source_key = source_name.strip().upper()
if source_key == "HL2":
    source = (high + low) / 2.0
elif source_key == "HLC3":
    source = (high + low + close) / 3.0
elif source_key == "OHLC4":
    source = (open_price + high + low + close) / 4.0
else:
    source = close.copy()

source_values = source.to_numpy(dtype=float)
close_values = close.to_numpy(dtype=float)
cci = np.full(n, np.nan, dtype=float)
for index in range(cci_period - 1, n):
    window = source_values[index - cci_period + 1:index + 1]
    if not all(np.isfinite(float(value)) for value in window):
        continue
    basis = sum(float(value) for value in window) / len(window)
    mean_deviation = (
        sum(abs(float(value) - basis) for value in window) / len(window)
    )
    if mean_deviation > 0.0:
        cci[index] = (source_values[index] - basis) / (0.015 * mean_deviation)

previous_close = close.shift(1)
true_range = pd.concat(
    [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
    axis=1,
).max(axis=1, skipna=True)
tr_values = true_range.to_numpy(dtype=float)
atr = np.full(n, np.nan, dtype=float)
if n >= atr_period:
    atr[atr_period - 1] = (
        sum(float(value) for value in tr_values[:atr_period]) / atr_period
    )
    for index in range(atr_period, n):
        atr[index] = (
            atr[index - 1] * (atr_period - 1) + tr_values[index]
        ) / atr_period

midpoint = ((high + low) / 2.0).to_numpy(dtype=float)
raw_up = midpoint - atr_factor * atr
raw_down = midpoint + atr_factor * atr
trend_up = np.full(n, np.nan, dtype=float)
trend_down = np.full(n, np.nan, dtype=float)
trend = np.ones(n, dtype=int)
trail = np.full(n, np.nan, dtype=float)

for index in range(n):
    previous_up = (
        trend_up[index - 1]
        if index > 0 and np.isfinite(trend_up[index - 1])
        else 0.0
    )
    previous_down = (
        trend_down[index - 1]
        if index > 0 and np.isfinite(trend_down[index - 1])
        else 0.0
    )
    previous_cci = cci[index - 1] if index > 0 else np.nan

    if np.isfinite(raw_up[index]):
        trend_up[index] = (
            max(raw_up[index], previous_up)
            if np.isfinite(previous_cci) and previous_cci > cci_midline
            else raw_up[index]
        )
    if np.isfinite(raw_down[index]):
        trend_down[index] = (
            min(raw_down[index], previous_down)
            if np.isfinite(previous_cci) and previous_cci < cci_midline
            else raw_down[index]
        )

    if np.isfinite(cci[index]):
        if cci[index] > cci_midline:
            trend[index] = 1
        elif cci[index] < cci_midline:
            trend[index] = -1
        elif index > 0:
            trend[index] = trend[index - 1]
    elif index > 0:
        trend[index] = trend[index - 1]

    active_value = trend_up[index] if trend[index] == 1 else trend_down[index]
    if np.isfinite(active_value):
        trail[index] = active_value

trail_plot = [
    {
        "value": float(value),
        "color": "#00A651" if close_values[index] >= value else "#FF3030",
    }
    if np.isfinite(value)
    else None
    for index, value in enumerate(trail)
]

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "CCI SuperTrend",
            "data": trail_plot,
            "color": "#00A651",
            "type": "line",
            "lineWidth": 4,
            "overlay": True,
        },
    ],
    "signals": [],
    "layers": [],
    "calculatedVars": {
        "lastCCI": float(cci[-1]) if n and np.isfinite(cci[-1]) else None,
        "lastTrail": float(trail[-1]) if n and np.isfinite(trail[-1]) else None,
        "regime": "bullish" if n and trend[-1] == 1 else "bearish",
    },
}
