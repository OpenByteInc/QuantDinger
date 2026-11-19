# ============================================================
# QuantDinger Machine Learning Moving Average
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Machine Learning Moving Average"
my_indicator_description = (
    "Uses radial-basis-function Gaussian-process regression to estimate an endpoint moving average, "
    "builds a mean-absolute-error envelope, and tracks bullish or bearish breakout state."
)

# @param window int 100 Gaussian process training window range=2:300:1
# @param forecast int 0 Forecast offset range=-90:300:1
# @param sigma float 0.01 Kernel regularization range=0.0:10.0:0.01
# @param envelope_multiplier float 2.0 Error envelope multiplier range=0.0:10.0:0.1
# @param source str close Price source values=close,open,high,low,hl2,hlc3,ohlc4
# @param upper_color str #5B9CF6 Upper envelope color
# @param lower_color str #E91E63 Lower envelope color
# @param bullish_color str #3179F5 Bullish average color
# @param bearish_color str #E91E63 Bearish average color

window = min(300, max(2, int(params.get("window", 100))))
forecast = min(300, max(-window + 1, int(params.get("forecast", 0))))
sigma = min(10.0, max(0.0, float(params.get("sigma", 0.01))))
envelope_multiplier = min(10.0, max(0.0, float(params.get("envelope_multiplier", 2.0))))
source_name = str(params.get("source", "close"))
upper_color = str(params.get("upper_color", "#5B9CF6"))
lower_color = str(params.get("lower_color", "#E91E63"))
bullish_color = str(params.get("bullish_color", "#3179F5"))
bearish_color = str(params.get("bearish_color", "#E91E63"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

source_map = {
    "close": close,
    "open": open_price,
    "high": high,
    "low": low,
    "hl2": (high + low) / 2.0,
    "hlc3": (high + low + close) / 3.0,
    "ohlc4": (open_price + high + low + close) / 4.0,
}
source = source_map.get(source_name, close)

x_train = np.arange(window, dtype=float)
test_count = window + forecast
x_test = np.arange(test_count, dtype=float)
distance_train = x_train[:, None] - x_train[None, :]
kernel_train = np.exp(-(distance_train * distance_train) / (2.0 * window * window))
kernel_train = kernel_train + np.eye(window, dtype=float) * (sigma * sigma)
distance_star = x_train[:, None] - x_test[None, :]
kernel_star = np.exp(-(distance_star * distance_star) / (2.0 * window * window))
kernel_weights = (kernel_star.T @ np.linalg.pinv(kernel_train))[-1]

endpoint = np.full(n, np.nan, dtype=float)
for index in range(window + 1, n):
    training_values = source[index - window + 1:index + 1]
    training_mean = float(np.mean(training_values))
    endpoint[index] = float(np.dot(kernel_weights, training_values - training_mean) + training_mean)

absolute_error = np.abs(source - endpoint)
mae = np.full(n, np.nan, dtype=float)
for index in range(window - 1, n):
    values = absolute_error[index - window + 1:index + 1]
    if np.all(np.isfinite(values)):
        mae[index] = float(np.mean(values)) * envelope_multiplier

upper = endpoint + mae
lower = endpoint - mae
state = np.zeros(n, dtype=int)
for index in range(1, n):
    state[index] = state[index - 1]
    if not (np.isfinite(endpoint[index]) and np.isfinite(endpoint[index - 1]) and np.isfinite(upper[index]) and np.isfinite(lower[index])):
        continue
    if close[index] > upper[index] and endpoint[index] > endpoint[index - 1]:
        state[index] = 1
    elif close[index] < lower[index] and endpoint[index] < endpoint[index - 1]:
        state[index] = 0

bullish_line = [None for _ in range(n)]
bearish_line = [None for _ in range(n)]
turn_points = [None for _ in range(n)]
for index in range(n):
    if not np.isfinite(endpoint[index]):
        continue
    target = bullish_line if state[index] == 1 else bearish_line
    target[index] = float(endpoint[index])
    if index > 0 and state[index] != state[index - 1]:
        previous_target = bearish_line if state[index] == 1 else bullish_line
        if np.isfinite(endpoint[index - 1]):
            target[index - 1] = float(endpoint[index - 1])
        previous_target[index] = float(endpoint[index])
        turn_points[index] = float(endpoint[index])

upper_data = [float(value) if np.isfinite(value) else None for value in upper]
lower_data = [float(value) if np.isfinite(value) else None for value in lower]
plots = [
    {"name": "Bullish GPR Average", "data": bullish_line, "color": bullish_color, "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Bearish GPR Average", "data": bearish_line, "color": bearish_color, "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "Trend Change", "data": turn_points, "color": bullish_color, "type": "circle", "size": 5, "overlay": True},
    {"name": "Upper Error Envelope", "data": upper_data, "color": upper_color, "type": "line", "lineWidth": 1, "opacity": 0.55, "overlay": True},
    {"name": "Lower Error Envelope", "data": lower_data, "color": lower_color, "type": "line", "lineWidth": 1, "opacity": 0.55, "overlay": True},
]

output = {"name": my_indicator_name, "plots": plots, "signals": []}
