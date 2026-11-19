# ============================================================
# QuantDinger RSI Momentum Divergence Zones
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger RSI Momentum Divergence Zones"
my_indicator_description = "Detects price-versus-RSI divergences on ten-bar momentum and maintains bounded overlay levels until invalidation."

# @param rsi_length int 14 RSI length range=1:500:1
# @param enable_divergence bool true Enable divergence detection
# @param show_divergence_zones bool true Show divergence zones
# @param divergence_zone_count int 10 Maximum active zones per side range=0:250:1
# @param bearish_color str #AE4CE6 Bearish divergence color
# @param bullish_color str #33C570 Bullish divergence color

rsi_length = min(500, max(1, int(params.get("rsi_length", 14))))
enable_divergence = bool(params.get("enable_divergence", True))
show_divergence_zones = bool(params.get("show_divergence_zones", True))
divergence_zone_count = min(250, max(0, int(params.get("divergence_zone_count", 10))))
bearish_color = str(params.get("bearish_color", "#AE4CE6"))
bullish_color = str(params.get("bullish_color", "#33C570"))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    seed = []
    state = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        if not np.isfinite(state):
            seed.append(float(value))
            if len(seed) == length:
                state = float(np.mean(seed))
                result[index] = state
        else:
            state = (state * (length - 1.0) + float(value)) / length
            result[index] = state
    return result


def _rgba(color, opacity):
    text = str(color).lstrip("#")
    if len(text) == 6:
        try:
            red, green, blue = int(text[:2], 16), int(text[2:4], 16), int(text[4:], 16)
            return f"rgba({red},{green},{blue},{opacity:.3f})"
        except ValueError:
            pass
    return color


def _event(mask, prices, text, color):
    return [{"active": True, "price": float(prices[index]), "text": text, "color": color} if mask[index] and np.isfinite(prices[index]) else None for index in range(len(mask))]


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = np.nan if column == "volume" else 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)

momentum = np.full(n, np.nan, dtype=float)
if n > 10:
    momentum[10:] = close[10:] - close[:-10]
change = np.diff(momentum, prepend=np.nan)
average_gain = _rma(np.where(np.isfinite(change), np.maximum(change, 0.0), np.nan), rsi_length)
average_loss = _rma(np.where(np.isfinite(change), np.maximum(-change, 0.0), np.nan), rsi_length)
rsi = np.full(n, np.nan, dtype=float)
for index in range(n):
    if np.all(np.isfinite([average_gain[index], average_loss[index]])):
        rsi[index] = 100.0 if average_loss[index] == 0.0 else 0.0 if average_gain[index] == 0.0 else 100.0 - 100.0 / (1.0 + average_gain[index] / average_loss[index])

left_bars = 5
right_bars = 5
minimum_spacing = 5
maximum_spacing = 50
found_low = np.zeros(n, dtype=bool)
found_high = np.zeros(n, dtype=bool)
pivot_rsi = np.full(n, np.nan, dtype=float)
pivot_low_price = np.full(n, np.nan, dtype=float)
pivot_high_price = np.full(n, np.nan, dtype=float)
previous_low_rsi = np.full(n, np.nan, dtype=float)
previous_high_rsi = np.full(n, np.nan, dtype=float)
previous_low_price = np.full(n, np.nan, dtype=float)
previous_high_price = np.full(n, np.nan, dtype=float)
low_spacing = np.full(n, -1, dtype=int)
high_spacing = np.full(n, -1, dtype=int)
rsi_higher_low = np.zeros(n, dtype=bool)
rsi_lower_high = np.zeros(n, dtype=bool)
price_lower_low = np.zeros(n, dtype=bool)
price_higher_high = np.zeros(n, dtype=bool)
bull_divergence = np.zeros(n, dtype=bool)
bear_divergence = np.zeros(n, dtype=bool)

low_occurrences = []
high_occurrences = []
if enable_divergence:
    for confirmation in range(left_bars + right_bars, n):
        center = confirmation - right_bars
        window = rsi[center - left_bars:center + right_bars + 1]
        if len(window) != left_bars + right_bars + 1 or not np.all(np.isfinite(window)):
            continue
        found_low[confirmation] = rsi[center] <= float(np.min(window))
        found_high[confirmation] = rsi[center] >= float(np.max(window))
        if found_low[confirmation]:
            pivot_rsi[confirmation] = rsi[center]
            pivot_low_price[confirmation] = low[center]
            if low_occurrences:
                previous_confirmation, prior_rsi, prior_price = low_occurrences[-1]
                spacing = confirmation - previous_confirmation - 1
                previous_low_rsi[confirmation] = prior_rsi
                previous_low_price[confirmation] = prior_price
                low_spacing[confirmation] = spacing
                rsi_higher_low[confirmation] = rsi[center] > prior_rsi and minimum_spacing <= spacing <= maximum_spacing
                price_lower_low[confirmation] = low[center] < prior_price
                bull_divergence[confirmation] = rsi_higher_low[confirmation] and price_lower_low[confirmation]
            low_occurrences.append((confirmation, float(rsi[center]), float(low[center])))
        if found_high[confirmation]:
            pivot_rsi[confirmation] = rsi[center]
            pivot_high_price[confirmation] = high[center]
            if high_occurrences:
                previous_confirmation, prior_rsi, prior_price = high_occurrences[-1]
                spacing = confirmation - previous_confirmation - 1
                previous_high_rsi[confirmation] = prior_rsi
                previous_high_price[confirmation] = prior_price
                high_spacing[confirmation] = spacing
                rsi_lower_high[confirmation] = rsi[center] < prior_rsi and minimum_spacing <= spacing <= maximum_spacing
                price_higher_high[confirmation] = high[center] > prior_price
                bear_divergence[confirmation] = rsi_lower_high[confirmation] and price_higher_high[confirmation]
            high_occurrences.append((confirmation, float(rsi[center]), float(high[center])))

active_bull = []
active_bear = []
completed_levels = []
bull_zone_created = np.zeros(n, dtype=bool)
bear_zone_created = np.zeros(n, dtype=bool)
bull_zone_broken = np.zeros(n, dtype=bool)
bear_zone_broken = np.zeros(n, dtype=bool)
bull_active_count = np.zeros(n, dtype=int)
bear_active_count = np.zeros(n, dtype=int)
level_identifier = 0

if show_divergence_zones:
    for index in range(n):
        for is_bull, triggered, price, collection in (
            (False, bear_divergence[index], high[index - right_bars] if index >= right_bars else np.nan, active_bear),
            (True, bull_divergence[index], low[index - right_bars] if index >= right_bars else np.nan, active_bull),
        ):
            if triggered and np.isfinite(price):
                collection.append({"id": int(level_identifier), "bullish": bool(is_bull), "startIndex": int(index - right_bars), "endIndex": int(index), "price": float(price), "active": True})
                level_identifier += 1
                (bull_zone_created if is_bull else bear_zone_created)[index] = True
            while len(collection) > divergence_zone_count:
                collection.pop(0)
            survivors = []
            for level in collection:
                remains_active = high[index] > level["price"] if is_bull else low[index] < level["price"]
                if remains_active:
                    level["endIndex"] = int(index + 15)
                    survivors.append(level)
                else:
                    level["active"] = False
                    level["endIndex"] = int(index)
                    level["breakIndex"] = int(index)
                    completed_levels.append(level)
                    (bull_zone_broken if is_bull else bear_zone_broken)[index] = True
            collection[:] = survivors
        bull_active_count[index] = len(active_bull)
        bear_active_count[index] = len(active_bear)

layers = []
for level in completed_levels:
    color = bullish_color if level["bullish"] else bearish_color
    layers.append({"type": "line", "role": "Broken Bullish Divergence" if level["bullish"] else "Broken Bearish Divergence", "startIndex": level["startIndex"], "endIndex": level["endIndex"], "y1": level["price"], "y2": level["price"], "color": color, "lineWidth": 1, "lineStyle": "dashed"})
for level in active_bear + active_bull:
    color = bullish_color if level["bullish"] else bearish_color
    role = "Bullish Divergence Zone" if level["bullish"] else "Bearish Divergence Zone"
    layers.append({"type": "line", "role": f"{role} Glow", "startIndex": level["startIndex"], "endIndex": level["endIndex"], "y1": level["price"], "y2": level["price"], "color": _rgba(color, 0.30), "lineWidth": 6})
    layers.append({"type": "line", "role": role, "startIndex": level["startIndex"], "endIndex": level["endIndex"], "y1": level["price"], "y2": level["price"], "color": color, "lineWidth": 1})

bull_at_pivot = np.zeros(n, dtype=bool)
bear_at_pivot = np.zeros(n, dtype=bool)
bull_rsi_at_pivot = np.full(n, np.nan, dtype=float)
bear_rsi_at_pivot = np.full(n, np.nan, dtype=float)
for confirmation in np.flatnonzero(bull_divergence):
    pivot_index = confirmation - right_bars
    if pivot_index >= 0:
        bull_at_pivot[pivot_index] = True
        bull_rsi_at_pivot[pivot_index] = rsi[pivot_index]
for confirmation in np.flatnonzero(bear_divergence):
    pivot_index = confirmation - right_bars
    if pivot_index >= 0:
        bear_at_pivot[pivot_index] = True
        bear_rsi_at_pivot[pivot_index] = rsi[pivot_index]

rsi_fill = [{"upper": float(max(rsi[index], 50.0)), "lower": float(min(rsi[index], 50.0)), "color": bearish_color if rsi[index] >= 50.0 else bullish_color, "opacity": min(0.28, 0.08 + abs(rsi[index] - 50.0) / 200.0)} if np.isfinite(rsi[index]) else None for index in range(n)]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "RSI Momentum Fill", "title": False, "data": rsi_fill, "color": bullish_color, "type": "cloud", "overlay": False},
        {"name": "Center", "title": True, "data": [50.0] * n, "color": "#808080", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "RSI", "title": True, "data": _finite(rsi), "color": "#58A6FF", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Bullish RSI Higher Low", "title": True, "data": _finite(bull_rsi_at_pivot), "color": bullish_color, "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Bearish RSI Lower High", "title": True, "data": _finite(bear_rsi_at_pivot), "color": bearish_color, "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "name": "Bullish RSI Divergence", "text": "Bull", "data": _event(bull_at_pivot, bull_rsi_at_pivot, "Bull", bullish_color), "color": bullish_color, "renderMode": "events", "overlay": False},
        {"type": "sell", "name": "Bearish RSI Divergence", "text": "Bear", "data": _event(bear_at_pivot, bear_rsi_at_pivot, "Bear", bearish_color), "color": bearish_color, "renderMode": "events", "overlay": False},
        {"type": "buy", "name": "Bullish Price Divergence", "text": "Bull", "data": _event(bull_at_pivot, low, "▲ Bull", bullish_color), "color": bullish_color, "renderMode": "events", "overlay": True},
        {"type": "sell", "name": "Bearish Price Divergence", "text": "Bear", "data": _event(bear_at_pivot, high, "Bear ▼", bearish_color), "color": bearish_color, "renderMode": "events", "overlay": True},
    ],
    "layers": layers,
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "momentum": _finite(momentum), "averageGain": _finite(average_gain), "averageLoss": _finite(average_loss), "rsi": _finite(rsi),
        "foundPivotLow": found_low.tolist(), "foundPivotHigh": found_high.tolist(), "pivotRsi": _finite(pivot_rsi),
        "pivotLowPrice": _finite(pivot_low_price), "pivotHighPrice": _finite(pivot_high_price),
        "previousLowRsi": _finite(previous_low_rsi), "previousHighRsi": _finite(previous_high_rsi), "previousLowPrice": _finite(previous_low_price), "previousHighPrice": _finite(previous_high_price),
        "lowSpacing": low_spacing.tolist(), "highSpacing": high_spacing.tolist(), "rsiHigherLow": rsi_higher_low.tolist(), "rsiLowerHigh": rsi_lower_high.tolist(),
        "priceLowerLow": price_lower_low.tolist(), "priceHigherHigh": price_higher_high.tolist(), "bullDivergence": bull_divergence.tolist(), "bearDivergence": bear_divergence.tolist(),
        "bullZoneCreated": bull_zone_created.tolist(), "bearZoneCreated": bear_zone_created.tolist(), "bullZoneBroken": bull_zone_broken.tolist(), "bearZoneBroken": bear_zone_broken.tolist(),
        "bullActiveCount": bull_active_count.tolist(), "bearActiveCount": bear_active_count.tolist(),
        "activeBullLevels": active_bull, "activeBearLevels": active_bear, "completedLevels": completed_levels,
    },
}
