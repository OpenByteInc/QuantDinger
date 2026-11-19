# ============================================================
# QuantDinger Pivot Consolidation Zone
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Pivot Consolidation Zone"
my_indicator_description = "Builds retested pivot consolidation zones with a price-binned buy/sell volume profile, confirmed breakouts, and managed risk targets."

# @param pivot_length int 10 Symmetric pivot length range=2:30:1
# @param max_pair_bars int 100 Maximum pivot-pair distance range=20:600:1
# @param max_left_back int 250 Maximum zone left-edge search range=20:600:1
# @param use_atr_cap bool true Cap zone height by ATR
# @param atr_length int 100 Zone ATR length range=1:500:1
# @param max_zone_atr float 5.0 Maximum zone height in ATR range=0:100:0.1
# @param min_breakout_atr float 0.0 Minimum breakout size in ATR range=0:20:0.1
# @param show_profile bool true Show zone volume profile
# @param profile_bins int 30 Number of profile bins range=3:40:1
# @param profile_width int 30 Maximum profile width in bars range=5:100:1
# @param include_outer_wicks bool false Assign out-of-zone wick volume to edge bins
# @param show_box_volume_bars bool true Show buy/sell bars below zones
# @param show_box_volume_label bool true Show buy/sell percentage label
# @param show_breakout_signals bool true Show breakout markers
# @param require_volume_confirmation bool false Require dominant volume confirmation
# @param recolor_zone_on_breakout bool true Recolor confirmed breakout zones
# @param show_pivot_markers bool false Show zone-forming pivot markers
# @param show_test_markers bool false Show numbered boundary-test markers
# @param show_targets bool true Show TP/SL levels
# @param use_atr_stop bool true Calculate stop distance from ATR
# @param target_atr_length int 14 Target ATR length range=1:500:1
# @param stop_atr_multiple float 1.5 Stop ATR multiplier range=0:100:0.1
# @param stop_percent float 1.0 Stop percentage range=0:100:0.1
# @param target_1_rr float 1.0 Target 1 risk/reward range=0:100:0.1
# @param target_2_rr float 2.0 Target 2 risk/reward range=0:100:0.1
# @param target_3_rr float 3.0 Target 3 risk/reward range=0:100:0.1
# @param show_stop bool true Show stop level
# @param show_target_1 bool true Show target 1
# @param show_target_2 bool true Show target 2
# @param show_target_3 bool true Show target 3

pivot_length = min(30, max(2, int(params.get("pivot_length", 10))))
max_pair_bars = max(20, int(params.get("max_pair_bars", 100)))
max_left_back = max(20, int(params.get("max_left_back", 250)))
use_atr_cap = bool(params.get("use_atr_cap", True))
atr_length = max(1, int(params.get("atr_length", 100)))
max_zone_atr = max(0.0, float(params.get("max_zone_atr", 5.0)))
min_breakout_atr = max(0.0, float(params.get("min_breakout_atr", 0.0)))
show_profile = bool(params.get("show_profile", True))
profile_bins = min(40, max(3, int(params.get("profile_bins", 30))))
profile_width = min(100, max(5, int(params.get("profile_width", 30))))
include_outer_wicks = bool(params.get("include_outer_wicks", False))
show_box_volume_bars = bool(params.get("show_box_volume_bars", True))
show_box_volume_label = bool(params.get("show_box_volume_label", True))
show_breakout_signals = bool(params.get("show_breakout_signals", True))
require_volume_confirmation = bool(params.get("require_volume_confirmation", False))
recolor_zone_on_breakout = bool(params.get("recolor_zone_on_breakout", True))
show_pivot_markers = bool(params.get("show_pivot_markers", False))
show_test_markers = bool(params.get("show_test_markers", False))
show_targets = bool(params.get("show_targets", True))
use_atr_stop = bool(params.get("use_atr_stop", True))
target_atr_length = max(1, int(params.get("target_atr_length", 14)))
stop_atr_multiple = float(params.get("stop_atr_multiple", 1.5))
stop_percent = float(params.get("stop_percent", 1.0))
target_1_rr = float(params.get("target_1_rr", 1.0))
target_2_rr = float(params.get("target_2_rr", 2.0))
target_3_rr = float(params.get("target_3_rr", 3.0))
show_stop = bool(params.get("show_stop", True))
show_target_1 = bool(params.get("show_target_1", True))
show_target_2 = bool(params.get("show_target_2", True))
show_target_3 = bool(params.get("show_target_3", True))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _atr(high_values, low_values, close_values, length):
    size = len(close_values)
    true_range = np.full(size, np.nan, dtype=float)
    result = np.full(size, np.nan, dtype=float)
    for position in range(size):
        if position == 0:
            true_range[position] = high_values[position] - low_values[position]
        else:
            true_range[position] = max(
                high_values[position] - low_values[position],
                abs(high_values[position] - close_values[position - 1]),
                abs(low_values[position] - close_values[position - 1]),
            )
        if position == length - 1:
            result[position] = float(np.mean(true_range[:length]))
        elif position >= length:
            result[position] = (result[position - 1] * (length - 1) + true_range[position]) / length
    return result


def _add_profile(bull_bins, bear_bins, top, bottom, bar_high, bar_low, bar_close, bar_volume, clip):
    bin_height = (top - bottom) / len(bull_bins)
    if bin_height <= 0.0 or bar_high <= bar_low:
        return
    full_range = bar_high - bar_low
    close_fraction = (bar_close - bar_low) / full_range
    inverse_fraction = (bar_high - bar_close) / full_range
    core_high = min(bar_high, top)
    core_low = max(bar_low, bottom)
    if core_high > core_low:
        for bin_index in range(len(bull_bins)):
            bin_top = top - bin_index * bin_height
            bin_bottom = bin_top - bin_height
            overlap_high = min(core_high, bin_top)
            overlap_low = max(core_low, bin_bottom)
            if overlap_high > overlap_low:
                added_volume = bar_volume * (overlap_high - overlap_low) / full_range
                bull_bins[bin_index] += added_volume * close_fraction
                bear_bins[bin_index] += added_volume * inverse_fraction
    if not clip and bar_high > top:
        overlap = bar_high - max(bar_low, top)
        if overlap > 0.0:
            added_volume = bar_volume * overlap / full_range
            bull_bins[0] += added_volume * close_fraction
            bear_bins[0] += added_volume * inverse_fraction
    if not clip and bar_low < bottom:
        overlap = min(bar_high, bottom) - bar_low
        if overlap > 0.0:
            added_volume = bar_volume * overlap / full_range
            bull_bins[-1] += added_volume * close_fraction
            bear_bins[-1] += added_volume * inverse_fraction


def _rounded(value):
    return int(np.floor(float(value) + 0.5))


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close", "volume"]).reset_index(drop=True)
times = df["time"].to_numpy(dtype=float)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].to_numpy(dtype=float)
n = len(df)

zone_atr = _atr(high, low, close, atr_length)
target_atr = _atr(high, low, close, target_atr_length)
break_size = zone_atr * min_breakout_atr
pivot_high = np.zeros(n, dtype=bool)
pivot_low = np.zeros(n, dtype=bool)
bull_breakout = np.zeros(n, dtype=bool)
bear_breakout = np.zeros(n, dtype=bool)
zone_active_series = np.zeros(n, dtype=bool)
zone_top_series = np.full(n, np.nan, dtype=float)
zone_bottom_series = np.full(n, np.nan, dtype=float)
profile_bull_total = np.zeros(n, dtype=float)
profile_bear_total = np.zeros(n, dtype=float)
candidate_high_count = np.zeros(n, dtype=int)
candidate_low_count = np.zeros(n, dtype=int)
tpsl_direction = np.zeros(n, dtype=int)

high_candidates = []
low_candidates = []
zones = []
marker_layers = []
trades = []
active_zone = None
current_trade = None

for index in range(n):
    threshold = float(break_size[index]) if np.isfinite(break_size[index]) else np.nan
    center = index - pivot_length
    if index >= pivot_length * 2:
        high_window = high[center - pivot_length:center + pivot_length + 1]
        low_window = low[center - pivot_length:center + pivot_length + 1]
        if high[center] == np.max(high_window):
            pivot_high[index] = True
            high_candidates.append({
                "price": float(high[center]), "opposite": float(low[center]), "index": int(center),
                "tests": 0, "inside": False, "testBars": [],
            })
            if len(high_candidates) > 40:
                high_candidates.pop(0)
        if low[center] == np.min(low_window):
            pivot_low[index] = True
            low_candidates.append({
                "price": float(low[center]), "opposite": float(high[center]), "index": int(center),
                "tests": 0, "inside": False, "testBars": [],
            })
            if len(low_candidates) > 40:
                low_candidates.pop(0)

    for candidate_index in range(len(high_candidates) - 1, -1, -1):
        candidate = high_candidates[candidate_index]
        invalidated = np.isfinite(threshold) and close[index] > candidate["price"] + threshold
        if invalidated:
            high_candidates.pop(candidate_index)
        elif low[index] <= candidate["price"] and high[index] >= candidate["opposite"]:
            if not candidate["inside"]:
                candidate["tests"] += 1
                candidate["inside"] = True
                candidate["testBars"].append(int(index))
        else:
            candidate["inside"] = False

    for candidate_index in range(len(low_candidates) - 1, -1, -1):
        candidate = low_candidates[candidate_index]
        invalidated = np.isfinite(threshold) and close[index] < candidate["price"] - threshold
        if invalidated:
            low_candidates.pop(candidate_index)
        elif high[index] >= candidate["price"] and low[index] <= candidate["opposite"]:
            if not candidate["inside"]:
                candidate["tests"] += 1
                candidate["inside"] = True
                candidate["testBars"].append(int(index))
        else:
            candidate["inside"] = False

    candidate_high_count[index] = len(high_candidates)
    candidate_low_count[index] = len(low_candidates)
    selected_high = None
    selected_low = None
    best_start = None
    for high_candidate in high_candidates:
        if high_candidate["tests"] < 1:
            continue
        for low_candidate in low_candidates:
            if low_candidate["tests"] < 1 or high_candidate["price"] <= low_candidate["price"]:
                continue
            start_index = min(high_candidate["index"], low_candidate["index"])
            bars_back = index - start_index
            if bars_back < 0 or bars_back > max_pair_bars:
                continue
            contained = True
            for offset in range(1, bars_back + 1):
                historical_close = close[index - offset]
                if np.isfinite(threshold) and (
                    historical_close > high_candidate["price"] + threshold
                    or historical_close < low_candidate["price"] - threshold
                ):
                    contained = False
            passes_atr = (not use_atr_cap) or (
                np.isfinite(zone_atr[index])
                and high_candidate["price"] - low_candidate["price"] <= max_zone_atr * zone_atr[index]
            )
            if contained and passes_atr and (best_start is None or start_index < best_start):
                best_start = int(start_index)
                selected_high = high_candidate
                selected_low = low_candidate

    if active_zone is None and selected_high is not None and selected_low is not None:
        top = float(selected_high["price"])
        bottom = float(selected_low["price"])
        pivot_offset = index - min(selected_high["index"], selected_low["index"])
        left_offset = int(pivot_offset)
        limit = min(max_left_back, index)
        if pivot_offset < limit:
            for offset in range(pivot_offset + 1, limit + 1):
                historical_close = close[index - offset]
                if np.isfinite(threshold) and (historical_close > top + threshold or historical_close < bottom - threshold):
                    break
                left_offset = int(offset)
        for previous_zone in zones:
            previous_zone["profileVisible"] = False
        active_zone = {
            "startIndex": int(index - left_offset), "formedIndex": int(index), "endIndex": int(index),
            "top": top, "bottom": bottom, "breakIndex": None, "breakDirection": 0,
            "confirmed": False, "colorSide": 0, "profileVisible": True,
            "bullBins": [0.0] * profile_bins, "bearBins": [0.0] * profile_bins,
            "profileEndIndex": int(index), "bullTotal": 0.0, "bearTotal": 0.0,
            "bullBarRight": int(index - left_offset), "bearBarRight": int(index - left_offset),
            "volumeBarTop": None, "volumeBarMid": None, "volumeBarBottom": None,
            "highPivotIndex": int(selected_high["index"]), "lowPivotIndex": int(selected_low["index"]),
            "highTestBars": list(selected_high["testBars"]), "lowTestBars": list(selected_low["testBars"]),
        }
        zones.append(active_zone)
        for offset in range(1, left_offset + 1):
            historical = index - offset
            _add_profile(
                active_zone["bullBins"], active_zone["bearBins"], top, bottom,
                high[historical], low[historical], close[historical], volume[historical], not include_outer_wicks,
            )
        if show_pivot_markers:
            marker_layers.extend([
                {"type": "label", "role": "Upper Pivot", "index": int(selected_high["index"]), "price": top, "text": "▼", "color": "#CF1717", "textColor": "#FFFFFF", "direction": "down", "size": "small"},
                {"type": "label", "role": "Lower Pivot", "index": int(selected_low["index"]), "price": bottom, "text": "▲", "color": "#06A22F", "textColor": "#FFFFFF", "direction": "up", "size": "small"},
            ])
        if show_test_markers:
            for number, test_index in enumerate(selected_high["testBars"], start=1):
                marker_layers.append({"type": "label", "role": "Upper Boundary Test", "index": int(test_index), "price": top, "text": str(number), "color": "rgba(207,23,23,0.60)", "textColor": "#FFFFFF", "size": "small"})
            for number, test_index in enumerate(selected_low["testBars"], start=1):
                marker_layers.append({"type": "label", "role": "Lower Boundary Test", "index": int(test_index), "price": bottom, "text": str(number), "color": "rgba(6,162,47,0.60)", "textColor": "#FFFFFF", "size": "small"})

    breakout_up = False
    breakout_down = False
    if active_zone is not None:
        active_zone["endIndex"] = int(index)
        active_zone["profileEndIndex"] = int(index)
        breakout_up = close[index] > active_zone["top"] + threshold
        breakout_down = close[index] < active_zone["bottom"] - threshold
        is_breakout = bool(breakout_up or breakout_down)
        if not is_breakout:
            _add_profile(
                active_zone["bullBins"], active_zone["bearBins"], active_zone["top"], active_zone["bottom"],
                high[index], low[index], close[index], volume[index], not include_outer_wicks,
            )
        active_zone["bullTotal"] = float(sum(active_zone["bullBins"]))
        active_zone["bearTotal"] = float(sum(active_zone["bearBins"]))
        total_volume = active_zone["bullTotal"] + active_zone["bearTotal"]
        full_width = max((index + 1) - active_zone["startIndex"], 1)
        bull_ratio = active_zone["bullTotal"] / total_volume if total_volume > 0.0 else 0.5
        bear_ratio = active_zone["bearTotal"] / total_volume if total_volume > 0.0 else 0.5
        active_zone["bullBarRight"] = active_zone["startIndex"] + _rounded(full_width * bull_ratio)
        active_zone["bearBarRight"] = active_zone["startIndex"] + _rounded(full_width * bear_ratio)
        if np.isfinite(zone_atr[index]):
            bar_height = zone_atr[index] * 0.18
            gap_height = zone_atr[index] * 0.08
            bull_top = active_zone["bottom"] - gap_height
            bull_bottom = bull_top - bar_height
            bear_top = bull_bottom - gap_height
            bear_bottom = bear_top - bar_height
            active_zone["volumeBarTop"] = float(bull_top)
            active_zone["volumeBarMid"] = float(bull_bottom)
            active_zone["volumeBarBearTop"] = float(bear_top)
            active_zone["volumeBarBottom"] = float(bear_bottom)
        profile_bull_total[index] = active_zone["bullTotal"]
        profile_bear_total[index] = active_zone["bearTotal"]
        if is_breakout:
            bull_dominant = active_zone["bullTotal"] >= active_zone["bearTotal"]
            bear_dominant = active_zone["bearTotal"] > active_zone["bullTotal"]
            valid_up = bool(breakout_up and (not require_volume_confirmation or bull_dominant))
            valid_down = bool(breakout_down and (not require_volume_confirmation or bear_dominant))
            if show_breakout_signals:
                bull_breakout[index] = valid_up
                bear_breakout[index] = valid_down
            active_zone["breakIndex"] = int(index)
            active_zone["breakDirection"] = 1 if breakout_up else -1
            active_zone["confirmed"] = bool(valid_up or valid_down)
            if recolor_zone_on_breakout:
                active_zone["colorSide"] = 1 if valid_up else -1 if valid_down else 0
            active_zone = None

    if show_targets and (bull_breakout[index] or bear_breakout[index]):
        if current_trade is not None:
            current_trade["visible"] = False
        direction = 1 if bull_breakout[index] else -1
        entry = float(close[index])
        risk_distance = (
            float(target_atr[index]) * stop_atr_multiple
            if use_atr_stop and np.isfinite(target_atr[index])
            else entry * stop_percent / 100.0 if not use_atr_stop else np.nan
        )
        stop = entry - direction * risk_distance if np.isfinite(risk_distance) else np.nan
        target_1 = entry + direction * risk_distance * target_1_rr if np.isfinite(risk_distance) else np.nan
        target_2 = entry + direction * risk_distance * target_2_rr if np.isfinite(risk_distance) else np.nan
        target_3 = entry + direction * risk_distance * target_3_rr if np.isfinite(risk_distance) else np.nan
        enabled_targets = []
        if show_target_1 and np.isfinite(target_1):
            enabled_targets.append(target_1)
        if show_target_2 and np.isfinite(target_2):
            enabled_targets.append(target_2)
        if show_target_3 and np.isfinite(target_3):
            enabled_targets.append(target_3)
        current_trade = {
            "direction": int(direction), "entryIndex": int(index), "endIndex": int(index + 1),
            "entry": entry, "stop": float(stop) if np.isfinite(stop) else None,
            "target1": float(target_1) if np.isfinite(target_1) else None,
            "target2": float(target_2) if np.isfinite(target_2) else None,
            "target3": float(target_3) if np.isfinite(target_3) else None,
            "extreme": float(max(enabled_targets) if direction == 1 else min(enabled_targets)) if enabled_targets else None,
            "active": True, "visible": True, "stopHit": False, "targetHit": False,
        }
        trades.append(current_trade)

    if current_trade is not None and current_trade["active"]:
        current_trade["endIndex"] = int(index + 1)
        stop_hit = current_trade["stop"] is not None and (
            low[index] <= current_trade["stop"] if current_trade["direction"] == 1 else high[index] >= current_trade["stop"]
        )
        target_hit = current_trade["extreme"] is not None and (
            high[index] >= current_trade["extreme"] if current_trade["direction"] == 1 else low[index] <= current_trade["extreme"]
        )
        if index > current_trade["entryIndex"] and (stop_hit or target_hit):
            current_trade["endIndex"] = int(index)
            current_trade["active"] = False
            current_trade["stopHit"] = bool(stop_hit)
            current_trade["targetHit"] = bool(target_hit)
    tpsl_direction[index] = current_trade["direction"] if current_trade is not None and current_trade["active"] else 0
    if active_zone is not None:
        zone_active_series[index] = True
        zone_top_series[index] = active_zone["top"]
        zone_bottom_series[index] = active_zone["bottom"]

layers = list(marker_layers)
for zone in zones:
    side = zone["colorSide"]
    zone_color = "#06A22F" if side > 0 else "#CF1717" if side < 0 else "#808080"
    layers.append({
        "type": "zone", "role": "Consolidation Zone", "startIndex": zone["startIndex"], "endIndex": zone["endIndex"],
        "top": zone["top"], "bottom": zone["bottom"], "fillColor": "rgba(6,162,47,0.15)" if side > 0 else "rgba(207,23,23,0.15)" if side < 0 else "rgba(128,128,128,0.15)",
        "borderColor": zone_color, "borderSize": 1,
    })
    if show_box_volume_bars and zone["volumeBarTop"] is not None:
        layers.extend([
            {"type": "zone", "role": "Zone Buy Volume", "startIndex": zone["startIndex"], "endIndex": zone["bullBarRight"], "top": zone["volumeBarTop"], "bottom": zone["volumeBarMid"], "fillColor": "rgba(6,162,47,0.85)", "borderColor": "rgba(6,162,47,0.85)", "borderSize": 1},
            {"type": "zone", "role": "Zone Sell Volume", "startIndex": zone["startIndex"], "endIndex": zone["bearBarRight"], "top": zone["volumeBarBearTop"], "bottom": zone["volumeBarBottom"], "fillColor": "rgba(207,23,23,0.85)", "borderColor": "rgba(207,23,23,0.85)", "borderSize": 1},
        ])
    if show_box_volume_label and zone["volumeBarBottom"] is not None:
        total = zone["bullTotal"] + zone["bearTotal"]
        bull_percentage = np.floor(((zone["bullTotal"] / total * 100.0) if total > 0.0 else 50.0) * 10.0 + 0.5) / 10.0
        label_color = "#06A22F" if bull_percentage > 50.0 else "#CF1717" if bull_percentage < 50.0 else "#808080"
        layers.append({"type": "label", "role": "Zone Volume Split", "index": zone["startIndex"], "price": zone["volumeBarBottom"], "text": f"Buy {bull_percentage:.1f}%\nSell {100.0 - bull_percentage:.1f}%", "color": label_color, "textColor": "#FFFFFF", "size": "small"})

latest_profile_zone = next((zone for zone in reversed(zones) if zone["profileVisible"]), None)
if show_profile and latest_profile_zone is not None:
    totals = [latest_profile_zone["bullBins"][position] + latest_profile_zone["bearBins"][position] for position in range(profile_bins)]
    maximum_total = max(totals) if totals else 0.0
    nominal_height = (latest_profile_zone["top"] - latest_profile_zone["bottom"]) / profile_bins
    gap = nominal_height * 0.10
    bin_height = (latest_profile_zone["top"] - latest_profile_zone["bottom"] - gap * (profile_bins - 1)) / profile_bins
    left_anchor = latest_profile_zone["profileEndIndex"] + 1
    for bin_index in range(profile_bins):
        total = totals[bin_index]
        width = _rounded(profile_width * total / maximum_total) if maximum_total > 0.0 else 0
        bull_width = _rounded(width * latest_profile_zone["bullBins"][bin_index] / total) if total > 0.0 else 0
        bear_width = width - bull_width
        bin_top = latest_profile_zone["top"] - bin_index * (bin_height + gap)
        bin_bottom = bin_top - bin_height
        layers.extend([
            {"type": "zone", "role": "Profile Buy", "startIndex": left_anchor, "endIndex": left_anchor + bull_width, "top": float(bin_top), "bottom": float(bin_bottom), "fillColor": "rgba(6,162,47,0.40)", "borderColor": "rgba(6,162,47,0.40)", "borderSize": 1},
            {"type": "zone", "role": "Profile Sell", "startIndex": left_anchor + bull_width, "endIndex": left_anchor + bull_width + bear_width, "top": float(bin_top), "bottom": float(bin_bottom), "fillColor": "rgba(207,23,23,0.40)", "borderColor": "rgba(207,23,23,0.40)", "borderSize": 1},
        ])

if current_trade is not None and current_trade["visible"]:
    direction = current_trade["direction"]
    trade_color = "#06A22F" if direction == 1 else "#CF1717"
    opposite_color = "#CF1717" if direction == 1 else "#06A22F"
    entry_index = current_trade["entryIndex"]
    end_index = current_trade["endIndex"]
    layers.extend([
        {"type": "line", "role": "Entry", "startIndex": entry_index, "endIndex": end_index, "y1": current_trade["entry"], "y2": current_trade["entry"], "color": trade_color, "lineWidth": 2},
        {"type": "label", "role": "Entry", "index": end_index, "price": current_trade["entry"], "text": f"{'BUY' if direction == 1 else 'SELL'} {current_trade['entry']:.6g}", "color": trade_color, "textColor": "#FFFFFF", "size": "small"},
    ])
    if show_stop and current_trade["stop"] is not None:
        layers.extend([
            {"type": "line", "role": "Stop Loss", "startIndex": entry_index, "endIndex": end_index, "y1": current_trade["stop"], "y2": current_trade["stop"], "color": opposite_color, "lineWidth": 1, "dashed": True},
            {"type": "label", "role": "Stop Loss", "index": end_index, "price": current_trade["stop"], "text": f"SL {current_trade['stop']:.6g}", "color": opposite_color, "textColor": "#FFFFFF", "size": "small"},
            {"type": "zone", "role": "Risk", "startIndex": entry_index, "endIndex": end_index, "top": max(current_trade["entry"], current_trade["stop"]), "bottom": min(current_trade["entry"], current_trade["stop"]), "fillColor": "rgba(207,23,23,0.20)" if direction == 1 else "rgba(6,162,47,0.20)", "borderColor": "rgba(0,0,0,0)", "borderSize": 0},
        ])
    for enabled, key, role in ((show_target_1, "target1", "TP1"), (show_target_2, "target2", "TP2"), (show_target_3, "target3", "TP3")):
        price = current_trade[key]
        if enabled and price is not None:
            layers.extend([
                {"type": "line", "role": role, "startIndex": entry_index, "endIndex": end_index, "y1": price, "y2": price, "color": trade_color, "lineWidth": 1, "dotted": role == "TP3"},
                {"type": "label", "role": role, "index": end_index, "price": price, "text": f"{role} {price:.6g}", "color": trade_color, "textColor": "#FFFFFF", "size": "small"},
            ])
    if current_trade["extreme"] is not None:
        layers.append({"type": "zone", "role": "Reward", "startIndex": entry_index, "endIndex": end_index, "top": max(current_trade["entry"], current_trade["extreme"]), "bottom": min(current_trade["entry"], current_trade["extreme"]), "fillColor": "rgba(6,162,47,0.20)" if direction == 1 else "rgba(207,23,23,0.20)", "borderColor": "rgba(0,0,0,0)", "borderSize": 0})


def _breakout_points(events, bullish):
    result = [None] * n
    for position in np.flatnonzero(events):
        result[position] = {
            "active": True, "price": float(low[position] if bullish else high[position]),
            "text": "▲" if bullish else "▼", "color": "#06A22F" if bullish else "#CF1717",
        }
    return result


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Active Zone Top", "title": False, "data": _finite(zone_top_series), "color": "rgba(128,128,128,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Active Zone Bottom", "title": False, "data": _finite(zone_bottom_series), "color": "rgba(128,128,128,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "name": "Zone Breakout Up", "data": _breakout_points(bull_breakout, True), "color": "#06A22F", "renderMode": "events"},
        {"type": "sell", "name": "Zone Breakout Down", "data": _breakout_points(bear_breakout, False), "color": "#CF1717", "renderMode": "events"},
    ],
    "layers": layers,
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "zoneAtr": _finite(zone_atr), "targetAtr": _finite(target_atr), "breakSize": _finite(break_size),
        "pivotHigh": pivot_high.tolist(), "pivotLow": pivot_low.tolist(),
        "candidateHighCount": candidate_high_count.tolist(), "candidateLowCount": candidate_low_count.tolist(),
        "zoneActive": zone_active_series.tolist(), "zoneTop": _finite(zone_top_series), "zoneBottom": _finite(zone_bottom_series),
        "bullBreakout": bull_breakout.tolist(), "bearBreakout": bear_breakout.tolist(),
        "profileBullTotal": _finite(profile_bull_total), "profileBearTotal": _finite(profile_bear_total),
        "tpslDirection": tpsl_direction.tolist(), "zones": zones, "trades": trades,
        "remainingHighCandidates": high_candidates, "remainingLowCandidates": low_candidates,
    },
}
