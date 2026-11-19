# ============================================================
# QuantDinger Auto Wolfe Waves
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Auto Wolfe Waves"
my_indicator_description = (
    "Detects confirmed five-point Wolfe Wave structures, tracks their entry window and trade-level "
    "lifecycle, and renders active, developing, and recently completed geometry on the price chart."
)

# @param preset str Day Detection preset values=Scalp,Day,Swing,Custom
# @param direction str Both Direction values=Both,Bullish_Only,Bearish_Only
# @param swing_strength int 5 Custom swing strength range=2:30:1
# @param entry_window int 10 Custom entry window range=1:50:1
# @param allow_skips bool true Allow one skipped swing per leg
# @param longer_waves bool false Enable longer-swing layer
# @param longer_multiple int 3 Longer-swing multiple range=2:4:1
# @param show_levels bool true Show entry, stop, and target levels
# @param reached_targets str Clear Reached target handling values=Clear,Keep
# @param target_1 float 0.5 Target 1 in R range=0.1:10.0:0.05
# @param target_2 float 0.75 Target 2 in R range=0.1:10.0:0.05
# @param target_3 float 1.0 Target 3 in R range=0.1:10.0:0.05
# @param target_color str #8C66FF Target color
# @param stop_color str #D9453D Stop color
# @param show_forming bool true Show developing waves
# @param kept_waves int 1 Completed waves retained range=0:5:1
# @param bullish_color str #26A69A Bullish wave color
# @param bearish_color str #EF5350 Bearish wave color
# @param broken_color str #787B86 Invalidated wave color
# @param zigzag_width int 2 Zigzag width range=1:4:1
# @param zigzag_style str Solid Zigzag style values=Solid,Dashed,Dotted
# @param guide_width int 1 Guide width range=1:4:1
# @param guide_style str Solid Guide style values=Solid,Dashed,Dotted
# @param projection_width int 1 Point 1 to point 4 width range=1:4:1
# @param projection_style str Dotted Point 1 to point 4 style values=Solid,Dashed,Dotted
# @param show_fill bool true Show wedge fill
# @param fill_transparency int 88 Wedge transparency range=50:100:1
# @param show_points bool true Show point labels
# @param show_panel bool true Show wave panel
# @param panel_position str Auto Panel position values=Auto,Top_Right,Top_Left,Bottom_Right,Bottom_Left
# @param text_size str Small Text size values=Tiny,Small,Normal

preset = str(params.get("preset", "Day"))
if preset not in ("Scalp", "Day", "Swing", "Custom"):
    preset = "Day"
direction = str(params.get("direction", "Both"))
if direction not in ("Both", "Bullish_Only", "Bearish_Only"):
    direction = "Both"
custom_strength = min(30, max(2, int(params.get("swing_strength", 5))))
custom_window = min(50, max(1, int(params.get("entry_window", 10))))
swing_strength = 3 if preset == "Scalp" else 5 if preset == "Day" else 8 if preset == "Swing" else custom_strength
entry_window = 5 if preset == "Scalp" else 10 if preset == "Day" else 15 if preset == "Swing" else custom_window
allow_skips = bool(params.get("allow_skips", True))
longer_waves = bool(params.get("longer_waves", False))
longer_multiple = min(4, max(2, int(params.get("longer_multiple", 3))))
show_levels = bool(params.get("show_levels", True))
reached_targets = str(params.get("reached_targets", "Clear"))
if reached_targets not in ("Clear", "Keep"):
    reached_targets = "Clear"
target_multiples = [
    min(10.0, max(0.1, float(params.get("target_1", 0.5)))),
    min(10.0, max(0.1, float(params.get("target_2", 0.75)))),
    min(10.0, max(0.1, float(params.get("target_3", 1.0)))),
]
target_color = str(params.get("target_color", "#8C66FF"))
stop_color = str(params.get("stop_color", "#D9453D"))
show_forming = bool(params.get("show_forming", True))
kept_waves = min(5, max(0, int(params.get("kept_waves", 1))))
bullish_color = str(params.get("bullish_color", "#26A69A"))
bearish_color = str(params.get("bearish_color", "#EF5350"))
broken_color = str(params.get("broken_color", "#787B86"))
zigzag_width = min(4, max(1, int(params.get("zigzag_width", 2))))
zigzag_style = str(params.get("zigzag_style", "Solid"))
guide_width = min(4, max(1, int(params.get("guide_width", 1))))
guide_style = str(params.get("guide_style", "Solid"))
projection_width = min(4, max(1, int(params.get("projection_width", 1))))
projection_style = str(params.get("projection_style", "Dotted"))
show_fill = bool(params.get("show_fill", True))
fill_transparency = min(100, max(50, int(params.get("fill_transparency", 88))))
show_points = bool(params.get("show_points", True))
show_panel = bool(params.get("show_panel", True))
panel_position = str(params.get("panel_position", "Auto"))
text_size = str(params.get("text_size", "Small"))
show_event_markers = bool(params.get("show_event_markers", False))

PIVOT_HORIZON = 500
VOLATILITY_LENGTH = 14
OVERSHOOT_ATR = 1.0
STOP_BEYOND_ATR = 0.1
MIN_STOP_ATR = 0.5
MINOR_FRACTION = 0.382
ACTIVE_MAX = 4
CHAIN_MAX = 13
DRAW_MAX_BACK = 4500

WAITING = 1
ENTERED = 2
ENDED = 3

END_BEYOND_FIVE = 1
END_NO_ENTRY = 2
END_STOP = 3
END_FINAL = 4
END_SAME_BAR = 5
END_AGED = 6
END_PAST_24 = 7

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def rgba(color, alpha):
    value = str(color).strip()
    if len(value) == 7 and value.startswith("#"):
        try:
            red = int(value[1:3], 16)
            green = int(value[3:5], 16)
            blue = int(value[5:7], 16)
            return f"rgba({red},{green},{blue},{min(1.0, max(0.0, float(alpha))):.3f})"
        except ValueError:
            return value
    return value


def simple_average(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) < length:
        return result
    running = 0.0
    invalid = 0
    for index, value in enumerate(source):
        if np.isfinite(value):
            running += value
        else:
            invalid += 1
        if index >= length:
            leaving = source[index - length]
            if np.isfinite(leaving):
                running -= leaving
            else:
                invalid -= 1
        if index >= length - 1 and invalid == 0:
            result[index] = running / length
    return result


true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not (np.isfinite(high[index]) and np.isfinite(low[index]) and np.isfinite(close[index])):
        continue
    if index == 0 or not np.isfinite(close[index - 1]):
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(
            high[index] - low[index],
            abs(high[index] - close[index - 1]),
            abs(low[index] - close[index - 1]),
        )
volatility = simple_average(true_range, VOLATILITY_LENGTH)


def confirmed_pivot_events(strength):
    events = {}
    if strength < 1:
        return events
    for center in range(strength, n - strength):
        high_window = high[center - strength:center + strength + 1]
        low_window = low[center - strength:center + strength + 1]
        if np.all(np.isfinite(high_window)):
            neighbors = np.concatenate((high_window[:strength], high_window[strength + 1:]))
            if high[center] > float(np.max(neighbors)):
                events.setdefault(center + strength, []).append(
                    {"bar": center, "price": float(high[center]), "side": 1}
                )
        if np.all(np.isfinite(low_window)):
            neighbors = np.concatenate((low_window[:strength], low_window[strength + 1:]))
            if low[center] < float(np.min(neighbors)):
                events.setdefault(center + strength, []).append(
                    {"bar": center, "price": float(low[center]), "side": -1}
                )
    return events


def accept_pivot(chain, pivot):
    if chain and chain[-1]["side"] == pivot["side"]:
        more_extreme = pivot["price"] >= chain[-1]["price"] if pivot["side"] == 1 else pivot["price"] <= chain[-1]["price"]
        if more_extreme:
            chain[-1] = pivot
        return more_extreme
    chain.append(pivot)
    if len(chain) > CHAIN_MAX:
        del chain[0:len(chain) - CHAIN_MAX]
    return True


def line_at(first, second, target):
    span = second["bar"] - first["bar"]
    if span == 0:
        return float(second["price"])
    return float(first["price"] + (second["price"] - first["price"]) * (target - first["bar"]) / span)


def line_step(first, second):
    span = max(1, abs(second["bar"] - first["bar"]))
    scale = max(abs(first["price"]), abs(second["price"]), 1.0)
    return scale * 1e-10 / span


def on_line(gap, price, step):
    tolerance = min(1e-10 * max(abs(price), 1.0), 0.25 * max(step, 1e-15))
    return 0.0 if abs(gap) <= tolerance else float(gap)


def span_for_mask(mask, leg):
    return 3 if ((mask >> (3 - leg)) & 1) else 1


def skipped_count(mask):
    return sum(1 for leg in range(4) if span_for_mask(mask, leg) == 3)


def directed_leg(chain, older_index, newer_index):
    older = chain[older_index]
    newer = chain[newer_index]
    return newer["bar"] > older["bar"] and newer["side"] * (newer["price"] - older["price"]) > 0.0


def dominant_leg(chain, older_index, newer_index):
    first = chain[older_index]["price"]
    last = chain[newer_index]["price"]
    lower = min(first, last)
    upper = max(first, last)
    if newer_index - older_index <= 1:
        return True
    for index in range(older_index + 1, newer_index):
        price = chain[index]["price"]
        if price <= lower or price >= upper:
            return False
    if newer_index - older_index > 2:
        cap = MINOR_FRACTION * (upper - lower)
        for index in range(older_index + 1, newer_index - 1):
            if abs(chain[index + 1]["price"] - chain[index]["price"]) > cap:
                return False
    return True


def candidate_points(chain, mask):
    indices = [0] * 5
    cursor = len(chain) - 1
    if cursor < 0:
        return None
    indices[4] = cursor
    for leg in range(3, -1, -1):
        cursor -= span_for_mask(mask, leg)
        if cursor < 0:
            return None
        indices[leg] = cursor
    for leg in range(4):
        if not directed_leg(chain, indices[leg], indices[leg + 1]):
            return None
        if not dominant_leg(chain, indices[leg], indices[leg + 1]):
            return None
    return [chain[index] for index in indices]


def wave_side(points):
    return 1 if points[4]["side"] == -1 else -1


def direction_allowed(side):
    return direction == "Both" or (direction == "Bullish_Only" and side == 1) or (direction == "Bearish_Only" and side == -1)


def wave_rules_pass(points, atr_value):
    if not np.isfinite(atr_value) or atr_value <= 0.0:
        return False
    p1, p2, p3, p4, p5 = points
    side = wave_side(points)
    width_at_5 = on_line(
        side * (line_at(p2, p4, p5["bar"]) - line_at(p1, p3, p5["bar"])),
        p5["price"],
        line_step(p1, p3) / max(1, p4["bar"] - p2["bar"]),
    )
    width_at_4 = side * (p4["price"] - line_at(p1, p3, p4["bar"]))
    overshoot = on_line(
        side * (line_at(p1, p3, p5["bar"]) - p5["price"]),
        p5["price"],
        line_step(p1, p3),
    )
    return (
        side * (p1["price"] - p3["price"]) > 0.0
        and side * (p4["price"] - p1["price"]) > 0.0
        and width_at_5 > 0.0
        and on_line(width_at_4 - width_at_5, p5["price"], line_step(p1, p3)) > 0.0
        and overshoot >= 0.0
        and overshoot <= OVERSHOOT_ATR * atr_value
    )


def wave_key(points):
    return (wave_side(points), points[1]["bar"], points[2]["bar"], points[3]["bar"])


def select_wave(chain, atr_value, seen_keys):
    if not chain or not direction_allowed(-chain[-1]["side"]):
        return None
    maximum_skips = 4 if allow_skips else 0
    for skip_count in range(maximum_skips + 1):
        for mask in range(16):
            if skipped_count(mask) != skip_count:
                continue
            points = candidate_points(chain, mask)
            if points is None:
                continue
            if wave_key(points) not in seen_keys and wave_rules_pass(points, atr_value):
                return points
    return None


def forming_span(mask, leg):
    return 3 if ((mask >> (2 - leg)) & 1) else 1


def forming_points(chain, mask):
    indices = [0] * 4
    cursor = len(chain) - 1
    if cursor < 0:
        return None
    indices[3] = cursor
    for leg in range(2, -1, -1):
        cursor -= forming_span(mask, leg)
        if cursor < 0:
            return None
        indices[leg] = cursor
    for leg in range(3):
        if not directed_leg(chain, indices[leg], indices[leg + 1]):
            return None
        if not dominant_leg(chain, indices[leg], indices[leg + 1]):
            return None
    return [chain[index] for index in indices]


def forming_apex(points):
    p1, p2, p3, p4 = points
    side = p4["side"]
    slope_13 = (p3["price"] - p1["price"]) / (p3["bar"] - p1["bar"])
    slope_24 = (p4["price"] - p2["price"]) / (p4["bar"] - p2["bar"])
    width = side * (p4["price"] - line_at(p1, p3, p4["bar"]))
    narrowing = on_line(
        side * (slope_13 - slope_24),
        p4["price"],
        line_step(p1, p3) / max(1, p4["bar"] - p2["bar"]),
    )
    if not (
        side * (p1["price"] - p3["price"]) > 0.0
        and side * (p4["price"] - p1["price"]) > 0.0
        and width > 0.0
        and narrowing > 0.0
    ):
        return np.nan
    return float(p4["bar"] + width / narrowing)


def forming_line_at(candidate, index):
    return line_at(candidate["points"][0], candidate["points"][2], index)


def forming_far_at(candidate, index):
    return forming_line_at(candidate, index) - candidate["side"] * OVERSHOOT_ATR * candidate["atr"]


def forming_holds(candidate, bar_now, from_bar):
    if candidate is None or bar_now >= candidate["apex"]:
        return False
    if bar_now - candidate["points"][0]["bar"] > PIVOT_HORIZON:
        return False
    for index in range(max(from_bar, 0), bar_now + 1):
        against = low[index] if candidate["side"] > 0 else high[index]
        if np.isfinite(against) and candidate["side"] * (against - forming_far_at(candidate, index)) < 0.0:
            return False
    return True


def select_forming(chain, atr_value, seen_keys, bar_now):
    if not np.isfinite(atr_value) or atr_value <= 0.0 or not chain or not direction_allowed(chain[-1]["side"]):
        return None
    maximum_skips = 3 if allow_skips else 0
    for skip_count in range(maximum_skips + 1):
        for mask in range(8):
            if skipped_count(mask) != skip_count:
                continue
            points = forming_points(chain, mask)
            if points is None:
                continue
            apex = forming_apex(points)
            key = (points[3]["side"], points[1]["bar"], points[2]["bar"], points[3]["bar"])
            if not np.isfinite(apex) or key in seen_keys:
                continue
            candidate = {"points": points, "side": points[3]["side"], "atr": float(atr_value), "apex": apex}
            if forming_holds(candidate, bar_now, points[3]["bar"] + 1):
                return candidate
    return None


def apex_of(points):
    p1, p2, p3, p4, p5 = points
    width_4 = p4["price"] - line_at(p1, p3, p4["bar"])
    width_5 = line_at(p2, p4, p5["bar"]) - line_at(p1, p3, p5["bar"])
    divisor = width_4 - width_5
    if abs(divisor) <= 1e-15:
        return float(p5["bar"])
    return float(p4["bar"] + width_4 * (p5["bar"] - p4["bar"]) / divisor)


def line_13_at(wave, index):
    return line_at(wave["points"][0], wave["points"][2], index)


def line_24_at(wave, index):
    return line_at(wave["points"][1], wave["points"][3], index)


def line_14_at(wave, index):
    return line_at(wave["points"][0], wave["points"][3], index)


levels_valid = target_multiples[0] < target_multiples[1] < target_multiples[2]


def set_levels(wave):
    raw_stop = wave["points"][4]["price"] - wave["side"] * STOP_BEYOND_ATR * wave["atr"]
    distance = max(wave["side"] * (wave["entry"] - raw_stop), MIN_STOP_ATR * wave["atr"])
    wave["stop"] = float(wave["entry"] - wave["side"] * distance)
    wave["unit"] = abs(wave["entry"] - wave["stop"])
    wave["tiers"] = []
    wave["reached"] = []
    if levels_valid:
        for multiple in target_multiples:
            wave["tiers"].append(float(wave["entry"] + wave["side"] * multiple * wave["unit"]))
            wave["reached"].append(-1)


formed_events = np.full(n, np.nan, dtype=float)
entry_events = np.full(n, np.nan, dtype=float)
level_events = np.full(n, np.nan, dtype=float)
ended_events = np.full(n, np.nan, dtype=float)


def record_event(kind, wave, bar_now, fresh=None):
    if kind == "formed":
        formed_events[bar_now] = wave["points"][4]["price"]
    elif kind == "entry":
        entry_events[bar_now] = wave["entry"]
    elif kind == "level":
        indices = fresh or []
        if indices:
            level_events[bar_now] = wave["tiers"][max(indices)]
    elif kind == "ended":
        ended_events[bar_now] = close[bar_now]


def end_wave(wave, bar_now, reason):
    wave["state"] = ENDED
    wave["reason"] = reason
    wave["end_bar"] = bar_now
    record_event("ended", wave, bar_now)


def step_waiting(wave, bar_now):
    against = low[bar_now] if wave["side"] > 0 else high[bar_now]
    inside_13 = on_line(
        wave["side"] * (close[bar_now] - line_13_at(wave, bar_now)),
        close[bar_now],
        line_step(wave["points"][0], wave["points"][2]),
    )
    past_24 = on_line(
        wave["side"] * (close[bar_now] - line_24_at(wave, bar_now)),
        close[bar_now],
        line_step(wave["points"][1], wave["points"][3]),
    )
    if wave["side"] * (wave["points"][4]["price"] - against) > 0.0:
        end_wave(wave, bar_now, END_BEYOND_FIVE)
    elif inside_13 > 0.0 and past_24 > 0.0:
        end_wave(wave, bar_now, END_PAST_24)
    elif inside_13 > 0.0:
        wave["entry"] = float(close[bar_now])
        wave["entry_bar"] = bar_now
        set_levels(wave)
        wave["state"] = ENTERED
        record_event("entry", wave, bar_now)
    elif bar_now - wave["admit_bar"] + 1 >= entry_window:
        end_wave(wave, bar_now, END_NO_ENTRY)


def step_entered(wave, bar_now):
    favourable = high[bar_now] if wave["side"] > 0 else low[bar_now]
    adverse = low[bar_now] if wave["side"] > 0 else high[bar_now]
    stop_hit = wave["side"] * (wave["stop"] - adverse) >= 0.0
    fresh = []
    for tier, price in enumerate(wave["tiers"]):
        if wave["reached"][tier] < 0 and wave["side"] * (favourable - price) >= 0.0:
            wave["reached"][tier] = bar_now
            fresh.append(tier)
    if stop_hit:
        wave["stop_bar"] = bar_now
        end_wave(wave, bar_now, END_SAME_BAR if fresh else END_STOP)
        return
    if fresh:
        record_event("level", wave, bar_now, fresh)
    if fresh and wave["tiers"] and wave["reached"][-1] >= 0:
        end_wave(wave, bar_now, END_FINAL)
    elif bar_now - wave["entry_bar"] >= wave["age_bars"]:
        end_wave(wave, bar_now, END_AGED)


def new_wave(points, bar_now, atr_value, longer):
    return {
        "longer": bool(longer),
        "points": points,
        "side": wave_side(points),
        "key": wave_key(points),
        "admit_bar": bar_now,
        "atr": float(atr_value),
        "age_bars": points[4]["bar"] - points[0]["bar"],
        "apex": apex_of(points),
        "state": WAITING,
        "reason": 0,
        "end_bar": -1,
        "entry": np.nan,
        "entry_bar": -1,
        "stop": np.nan,
        "stop_bar": -1,
        "unit": np.nan,
        "tiers": [],
        "reached": [],
    }


def live_count(live, longer):
    return sum(1 for wave in live if wave["state"] != ENDED and wave["longer"] == longer)


def try_admit(chain, seen_keys, live, bar_now, atr_value, longer):
    points = select_wave(chain, atr_value, seen_keys)
    if points is None or bar_now - points[0]["bar"] > DRAW_MAX_BACK:
        return None
    if live_count(live, longer) >= ACTIVE_MAX:
        return None
    wave = new_wave(points, bar_now, atr_value, longer)
    seen_keys.add(wave["key"])
    live.append(wave)
    record_event("formed", wave, bar_now)
    step_waiting(wave, bar_now)
    return wave


def run_layer(layer, pivot_events, strength, bar_now, atr_value, live, recent):
    chain = layer["chain"]
    while chain and bar_now - chain[0]["bar"] > PIVOT_HORIZON:
        chain.pop(0)
    chain_changed = False
    for pivot in pivot_events.get(bar_now, []):
        if accept_pivot(chain, pivot):
            chain_changed = True
            admitted = try_admit(chain, layer["seen"], live, bar_now, atr_value, layer["longer"])
            if admitted is not None:
                recent.insert(0, admitted)
                del recent[3:]
    if chain_changed:
        layer["forming"] = select_forming(chain, atr_value, layer["seen"], bar_now)
    elif layer["forming"] is not None and not forming_holds(layer["forming"], bar_now, bar_now):
        layer["forming"] = None


main_events = confirmed_pivot_events(swing_strength)
longer_strength = swing_strength * longer_multiple
longer_enabled = longer_waves and PIVOT_HORIZON >= 3 * longer_strength
longer_events = confirmed_pivot_events(longer_strength) if longer_enabled else {}
layers_state = [
    {"chain": [], "seen": set(), "forming": None, "longer": False},
    {"chain": [], "seen": set(), "forming": None, "longer": True},
]
live_waves = []
completed_waves = []
recent_waves = []

for bar_now in range(n):
    if not (np.isfinite(high[bar_now]) and np.isfinite(low[bar_now]) and np.isfinite(close[bar_now])):
        continue
    for wave in list(live_waves):
        if wave["state"] == WAITING:
            step_waiting(wave, bar_now)
        elif wave["state"] == ENTERED and bar_now > wave["entry_bar"]:
            step_entered(wave, bar_now)
    run_layer(layers_state[0], main_events, swing_strength, bar_now, volatility[bar_now], live_waves, recent_waves)
    if longer_enabled:
        run_layer(layers_state[1], longer_events, longer_strength, bar_now, volatility[bar_now], live_waves, recent_waves)
    newly_completed = [wave for wave in live_waves if wave["state"] == ENDED]
    if newly_completed:
        live_waves = [wave for wave in live_waves if wave["state"] != ENDED]
        completed_waves = list(reversed(newly_completed)) + completed_waves
    completed_waves = [
        wave for wave in completed_waves
        if bar_now - wave["points"][0]["bar"] <= DRAW_MAX_BACK
    ][:kept_waves]


def reason_name(reason):
    names = {
        END_BEYOND_FIVE: "Beyond point 5",
        END_NO_ENTRY: "No entry",
        END_STOP: "Stop reached",
        END_FINAL: "T3 reached",
        END_SAME_BAR: "Order unknown",
        END_AGED: "Aged out",
        END_PAST_24: "Past 2-4 line",
    }
    return names.get(reason, "")


def state_name(wave):
    if wave["state"] == WAITING:
        return "Waiting"
    if wave["state"] == ENTERED:
        return "Entry set"
    return "Ended · " + reason_name(wave["reason"])


def wave_color(wave):
    broken = wave["reason"] in (END_BEYOND_FIVE, END_STOP, END_SAME_BAR)
    base = broken_color if broken else bullish_color if wave["side"] == 1 else bearish_color
    return rgba(base, 0.60) if wave["state"] == ENDED else base


def add_line(target, start_index, end_index, start_price, end_price, color, width, style):
    if end_index < start_index:
        return
    target.append({
        "type": "line",
        "startIndex": int(start_index),
        "endIndex": int(end_index),
        "startPrice": float(start_price),
        "endPrice": float(end_price),
        "color": color,
        "lineWidth": int(width),
        "dashed": style in ("Dashed", "Dotted"),
    })


font_size = 8 if text_size == "Tiny" else 10 if text_size == "Small" else 12
drawn_layers = []
plots = [{
    "name": "Runtime Anchor",
    "title": False,
    "data": [float(value) if np.isfinite(value) else None for value in close],
    "color": "rgba(0,0,0,0)",
    "opacity": 0.0,
    "type": "line",
    "lineWidth": 1,
    "overlay": True,
}]

displayed_waves = live_waves + completed_waves[:kept_waves]
for wave_number, wave in enumerate(displayed_waves):
    points = wave["points"]
    color = wave_color(wave)
    weight = min(4, zigzag_width + (1 if wave["longer"] else 0))
    right = n - 1 if wave["state"] != ENDED else max(wave["points"][4]["bar"], wave["end_bar"])
    right = min(n - 1, right)
    for point_index in range(4):
        first = points[point_index]
        second = points[point_index + 1]
        add_line(
            drawn_layers, first["bar"], second["bar"], first["price"], second["price"],
            color, weight, zigzag_style,
        )
    guide_right = max(points[3]["bar"], min(right, int(np.floor(wave["apex"]))))
    guide_color = rgba(color, 0.75)
    add_line(
        drawn_layers, points[0]["bar"], guide_right, points[0]["price"], line_13_at(wave, guide_right),
        guide_color, min(4, guide_width + (1 if wave["longer"] else 0)), guide_style,
    )
    add_line(
        drawn_layers, points[1]["bar"], guide_right, points[1]["price"], line_24_at(wave, guide_right),
        guide_color, min(4, guide_width + (1 if wave["longer"] else 0)), guide_style,
    )
    add_line(
        drawn_layers, points[0]["bar"], right, points[0]["price"], line_14_at(wave, right),
        rgba(color, 0.65), min(4, projection_width + (1 if wave["longer"] else 0)), projection_style,
    )
    if show_fill and guide_right >= points[1]["bar"]:
        cloud = [None for _ in range(n)]
        for index in range(points[1]["bar"], guide_right + 1):
            first_value = line_13_at(wave, index)
            second_value = line_24_at(wave, index)
            cloud[index] = {"upper": float(max(first_value, second_value)), "lower": float(min(first_value, second_value))}
        plots.append({
            "name": f"Wolfe Wedge {wave_number + 1}",
            "title": False,
            "data": cloud,
            "color": color,
            "type": "cloud",
            "opacity": min(0.30, max(0.01, (100.0 - fill_transparency) / 100.0)),
            "overlay": True,
        })
    if show_points:
        for point_index, point in enumerate(points):
            drawn_layers.append({
                "type": "label",
                "index": int(point["bar"]),
                "price": float(point["price"]),
                "text": ("Long " if wave["longer"] and point_index == 4 else "") + str(point_index + 1),
                "color": color,
                "textColor": color,
                "side": "above" if point["side"] == 1 else "below",
                "fontSize": font_size,
            })
    if show_levels and np.isfinite(wave["entry"]):
        level_specs = [
            ("Entry", wave["entry"], "#D6DCE5", False),
            ("Stop", wave["stop"], stop_color, False),
        ]
        for tier, price in enumerate(wave["tiers"]):
            reached = wave["reached"][tier] >= 0
            if reached_targets == "Clear" and reached:
                continue
            level_specs.append((f"T{tier + 1}" + (" ✓" if reached else ""), price, target_color, reached))
        for level_index, (name, price, level_color, reached) in enumerate(level_specs):
            add_line(
                drawn_layers, wave["entry_bar"], right, price, price,
                rgba(level_color, 0.55 if not reached else 0.75), 1, "Dotted",
            )
            drawn_layers.append({
                "type": "label",
                "index": int(right),
                "price": float(price),
                "text": ("Long " if wave["longer"] else "") + name,
                "color": rgba(level_color, 0.85),
                "textColor": level_color,
                "side": "center",
                "fontSize": font_size,
            })


if show_forming:
    for layer_index, state in enumerate(layers_state):
        candidate = state["forming"]
        if candidate is None or not forming_holds(candidate, n - 1, n - 1):
            continue
        points = candidate["points"]
        color = rgba(bullish_color if candidate["side"] == 1 else bearish_color, 0.60)
        for point_index in range(3):
            add_line(
                drawn_layers,
                points[point_index]["bar"], points[point_index + 1]["bar"],
                points[point_index]["price"], points[point_index + 1]["price"],
                color, min(4, zigzag_width + layer_index), "Dashed",
            )
        add_line(
            drawn_layers, points[0]["bar"], n - 1, points[0]["price"], forming_line_at(candidate, n - 1),
            color, min(4, guide_width + layer_index), "Dashed",
        )
        add_line(
            drawn_layers, points[1]["bar"], n - 1, points[1]["price"],
            line_at(points[1], points[3], n - 1), color, min(4, guide_width + layer_index), "Dashed",
        )
        add_line(
            drawn_layers, points[3]["bar"], n - 1,
            forming_far_at(candidate, points[3]["bar"]), forming_far_at(candidate, n - 1),
            rgba(color, 0.65), 1, "Dotted",
        )
        band = [None for _ in range(n)]
        for index in range(points[3]["bar"], n):
            near = forming_line_at(candidate, index)
            far = forming_far_at(candidate, index)
            band[index] = {"upper": float(max(near, far)), "lower": float(min(near, far))}
        plots.append({
            "name": "Longer Developing Point 5 Band" if state["longer"] else "Developing Point 5 Band",
            "title": False,
            "data": band,
            "color": color,
            "type": "cloud",
            "opacity": 0.15,
            "overlay": True,
        })
        drawn_layers.append({
            "type": "label",
            "index": n - 1,
            "price": float((forming_line_at(candidate, n - 1) + forming_far_at(candidate, n - 1)) * 0.5),
            "text": ("Longer " if state["longer"] else "") + "Developing · point 5 band",
            "color": color,
            "textColor": color,
            "side": "center",
            "fontSize": font_size,
        })


def event_values(values):
    if not show_event_markers:
        return [None for _ in range(n)]
    return [float(value) if np.isfinite(value) else None for value in values]


signals = [
    {"type": "buy", "name": "Wolfe Wave Formed", "text": "W", "color": bullish_color, "renderMode": "events", "data": event_values(formed_events)},
    {"type": "buy", "name": "Wolfe Wave Entry", "text": "E", "color": target_color, "renderMode": "events", "data": event_values(entry_events)},
    {"type": "buy", "name": "Wolfe Target Reached", "text": "T", "color": target_color, "renderMode": "events", "data": event_values(level_events)},
    {"type": "sell", "name": "Wolfe Wave Ended", "text": "X", "color": broken_color, "renderMode": "events", "data": event_values(ended_events)},
]

tables = []
if show_panel:
    rows = []
    if recent_waves:
        for wave_index, wave in enumerate(recent_waves[:3]):
            prefix = "Longer " if wave["longer"] else ""
            rows.append([
                f"{prefix}{'Bullish' if wave['side'] == 1 else 'Bearish'} {wave_index + 1}",
                state_name(wave),
            ])
            if np.isfinite(wave["entry"]):
                rows.append(["Entry / Stop", f"{wave['entry']:.2f} / {wave['stop']:.2f}"])
                if wave["tiers"]:
                    rows.append(["T1 / T2 / T3", " / ".join(f"{price:.2f}" for price in wave["tiers"])])
    else:
        rows.append(["Status", "No confirmed Wolfe Wave"])
    if not levels_valid:
        rows.append(["Notice", "Targets must increase from T1 to T3"])
    rows.append(["Method", "Confirmed pivots · close-based entry"])
    newest = recent_waves[0] if recent_waves else None
    auto_position = "bottom-left" if newest is not None and newest["side"] == 1 else "top-right"
    position_map = {
        "Top_Right": "top-right",
        "Top_Left": "top-left",
        "Bottom_Right": "bottom-right",
        "Bottom_Left": "bottom-left",
    }
    tables.append({
        "title": "AUTO WOLFE WAVES",
        "position": auto_position if panel_position == "Auto" else position_map.get(panel_position, "top-right"),
        "compact": True,
        "borderColor": "#7D8896",
        "accentColor": bullish_color if newest is None or newest["side"] == 1 else bearish_color,
        "rows": rows,
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": drawn_layers[-500:],
    "tables": tables,
}
