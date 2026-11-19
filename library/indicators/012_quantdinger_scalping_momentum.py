# ============================================================
# QuantDinger Scalping Momentum
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Scalping Momentum"
my_indicator_description = (
    "A fast signal-line oscillator measured against a tolerance-filtered, "
    "double-smoothed price baseline."
)

# @param tolerance_percent float 1.0 Baseline tolerance percent range=0.0:20.0:0.1
# @param main_period int 100 Main smoothing period range=2:400:1
# @param signal_period int 7 Signal period range=1:100:1

tolerance_percent = max(0.0, float(params.get("tolerance_percent", 1.0)))
main_period = max(2, int(params.get("main_period", 100)))
signal_period = max(1, int(params.get("signal_period", 7)))

df = df.copy()
close = df["close"].astype(float)
first_period = int(np.ceil(main_period / 2.0))
second_period = int(np.floor(main_period / 2.0)) + 1
baseline = close.rolling(
    window=first_period, min_periods=first_period
).mean().rolling(window=second_period, min_periods=second_period).mean()
tolerance = baseline * tolerance_percent / 100.0
filtered_baseline = baseline.where(
    (baseline > close + tolerance) | (baseline < close - tolerance),
    close,
)
signal_line = close.rolling(window=signal_period, min_periods=signal_period).mean()
momentum = signal_line - filtered_baseline

momentum_plot = [
    float(value) if np.isfinite(value) else None for value in momentum
]
zero_plot = [0.0] * len(df)
cloud_plot = []
for value in momentum:
    if not np.isfinite(value):
        cloud_plot.append(None)
        continue
    numeric_value = float(value)
    cloud_plot.append(
        {
            "upper": max(numeric_value, 0.0),
            "lower": min(numeric_value, 0.0),
            "color": "#26A69A" if numeric_value >= 0.0 else "#EF5350",
        }
    )

output = {
    "name": my_indicator_name,
    "plots": [
        {
            "name": "Scalping Momentum",
            "data": momentum_plot,
            "color": "#800000",
            "type": "line",
            "lineWidth": 2,
            "overlay": False,
        },
        {
            "name": "Zero Line",
            "data": zero_plot,
            "color": "#808080",
            "type": "line",
            "lineWidth": 1,
            "overlay": False,
        },
        {
            "name": "Momentum Cloud",
            "title": False,
            "data": cloud_plot,
            "color": "#26A69A",
            "type": "cloud",
            "opacity": 0.18,
            "overlay": False,
        },
    ],
    "signals": [],
    "layers": [],
}
