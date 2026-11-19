# ============================================================
# QuantDinger Inverse Fisher Combo
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Inverse Fisher Combo"
my_indicator_description = (
    "Inverse Fisher transforms of CCI, RSI, stochastic, and money flow with an optional composite average."
)

# @param show_stochastic bool false Show stochastic transform
# @param show_rsi bool false Show RSI transform
# @param show_cci bool true Show CCI transform
# @param show_mfi bool false Show money flow transform
# @param show_average bool false Show composite average
# @param cci_period int 5 CCI period range=1:200:1
# @param rsi_period int 5 RSI period range=1:200:1
# @param stochastic_period int 5 Stochastic period range=1:200:1
# @param mfi_period int 5 Money flow period range=1:200:1
# @param smoothing_period int 9 Smoothing period range=1:200:1

show_stochastic = bool(params.get("show_stochastic", False))
show_rsi = bool(params.get("show_rsi", False))
show_cci = bool(params.get("show_cci", True))
show_mfi = bool(params.get("show_mfi", False))
show_average = bool(params.get("show_average", False))
cci_period = max(1, int(params.get("cci_period", 5)))
rsi_period = max(1, int(params.get("rsi_period", 5)))
stochastic_period = max(1, int(params.get("stochastic_period", 5)))
mfi_period = max(1, int(params.get("mfi_period", 5)))
smoothing_period = max(1, int(params.get("smoothing_period", 9)))

df = df.copy()
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)
volume = df["volume"].astype(float)
n = len(df)

def wma(series, period):
    denominator = float(period * (period + 1) / 2)

    def calculate(values):
        total = 0.0
        for index in range(len(values)):
            total += float(values[index]) * float(index + 1)
        return total / denominator

    return series.rolling(window=period, min_periods=period).apply(calculate, raw=True)

def wilder_rsi(series, period):
    values = series.to_numpy(dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    if period == 1:
        for index in range(1, len(values)):
            difference = values[index] - values[index - 1]
            result[index] = 100.0 if difference > 0.0 else 0.0 if difference < 0.0 else 50.0
        return pd.Series(result, index=series.index)
    if len(values) <= period:
        return pd.Series(result, index=series.index)
    changes = np.diff(values)
    gains = np.maximum(changes, 0.0)
    losses = np.maximum(-changes, 0.0)
    average_gain = float(np.mean(gains[:period]))
    average_loss = float(np.mean(losses[:period]))
    for index in range(period, len(values)):
        if index > period:
            average_gain = (average_gain * (period - 1) + gains[index - 1]) / period
            average_loss = (average_loss * (period - 1) + losses[index - 1]) / period
        if average_gain == 0.0 and average_loss == 0.0:
            result[index] = 50.0
        elif average_loss == 0.0:
            result[index] = 100.0
        elif average_gain == 0.0:
            result[index] = 0.0
        else:
            result[index] = 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return pd.Series(result, index=series.index)

def inverse_fisher(series):
    clipped = series.clip(lower=-20.0, upper=20.0)
    exponential = np.exp(2.0 * clipped)
    return (exponential - 1.0) / (exponential + 1.0)

cci_mean = close.rolling(window=cci_period, min_periods=cci_period).mean()
cci_deviation = close.rolling(window=cci_period, min_periods=cci_period).apply(
    lambda values: float(np.mean(np.abs(values - np.mean(values)))), raw=True
)
cci = (close - cci_mean) / (0.015 * cci_deviation.replace(0.0, np.nan))
cci_transform = inverse_fisher(wma(0.1 * (cci / 4.0), smoothing_period))

rsi = wilder_rsi(close, rsi_period)
rsi_transform = inverse_fisher(wma(0.1 * (rsi - 50.0), smoothing_period))

lowest_low = low.rolling(window=stochastic_period, min_periods=stochastic_period).min()
highest_high = high.rolling(window=stochastic_period, min_periods=stochastic_period).max()
stochastic = 100.0 * (close - lowest_low) / (highest_high - lowest_low).replace(0.0, np.nan)
stochastic_transform = inverse_fisher(wma(0.1 * (stochastic - 50.0), smoothing_period))

typical = (high + low + close) / 3.0
typical_change = typical.diff()
positive_flow = (volume * typical).where(typical_change > 0.0, 0.0)
negative_flow = (volume * typical).where(typical_change < 0.0, 0.0)
positive_sum = positive_flow.rolling(window=mfi_period, min_periods=mfi_period).sum()
negative_sum = negative_flow.rolling(window=mfi_period, min_periods=mfi_period).sum()
mfi = 100.0 - 100.0 / (1.0 + positive_sum / negative_sum.replace(0.0, np.nan))
mfi = mfi.where(~((negative_sum == 0.0) & (positive_sum > 0.0)), 100.0)
mfi = mfi.where(~((positive_sum == 0.0) & (negative_sum > 0.0)), 0.0)
mfi = mfi.where(~((positive_sum == 0.0) & (negative_sum == 0.0)), np.nan)
mfi_transform = inverse_fisher(wma(0.1 * (mfi - 50.0), smoothing_period))
average_transform = (cci_transform + rsi_transform + stochastic_transform + mfi_transform) / 4.0

def visible(series, enabled):
    return [float(value) if enabled and np.isfinite(value) else None for value in series.to_numpy(dtype=float)]

def finite(series):
    return [float(value) if np.isfinite(value) else None for value in series.to_numpy(dtype=float)]


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Composite Average", "data": visible(average_transform, show_average), "color": "#800080", "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "Stochastic Transform", "data": visible(stochastic_transform, show_stochastic), "color": "#0000FF", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "CCI Transform", "data": visible(cci_transform, show_cci), "color": "#FF0000", "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "RSI Transform", "data": visible(rsi_transform, show_rsi), "color": "#B0BEC5", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Money Flow Transform", "data": visible(mfi_transform, show_mfi), "color": "#A020F0", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Upper Threshold", "data": [0.5 for _ in range(n)], "color": "#FF0000", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Lower Threshold", "data": [-0.5 for _ in range(n)], "color": "#008000", "type": "line", "lineWidth": 1, "overlay": False},
    ],
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "cci": finite(cci), "cciTransform": finite(cci_transform),
        "rsi": finite(rsi), "rsiTransform": finite(rsi_transform),
        "stochastic": finite(stochastic), "stochasticTransform": finite(stochastic_transform),
        "positiveFlowSum": finite(positive_sum), "negativeFlowSum": finite(negative_sum),
        "mfi": finite(mfi), "mfiTransform": finite(mfi_transform),
        "averageTransform": finite(average_transform),
    },
}
