# ============================================================
# QuantDinger Pivot Consolidation Zone
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Pivot Consolidation Zone"
my_indicator_description = (
    "Pairs tested swing pivots into consolidation zones, builds an internal buy/sell "
    "volume profile, confirms breakouts, and maintains a live risk/reward map."
)

# @param pivot_length int 10 Pivot length range=2:30:1
# @param max_pair_bars int 100 Maximum pivot pair distance range=20:500:10
# @param max_left_back int 250 Maximum zone left extension range=20:1000:10
# @param max_history_zones int 8 Maximum visible historical zones range=1:50:1
# @param use_atr_cap bool true Cap zone height by ATR
# @param atr_length int 100 Zone ATR length range=1:500:1
# @param max_zone_atr float 5.0 Maximum zone height in ATR range=0.0:20.0:0.5
# @param minimum_breakout_atr float 0.0 Minimum breakout size in ATR range=0.0:5.0:0.1
# @param show_profile bool true Show zone volume profile
# @param profile_bins int 30 Profile bins range=3:40:1
# @param profile_width int 30 Maximum profile width range=5:100:1
# @param include_outer_wicks bool false Include outer wick volume
# @param show_volume_bars bool true Show buy and sell volume bars
# @param show_volume_label bool true Show buy and sell percentage label
# @param volume_label_size str Small Volume label size values=Tiny,Small,Normal
# @param show_breakout_signals bool true Show breakout signals
# @param require_volume_confirmation bool false Require dominant volume confirmation
# @param recolor_zone_on_breakout bool true Recolor zone after breakout
# @param signal_size str Small Signal size values=Tiny,Small,Normal
# @param show_pivot_markers bool false Show pivot markers
# @param show_test_markers bool false Show boundary test markers
# @param marker_size str Small Marker size values=Tiny,Small,Normal
# @param bullish_color str #06A22F Bullish color
# @param bearish_color str #CF1717 Bearish color
# @param neutral_color str #808080 Neutral color
# @param zone_transparency int 85 Zone transparency range=0:100:1
# @param border_transparency int 0 Border transparency range=0:100:1
# @param profile_transparency int 60 Profile transparency range=0:100:1
# @param show_targets bool true Show risk and reward targets
# @param use_atr_stop bool true Use ATR stop distance
# @param target_atr_length int 14 Target ATR length range=1:500:1
# @param stop_atr_multiplier float 1.5 Stop ATR multiplier range=0.1:10.0:0.1
# @param stop_percent float 1.0 Stop percent range=0.1:20.0:0.1
# @param target_one_rr float 1.0 First target risk reward range=0.1:20.0:0.1
# @param target_two_rr float 2.0 Second target risk reward range=0.1:20.0:0.1
# @param target_three_rr float 3.0 Third target risk reward range=0.1:20.0:0.1
# @param show_stop bool true Show stop level
# @param show_target_one bool true Show first target
# @param show_target_two bool true Show second target
# @param show_target_three bool true Show third target

def bound_int(name, default, lower, upper):
    return min(upper, max(lower, int(params.get(name, default))))

def bound_float(name, default, lower, upper):
    return min(upper, max(lower, float(params.get(name, default))))

pivot_length = bound_int("pivot_length", 10, 2, 30)
max_pair_bars = bound_int("max_pair_bars", 100, 20, 500)
max_left_back = bound_int("max_left_back", 250, 20, 1000)
max_history_zones = bound_int("max_history_zones", 8, 1, 50)
use_atr_cap = bool(params.get("use_atr_cap", True))
atr_length = bound_int("atr_length", 100, 1, 500)
max_zone_atr = bound_float("max_zone_atr", 5.0, 0.0, 20.0)
minimum_breakout_atr = bound_float("minimum_breakout_atr", 0.0, 0.0, 5.0)
show_profile = bool(params.get("show_profile", True))
profile_bins = bound_int("profile_bins", 30, 3, 40)
profile_width = bound_int("profile_width", 30, 5, 100)
include_outer_wicks = bool(params.get("include_outer_wicks", False))
show_volume_bars = bool(params.get("show_volume_bars", True))
show_volume_label = bool(params.get("show_volume_label", True))
volume_label_size = str(params.get("volume_label_size", "Small"))
show_breakout_signals = bool(params.get("show_breakout_signals", True))
require_volume_confirmation = bool(params.get("require_volume_confirmation", False))
recolor_zone_on_breakout = bool(params.get("recolor_zone_on_breakout", True))
signal_size = str(params.get("signal_size", "Small"))
show_pivot_markers = bool(params.get("show_pivot_markers", False))
show_test_markers = bool(params.get("show_test_markers", False))
marker_size = str(params.get("marker_size", "Small"))
bullish_color = str(params.get("bullish_color", "#06A22F"))
bearish_color = str(params.get("bearish_color", "#CF1717"))
neutral_color = str(params.get("neutral_color", "#808080"))
zone_transparency = bound_int("zone_transparency", 85, 0, 100)
border_transparency = bound_int("border_transparency", 0, 0, 100)
profile_transparency = bound_int("profile_transparency", 60, 0, 100)
show_targets = bool(params.get("show_targets", True))
use_atr_stop = bool(params.get("use_atr_stop", True))
target_atr_length = bound_int("target_atr_length", 14, 1, 500)
stop_atr_multiplier = bound_float("stop_atr_multiplier", 1.5, 0.1, 10.0)
stop_percent = bound_float("stop_percent", 1.0, 0.1, 20.0)
target_one_rr = bound_float("target_one_rr", 1.0, 0.1, 20.0)
target_two_rr = bound_float("target_two_rr", 2.0, 0.1, 20.0)
target_three_rr = bound_float("target_three_rr", 3.0, 0.1, 20.0)
show_stop = bool(params.get("show_stop", True))
show_target_one = bool(params.get("show_target_one", True))
show_target_two = bool(params.get("show_target_two", True))
show_target_three = bool(params.get("show_target_three", True))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)
epsilon = 1e-10

def color_with_opacity(color, opacity):
    value = str(color).strip()
    alpha = min(1.0, max(0.0, float(opacity)))
    if value.startswith("#") and len(value) in (4, 7):
        if len(value) == 4:
            red, green, blue = (int(value[position] * 2, 16) for position in range(1, 4))
        else:
            red, green, blue = int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
        return f"rgba({red},{green},{blue},{alpha:.3f})"
    if value.lower().startswith("rgba("):
        parts = value[value.find("(") + 1:value.rfind(")")].split(",")
        if len(parts) >= 3:
            return f"rgba({parts[0].strip()},{parts[1].strip()},{parts[2].strip()},{alpha:.3f})"
    return value

def rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    result[length - 1] = float(np.mean(values[:length]))
    for index in range(length, len(values)):
        result[index] = (result[index - 1] * (length - 1) + float(values[index])) / length
    return result

true_range = np.zeros(n, dtype=float)
for index in range(n):
    true_range[index] = high[index] - low[index] if index == 0 else max(
        high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1])
    )
zone_atr = rma(true_range, atr_length)
target_atr = rma(true_range, target_atr_length)

def pivot_at(values, confirmation_index, length, high_pivot):
    origin = confirmation_index - length
    if origin < length:
        return None
    window = values[origin - length:confirmation_index + 1]
    if len(window) != length * 2 + 1 or not np.all(np.isfinite(window)):
        return None
    candidate = float(values[origin])
    extreme = float(np.max(window) if high_pivot else np.min(window))
    return (origin, candidate) if candidate == extreme else None

def add_bar_to_profile(zone, bar_index):
    top = zone["top"]
    bottom = zone["bottom"]
    height = (top - bottom) / profile_bins
    bar_high = float(high[bar_index])
    bar_low = float(low[bar_index])
    if height <= 0.0 or bar_high <= bar_low:
        return
    full_range = bar_high - bar_low
    core_high = min(bar_high, top)
    core_low = max(bar_low, bottom)
    if core_high > core_low:
        for bin_index in range(profile_bins):
            bin_top = top - bin_index * height
            bin_bottom = bin_top - height
            overlap_high = min(core_high, bin_top)
            overlap_low = max(core_low, bin_bottom)
            if overlap_high <= overlap_low:
                continue
            added = volume[bar_index] * (overlap_high - overlap_low) / full_range
            zone["bull_bins"][bin_index] += added * (close[bar_index] - bar_low) / full_range
            zone["bear_bins"][bin_index] += added * (bar_high - close[bar_index]) / full_range
    if include_outer_wicks:
        if bar_high > top:
            overlap_above = bar_high - max(bar_low, top)
            if overlap_above > 0.0:
                added = volume[bar_index] * overlap_above / full_range
                zone["bull_bins"][0] += added * (close[bar_index] - bar_low) / full_range
                zone["bear_bins"][0] += added * (bar_high - close[bar_index]) / full_range
        if bar_low < bottom:
            overlap_below = min(bar_high, bottom) - bar_low
            if overlap_below > 0.0:
                added = volume[bar_index] * overlap_below / full_range
                zone["bull_bins"][-1] += added * (close[bar_index] - bar_low) / full_range
                zone["bear_bins"][-1] += added * (bar_high - close[bar_index]) / full_range

high_pivots = []
low_pivots = []
zones = []
active_zone = None
marker_events = []
buy_signals = [None for _ in range(n)]
sell_signals = [None for _ in range(n)]
trade = None

for index in range(n):
    current_atr = float(zone_atr[index]) if np.isfinite(zone_atr[index]) else max(float(true_range[index]), epsilon)
    breakout_size = minimum_breakout_atr * current_atr
    high_pivot = pivot_at(high, index, pivot_length, True)
    low_pivot = pivot_at(low, index, pivot_length, False)
    if high_pivot is not None:
        origin, price = high_pivot
        high_pivots.append({"price": price, "opposite": float(low[origin]), "index": origin, "tests": 0, "inside": False, "test_bars": []})
        high_pivots = high_pivots[-40:]
    if low_pivot is not None:
        origin, price = low_pivot
        low_pivots.append({"price": price, "opposite": float(high[origin]), "index": origin, "tests": 0, "inside": False, "test_bars": []})
        low_pivots = low_pivots[-40:]

    for pivot in list(reversed(high_pivots)):
        if close[index] > pivot["price"] + breakout_size:
            high_pivots.remove(pivot)
        elif high[index] >= pivot["price"] and close[index] <= pivot["price"] + breakout_size:
            if not pivot["inside"]:
                pivot["tests"] += 1
                pivot["inside"] = True
                pivot["test_bars"].append(index)
        else:
            pivot["inside"] = False
    for pivot in list(reversed(low_pivots)):
        if close[index] < pivot["price"] - breakout_size:
            low_pivots.remove(pivot)
        elif low[index] <= pivot["price"] and close[index] >= pivot["price"] - breakout_size:
            if not pivot["inside"]:
                pivot["tests"] += 1
                pivot["inside"] = True
                pivot["test_bars"].append(index)
        else:
            pivot["inside"] = False

    best_pair = None
    for high_item in high_pivots:
        if high_item["tests"] < 1:
            continue
        for low_item in low_pivots:
            if low_item["tests"] < 1 or high_item["price"] <= low_item["price"]:
                continue
            start = min(high_item["index"], low_item["index"])
            bars_back = index - start
            if bars_back < 0 or bars_back > max_pair_bars:
                continue
            contained = True
            for check_index in range(start + 1, index):
                if close[check_index] > high_item["price"] + breakout_size or close[check_index] < low_item["price"] - breakout_size:
                    contained = False
                    break
            passes_atr = not use_atr_cap or high_item["price"] - low_item["price"] <= max_zone_atr * current_atr
            if contained and passes_atr and (best_pair is None or start > best_pair[0]):
                best_pair = (start, high_item, low_item)

    if active_zone is None and best_pair is not None:
        _, high_item, low_item = best_pair
        top = float(high_item["price"])
        bottom = float(low_item["price"])
        pivot_start = min(high_item["index"], low_item["index"])
        left_index = pivot_start
        for check_index in range(pivot_start - 1, max(-1, pivot_start - max_left_back - 1), -1):
            if close[check_index] > top + breakout_size or close[check_index] < bottom - breakout_size:
                break
            left_index = check_index
        for previous_zone in zones:
            previous_zone["profile_visible"] = False
        active_zone = {
            "top": top,
            "bottom": bottom,
            "left": left_index,
            "end": index,
            "color": neutral_color,
            "bull_bins": np.zeros(profile_bins, dtype=float),
            "bear_bins": np.zeros(profile_bins, dtype=float),
            "profile_visible": True,
            "high_pivot": dict(high_item),
            "low_pivot": dict(low_item),
        }
        for historical_index in range(left_index, index):
            add_bar_to_profile(active_zone, historical_index)
        zones.append(active_zone)
        if show_pivot_markers:
            marker_events.extend(
                [
                    {"index": high_item["index"], "price": top, "text": "v", "color": bearish_color, "side": "down"},
                    {"index": low_item["index"], "price": bottom, "text": "^", "color": bullish_color, "side": "up"},
                ]
            )
        if show_test_markers:
            for number, test_index in enumerate(high_item["test_bars"], 1):
                marker_events.append({"index": test_index, "price": top, "text": str(number), "color": bearish_color, "side": "center"})
            for number, test_index in enumerate(low_item["test_bars"], 1):
                marker_events.append({"index": test_index, "price": bottom, "text": str(number), "color": bullish_color, "side": "center"})

    signal_direction = 0
    if active_zone is not None:
        active_zone["end"] = index
        breakout_up = close[index] > active_zone["top"] + breakout_size
        breakout_down = close[index] < active_zone["bottom"] - breakout_size
        if not breakout_up and not breakout_down:
            add_bar_to_profile(active_zone, index)
        if breakout_up or breakout_down:
            total_bull = float(np.sum(active_zone["bull_bins"]))
            total_bear = float(np.sum(active_zone["bear_bins"]))
            valid_up = breakout_up and (not require_volume_confirmation or total_bull >= total_bear)
            valid_down = breakout_down and (not require_volume_confirmation or total_bear > total_bull)
            if valid_up:
                signal_direction = 1
                if recolor_zone_on_breakout:
                    active_zone["color"] = bullish_color
            elif valid_down:
                signal_direction = -1
                if recolor_zone_on_breakout:
                    active_zone["color"] = bearish_color
            if show_breakout_signals and signal_direction == 1:
                buy_signals[index] = {"value": float(low[index]), "text": "BUY", "color": bullish_color}
            elif show_breakout_signals and signal_direction == -1:
                sell_signals[index] = {"value": float(high[index]), "text": "SELL", "color": bearish_color}
            active_zone = None

    if show_targets and signal_direction != 0:
        entry = float(close[index])
        target_atr_value = float(target_atr[index]) if np.isfinite(target_atr[index]) else current_atr
        risk = target_atr_value * stop_atr_multiplier if use_atr_stop else entry * stop_percent / 100.0
        stop = entry - risk if signal_direction == 1 else entry + risk
        target_one = entry + risk * target_one_rr if signal_direction == 1 else entry - risk * target_one_rr
        target_two = entry + risk * target_two_rr if signal_direction == 1 else entry - risk * target_two_rr
        target_three = entry + risk * target_three_rr if signal_direction == 1 else entry - risk * target_three_rr
        enabled_targets = [value for enabled, value in ((show_target_one, target_one), (show_target_two, target_two), (show_target_three, target_three)) if enabled]
        trade = {
            "direction": signal_direction,
            "start": index,
            "end": index + 1,
            "entry": entry,
            "stop": stop,
            "target_one": target_one,
            "target_two": target_two,
            "target_three": target_three,
            "extreme": enabled_targets[-1] if enabled_targets else None,
            "active": True,
        }

    if trade is not None and trade["active"]:
        trade["end"] = index + 1
        stop_hit = low[index] <= trade["stop"] if trade["direction"] == 1 else high[index] >= trade["stop"]
        target_hit = trade["extreme"] is not None and (high[index] >= trade["extreme"] if trade["direction"] == 1 else low[index] <= trade["extreme"])
        if index > trade["start"] and (stop_hit or target_hit):
            trade["end"] = index
            trade["active"] = False

layers = []
label_sizes = {"Tiny": 9, "Small": 10, "Normal": 12}
visible_zones = zones[-max_history_zones:]
volume_label_keys = {(zone["left"], zone["end"]) for zone in visible_zones[-4:]}
for zone in visible_zones:
    total_bull = float(np.sum(zone["bull_bins"]))
    total_bear = float(np.sum(zone["bear_bins"]))
    total_volume = total_bull + total_bear
    layers.append(
        {
            "type": "zone",
            "startIndex": int(zone["left"]),
            "endIndex": int(zone["end"]),
            "top": float(zone["top"]),
            "bottom": float(zone["bottom"]),
            "fillColor": zone["color"],
            "opacity": (100 - zone_transparency) / 100.0,
            "borderColor": color_with_opacity(zone["color"], (100 - border_transparency) / 100.0),
            "borderSize": 1,
            "extendRight": False,
        }
    )
    atr_index = min(max(zone["end"], 0), n - 1)
    local_atr = float(zone_atr[atr_index]) if np.isfinite(zone_atr[atr_index]) else max(float(true_range[atr_index]), epsilon)
    gap = local_atr * 0.08
    bar_height = local_atr * 0.18
    bull_top = zone["bottom"] - gap
    bull_bottom = bull_top - bar_height
    bear_top = bull_bottom - gap
    bear_bottom = bear_top - bar_height
    width = max(zone["end"] + 1 - zone["left"], 1)
    if show_volume_bars:
        bull_width = round(width * (total_bull / total_volume)) if total_volume > 0.0 else round(width * 0.5)
        bear_width = round(width * (total_bear / total_volume)) if total_volume > 0.0 else round(width * 0.5)
        if bull_width > 0:
            layers.append({"type": "zone", "startIndex": int(zone["left"]), "endIndex": int(min(zone["end"], zone["left"] + bull_width - 1)), "top": float(bull_top), "bottom": float(bull_bottom), "fillColor": bullish_color, "opacity": 0.85, "borderColor": bullish_color, "borderSize": 1, "extendRight": False})
        if bear_width > 0:
            layers.append({"type": "zone", "startIndex": int(zone["left"]), "endIndex": int(min(zone["end"], zone["left"] + bear_width - 1)), "top": float(bear_top), "bottom": float(bear_bottom), "fillColor": bearish_color, "opacity": 0.85, "borderColor": bearish_color, "borderSize": 1, "extendRight": False})
    if show_volume_label and (zone["left"], zone["end"]) in volume_label_keys:
        bull_percent = round((total_bull / total_volume * 100.0) if total_volume > 0.0 else 50.0, 1)
        background = bullish_color if bull_percent > 50.0 else bearish_color if bull_percent < 50.0 else neutral_color
        layers.append({"type": "label", "index": int(zone["left"]), "price": float(bear_bottom - gap * 0.5), "text": f"Buy {bull_percent:.1f}%\nSell {100.0 - bull_percent:.1f}%", "color": background, "textColor": "#FFFFFF", "fontSize": label_sizes.get(volume_label_size, 10), "side": "up"})
    if show_profile and zone["profile_visible"]:
        totals = zone["bull_bins"] + zone["bear_bins"]
        maximum = float(np.max(totals)) if len(totals) else 0.0
        nominal = (zone["top"] - zone["bottom"]) / profile_bins
        bin_gap = nominal * 0.10
        bin_height = (zone["top"] - zone["bottom"] - bin_gap * (profile_bins - 1)) / profile_bins
        profile_right = int(min(n - 1, zone["end"]))
        available_width = max(1, min(profile_width, profile_right - int(zone["left"]) + 1))
        for bin_index in range(profile_bins):
            total = float(totals[bin_index])
            bin_width = round(available_width * total / maximum) if maximum > 0.0 else 0
            bull_width = round(bin_width * zone["bull_bins"][bin_index] / total) if total > 0.0 else 0
            bear_width = bin_width - bull_width
            bin_top = zone["top"] - bin_index * (bin_height + bin_gap)
            bin_bottom = bin_top - bin_height
            profile_left = max(int(zone["left"]), profile_right - bin_width + 1)
            if bull_width > 0:
                layers.append({"type": "zone", "startIndex": profile_left, "endIndex": int(profile_left + bull_width - 1), "top": float(bin_top), "bottom": float(bin_bottom), "fillColor": bullish_color, "opacity": (100 - profile_transparency) / 100.0, "borderColor": bullish_color, "borderSize": 1, "extendRight": False})
            if bear_width > 0:
                bear_start = profile_left + bull_width
                layers.append({"type": "zone", "startIndex": int(bear_start), "endIndex": int(bear_start + bear_width - 1), "top": float(bin_top), "bottom": float(bin_bottom), "fillColor": bearish_color, "opacity": (100 - profile_transparency) / 100.0, "borderColor": bearish_color, "borderSize": 1, "extendRight": False})

for event in marker_events:
    layers.append({"type": "label", "index": int(event["index"]), "price": float(event["price"]), "text": event["text"], "color": event["color"], "textColor": "#FFFFFF", "fontSize": label_sizes.get(marker_size, 10), "side": event["side"]})

if trade is not None:
    direction_color = bullish_color if trade["direction"] == 1 else bearish_color
    stop_color = bearish_color if trade["direction"] == 1 else bullish_color
    end = int(min(n - 1, trade["end"]))
    def add_level(name, price, color, width=1, dashed=False):
        layers.append({"type": "line", "startIndex": int(trade["start"]), "endIndex": end, "y1": float(price), "y2": float(price), "color": color, "lineWidth": width, "dashed": dashed})
        layers.append({"type": "label", "index": end, "price": float(price), "text": f"{name} {price:.6g}", "color": color, "textColor": "#FFFFFF", "side": "center"})
    add_level("BUY" if trade["direction"] == 1 else "SELL", trade["entry"], direction_color, 2)
    if show_stop:
        add_level("SL", trade["stop"], stop_color, 1, True)
        layers.append({"type": "zone", "startIndex": int(trade["start"]), "endIndex": end, "top": float(max(trade["entry"], trade["stop"])), "bottom": float(min(trade["entry"], trade["stop"])), "fillColor": stop_color, "opacity": 0.20, "borderColor": "rgba(0,0,0,0)", "borderSize": 0, "extendRight": False})
    target_values = []
    if show_target_one:
        add_level("TP1", trade["target_one"], direction_color)
        target_values.append(trade["target_one"])
    if show_target_two:
        add_level("TP2", trade["target_two"], direction_color)
        target_values.append(trade["target_two"])
    if show_target_three:
        add_level("TP3", trade["target_three"], direction_color, 1, True)
        target_values.append(trade["target_three"])
    if target_values:
        extreme = max(target_values) if trade["direction"] == 1 else min(target_values)
        layers.append({"type": "zone", "startIndex": int(trade["start"]), "endIndex": end, "top": float(max(trade["entry"], extreme)), "bottom": float(min(trade["entry"], extreme)), "fillColor": direction_color, "opacity": 0.20, "borderColor": "rgba(0,0,0,0)", "borderSize": 0, "extendRight": False})

plots = [{"name": "Consolidation Anchor", "title": False, "data": [float(value) for value in close], "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True}]
signals = [
    {"type": "buy", "name": "Zone Breakout Up", "text": "BUY", "data": buy_signals, "color": bullish_color, "renderMode": "events", "size": signal_size.lower()},
    {"type": "sell", "name": "Zone Breakout Down", "text": "SELL", "data": sell_signals, "color": bearish_color, "renderMode": "events", "size": signal_size.lower()},
]

kept_layer_indices = set()
for layer_type in ("zone", "line", "label"):
    matching_indices = [index for index, layer in enumerate(layers) if layer.get("type") == layer_type]
    kept_layer_indices.update(matching_indices[-500:])
limited_layers = [layer for index, layer in enumerate(layers) if index in kept_layer_indices]

output = {"name": my_indicator_name, "plots": plots, "signals": signals, "layers": limited_layers}
