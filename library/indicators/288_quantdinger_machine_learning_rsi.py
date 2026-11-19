# ============================================================
# QuantDinger Machine Learning RSI
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Machine Learning RSI"
my_indicator_description = "Enhances a configurable RSI with normalized multi-feature nearest neighbors and optional adaptive filtering."

# @param rsi_length int 14 RSI length range=1:200:1
# @param use_smoothing bool true Apply moving average
# @param smoothing_length int 3 Moving-average length range=1:200:1
# @param ma_type str ALMA Moving-average type values=SMA,EMA,DEMA,TEMA,WMA,VWMA,SMMA,HMA,LSMA,ALMA
# @param alma_sigma int 4 ALMA sigma range=1:20:1
# @param overbought int 70 Overbought level range=50:100:1
# @param oversold int 30 Oversold level range=0:50:1
# @param use_knn bool true Enable KNN enhancement
# @param knn_neighbors int 5 Number of neighbors range=1:50:1
# @param knn_lookback int 100 Historical KNN period range=20:500:1
# @param knn_weight float 0.4 Machine-learning weight range=0.0:1.0:0.1
# @param feature_count int 3 Number of features range=1:5:1
# @param use_filter bool true Apply additional filtering
# @param filter_method str Kalman Filtering method values=None,Kalman,DoubleEMA,ALMA
# @param filter_strength float 0.3 Filtering strength range=0.01:1.0:0.01
# @param neon_effect bool true Enable neon gradients
# @param color_scheme str Classic Color palette values=Classic,Deuteranopia,Protanopia,Tritanopia,Monochrome
# @param color_mode str Trend Coloring mode values=None,Trend,Impulse
# @param show_table bool true Show status table

rsi_length = min(200, max(1, int(params.get("rsi_length", 14))))
use_smoothing = bool(params.get("use_smoothing", True))
smoothing_length = min(200, max(1, int(params.get("smoothing_length", 3))))
ma_type = str(params.get("ma_type", "ALMA"))
alma_sigma = min(20, max(1, int(params.get("alma_sigma", 4))))
overbought = min(100, max(50, int(params.get("overbought", 70))))
oversold = min(50, max(0, int(params.get("oversold", 30))))
use_knn = bool(params.get("use_knn", True))
knn_neighbors = min(50, max(1, int(params.get("knn_neighbors", 5))))
knn_lookback = min(500, max(20, int(params.get("knn_lookback", 100))))
knn_weight = min(1.0, max(0.0, float(params.get("knn_weight", 0.4))))
feature_count = min(5, max(1, int(params.get("feature_count", 3))))
use_filter = bool(params.get("use_filter", True))
filter_method = str(params.get("filter_method", "Kalman"))
filter_strength = min(1.0, max(0.01, float(params.get("filter_strength", 0.3))))
neon_effect = bool(params.get("neon_effect", True))
color_scheme = str(params.get("color_scheme", "Classic"))
color_mode = str(params.get("color_mode", "Trend"))
show_table = bool(params.get("show_table", True))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, period):
    return pd.Series(values, dtype=float).rolling(period, min_periods=period).mean().to_numpy(dtype=float)


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _rma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    finite_indices = np.flatnonzero(np.isfinite(values))
    if len(finite_indices) < period:
        return result
    seed_end = finite_indices[period - 1]
    seed_values = values[finite_indices[:period]]
    previous = float(np.mean(seed_values))
    result[seed_end] = previous
    for index in range(seed_end + 1, len(values)):
        if np.isfinite(values[index]):
            previous = (previous * (period - 1.0) + values[index]) / period
            result[index] = previous
    return result


def _wma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, period + 1, dtype=float)
    divisor = float(np.sum(weights))
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / divisor)
    return result


def _vwma(values, volumes, period):
    numerator = pd.Series(values * volumes, dtype=float).rolling(period, min_periods=period).sum().to_numpy(dtype=float)
    denominator = pd.Series(volumes, dtype=float).rolling(period, min_periods=period).sum().to_numpy(dtype=float)
    return np.divide(numerator, denominator, out=np.full(len(values), np.nan), where=denominator != 0.0)


def _linreg(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    x = np.arange(period, dtype=float)
    x_mean = float(np.mean(x))
    denominator = float(np.sum((x - x_mean) ** 2))
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            slope = float(np.sum((x - x_mean) * (window - np.mean(window))) / denominator) if denominator else 0.0
            intercept = float(np.mean(window) - slope * x_mean)
            result[index] = intercept + slope * (period - 1)
    return result


def _slope(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    x = np.arange(period, dtype=float)
    x_mean = float(np.mean(x))
    denominator = float(np.sum((x - x_mean) ** 2))
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.sum((x - x_mean) * (window - np.mean(window))) / denominator) if denominator else 0.0
    return result


def _alma(values, period, offset, sigma):
    result = np.full(len(values), np.nan, dtype=float)
    positions = np.arange(period, dtype=float)
    center = offset * (period - 1.0)
    scale = period / float(sigma)
    weights = np.exp(-((positions - center) ** 2) / (2.0 * scale * scale))
    weights /= np.sum(weights)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights))
    return result


def _moving_average(kind, values, period, volumes, sigma):
    if kind == "SMA":
        return _sma(values, period)
    if kind == "EMA":
        return _ema(values, period)
    if kind == "DEMA":
        first = _ema(values, period); return 2.0 * first - _ema(first, period)
    if kind == "TEMA":
        first = _ema(values, period); second = _ema(first, period); return 3.0 * (first - second) + _ema(second, period)
    if kind == "WMA":
        return _wma(values, period)
    if kind == "VWMA":
        return _vwma(values, volumes, period)
    if kind == "SMMA":
        return _rma(values, period)
    if kind == "HMA":
        return _wma(2.0 * _wma(values, max(1, period // 2)) - _wma(values, period), max(1, int(round(np.sqrt(period)))))
    if kind == "LSMA":
        return _linreg(values, period)
    return _alma(values, period, 0.85, sigma)


def _rsi(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) <= period:
        return result
    changes = np.diff(values)
    gains = np.maximum(changes, 0.0)
    losses = np.maximum(-changes, 0.0)
    average_gain = float(np.mean(gains[:period]))
    average_loss = float(np.mean(losses[:period]))
    for index in range(period, len(values)):
        if index > period:
            average_gain = (average_gain * (period - 1.0) + gains[index - 1]) / period
            average_loss = (average_loss * (period - 1.0) + losses[index - 1]) / period
        result[index] = 50.0 if average_gain == 0.0 and average_loss == 0.0 else 100.0 if average_loss == 0.0 else 0.0 if average_gain == 0.0 else 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return result


def _momentum(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) > period:
        result[period:] = values[period:] - values[:-period]
    return result


def _stdev(values, period):
    return pd.Series(values, dtype=float).rolling(period, min_periods=period).std(ddof=0).to_numpy(dtype=float)


def _normalize(values, period):
    series = pd.Series(values, dtype=float)
    maximum = series.rolling(period, min_periods=1).max().to_numpy(dtype=float)
    minimum = series.rolling(period, min_periods=1).min().to_numpy(dtype=float)
    span = maximum - minimum
    result = np.full(len(values), np.nan, dtype=float)
    finite = np.isfinite(values) & np.isfinite(maximum) & np.isfinite(minimum)
    nonzero = finite & (span != 0.0)
    result[nonzero] = (values[nonzero] - minimum[nonzero]) / span[nonzero]
    result[finite & (span == 0.0)] = 0.5
    return result


frame = df.copy().reset_index(drop=True)
if "time" not in frame.columns:
    frame["time"] = np.arange(len(frame), dtype=float) * 3600000.0
if "volume" not in frame.columns:
    frame["volume"] = 0.0
for column in ("time", "open", "high", "low", "close", "volume"):
    frame[column] = pd.to_numeric(frame[column], errors="coerce")
frame["volume"] = frame["volume"].fillna(0.0)
frame = frame.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
close = frame["close"].to_numpy(dtype=float)
volume = frame["volume"].to_numpy(dtype=float)
n = len(frame)

base_rsi = _rsi(close, rsi_length)
smoothed_rsi = _moving_average(ma_type, base_rsi, smoothing_length, volume, alma_sigma)
standard_rsi = smoothed_rsi if use_smoothing else base_rsi.copy()
normalized_features = [
    _normalize(standard_rsi, knn_lookback),
    _normalize(_momentum(standard_rsi, 3), knn_lookback),
    _normalize(_stdev(standard_rsi, 10), knn_lookback),
    _normalize(_slope(standard_rsi, 5), knn_lookback),
    _normalize(_momentum(close, 5), knn_lookback),
]

knn_rsi = standard_rsi.copy()
knn_divergence = np.zeros(n, dtype=float)
effective_k = np.zeros(n, dtype=int)
adaptive_oversold = np.full(n, 30.0, dtype=float)
adaptive_overbought = np.full(n, 70.0, dtype=float)
neighbor_weight_sum = np.zeros(n, dtype=float)

for index in range(n):
    current = np.asarray([feature[index] for feature in normalized_features[:feature_count]], dtype=float)
    if not np.all(np.isfinite(current)):
        continue
    candidates = []
    maximum_lag = min(knn_lookback, index - 1)
    for lag in range(1, maximum_lag + 1):
        historical_index = index - lag
        historical = np.asarray([feature[historical_index] for feature in normalized_features[:feature_count]], dtype=float)
        if np.all(np.isfinite(historical)):
            candidates.append((float(np.sqrt(np.sum((current - historical) ** 2))), lag))
    if len(candidates) <= 1:
        continue
    candidates.sort(key=lambda item: item[0])
    selected = candidates[:min(knn_neighbors, len(candidates))]
    effective_k[index] = len(selected)
    weighted_sum = 0.0
    weight_sum = 0.0
    divergence_sum = 0.0
    divergence_count = 0
    overbought_candidates = []
    oversold_candidates = []
    for distance, lag in selected:
        weight = 1.0 if distance < 0.0001 else 1.0 / distance
        neighbor_index = index - (lag - 1)
        neighbor_rsi = standard_rsi[neighbor_index]
        if np.isfinite(neighbor_rsi):
            weighted_sum += neighbor_rsi * weight
            weight_sum += weight
        historical_index = index - lag
        if np.isfinite(standard_rsi[index]) and np.isfinite(standard_rsi[historical_index]):
            divergence_sum += abs(standard_rsi[index] - standard_rsi[historical_index])
            divergence_count += 1
        older_index = index - (lag + 5)
        if older_index >= 0 and close[historical_index] != 0.0:
            future_return = close[older_index] - close[historical_index]
            if future_return > close[historical_index] * 0.02 and np.isfinite(standard_rsi[historical_index]):
                overbought_candidates.append(standard_rsi[historical_index])
            if future_return < close[historical_index] * -0.02 and np.isfinite(standard_rsi[historical_index]):
                oversold_candidates.append(standard_rsi[historical_index])
    if weight_sum > 0.0:
        neighbor_average = weighted_sum / weight_sum
        knn_rsi[index] = float(np.clip((1.0 - knn_weight) * standard_rsi[index] + knn_weight * neighbor_average, 0.0, 100.0))
        knn_divergence[index] = divergence_sum / divergence_count if divergence_count else 0.0
        neighbor_weight_sum[index] = weight_sum
    if overbought_candidates:
        adaptive_overbought[index] = float(np.mean(overbought_candidates))
    if oversold_candidates:
        adaptive_oversold[index] = float(np.mean(oversold_candidates))

ml_rsi = knn_rsi if use_knn else standard_rsi.copy()
if filter_method == "None":
    filtered_rsi = ml_rsi.copy()
elif filter_method == "Kalman":
    filtered_rsi = np.full(n, np.nan, dtype=float)
    state = np.nan
    for index, value in enumerate(ml_rsi):
        if not np.isfinite(state):
            state = value
        elif np.isfinite(value):
            state = state + filter_strength * (value - state)
        filtered_rsi[index] = state
elif filter_method == "DoubleEMA":
    filtered_rsi = _ema(_ema(ml_rsi, max(1, int(round(filter_strength * 10.0)))), max(1, int(round(filter_strength * 5.0))))
else:
    filtered_rsi = _alma(ml_rsi, max(1, int(round(filter_strength * 20.0))), 0.0, 6.0)
final_rsi = filtered_rsi if use_filter else ml_rsi.copy()

palette = {
    "Deuteranopia": ("#00AAFF", "#FFCC00"), "Protanopia": ("#00AAE6", "#E6AF00"),
    "Tritanopia": ("#FF7000", "#0021AB"), "Monochrome": ("#DEE2E6", "#495057"),
}
if color_scheme == "Classic":
    bullish_color, bearish_color = ("#00FFBB", "#FF1100") if neon_effect else ("#00B712", "#C30010")
else:
    bullish_color, bearish_color = palette.get(color_scheme, ("#00FFBB", "#FF1100"))

line_colors = []
for value in final_rsi:
    if color_mode == "None" or not np.isfinite(value):
        line_colors.append("rgba(0,0,0,0)")
    elif color_mode in ("Trend", "Trend Following"):
        line_colors.append(bullish_color if value > 50.0 else bearish_color)
    else:
        line_colors.append(bullish_color if value > overbought else bearish_color if value < oversold else "#FFFFFF" if 45.0 < value < 55.0 else "#808080")

center_cloud = []
for index, value in enumerate(final_rsi):
    if neon_effect and np.isfinite(value):
        color = line_colors[index]
        center_cloud.append({"upper": float(max(value, 50.0)), "lower": float(min(value, 50.0)), "topColor": color if value > 50.0 else "rgba(0,0,0,0)", "bottomColor": "rgba(0,0,0,0)" if value > 50.0 else color})
    else:
        center_cloud.append(None)
overbought_cloud = [{"upper": 100.0, "lower": float(overbought), "topColor": "rgba(0,0,0,0)", "bottomColor": bearish_color} for _ in range(n)]
oversold_cloud = [{"upper": float(oversold), "lower": 0.0, "topColor": bullish_color, "bottomColor": "rgba(0,0,0,0)"} for _ in range(n)]

plots = [
    {"name": "RSI KNN", "data": _finite(final_rsi), "color": line_colors, "type": "line", "lineWidth": 3, "overlay": False},
    {"name": "50 Level", "data": [50.0] * n, "color": "rgba(255,255,255,0.30)", "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Overbought", "data": [float(overbought)] * n, "color": bearish_color, "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Oversold", "data": [float(oversold)] * n, "color": bullish_color, "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "RSI Gradient", "title": False, "data": center_cloud, "color": bullish_color, "type": "cloud", "overlay": False},
    {"name": "Overbought Zone", "title": False, "data": overbought_cloud, "color": bearish_color, "type": "cloud", "overlay": False},
    {"name": "Oversold Zone", "title": False, "data": oversold_cloud, "color": bullish_color, "type": "cloud", "overlay": False},
]

bar_colors = [f"{bullish_color}CC" if np.isfinite(value) and value > 50.0 else f"{bearish_color}CC" for value in final_rsi]
latest_value = final_rsi[-1] if n else np.nan
context = "Bullish Trend" if color_mode in ("Trend", "Trend Following") and np.isfinite(latest_value) and latest_value > 50.0 else "Bearish Trend" if color_mode in ("Trend", "Trend Following") and np.isfinite(latest_value) else "Bullish Impulse" if color_mode == "Impulse" and np.isfinite(latest_value) and latest_value > overbought else "Bearish Impulse" if color_mode == "Impulse" and np.isfinite(latest_value) and latest_value < oversold else "None"
tables = []
if show_table:
    tables.append({"title": "Machine Learning RSI", "position": "top-right", "compact": True, "rows": [["Mode", color_mode], ["Detected", context], ["Machine Learning", "YES" if use_knn else "NO"], ["OB/OS Thresholds", f"{overbought} / {oversold}"], ["Effective K", str(int(effective_k[-1])) if n else "0"], ["KNN Divergence", f"{knn_divergence[-1]:.2f}" if n else "0.00"]]})

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": [], "layers": [], "tables": tables,
    "bar_colors": bar_colors, "background_colors": [None] * n,
    "calculatedVars": {
        "baseRSI": _finite(base_rsi), "smoothedRSI": _finite(smoothed_rsi), "standardRSI": _finite(standard_rsi),
        "feature1": _finite(normalized_features[0]), "feature2": _finite(normalized_features[1]), "feature3": _finite(normalized_features[2]),
        "feature4": _finite(normalized_features[3]), "feature5": _finite(normalized_features[4]),
        "knnRSI": _finite(knn_rsi), "knnDivergence": _finite(knn_divergence), "effectiveK": effective_k.tolist(),
        "adaptiveOversoldML": _finite(adaptive_oversold), "adaptiveOverboughtML": _finite(adaptive_overbought), "neighborWeightSum": _finite(neighbor_weight_sum),
        "mlRSI": _finite(ml_rsi), "filteredRSI": _finite(filtered_rsi), "finalRSI": _finite(final_rsi),
    },
}
