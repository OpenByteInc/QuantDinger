# ============================================================
# QuantDinger Squeeze Pro
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Squeeze Pro"
my_indicator_description = (
    "A linear-regression momentum histogram with four Bollinger-versus-"
    "Keltner compression states and squeeze start or release events."
)

# @param length int 20 Squeeze period range=2:200:1
# @param bollinger_multiplier float 2.0 Bollinger multiplier range=0.1:10.0:0.1
# @param keltner_tight float 1.0 Tight Keltner multiplier range=0.1:10.0:0.1
# @param keltner_medium float 1.5 Medium Keltner multiplier range=0.1:10.0:0.1
# @param keltner_wide float 2.0 Wide Keltner multiplier range=0.1:10.0:0.1
# @param alert_start bool true Show squeeze start events
# @param alert_fire bool true Show squeeze release events

length = max(2, int(params.get("length", 20)))
bollinger_multiplier = max(0.1, float(params.get("bollinger_multiplier", 2.0)))
keltner_tight = max(0.1, float(params.get("keltner_tight", 1.0)))
keltner_medium = max(0.1, float(params.get("keltner_medium", 1.5)))
keltner_wide = max(0.1, float(params.get("keltner_wide", 2.0)))
alert_start = bool(params.get("alert_start", True))
alert_fire = bool(params.get("alert_fire", True))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def linear_regression(values, period):
    result = np.full(n, np.nan, dtype=float)
    x_sum = float(period * (period - 1)) / 2.0
    x2_sum = float(period * (period - 1) * (2 * period - 1)) / 6.0
    denominator = period * x2_sum - x_sum * x_sum
    for index in range(period - 1, n):
        y_sum = 0.0
        xy_sum = 0.0
        valid = True
        for position in range(period):
            value = values[index - period + 1 + position]
            if not np.isfinite(value):
                valid = False
                break
            y_sum += value
            xy_sum += float(position) * value
        if valid:
            slope = (period * xy_sum - x_sum * y_sum) / denominator if denominator != 0.0 else 0.0
            intercept = (y_sum - slope * x_sum) / period
            result[index] = intercept + slope * float(period - 1)
    return result


basis = pd.Series(close).rolling(length, min_periods=length).mean().to_numpy(dtype=float)
deviation = pd.Series(close).rolling(length, min_periods=length).std(ddof=0).to_numpy(dtype=float) * bollinger_multiplier
bollinger_upper = basis + deviation
bollinger_lower = basis - deviation
previous_close = np.concatenate(([close[0]], close[:-1]))
true_range = np.maximum(high - low, np.maximum(np.abs(high - previous_close), np.abs(low - previous_close)))
range_average = pd.Series(true_range).rolling(length, min_periods=length).mean().to_numpy(dtype=float)
wide_upper = basis + range_average * keltner_wide
wide_lower = basis - range_average * keltner_wide
medium_upper = basis + range_average * keltner_medium
medium_lower = basis - range_average * keltner_medium
tight_upper = basis + range_average * keltner_tight
tight_lower = basis - range_average * keltner_tight

rolling_high = pd.Series(high).rolling(length, min_periods=length).max().to_numpy(dtype=float)
rolling_low = pd.Series(low).rolling(length, min_periods=length).min().to_numpy(dtype=float)
raw_momentum = close - (((rolling_high + rolling_low) / 2.0 + basis) / 2.0)
momentum = linear_regression(raw_momentum, length)
no_squeeze = (bollinger_lower < wide_lower) | (bollinger_upper > wide_upper)
low_squeeze = (bollinger_lower >= wide_lower) | (bollinger_upper <= wide_upper)
medium_squeeze = (bollinger_lower >= medium_lower) | (bollinger_upper <= medium_upper)
high_squeeze = (bollinger_lower >= tight_lower) | (bollinger_upper <= tight_upper)

momentum_plot = []
squeeze_plot = []
squeeze_start = np.zeros(n, dtype=bool)
squeeze_fire = np.zeros(n, dtype=bool)
for index in range(n):
    if np.isfinite(momentum[index]):
        rising = index > 0 and np.isfinite(momentum[index - 1]) and momentum[index] > momentum[index - 1]
        color = "#00FFFF" if momentum[index] > 0.0 and rising else "#2962FF" if momentum[index] > 0.0 else "#EF4444" if not rising else "#FACC15"
        momentum_plot.append({"value": float(momentum[index]), "color": color})
    else:
        momentum_plot.append(None)
    squeeze_color = "#FB8C00" if high_squeeze[index] else "#EF4444" if medium_squeeze[index] else "#111827" if low_squeeze[index] else "#16A34A"
    squeeze_plot.append({"value": 0.0, "color": squeeze_color})
    if index > 0:
        squeeze_start[index] = bool(no_squeeze[index - 1] and not no_squeeze[index])
        squeeze_fire[index] = bool(no_squeeze[index] and not no_squeeze[index - 1])

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Momentum", "data": momentum_plot, "color": "#00FFFF", "type": "histogram", "baseValue": 0.0, "lineWidth": 2, "overlay": False},
        {"name": "Squeeze State", "data": squeeze_plot, "color": "#16A34A", "type": "line", "lineWidth": 3, "overlay": False},
    ],
    "signals": [
        {"type": "sell", "text": "SQZ", "name": "Squeeze Started", "data": [0.0 if alert_start and squeeze_start[i] else None for i in range(n)], "color": "#FB8C00", "renderMode": "events"},
        {"type": "buy", "text": "FIRE", "name": "Squeeze Fired", "data": [0.0 if alert_fire and squeeze_fire[i] else None for i in range(n)], "color": "#16A34A", "renderMode": "events"},
    ],
    "layers": [],
}
