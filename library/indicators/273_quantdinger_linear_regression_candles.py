# ============================================================
# QuantDinger Linear Regression Candles
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Linear Regression Candles"
my_indicator_description = "Smooths OHLC candles with rolling least-squares regression and overlays a selectable SMA or EMA signal line."

# @param signal_length int 11 Signal smoothing range=1:200:1
# @param signal_type str SMA Signal average values=SMA,EMA
# @param use_regression bool true Smooth OHLC with linear regression
# @param regression_length int 11 Linear regression length range=1:200:1
# @param show_candles bool true Show regression candles
# @param bullish_color color #26A69A Bullish candle color
# @param bearish_color color #EF5350 Bearish candle color
# @param signal_color color #E0E0E0 Signal line color

signal_length = min(200, max(1, int(params.get("signal_length", 11))))
signal_type = str(params.get("signal_type", "SMA")).upper()
if signal_type not in ("SMA", "EMA"):
    signal_type = "SMA"
use_regression = bool(params.get("use_regression", True))
regression_length = min(200, max(1, int(params.get("regression_length", 11))))
show_candles = bool(params.get("show_candles", True))
bullish_color = str(params.get("bullish_color", "#26A69A"))
bearish_color = str(params.get("bearish_color", "#EF5350"))
signal_color = str(params.get("signal_color", "#E0E0E0"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def _linear_regression(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    if length == 1:
        result[:] = values
        return result
    x = np.arange(length, dtype=float)
    x_mean = (length - 1.0) * 0.5
    denominator = float(np.sum((x - x_mean) ** 2))
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            y_mean = float(np.mean(window))
            slope = float(np.sum((x - x_mean) * (window - y_mean)) / denominator)
            result[index] = y_mean + slope * x_mean
    return result


def _ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    state = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            state = value if not np.isfinite(state) else alpha * value + (1.0 - alpha) * state
            result[index] = state
    return result


if use_regression:
    regression_open = _linear_regression(open_price, regression_length)
    regression_high = _linear_regression(high, regression_length)
    regression_low = _linear_regression(low, regression_length)
    regression_close = _linear_regression(close, regression_length)
else:
    regression_open = open_price.copy()
    regression_high = high.copy()
    regression_low = low.copy()
    regression_close = close.copy()

if signal_type == "SMA":
    signal = pd.Series(regression_close).rolling(signal_length, min_periods=signal_length).mean().to_numpy(dtype=float)
else:
    signal = _ema(regression_close, signal_length)

bullish = np.isfinite(regression_open) & np.isfinite(regression_close) & (regression_open < regression_close)
candles = []
for index in range(n):
    if not show_candles or not np.all(np.isfinite([regression_open[index], regression_high[index], regression_low[index], regression_close[index]])):
        candles.append(None)
        continue
    color = bullish_color if bullish[index] else bearish_color
    candles.append({
        "open": float(regression_open[index]),
        "high": float(regression_high[index]),
        "low": float(regression_low[index]),
        "close": float(regression_close[index]),
        "color": color,
        "fillColor": color,
        "borderColor": color,
        "wickColor": color,
    })


def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Regression Candles", "data": candles, "type": "candlestick", "overlay": True},
        {"name": "Signal", "data": _finite_or_none(signal), "color": signal_color, "type": "line", "lineWidth": 2, "overlay": True},
    ],
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "regressionOpen": _finite_or_none(regression_open),
        "regressionHigh": _finite_or_none(regression_high),
        "regressionLow": _finite_or_none(regression_low),
        "regressionClose": _finite_or_none(regression_close),
        "signal": _finite_or_none(signal),
        "bullish": bullish.tolist(),
    },
}
