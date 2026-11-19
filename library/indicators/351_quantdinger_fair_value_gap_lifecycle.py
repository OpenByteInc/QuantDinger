# ============================================================
# QuantDinger Fair Value Gap Lifecycle
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Fair Value Gap Lifecycle"
my_indicator_description = "Tracks chart and completed higher-timeframe fair value gaps through touch, midpoint, mitigation, inversion, fading, and retention states."
indicator_timeframe_param = "higher_timeframe"
indicator_timeframes = ["15m", "1H", "4H", "1D", "1W", "1M"]

# @param min_size_atr float 0.0 Minimum gap size in ATR range=0:100:0.1
# @param auto_threshold bool false Use running relative-size threshold
# @param require_displacement bool false Require middle-candle displacement
# @param mitigation_source str Wick Mitigation source values=Wick,Close
# @param mitigation_point str Far-edge Mitigation point values=Near-edge,Midpoint,Far-edge
# @param track_inversions bool true Track one inversion before mitigation
# @param enable_higher_timeframe bool true Enable higher timeframe
# @param higher_timeframe str Auto Higher timeframe values=Auto,15m,1H,4H,1D,1W,1M
# @param palette str GBB Color palette values=GBB,Calm-blue-amber,Custom
# @param custom_bull_color str #00E676 Custom bullish color
# @param custom_bear_color str #FF3D71 Custom bearish color
# @param glow_intensity int 100 Glow intensity percent range=0:150:1
# @param spotlight_gaps int 6 Nearest active glowing gaps range=0:20:1
# @param fade_bars int 100 Glow half-life in bars range=1:100000:1
# @param keep_mitigated_bars int 500 Retain mitigated gaps range=0:100000:1
# @param label_mode str HTF-badge Gap labels values=HTF-badge,All,None
# @param show_within_atr float 6.0 Draw distance in ATR range=0:1000:1
# @param show_table bool true Show summary table

min_size_atr = min(100.0, max(0.0, float(params.get("min_size_atr", 0.0))))
auto_threshold = bool(params.get("auto_threshold", False))
require_displacement = bool(params.get("require_displacement", False))
mitigation_source = str(params.get("mitigation_source", "Wick"))
if mitigation_source not in ("Wick", "Close"):
    mitigation_source = "Wick"
mitigation_point = str(params.get("mitigation_point", "Far-edge")).replace("-", " ")
if mitigation_point not in ("Near edge", "Midpoint", "Far edge"):
    mitigation_point = "Far edge"
track_inversions = bool(params.get("track_inversions", True))
enable_higher_timeframe = bool(params.get("enable_higher_timeframe", True))
higher_timeframe = str(params.get("higher_timeframe", "Auto"))
palette = str(params.get("palette", "GBB"))
custom_bull_color = str(params.get("custom_bull_color", "#00E676"))
custom_bear_color = str(params.get("custom_bear_color", "#FF3D71"))
glow_intensity = min(150, max(0, int(params.get("glow_intensity", 100))))
spotlight_gaps = min(20, max(0, int(params.get("spotlight_gaps", 6))))
fade_bars = min(100000, max(1, int(params.get("fade_bars", 100))))
keep_mitigated_bars = min(100000, max(0, int(params.get("keep_mitigated_bars", 500))))
label_mode = str(params.get("label_mode", "HTF-badge")).replace("-", " ")
if label_mode not in ("HTF badge", "All", "None"):
    label_mode = "HTF badge"
show_within_atr = min(1000.0, max(0.0, float(params.get("show_within_atr", 6.0))))
show_table = bool(params.get("show_table", True))

timeframe_seconds = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1H": 3600, "2H": 7200, "4H": 14400, "1D": 86400, "1W": 604800, "1M": 2592000}


def _timeframe_key(value):
    text = str(value or "1H").strip()
    aliases = {"1": "1m", "5": "5m", "15": "15m", "30": "30m", "60": "1H", "120": "2H", "240": "4H", "D": "1D", "W": "1W", "M": "1M"}
    return aliases.get(text, text)


def _auto_higher(chart_key):
    seconds = timeframe_seconds.get(chart_key, 3600)
    if seconds <= 180:
        return "15m"
    if seconds <= 300:
        return "1H"
    if seconds <= 1800:
        return "4H"
    if seconds <= 7200:
        return "1D"
    if seconds <= 86400:
        return "1W"
    return "1M"


def _normalize(frame):
    result = frame.copy().reset_index(drop=True)
    for column in ("time", "open", "high", "low", "close", "volume"):
        if column not in result.columns:
            result[column] = 0.0
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
    times = result["time"].to_numpy(dtype=float)
    if len(times) and np.nanmax(np.abs(times)) > 10_000_000_000:
        result["time"] = times / 1000.0
    return result


def _rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    result[length - 1] = float(np.mean(values[:length]))
    for index in range(length, len(values)):
        result[index] = (result[index - 1] * (length - 1.0) + values[index]) / length
    return result


def _atr(frame, length=14):
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    true_range = np.empty(len(frame), dtype=float)
    for index in range(len(frame)):
        true_range[index] = high[index] - low[index] if index == 0 else max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
    return true_range, _rma(true_range, length)


def _detect(high_one, low_one, close_two, high_three, low_three):
    if low_three > high_one and ((not require_displacement) or close_two > high_one):
        return 1, float(low_three), float(high_one)
    if high_three < low_one and ((not require_displacement) or close_two < low_one):
        return -1, float(low_one), float(high_three)
    return 0, np.nan, np.nan


df = df.copy()
base = _normalize(df)
base_time = base["time"].to_numpy(dtype=float)
base_high = base["high"].to_numpy(dtype=float)
base_low = base["low"].to_numpy(dtype=float)
base_close = base["close"].to_numpy(dtype=float)
n = len(base)
true_range, atr14 = _atr(base)
runtime_ctx = globals().get("ctx")
chart_timeframe = _timeframe_key(getattr(runtime_ctx, "timeframe", None) or "1H")
chart_seconds = timeframe_seconds.get(chart_timeframe, 3600)
resolved_higher = _auto_higher(chart_timeframe) if higher_timeframe == "Auto" else _timeframe_key(higher_timeframe)
higher_valid = enable_higher_timeframe and timeframe_seconds.get(resolved_higher, 0) > chart_seconds
raw_series = getattr(runtime_ctx, "raw_series", None)
higher_raw = raw_series(resolved_higher) if higher_valid and callable(raw_series) else None
higher_available = higher_raw is not None and len(higher_raw) > 0
higher = _normalize(higher_raw) if higher_available else pd.DataFrame()
higher_time = higher["time"].to_numpy(dtype=float) if higher_available else np.asarray([], dtype=float)
higher_high = higher["high"].to_numpy(dtype=float) if higher_available else np.asarray([], dtype=float)
higher_low = higher["low"].to_numpy(dtype=float) if higher_available else np.asarray([], dtype=float)
higher_close = higher["close"].to_numpy(dtype=float) if higher_available else np.asarray([], dtype=float)
_, higher_atr = _atr(higher) if higher_available else (np.asarray([], dtype=float), np.asarray([], dtype=float))

if palette == "Calm-blue-amber":
    bull_color, bear_color = "#2979FF", "#FFB000"
elif palette == "Custom":
    bull_color, bear_color = custom_bull_color, custom_bear_color
else:
    bull_color, bear_color = "#00E676", "#FF3D71"

states = {"fresh": 0, "touched": 1, "midpoint": 2, "mitigated": 3}
event_codes = {"none": 0, "newBull": 1, "newBear": 2, "touch": 3, "midpoint": 4, "mitigated": 5, "inverted": 6}
gaps = []
created_records = []
auto_sum = [0.0, 0.0]
auto_count = [0, 0]
cum_created = 0
cum_mitigated = 0
cum_chart_mitigated = 0
cap_hit = False
record_cap = max(1, 500 - 7 * spotlight_gaps - 2)

new_bull = np.zeros(n, dtype=bool)
new_bear = np.zeros(n, dtype=bool)
new_higher_bull = np.zeros(n, dtype=bool)
new_higher_bear = np.zeros(n, dtype=bool)
touch_event = np.zeros(n, dtype=bool)
midpoint_event = np.zeros(n, dtype=bool)
mitigation_event = np.zeros(n, dtype=bool)
inversion_event = np.zeros(n, dtype=bool)
last_event = np.zeros(n, dtype=int)
bull_count = np.zeros(n, dtype=int)
bear_count = np.zeros(n, dtype=int)
higher_count = np.zeros(n, dtype=int)
far_count = np.zeros(n, dtype=int)
mitigated_count = np.zeros(n, dtype=int)


def _passes(layer, height, atr_value, price):
    if auto_threshold:
        mean = auto_sum[layer] / auto_count[layer] if auto_count[layer] else 0.0
        relative = height / price if price != 0.0 else np.inf
        auto_sum[layer] += relative
        auto_count[layer] += 1
        return relative > mean
    if min_size_atr > 0.0:
        return np.isfinite(atr_value) and height >= min_size_atr * atr_value
    return True


def _distance(gap, price):
    if gap["bottom"] <= price <= gap["top"]:
        return 0.0
    return min(abs(price - gap["top"]), abs(price - gap["bottom"]))


def _add_gap(direction, top, bottom, layer, created_index, left_time, left_index):
    global cap_hit
    if len(gaps) >= record_cap:
        victim = next((index for index, item in enumerate(gaps) if item["state"] == states["mitigated"]), None)
        if victim is None:
            victim = next((index for index, item in enumerate(gaps) if not item.get("spot", False)), None)
            if victim is not None:
                cap_hit = True
        if victim is not None:
            gaps.pop(victim)
    gap = {
        "top": float(top), "bottom": float(bottom), "mid": float((top + bottom) / 2.0), "dir": int(direction), "layer": int(layer),
        "createdBar": int(created_index), "createdTime": float(base_time[created_index]), "leftTime": float(left_time), "leftIndex": int(left_index),
        "state": states["fresh"], "inverted": False, "maxPen": 0.0, "mitBar": -1, "mitTime": None, "spot": False,
    }
    gaps.append(gap)
    created_records.append(gap.copy())


def _update_gap(gap, index, close_value, close_valid):
    direction = gap["dir"]
    wick = base_low[index] if direction > 0 else base_high[index]
    near = gap["top"] if direction > 0 else gap["bottom"]
    far = gap["bottom"] if direction > 0 else gap["top"]
    height = gap["top"] - gap["bottom"]
    reached_near = wick <= near if direction > 0 else wick >= near
    event = event_codes["none"]
    if reached_near:
        penetration = gap["top"] - base_low[index] if direction > 0 else base_high[index] - gap["bottom"]
        gap["maxPen"] = min(height, max(gap["maxPen"], penetration))
        if gap["state"] < states["touched"]:
            gap["state"] = states["touched"]
            event = event_codes["touch"]
        reached_midpoint = wick <= gap["mid"] if direction > 0 else wick >= gap["mid"]
        if gap["state"] < states["midpoint"] and reached_midpoint:
            gap["state"] = states["midpoint"]
            event = event_codes["midpoint"]
    beyond_far = close_valid and (close_value < far if direction > 0 else close_value > far)
    if beyond_far:
        if track_inversions and not gap["inverted"]:
            gap["dir"] = -gap["dir"]
            gap["inverted"] = True
            gap["state"] = states["fresh"]
            gap["maxPen"] = 0.0
            gap["createdBar"] = index
            gap["createdTime"] = float(base_time[index])
            return event_codes["inverted"]
        gap["state"] = states["mitigated"]
        gap["mitBar"] = index
        gap["mitTime"] = float(base_time[index])
        return event_codes["mitigated"]
    point = near if mitigation_point == "Near edge" else gap["mid"] if mitigation_point == "Midpoint" else far
    source_value = wick if mitigation_source == "Wick" else close_value if close_valid else np.nan
    reached_point = np.isfinite(source_value) and (source_value <= point if direction > 0 else source_value >= point)
    if reached_point:
        gap["state"] = states["mitigated"]
        gap["mitBar"] = index
        gap["mitTime"] = float(base_time[index])
        return event_codes["mitigated"]
    return event


higher_cursor = 0
for index in range(n):
    while higher_cursor < len(higher_time) and higher_time[higher_cursor] < base_time[index]:
        higher_cursor += 1
    higher_new = higher_available and higher_cursor < len(higher_time) and abs(higher_time[higher_cursor] - base_time[index]) <= max(1.0, chart_seconds * 0.01)
    completed_higher_index = higher_cursor - 1 if higher_new else -1

    for gap in gaps:
        if gap["state"] == states["mitigated"]:
            continue
        close_valid = gap["layer"] == 0 or higher_new
        close_value = base_close[index] if gap["layer"] == 0 else higher_close[completed_higher_index] if completed_higher_index >= 0 else np.nan
        event = _update_gap(gap, index, close_value, close_valid)
        if gap["layer"] == 0:
            last_event[index] = max(last_event[index], event)
        touch_event[index] |= event == event_codes["touch"]
        midpoint_event[index] |= event == event_codes["midpoint"]
        mitigation_event[index] |= event == event_codes["mitigated"]
        inversion_event[index] |= event == event_codes["inverted"]
        if event == event_codes["mitigated"]:
            cum_mitigated += 1
            if gap["layer"] == 0:
                cum_chart_mitigated += 1

    gaps[:] = [gap for gap in gaps if not (gap["state"] == states["mitigated"] and index - gap["mitBar"] > keep_mitigated_bars)]

    if index >= 2:
        direction, top, bottom = _detect(base_high[index - 2], base_low[index - 2], base_close[index - 1], base_high[index], base_low[index])
        if direction and _passes(0, top - bottom, atr14[index], base_close[index]):
            _add_gap(direction, top, bottom, 0, index, base_time[index - 2], index - 2)
            cum_created += 1
            if direction > 0:
                new_bull[index] = True
                last_event[index] = max(last_event[index], event_codes["newBull"])
            else:
                new_bear[index] = True
                last_event[index] = max(last_event[index], event_codes["newBear"])

    if higher_new and completed_higher_index >= 3:
        newest = completed_higher_index
        direction, top, bottom = _detect(higher_high[newest - 2], higher_low[newest - 2], higher_close[newest - 1], higher_high[newest], higher_low[newest])
        atr_value = higher_atr[newest] if newest < len(higher_atr) else np.nan
        if direction and _passes(1, top - bottom, atr_value, higher_close[newest]):
            left_time = higher_time[newest - 2]
            left_index = int(np.searchsorted(base_time, left_time, side="left"))
            _add_gap(direction, top, bottom, 1, index, left_time, min(max(left_index, 0), max(0, n - 1)))
            cum_created += 1
            new_higher_bull[index] = direction > 0
            new_higher_bear[index] = direction < 0

    visible_active = []
    for gap in gaps:
        gap["visible"] = bool(show_within_atr <= 0.0 or not np.isfinite(atr14[index]) or _distance(gap, base_close[index]) <= show_within_atr * atr14[index])
        gap["spot"] = False
        if gap["state"] != states["mitigated"] and gap["visible"]:
            visible_active.append(gap)
    for gap in sorted(visible_active, key=lambda item: (_distance(item, base_close[index]), gaps.index(item)))[:spotlight_gaps]:
        gap["spot"] = True

    bull_count[index] = sum(gap["state"] != states["mitigated"] and gap["layer"] == 0 and gap["dir"] > 0 for gap in gaps)
    bear_count[index] = sum(gap["state"] != states["mitigated"] and gap["layer"] == 0 and gap["dir"] < 0 for gap in gaps)
    higher_count[index] = sum(gap["state"] != states["mitigated"] and gap["layer"] == 1 for gap in gaps)
    far_count[index] = sum(gap["state"] != states["mitigated"] and not gap.get("visible", True) for gap in gaps)
    mitigated_count[index] = cum_chart_mitigated


def _opacity(gap):
    freshness = max(0.5, 1.0 - 0.5 * max(0, n - 1 - gap["createdBar"]) / fade_bars)
    multiplier = (0.8 if gap["layer"] == 1 else 1.0) * (0.7 if gap["inverted"] else 1.0)
    transparency = max(0.0, min(100.0, 100.0 - 7.0 * glow_intensity / 100.0 * freshness * multiplier))
    return (100.0 - transparency) / 100.0


layers = []
for gap in gaps[-record_cap:]:
    if not gap.get("visible", True):
        continue
    color = bull_color if gap["dir"] > 0 else bear_color
    end_index = gap["mitBar"] if gap["state"] == states["mitigated"] else max(0, n - 1)
    if gap["state"] == states["mitigated"]:
        layers.append({"type": "zone", "role": "Mitigated FVG", "startIndex": gap["leftIndex"], "endIndex": end_index, "top": gap["top"], "bottom": gap["bottom"], "fillColor": "rgba(120,123,134,0.02)", "borderColor": "rgba(120,123,134,0.15)", "dashed": True})
        continue
    remainder_top = gap["top"] - gap["maxPen"] if gap["dir"] > 0 else gap["top"]
    remainder_bottom = gap["bottom"] if gap["dir"] > 0 else gap["bottom"] + gap["maxPen"]
    opacity = _opacity(gap) if gap.get("spot", False) else 0.04
    layers.append({"type": "zone", "role": "HTF FVG" if gap["layer"] else "FVG", "startIndex": gap["leftIndex"], "endIndex": end_index, "top": max(remainder_top, remainder_bottom), "bottom": min(remainder_top, remainder_bottom), "fillColor": color, "opacity": opacity, "borderColor": color, "dashed": gap["inverted"] or gap["layer"] == 1})
    if gap.get("spot", False):
        if gap["maxPen"] > 0:
            consumed_top = gap["top"] if gap["dir"] > 0 else gap["bottom"] + gap["maxPen"]
            consumed_bottom = gap["top"] - gap["maxPen"] if gap["dir"] > 0 else gap["bottom"]
            layers.append({"type": "zone", "role": "Consumed FVG", "startIndex": gap["leftIndex"], "endIndex": end_index, "top": max(consumed_top, consumed_bottom), "bottom": min(consumed_top, consumed_bottom), "fillColor": "rgba(0,0,0,0)", "borderColor": color, "dashed": True})
        if min(remainder_top, remainder_bottom) <= gap["mid"] <= max(remainder_top, remainder_bottom):
            layers.append({"type": "line", "role": "FVG Midpoint", "startIndex": gap["leftIndex"], "endIndex": end_index, "y1": gap["mid"], "y2": gap["mid"], "color": color, "dotted": True, "lineWidth": 1})
        label = "IFVG" if gap["inverted"] else resolved_higher if gap["layer"] == 1 else "FVG" if label_mode == "All" else ""
        if label_mode != "None" and label:
            layers.append({"type": "text", "role": "FVG Badge", "index": end_index, "price": gap["mid"], "text": f"{label} · {gap['mid']:.6g}", "color": color})


def _events(mask, prices, text, color, event_type):
    data = []
    for index, active in enumerate(mask):
        data.append({"active": True, "price": float(prices[index]), "text": text, "color": color} if active else None)
    return {"type": event_type, "name": text, "data": data, "color": color, "renderMode": "events"}


def _limited_event_masks(masks, recent_bars=120, total_cap=14, minimum_gap=5):
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


tables = []
if show_table:
    active = [gap for gap in gaps if gap["state"] != states["mitigated"]]
    above = [gap["bottom"] for gap in active if gap["bottom"] > base_close[-1]] if n else []
    below = [gap["top"] for gap in active if gap["top"] < base_close[-1]] if n else []
    warning = "Gap record budget reached" if cap_hit else "HTF data unavailable" if enable_higher_timeframe and higher_valid and not higher_available else ""
    fill_rate = 100.0 * cum_mitigated / cum_created if cum_created else None
    tables = [{
        "title": "Fair Value Gaps", "position": "top-right", "compact": True,
        "columns": [{"text": "Metric"}, {"text": "Value"}],
        "rows": [
            [{"text": "Active"}, {"text": f"{bull_count[-1] if n else 0} bull · {bear_count[-1] if n else 0} bear · {far_count[-1] if n else 0} far"}],
            [{"text": "Higher TF"}, {"text": f"{resolved_higher} · {higher_count[-1] if n else 0} active" if higher_valid else "Off"}],
            [{"text": "Above"}, {"text": "—" if not above else f"{min(above):.6g}"}],
            [{"text": "Below"}, {"text": "—" if not below else f"{max(below):.6g}"}],
            [{"text": "Fill rate"}, {"text": "—" if fill_rate is None else f"{fill_rate:.0f}%"}],
            [{"text": "Warning"}, {"text": warning}],
        ],
    }]

display_new_bull, display_new_bear, display_inversion, display_mitigation = _limited_event_masks(
    [new_bull, new_bear, inversion_event, mitigation_event]
)

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [{"name": "Price Guide", "title": False, "data": [float(value) for value in base_close], "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True}],
    "signals": [
        _events(display_new_bull, base_low, "New Bull FVG", bull_color, "buy"),
        _events(display_new_bear, base_high, "New Bear FVG", bear_color, "sell"),
        _events(display_inversion, base_close, "FVG Inversion", "#FFB000", "neutral"),
        _events(display_mitigation, base_close, "FVG Mitigated", "#787B86", "neutral"),
    ],
    "layers": layers,
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "trueRange": [float(value) for value in true_range],
        "atr14": [float(value) if np.isfinite(value) else None for value in atr14],
        "newBull": new_bull.tolist(), "newBear": new_bear.tolist(), "newHigherBull": new_higher_bull.tolist(), "newHigherBear": new_higher_bear.tolist(),
        "touch": touch_event.tolist(), "midpointTap": midpoint_event.tolist(), "mitigated": mitigation_event.tolist(), "inverted": inversion_event.tolist(),
        "lastEvent": [int(value) for value in last_event], "bullActive": [int(value) for value in bull_count], "bearActive": [int(value) for value in bear_count],
        "higherActive": [int(value) for value in higher_count], "farActive": [int(value) for value in far_count], "chartMitigated": [int(value) for value in mitigated_count],
        "resolvedHigherTimeframe": resolved_higher, "higherTimeframeValid": bool(higher_valid), "higherTimeframeAvailable": bool(higher_available),
        "cumulativeCreated": int(cum_created), "cumulativeMitigated": int(cum_mitigated), "capHit": bool(cap_hit), "records": gaps,
    },
}
