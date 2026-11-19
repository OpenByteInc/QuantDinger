# ============================================================
# QuantDinger Volume-Accelerated Directional Energy
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Volume-Accelerated Directional Energy"
my_indicator_description = "Separates volume-accelerated price energy into demand and supply ratios, a smoothed net signal, and an optional longer sentiment baseline."

# @param energy_length int 10 Energy length range=1:10000:1
# @param energy_average int 5 Directional energy average range=1:10000:1
# @param average_type str WMA Base average values=WMA,EMA,SMA
# @param smoothing int 3 Net smoothing range=1:10000:1
# @param show_sentiment bool false Show longer sentiment
# @param sentiment_length int 20 Sentiment length range=1:10000:1
# @param volume_calculation str Relative Volume calculation values=Relative,Full,None
# @param volume_lookback int 20 Relative-volume lookback range=1:10000:1

energy_length = min(10000, max(1, int(params.get("energy_length", 10))))
energy_average = min(10000, max(1, int(params.get("energy_average", 5))))
average_type = str(params.get("average_type", "WMA"))
average_type = average_type if average_type in ("WMA", "EMA", "SMA") else "WMA"
smoothing = min(10000, max(1, int(params.get("smoothing", 3))))
show_sentiment = bool(params.get("show_sentiment", False))
sentiment_length = min(10000, max(1, int(params.get("sentiment_length", 20))))
volume_calculation = str(params.get("volume_calculation", "Relative"))
volume_calculation = volume_calculation if volume_calculation in ("Relative", "Full", "None") else "Relative"
volume_lookback = min(10000, max(1, int(params.get("volume_lookback", 20))))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def _ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _wma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, length + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / denominator)
    return result


def _average(values, length):
    return _sma(values, length) if average_type == "SMA" else _ema(values, length) if average_type == "EMA" else _wma(values, length)


def _divide(numerator, denominator):
    result = np.full(len(numerator), np.nan, dtype=float)
    valid = np.isfinite(numerator) & np.isfinite(denominator) & (denominator != 0.0)
    result[valid] = numerator[valid] / denominator[valid]
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = np.nan if column == "volume" else 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].to_numpy(dtype=float)
n = len(df)

if volume_calculation == "None":
    volume_acceleration = np.ones(n, dtype=float)
elif volume_calculation == "Full":
    volume_acceleration = np.where(np.isfinite(volume), volume, 1.0)
else:
    volume_acceleration = np.full(n, np.nan, dtype=float)
    for index in range(n):
        if not np.isfinite(volume[index]):
            volume_acceleration[index] = 1.0
            continue
        window = volume[max(0, index - volume_lookback + 1):index + 1]
        finite_window = window[np.isfinite(window)]
        if len(finite_window):
            minimum, maximum = float(np.min(finite_window)), float(np.max(finite_window))
            if maximum != minimum:
                volume_acceleration[index] = (volume[index] - minimum) / (maximum - minimum)

two_bar_radius = np.empty(n, dtype=float)
price_change = np.full(n, np.nan, dtype=float)
for index in range(n):
    two_bar_radius[index] = (max(high[max(0, index - 1):index + 1]) - min(low[max(0, index - 1):index + 1])) / 2.0
if n > 1:
    price_change[1:] = np.diff(close)
shift_ratio = _divide(price_change, two_bar_radius)
restricted_shift = np.clip(shift_ratio, -1.0, 1.0)
weighted_shift = restricted_shift * volume_acceleration
directional_change = np.zeros(n, dtype=float)
previous_valid = 0.0
for index, value in enumerate(weighted_shift):
    if np.isfinite(value):
        previous_valid = float(value)
    directional_change[index] = previous_valid
demand_change = np.maximum(directional_change, 0.0)
supply_change = np.maximum(-directional_change, 0.0)
average_volume = _average(volume_acceleration, energy_length)
demand_ratio = _divide(_average(demand_change, energy_length), average_volume)
supply_ratio = _divide(_average(supply_change, energy_length), average_volume)
demand_energy = 100.0 * _wma(demand_ratio, energy_average)
supply_energy = 100.0 * _wma(supply_ratio, energy_average)
net_energy = demand_energy - supply_energy
signal = _wma(net_energy, smoothing)
sentiment_demand = 100.0 * _wma(demand_ratio, sentiment_length)
sentiment_supply = 100.0 * _wma(supply_ratio, sentiment_length)
sentiment = _wma(sentiment_demand - sentiment_supply, smoothing)


def _cross_above(first, second):
    return np.asarray([index > 0 and np.all(np.isfinite([first[index], first[index - 1], second[index], second[index - 1]])) and first[index] > second[index] and first[index - 1] <= second[index - 1] for index in range(n)], dtype=bool)


def _cross_below(first, second):
    return np.asarray([index > 0 and np.all(np.isfinite([first[index], first[index - 1], second[index], second[index - 1]])) and first[index] < second[index] and first[index - 1] >= second[index - 1] for index in range(n)], dtype=bool)


zero = np.zeros(n, dtype=float)
signal_up = _cross_above(signal, zero)
signal_down = _cross_below(signal, zero)
speed_up = _cross_above(signal, sentiment)
slow_down = _cross_below(signal, sentiment)
signal_colors = ["#359BFC" if np.isfinite(value) and value >= 0.0 else "#F57F17" for value in signal]
sentiment_colors = []
for index, value in enumerate(sentiment):
    growing = index > 0 and np.all(np.isfinite([value, sentiment[index - 1]])) and abs(value) >= abs(sentiment[index - 1])
    sentiment_colors.append("#1B5E20" if np.isfinite(value) and value >= 0.0 and growing else "#66BB6A" if np.isfinite(value) and value >= 0.0 else "#DC4C4A" if growing else "#EF8E98")
energy_cloud = [{"upper": float(max(demand_energy[index], supply_energy[index])), "lower": float(min(demand_energy[index], supply_energy[index])), "color": "#008000" if demand_energy[index] > supply_energy[index] else "#F23645", "opacity": 0.20} if np.all(np.isfinite([demand_energy[index], supply_energy[index]])) else None for index in range(n)]


def _events(mask, prices, text, color, event_type):
    return {"type": event_type, "name": text, "data": [{"active": True, "price": float(prices[index]), "text": text, "color": color} if active and np.isfinite(prices[index]) else None for index, active in enumerate(mask)], "color": color, "renderMode": "events"}


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Demand Energy", "data": _finite(demand_energy), "color": "rgba(0,255,255,0.70)", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Supply Energy", "data": _finite(supply_energy), "color": "rgba(255,152,0,0.70)", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Energy Fill", "title": False, "data": energy_cloud, "color": "#008000", "type": "cloud", "overlay": False},
        {"name": "Signal", "data": _finite(signal), "color": signal_colors, "type": "line", "lineWidth": 4, "overlay": False},
        {"name": "Sentiment", "title": show_sentiment, "data": _finite(sentiment) if show_sentiment else [None] * n, "color": sentiment_colors, "type": "histogram", "overlay": False},
        {"name": "Zero Line", "data": [0.0] * n, "color": "rgba(255,238,0,0.30)", "type": "line", "overlay": False},
    ],
    "signals": [
        _events(signal_up, signal, "Energy Up", "#359BFC", "buy"), _events(signal_down, signal, "Energy Down", "#F57F17", "sell"),
        _events(speed_up, signal, "Speeding Up", "#089981", "buy"), _events(slow_down, signal, "Slowing Down", "#F23645", "sell"),
    ],
    "layers": [], "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "volumeAcceleration": _finite(volume_acceleration), "twoBarRadius": _finite(two_bar_radius), "priceChange": _finite(price_change),
        "shiftRatio": _finite(shift_ratio), "restrictedShift": _finite(restricted_shift), "directionalChange": _finite(directional_change),
        "demandChange": _finite(demand_change), "supplyChange": _finite(supply_change), "averageVolume": _finite(average_volume),
        "demandRatio": _finite(demand_ratio), "supplyRatio": _finite(supply_ratio), "demandEnergy": _finite(demand_energy), "supplyEnergy": _finite(supply_energy),
        "netEnergy": _finite(net_energy), "signal": _finite(signal), "sentimentDemand": _finite(sentiment_demand), "sentimentSupply": _finite(sentiment_supply), "sentiment": _finite(sentiment),
        "signalUp": signal_up.tolist(), "signalDown": signal_down.tolist(), "speedUp": speed_up.tolist(), "slowDown": slow_down.tolist(),
    },
}
