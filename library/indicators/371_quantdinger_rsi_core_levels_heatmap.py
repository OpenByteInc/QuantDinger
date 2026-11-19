# ============================================================
# QuantDinger RSI Core Levels Heatmap
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger RSI Core Levels Heatmap"
my_indicator_description = "Combines an RSI heatmap with collision-filtered price support and resistance blocks derived from signal-line crosses."

# @param max_active_levels int 5 Maximum active levels range=1:500:1
# @param rsi_length int 14 RSI length range=1:10000:1
# @param signal_length int 14 RSI signal SMA length range=1:10000:1
# @param show_mid_zone bool true Show the RSI 40-60 squeeze track
# @param show_dashboard bool true Show nearest-level dashboard
# @param table_position str top_right Dashboard position values=top_right,bottom_right,top_left,bottom_left
# @param bull_color str #04C370 Bullish color
# @param bear_color str #FF0000 Bearish color
# @param signal_color str #FFA500 Signal-line color
# @param mid_zone_color str #FFFF00 Mid-zone color

max_active_levels = min(500, max(1, int(params.get("max_active_levels", 5))))
rsi_length = min(10000, max(1, int(params.get("rsi_length", 14))))
signal_length = min(10000, max(1, int(params.get("signal_length", 14))))
show_mid_zone = bool(params.get("show_mid_zone", True))
show_dashboard = bool(params.get("show_dashboard", True))
table_position_name = str(params.get("table_position", "top_right"))
table_position = {"top_right": "top-right", "bottom_right": "bottom-right", "top_left": "top-left", "bottom_left": "bottom-left"}.get(table_position_name, "top-right")
bull_color = str(params.get("bull_color", "#04C370"))
bear_color = str(params.get("bear_color", "#FF0000"))
signal_color = str(params.get("signal_color", "#FFA500"))
mid_zone_color = str(params.get("mid_zone_color", "#FFFF00"))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    seed = []
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed.append(float(value))
            if len(seed) == length:
                previous = float(np.mean(seed))
                result[index] = previous
        else:
            previous = (previous * (length - 1.0) + float(value)) / length
            result[index] = previous
    return result


def _rsi(values, length):
    change = np.diff(values, prepend=np.nan)
    gain = _rma(np.where(np.isfinite(change), np.maximum(change, 0.0), np.nan), length)
    loss = _rma(np.where(np.isfinite(change), np.maximum(-change, 0.0), np.nan), length)
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(len(values)):
        if np.all(np.isfinite([gain[index], loss[index]])):
            result[index] = 100.0 if loss[index] == 0.0 else 0.0 if gain[index] == 0.0 else 100.0 - 100.0 / (1.0 + gain[index] / loss[index])
    return result


def _sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = np.nan if column == "volume" else 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)

rsi = _rsi(close, rsi_length)
signal = _sma(rsi, signal_length)
bull_trigger = np.asarray([
    index > 0 and np.all(np.isfinite([rsi[index], rsi[index - 1], signal[index], signal[index - 1]]))
    and rsi[index] > signal[index] and rsi[index - 1] <= signal[index - 1] and rsi[index] < 50.0 for index in range(n)
], dtype=bool)
bear_trigger = np.asarray([
    index > 0 and np.all(np.isfinite([rsi[index], rsi[index - 1], signal[index], signal[index - 1]]))
    and rsi[index] < signal[index] and rsi[index - 1] >= signal[index - 1] and rsi[index] > 50.0 for index in range(n)
], dtype=bool)

created_support = np.zeros(n, dtype=bool)
created_resistance = np.zeros(n, dtype=bool)
broken_support = np.zeros(n, dtype=bool)
broken_resistance = np.zeros(n, dtype=bool)
created_support_price = np.full(n, np.nan, dtype=float)
created_resistance_price = np.full(n, np.nan, dtype=float)
closest_support = np.full(n, np.nan, dtype=float)
closest_resistance = np.full(n, np.nan, dtype=float)
active_count = np.zeros(n, dtype=int)
active_blocks = []
all_blocks = []
next_identifier = 0


def _overlaps(top_price, bottom_price):
    for item in active_blocks:
        if (item["bottom"] <= top_price <= item["top"]) or (item["bottom"] <= bottom_price <= item["top"]) or (top_price >= item["top"] and bottom_price <= item["bottom"]):
            return True
    return False


for index in range(n):
    if bull_trigger[index]:
        source_index = -1
        for offset in range(0, 11):
            candidate = index - offset
            if candidate >= 0 and close[candidate] < opened[candidate]:
                source_index = candidate
                break
        if source_index >= 0:
            top_price, bottom_price = opened[source_index], low[source_index]
            if not _overlaps(top_price, bottom_price):
                item = {"id": next_identifier, "start": source_index, "created": index, "top": float(top_price), "bottom": float(bottom_price), "bull": True, "rsi": float(rsi[source_index]) if np.isfinite(rsi[source_index]) else None, "broken": -1, "trimmed": False}
                next_identifier += 1
                active_blocks.append(item)
                all_blocks.append(item)
                created_support[index] = True
                created_support_price[index] = bottom_price
    if bear_trigger[index]:
        source_index = -1
        for offset in range(0, 11):
            candidate = index - offset
            if candidate >= 0 and close[candidate] > opened[candidate]:
                source_index = candidate
                break
        if source_index >= 0:
            top_price, bottom_price = high[source_index], opened[source_index]
            if not _overlaps(top_price, bottom_price):
                item = {"id": next_identifier, "start": source_index, "created": index, "top": float(top_price), "bottom": float(bottom_price), "bull": False, "rsi": float(rsi[source_index]) if np.isfinite(rsi[source_index]) else None, "broken": -1, "trimmed": False}
                next_identifier += 1
                active_blocks.append(item)
                all_blocks.append(item)
                created_resistance[index] = True
                created_resistance_price[index] = top_price
    for block_index in range(len(active_blocks) - 1, -1, -1):
        item = active_blocks[block_index]
        broken = close[index] < item["bottom"] if item["bull"] else close[index] > item["top"]
        if broken:
            item["broken"] = index
            if item["bull"]:
                broken_support[index] = True
            else:
                broken_resistance[index] = True
            active_blocks.pop(block_index)
    if index == n - 1 and len(active_blocks) > max_active_levels:
        remove_count = len(active_blocks) - max_active_levels
        for _ in range(remove_count):
            old_item = active_blocks.pop(0)
            old_item["trimmed"] = True
    support_candidates = [item["bottom"] for item in active_blocks if item["bull"] and close[index] - item["bottom"] >= 0.0]
    resistance_candidates = [item["top"] for item in active_blocks if not item["bull"] and item["top"] - close[index] >= 0.0]
    if support_candidates:
        closest_support[index] = max(support_candidates)
    if resistance_candidates:
        closest_resistance[index] = min(resistance_candidates)
    active_count[index] = len(active_blocks)

is_overbought = np.isfinite(rsi) & (rsi >= 70.0)
is_oversold = np.isfinite(rsi) & (rsi <= 30.0)
is_squeeze = np.isfinite(rsi) & (rsi >= 40.0) & (rsi <= 60.0) & show_mid_zone
overbought_entry = is_overbought & ~np.r_[False, is_overbought[:-1]]
oversold_entry = is_oversold & ~np.r_[False, is_oversold[:-1]]
rsi_colors = [bear_color if np.isfinite(value) and value >= 50.0 else bull_color for value in rsi]
overbought_track = np.where(is_overbought, 95.0, np.nan)
oversold_track = np.where(is_oversold, 5.0, np.nan)
squeeze_track = np.where(is_squeeze, 50.0, np.nan)


def _events(mask, prices, text, color, event_type):
    return {"type": event_type, "name": text, "data": [{"active": True, "price": float(prices[index]), "text": text, "color": color} if active and np.isfinite(prices[index]) else None for index, active in enumerate(mask)], "color": color, "renderMode": "events"}


def _limited_event_masks(masks, recent_bars=160, total_cap=16, minimum_gap=4):
    result = [np.zeros(n, dtype=bool) for _ in masks]
    candidates = []
    first_index = max(0, n - recent_bars)
    for mask_index, mask in enumerate(masks):
        for index in np.flatnonzero(np.asarray(mask, dtype=bool)):
            if index >= first_index:
                candidates.append((int(index), mask_index))
    selected_indices = []
    for index, mask_index in sorted(candidates, reverse=True):
        if len(selected_indices) >= total_cap:
            break
        if any(abs(index - selected) < minimum_gap for selected in selected_indices):
            continue
        result[mask_index][index] = True
        selected_indices.append(index)
    return result


display_created_support, display_created_resistance, display_broken_support, display_broken_resistance, display_overbought, display_oversold = _limited_event_masks([
    created_support, created_resistance, broken_support, broken_resistance, overbought_entry, oversold_entry,
])


rows = []
if n:
    rows.append(["Current Close", f"{close[-1]:.2f}", "Pivot Center"])
    if np.isfinite(closest_resistance[-1]):
        distance = closest_resistance[-1] - close[-1]
        rows.append(["Nearest Resistance", f"{closest_resistance[-1]:.2f}", f"{distance:.2f} ({distance / close[-1] * 100.0:.2f}%)"])
    else:
        rows.append(["Nearest Resistance", "None Active", "-"])
    if np.isfinite(closest_support[-1]):
        distance = close[-1] - closest_support[-1]
        rows.append(["Nearest Support", f"{closest_support[-1]:.2f}", f"{distance:.2f} ({distance / close[-1] * 100.0:.2f}%)"])
    else:
        rows.append(["Nearest Support", "None Active", "-"])

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "RSI", "title": True, "data": _finite(rsi), "color": rsi_colors, "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Signal", "title": True, "data": _finite(signal), "color": signal_color, "type": "line", "overlay": False},
        {"name": "Middle", "title": True, "data": [50.0] * n, "color": "#808080", "type": "line", "lineStyle": "dashed", "overlay": False},
        {"name": "Overbought Track", "title": True, "data": _finite(overbought_track), "color": bear_color, "type": "scatter", "overlay": False},
        {"name": "Squeeze Track", "title": show_mid_zone, "data": _finite(squeeze_track), "color": mid_zone_color, "type": "scatter", "overlay": False},
        {"name": "Oversold Track", "title": True, "data": _finite(oversold_track), "color": bull_color, "type": "scatter", "overlay": False},
        {"name": "Nearest Support", "title": True, "data": _finite(closest_support), "color": bull_color, "type": "step", "lineWidth": 2, "overlay": True},
        {"name": "Nearest Resistance", "title": True, "data": _finite(closest_resistance), "color": bear_color, "type": "step", "lineWidth": 2, "overlay": True},
    ],
    "signals": [
        _events(display_created_support, created_support_price, "RSI Support", bull_color, "support"), _events(display_created_resistance, created_resistance_price, "RSI Resistance", bear_color, "resistance"),
        _events(display_broken_support, close, "Support Broken", "#808080", "break"), _events(display_broken_resistance, close, "Resistance Broken", "#808080", "break"),
        _events(display_overbought, high, "OB", bear_color, "overbought"), _events(display_oversold, low, "OS", bull_color, "oversold"),
    ],
    "layers": [],
    "tables": [{"title": "RSI Core Levels", "position": table_position, "compact": True, "columns": [{"text": "Level Type"}, {"text": "Price Value"}, {"text": "Distance"}], "rows": rows}] if show_dashboard and rows else [],
    "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "rsi": _finite(rsi), "signal": _finite(signal), "bullTrigger": bull_trigger.tolist(), "bearTrigger": bear_trigger.tolist(),
        "createdSupport": created_support.tolist(), "createdResistance": created_resistance.tolist(), "brokenSupport": broken_support.tolist(), "brokenResistance": broken_resistance.tolist(),
        "createdSupportPrice": _finite(created_support_price), "createdResistancePrice": _finite(created_resistance_price),
        "closestSupport": _finite(closest_support), "closestResistance": _finite(closest_resistance), "activeCount": [int(value) for value in active_count],
        "isOverbought": is_overbought.tolist(), "isOversold": is_oversold.tolist(), "isSqueeze": is_squeeze.tolist(),
        "blocks": [{"id": int(item["id"]), "start": int(item["start"]), "created": int(item["created"]), "top": float(item["top"]), "bottom": float(item["bottom"]), "bull": bool(item["bull"]), "rsi": item["rsi"], "broken": int(item["broken"]), "trimmed": bool(item["trimmed"])} for item in all_blocks],
        "activeBlockIds": [int(item["id"]) for item in active_blocks],
    },
}
