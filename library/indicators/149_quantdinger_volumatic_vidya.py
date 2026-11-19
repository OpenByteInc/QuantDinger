# ============================================================
# QuantDinger Volumatic VIDYA
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Volumatic VIDYA"
my_indicator_description = (
    "Combines a momentum-adaptive VIDYA with ATR trend bands, pivot liquidity levels, "
    "short-horizon level-cross labels, and directional volume statistics."
)

# @param vidya_length int 10 VIDYA length range=2:200:1
# @param momentum_length int 20 VIDYA momentum length range=2:200:1
# @param band_distance float 2.0 ATR band distance range=0.1:10.0:0.1
# @param source str Close VIDYA source values=Close,Open,HL2,HLC3,OHLC4
# @param shadow bool true Show trend shadow

vidya_length = min(200, max(2, int(params.get("vidya_length", 10))))
momentum_length = min(200, max(2, int(params.get("momentum_length", 20))))
band_distance = min(10.0, max(0.1, float(params.get("band_distance", 2.0))))
source_name = str(params.get("source", "Close"))
shadow = bool(params.get("shadow", True))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].fillna(0.0).astype(float).to_numpy(dtype=float)
n = len(df)

hl2 = (high + low) / 2.0
source_map = {
    "Close": close,
    "Open": open_price,
    "HL2": hl2,
    "HLC3": (high + low + close) / 3.0,
    "OHLC4": (open_price + high + low + close) / 4.0,
}
source = source_map.get(source_name, close)

true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
atr = np.full(n, np.nan, dtype=float)
atr_length = 200
if n >= atr_length:
    atr[atr_length - 1] = float(np.mean(true_range[:atr_length]))
    for index in range(atr_length, n):
        atr[index] = (atr[index - 1] * (atr_length - 1) + true_range[index]) / atr_length

momentum = np.full(n, np.nan, dtype=float)
if n > 1:
    momentum[1:] = source[1:] - source[:-1]
positive = np.where(np.isfinite(momentum) & (momentum >= 0.0), momentum, 0.0)
negative = np.where(np.isfinite(momentum) & (momentum < 0.0), -momentum, 0.0)
positive_sum = pd.Series(positive).rolling(momentum_length, min_periods=momentum_length).sum().to_numpy(dtype=float)
negative_sum = pd.Series(negative).rolling(momentum_length, min_periods=momentum_length).sum().to_numpy(dtype=float)

raw_vidya = np.full(n, np.nan, dtype=float)
alpha = 2.0 / (vidya_length + 1.0)
previous_vidya = np.nan
for index in range(n):
    denominator = positive_sum[index] + negative_sum[index]
    if not np.isfinite(denominator) or denominator <= 1e-12:
        continue
    absolute_cmo = abs(100.0 * (positive_sum[index] - negative_sum[index]) / denominator)
    adaptive_alpha = alpha * absolute_cmo / 100.0
    if np.isfinite(previous_vidya):
        current_vidya = adaptive_alpha * source[index] + (1.0 - adaptive_alpha) * previous_vidya
    else:
        current_vidya = float(source[index])
    raw_vidya[index] = current_vidya
    previous_vidya = current_vidya

vidya = pd.Series(raw_vidya).rolling(15, min_periods=15).mean().to_numpy(dtype=float)
upper_band = vidya + atr * band_distance
lower_band = vidya - atr * band_distance

trend_up = np.zeros(n, dtype=bool)
smoothed = np.full(n, np.nan, dtype=float)
trend_cross_up = np.zeros(n, dtype=bool)
trend_cross_down = np.zeros(n, dtype=bool)
current_trend = False
for index in range(n):
    if index > 0 and np.isfinite(upper_band[index]) and np.isfinite(upper_band[index - 1]):
        if source[index - 1] <= upper_band[index - 1] and source[index] > upper_band[index]:
            current_trend = True
            trend_cross_up[index] = True
        if source[index - 1] >= lower_band[index - 1] and source[index] < lower_band[index]:
            current_trend = False
            trend_cross_down[index] = True
    trend_up[index] = current_trend
    if np.isfinite(lower_band[index]) and np.isfinite(upper_band[index]):
        smoothed[index] = lower_band[index] if current_trend else upper_band[index]
    if trend_cross_up[index] or trend_cross_down[index]:
        smoothed[index] = np.nan

pivot_left = 3
pivot_right = 3
active_low_lines = []
active_high_lines = []
finished_lines = []
cross_labels = []

for confirmation_index in range(pivot_left + pivot_right, n):
    candidate = confirmation_index - pivot_right
    window_start = candidate - pivot_left
    window_end = candidate + pivot_right + 1
    pivot_low = close[candidate] <= float(np.min(close[window_start:window_end]))
    pivot_high = high[candidate] >= float(np.max(high[window_start:window_end]))

    if pivot_low and np.isfinite(smoothed[confirmation_index]) and low[candidate] > smoothed[confirmation_index]:
        level_volume = float(np.mean(volume[max(0, confirmation_index - 5):confirmation_index + 1]))
        active_low_lines.append(
            {"start": candidate, "end": confirmation_index, "price": float(low[candidate]), "volume": level_volume, "color": "rgba(23,223,173,0.50)"}
        )
    if pivot_high and np.isfinite(smoothed[confirmation_index]) and high[candidate] < smoothed[confirmation_index]:
        level_volume = -float(np.mean(volume[max(0, confirmation_index - 5):confirmation_index + 1]))
        active_high_lines.append(
            {"start": candidate, "end": confirmation_index, "price": float(high[candidate]), "volume": level_volume, "color": "rgba(221,50,107,0.50)"}
        )

    if confirmation_index == 0 or not np.isfinite(smoothed[confirmation_index]) or not np.isfinite(smoothed[confirmation_index - 1]):
        continue
    next_high_lines = []
    for line in active_high_lines:
        age = confirmation_index - line["start"]
        if age > 50:
            line["end"] = min(n - 1, line["start"] + 50)
            finished_lines.append(line)
            continue
        line["end"] = confirmation_index
        crossed = smoothed[confirmation_index] < line["price"] and smoothed[confirmation_index - 1] >= line["price"]
        if crossed:
            finished_lines.append(line)
            cross_labels.append(
                {"index": confirmation_index - 1, "price": float(smoothed[confirmation_index - 1]), "volume": line["volume"], "side": "high"}
            )
        else:
            next_high_lines.append(line)
    active_high_lines = next_high_lines

    next_low_lines = []
    for line in active_low_lines:
        age = confirmation_index - line["start"]
        if age > 50:
            line["end"] = min(n - 1, line["start"] + 50)
            finished_lines.append(line)
            continue
        line["end"] = confirmation_index
        crossed = smoothed[confirmation_index] > line["price"] and smoothed[confirmation_index - 1] <= line["price"]
        if crossed:
            finished_lines.append(line)
            cross_labels.append(
                {"index": confirmation_index - 1, "price": float(smoothed[confirmation_index - 1]), "volume": line["volume"], "side": "low"}
            )
        else:
            next_low_lines.append(line)
    active_low_lines = next_low_lines

all_lines = (finished_lines + active_low_lines + active_high_lines)[-500:]
layers = []
for line in all_lines:
    layers.append(
        {
            "type": "line",
            "startIndex": int(line["start"]),
            "endIndex": int(min(n - 1, line["end"])),
            "y1": float(line["price"]),
            "y2": float(line["price"]),
            "color": line["color"],
            "lineWidth": 1,
        }
    )
for item in cross_labels[-250:]:
    text_value = f"{item['volume'] / 1000.0:.1f}K" if abs(item["volume"]) >= 1000.0 else f"{item['volume']:.0f}"
    layers.append(
        {
            "type": "label",
            "index": int(item["index"]),
            "price": float(item["price"]),
            "text": text_value + " ◉",
            "color": "rgba(0,0,0,0)",
            "textColor": "#DD326B" if item["side"] == "high" else "#17DFAD",
            "side": "center",
        }
    )

up_volume = 0.0
down_volume = 0.0
for index in range(n):
    if trend_cross_up[index] or trend_cross_down[index]:
        up_volume = 0.0
        down_volume = 0.0
    else:
        if close[index] > open_price[index]:
            up_volume += volume[index]
        elif close[index] < open_price[index]:
            down_volume += volume[index]
if n and np.isfinite(smoothed[-1]):
    average_volume = (up_volume + down_volume) / 2.0
    delta_percent = (up_volume - down_volume) / average_volume * 100.0 if average_volume > 0.0 else 0.0
    trend_color = "#17DFAD" if trend_up[-1] else "#DD326B"
    previous_close = close[-2] if n > 1 else close[-1]
    last_true_range = max(
        high[-1] - low[-1],
        abs(high[-1] - previous_close),
        abs(low[-1] - previous_close),
    )
    stats_price = float(smoothed[-1] + max(last_true_range, abs(smoothed[-1]) * 0.002) * 1.5)
    layers.append(
        {
            "type": "label",
            "index": int(n - 1),
            "price": stats_price,
            "text": f"Buy {up_volume:.0f} · Sell {down_volume:.0f}\nΔ Volume {delta_percent:.1f}%",
            "color": "rgba(23,223,173,0.10)" if trend_up[-1] else "rgba(221,50,107,0.10)",
            "textColor": trend_color,
            "side": "right",
            "size": "small",
        }
    )

trend_line = []
trend_shadow_upper = []
trend_shadow_lower = []
for index in range(n):
    if np.isfinite(smoothed[index]):
        color = "#17DFAD" if trend_up[index] else "#DD326B"
        trend_line.append({"value": float(smoothed[index]), "color": color})
        trend_shadow_upper.append(float(max(smoothed[index], hl2[index])) if shadow else None)
        trend_shadow_lower.append(float(min(smoothed[index], hl2[index])) if shadow else None)
    else:
        trend_line.append(None)
        trend_shadow_upper.append(None)
        trend_shadow_lower.append(None)

up_marks = [None for _ in range(n)]
down_marks = [None for _ in range(n)]
for index in range(1, n):
    if trend_cross_up[index - 1] and np.isfinite(smoothed[index]):
        up_marks[index] = float(smoothed[index])
    if trend_cross_down[index - 1] and np.isfinite(smoothed[index]):
        down_marks[index] = float(smoothed[index])

plots = [
    {"name": "Volumatic VIDYA", "data": trend_line, "color": "#17DFAD", "type": "line", "lineWidth": 2, "overlay": True},
    {"name": "VIDYA Shadow Upper", "title": False, "data": trend_shadow_upper, "color": "rgba(23,223,173,0.18)", "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "VIDYA Shadow Lower", "title": False, "data": trend_shadow_lower, "color": "rgba(221,50,107,0.18)", "type": "line", "lineWidth": 1, "overlay": True},
]
plots = [plot for plot in plots if any(item is not None for item in plot["data"])]
signals = []
if any(item is not None for item in up_marks):
    signals.append({"type": "buy", "text": "▲", "name": "VIDYA Trend Up", "data": up_marks, "color": "#17DFAD", "renderMode": "events"})
if any(item is not None for item in down_marks):
    signals.append({"type": "sell", "text": "▼", "name": "VIDYA Trend Down", "data": down_marks, "color": "#DD326B", "renderMode": "events"})
output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers}
