# ============================================================
# QuantDinger Momentum Shift
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

import math

my_indicator_name = "QuantDinger Momentum Shift"
my_indicator_description = "Hull-smoothed momentum shifts with six projected price zones, target-hit lifecycle and a gradient momentum pane."

# @param momentum_length int 50 Momentum difference length range=1:1000:1
# @param smoothing_length int 50 Hull smoothing length range=1:1000:1
# @param zone_size_multiplier float 1.0 Shift zone size range=0:100:0.1
# @param bullish_color str #3AD79B Bullish color
# @param bearish_color str #FB6B2E Bearish color
# @param maximum_zone_sets int 40 Maximum retained zone sets range=1:80:1

momentum_length = min(1000, max(1, int(params.get("momentum_length", 50))))
smoothing_length = min(1000, max(1, int(params.get("smoothing_length", 50))))
zone_size_multiplier = min(100.0, max(0.0, float(params.get("zone_size_multiplier", 1.0))))
bullish_color = str(params.get("bullish_color", "#3AD79B"))
bearish_color = str(params.get("bearish_color", "#FB6B2E"))
maximum_zone_sets = min(80, max(1, int(params.get("maximum_zone_sets", 40))))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _wma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, period + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.sum(window * weights) / denominator)
    return result


def _hma(values, period):
    half = max(1, int(period / 2))
    root = max(1, int(math.floor(math.sqrt(period) + 0.5)))
    return _wma(2.0 * _wma(values, half) - _wma(values, period), root)


def _rolling_extreme(values, period, maximum):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.any(np.isfinite(window)):
            result[index] = float(np.nanmax(window) if maximum else np.nanmin(window))
    return result


def _sma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def _rgb(color):
    text = str(color).strip()
    if text.startswith("#") and len(text) >= 7:
        try:
            return tuple(int(text[index:index + 2], 16) for index in (1, 3, 5))
        except ValueError:
            pass
    return (128, 128, 128)


def _gradient(value, low_value, high_value):
    if not np.all(np.isfinite([value, low_value, high_value])):
        return None
    ratio = 0.5 if high_value == low_value else min(1.0, max(0.0, (value - low_value) / (high_value - low_value)))
    low_rgb, high_rgb = _rgb(bearish_color), _rgb(bullish_color)
    channels = [int(round(low_rgb[index] + ratio * (high_rgb[index] - low_rgb[index]))) for index in range(3)]
    return f"#{channels[0]:02X}{channels[1]:02X}{channels[2]:02X}"


def _alpha(color, transparency):
    red, green, blue = _rgb(color)
    return f"rgba({red},{green},{blue},{max(0.0, min(1.0, 1.0 - transparency / 100.0)):.2f})"


df = df.copy().reset_index(drop=True)
if "time" not in df.columns:
    df["time"] = np.arange(len(df), dtype=float) * 3600000.0
if "volume" not in df.columns:
    df["volume"] = 0.0
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
times = df["time"].to_numpy(dtype=float)
if len(times) and max(abs(float(value)) for value in times) < 10_000_000_000:
    times = times * 1000.0
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)

raw_momentum = np.full(n, np.nan, dtype=float)
for index in range(momentum_length, n):
    raw_momentum[index] = close[index] - close[index - momentum_length]
momentum = _hma(raw_momentum, smoothing_length)
momentum_high = _rolling_extreme(momentum, 150, True)
momentum_low = _rolling_extreme(momentum, 150, False)
rising = np.zeros(n, dtype=bool)
falling = np.zeros(n, dtype=bool)
for index in range(3, n):
    if np.all(np.isfinite([momentum[index], momentum[index - 1], momentum[index - 2], momentum[index - 3]])):
        rising[index] = momentum[index] > momentum[index - 2] and momentum[index - 1] <= momentum[index - 3]
        falling[index] = momentum[index] < momentum[index - 2] and momentum[index - 1] >= momentum[index - 3]
distance = _sma(high - low, 100) * zone_size_multiplier

rsi_like_colors = [_gradient(momentum[index], momentum_low[index], momentum_high[index]) for index in range(n)]
up_negative = rising & np.isfinite(momentum) & (momentum < 0.0)
down_positive = falling & np.isfinite(momentum) & (momentum > 0.0)

active_up = []
active_down = []
completed = []
zone_set_order = []
zone_snapshots = []
next_set_id = 0
for index in range(n):
    if rising[index]:
        active_down = []
        if np.isfinite(distance[index]):
            new_set = []
            next_set_id += 1
            for level in range(6):
                item = {"set_id": next_set_id, "direction": 1, "level": level, "start_index": index + level * 3, "end_index": index + 5 + level * 3, "price": float(high[index] + distance[index] * level), "hit": False}
                active_up.append(item)
                new_set.append(item)
            zone_set_order.append(new_set)
    if falling[index]:
        active_up = []
        if np.isfinite(distance[index]):
            new_set = []
            next_set_id += 1
            for level in range(6):
                item = {"set_id": next_set_id, "direction": -1, "level": level, "start_index": index + level * 3, "end_index": index + 5 + level * 3, "price": float(low[index] - distance[index] * level), "hit": False}
                active_down.append(item)
                new_set.append(item)
            zone_set_order.append(new_set)
    for item in list(active_up):
        item["end_index"] = index + 5 + item["level"] * 3
        if high[index] >= item["price"]:
            item["end_index"] = index
            item["hit"] = True
            completed.append(item)
            active_up.remove(item)
    for item in list(active_down):
        item["end_index"] = index + 5 + item["level"] * 3
        if low[index] <= item["price"]:
            item["end_index"] = index
            item["hit"] = True
            completed.append(item)
            active_down.remove(item)
    zone_snapshots.append((len(active_up), len(active_down), len(completed)))

retained_sets = zone_set_order[-maximum_zone_sets:]
retained_ids = {item["set_id"] for zone_set in retained_sets for item in zone_set}
visible_items = [item for item in completed + active_up + active_down if item["set_id"] in retained_ids]
layers = []
time_step = float(np.median(np.diff(times[-min(n, 20):]))) if n >= 2 else 3_600_000.0


def _time_at(index):
    if not n:
        return 0.0
    return float(times[0] + time_step * index)


for item in visible_items:
    if not n:
        continue
    base_color = bullish_color if item["direction"] == 1 else bearish_color
    transparency = 60 - 10 * item["level"]
    layers.append({"type": "line", "role": f"{'Up' if item['direction'] == 1 else 'Down'} Zone {item['level']}", "start": _time_at(item["start_index"]), "end": _time_at(item["end_index"]), "y1": item["price"], "y2": item["price"], "color": _alpha(base_color, transparency), "lineWidth": 1, "hit": bool(item["hit"])})
    if item["level"] > 0:
        layers.append({"type": "label", "role": "Zone Number", "time": _time_at(item["start_index"]), "price": item["price"], "text": str(item["level"]), "color": "rgba(0,0,0,0)", "textColor": _alpha(base_color, transparency)})

momentum_fill = []
for index in range(n):
    if np.isfinite(momentum[index]) and rsi_like_colors[index]:
        momentum_fill.append(float(momentum[index]))
    else:
        momentum_fill.append(None)

signals = [
    {"type": "buy", "name": "Rising Shift", "text": "▲", "data": [float(low[index]) if rising[index] else None for index in range(n)], "color": _alpha(bullish_color, 50), "renderMode": "events"},
    {"type": "sell", "name": "Falling Shift", "text": "▼", "data": [float(high[index]) if falling[index] else None for index in range(n)], "color": _alpha(bearish_color, 50), "renderMode": "events"},
    {"type": "buy", "name": "Bullish Momentum Reversal", "text": "△", "data": [float(momentum[index]) if up_negative[index] else None for index in range(n)], "color": bullish_color, "renderMode": "events", "overlay": False},
    {"type": "sell", "name": "Bearish Momentum Reversal", "text": "▽", "data": [float(momentum[index]) if down_positive[index] else None for index in range(n)], "color": bearish_color, "renderMode": "events", "overlay": False},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Momentum", "data": _finite(momentum), "color": "#FFFFFF", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Momentum Gradient", "title": False, "data": momentum_fill, "color": rsi_like_colors, "type": "histogram", "baseValue": 0.0, "lineWidth": 2, "overlay": False},
        {"name": "Zero", "data": [0.0] * n, "color": "rgba(128,128,128,0.45)", "type": "line", "lineWidth": 1, "overlay": False},
    ],
    "signals": signals,
    "layers": layers,
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "rawMomentum": _finite(raw_momentum), "momentum": _finite(momentum), "momentumHigh": _finite(momentum_high), "momentumLow": _finite(momentum_low),
        "rising": rising.tolist(), "falling": falling.tolist(), "distance": _finite(distance), "gradientColors": rsi_like_colors,
        "bullishNegativeMomentum": up_negative.tolist(), "bearishPositiveMomentum": down_positive.tolist(),
        "zoneSnapshots": [[int(value) for value in snapshot] for snapshot in zone_snapshots],
        "activeUpZones": len(active_up), "activeDownZones": len(active_down), "completedZones": len(completed), "visibleZoneObjects": len(visible_items),
    },
}
