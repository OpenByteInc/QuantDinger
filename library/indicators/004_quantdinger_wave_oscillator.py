# ============================================================
# QuantDinger Wave Oscillator
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Wave Oscillator"
my_indicator_description = (
    "Momentum histogram measuring the difference between fast and slow "
    "simple moving averages, optionally normalized as a percentage of price."
)

# @param source str close Source values=open,high,low,close,hl2,hlc3,ohlc4
# @param fast_period int 5 Fast average period range=1:500:1
# @param slow_period int 35 Slow average period range=1:1000:1
# @param use_percent bool true Normalize the difference by current price

source_name = str(params.get("source", "close")).lower()
fast_period = min(500, max(1, int(params.get("fast_period", 5))))
slow_period = min(1000, max(1, int(params.get("slow_period", 35))))
use_percent = bool(params.get("use_percent", True))

df = df.copy()
open_values = df["open"].astype(float)
high_values = df["high"].astype(float)
low_values = df["low"].astype(float)
close_values = df["close"].astype(float)
sources = {
    "open": open_values,
    "high": high_values,
    "low": low_values,
    "close": close_values,
    "hl2": (high_values + low_values) / 2.0,
    "hlc3": (high_values + low_values + close_values) / 3.0,
    "ohlc4": (open_values + high_values + low_values + close_values) / 4.0,
}
source = sources.get(source_name, close_values)
fast_average = source.rolling(window=fast_period, min_periods=fast_period).mean()
slow_average = source.rolling(window=slow_period, min_periods=slow_period).mean()
difference = fast_average - slow_average
if use_percent:
    difference = difference / source.replace(0.0, np.nan) * 100.0

histogram = []
for value in difference.to_numpy(dtype=float):
    if np.isfinite(value):
        histogram.append(
            {
                "value": float(value),
                "color": "#20A464" if value > 0.0 else "#F23645",
            }
        )
    else:
        histogram.append(None)

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Wave Momentum",
            "data": histogram,
            "color": "#20A464",
            "type": "histogram",
            "baseValue": 0.0,
            "lineWidth": 2,
            "overlay": False,
        }
    ],
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None for _ in range(len(df))],
    "background_colors": [None for _ in range(len(df))],
    "calculatedVars": {
        "source": [float(value) if np.isfinite(value) else None for value in source.to_numpy(dtype=float)],
        "fastAverage": [float(value) if np.isfinite(value) else None for value in fast_average.to_numpy(dtype=float)],
        "slowAverage": [float(value) if np.isfinite(value) else None for value in slow_average.to_numpy(dtype=float)],
        "difference": [float(value) if np.isfinite(value) else None for value in difference.to_numpy(dtype=float)],
        "positive": [bool(np.isfinite(value) and value > 0.0) for value in difference.to_numpy(dtype=float)],
    },
}
