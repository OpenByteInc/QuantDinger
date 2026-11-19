# ============================================================
# QuantDinger Auto Structure Fibonacci
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Auto Structure Fibonacci"
my_indicator_description = (
    "Detects market structure with an ATR-confirmed ZigZag or a retracement chain, then keeps "
    "a bounded history of confirmed Fibonacci structures, swing annotations, and ATR reference levels."
)

# @param structure_method str ATR_ZigZag Structure method values=ATR_ZigZag,Retracement_Chain
# @param atr_confirmation float 0.5 ATR ZigZag confirmation ratio range=0.1:1.0:0.001
# @param chain_confirmation float 0.382 Retracement chain confirmation ratio range=0.1:1.0:0.001
# @param atr_length int 14 ATR length range=1:500:1
# @param atr_reversal_multiplier float 2.6 ATR reversal multiplier range=0.1:20.0:0.1
# @param minimum_fib_size float 0.0 Minimum Fibonacci size in ATR range=0.0:20.0:0.5
# @param minimum_break float 0.0 Minimum structure break in ATR range=0.0:10.0:0.05
# @param show_swings bool false Show swing structure
# @param show_swing_tags bool false Show swing labels
# @param atr_display str Off ATR display values=Off,Reversal_Level,ATR_Bands
# @param maximum_fibs int 10 Maximum historical Fibonacci structures range=1:50:1
# @param show_bullish bool true Show bullish structures
# @param show_bearish bool true Show bearish structures
# @param show_labels bool true Show level values
# @param show_prices bool false Show prices in level labels
# @param extension_bars int 30 Fibonacci extension bars range=0:400:1
# @param line_width int 1 Fibonacci line width range=1:4:1
# @param line_style str Solid Fibonacci line style values=Solid,Dashed,Dotted
# @param label_size str Small Level label size values=Tiny,Small,Normal
# @param level_0_color str #787B86 Level 0 color
# @param show_0382 bool true Show 0.382 level
# @param level_0382_color str #5B9CF6 Level 0.382 color
# @param level_05_color str #F5B400 Level 0.5 color
# @param level_0618_color str #F5B400 Level 0.618 color
# @param show_086 bool true Show 0.86 level
# @param level_086_color str #F23645 Level 0.86 color
# @param level_1_color str #787B86 Level 1 color
# @param show_gold_zone bool true Show 0.5 to 0.618 gold zone
# @param gold_zone_color str rgba(245,180,0,0.15) Gold zone fill color

structure_method = str(params.get("structure_method", "ATR_ZigZag"))
if structure_method not in ("ATR_ZigZag", "Retracement_Chain"):
    structure_method = "ATR_ZigZag"
atr_confirmation = min(1.0, max(0.1, float(params.get("atr_confirmation", 0.5))))
chain_confirmation = min(1.0, max(0.1, float(params.get("chain_confirmation", 0.382))))
atr_length = min(500, max(1, int(params.get("atr_length", 14))))
atr_reversal_multiplier = min(20.0, max(0.1, float(params.get("atr_reversal_multiplier", 2.6))))
minimum_fib_size = min(20.0, max(0.0, float(params.get("minimum_fib_size", 0.0))))
minimum_break = min(10.0, max(0.0, float(params.get("minimum_break", 0.0))))
show_swings = bool(params.get("show_swings", False))
show_swing_tags = bool(params.get("show_swing_tags", False))
atr_display = str(params.get("atr_display", "Off"))
if atr_display not in ("Off", "Reversal_Level", "ATR_Bands"):
    atr_display = "Off"
maximum_fibs = min(50, max(1, int(params.get("maximum_fibs", 10))))
show_bullish = bool(params.get("show_bullish", True))
show_bearish = bool(params.get("show_bearish", True))
show_labels = bool(params.get("show_labels", True))
show_prices = bool(params.get("show_prices", False))
extension_bars = min(400, max(0, int(params.get("extension_bars", 30))))
line_width = min(4, max(1, int(params.get("line_width", 1))))
line_style = str(params.get("line_style", "Solid"))
if line_style not in ("Solid", "Dashed", "Dotted"):
    line_style = "Solid"
label_size = str(params.get("label_size", "Small"))
if label_size not in ("Tiny", "Small", "Normal"):
    label_size = "Small"
level_label_font_size = {"Tiny": 8, "Small": 10, "Normal": 12}[label_size]
level_0_color = str(params.get("level_0_color", "#787B86"))
show_0382 = bool(params.get("show_0382", True))
level_0382_color = str(params.get("level_0382_color", "#5B9CF6"))
level_05_color = str(params.get("level_05_color", "#F5B400"))
level_0618_color = str(params.get("level_0618_color", "#F5B400"))
show_086 = bool(params.get("show_086", True))
level_086_color = str(params.get("level_086_color", "#F23645"))
level_1_color = str(params.get("level_1_color", "#787B86"))
show_gold_zone = bool(params.get("show_gold_zone", True))
gold_zone_color = str(params.get("gold_zone_color", "rgba(245,180,0,0.15)"))

bullish_structure_color = "#26A69A"
bearish_structure_color = "#EF5350"
muted_color = "#787B86"

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def wilder_average(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) < length:
        return result
    result[length - 1] = float(np.mean(source[:length]))
    for index in range(length, len(source)):
        result[index] = (result[index - 1] * (length - 1) + source[index]) / length
    return result


def fib_level(direction, high_price, low_price, ratio):
    price_range = high_price - low_price
    return high_price - price_range * ratio if direction == 1 else low_price + price_range * ratio


def structure_tag(is_high, price, previous_same_price, break_size):
    if previous_same_price is None:
        return "H" if is_high else "L"
    if is_high:
        if price > previous_same_price + break_size:
            return "HH"
        if price >= previous_same_price:
            return "EQH"
        return "LH"
    if price < previous_same_price - break_size:
        return "LL"
    if price <= previous_same_price:
        return "EQL"
    return "HL"


def make_fib(direction, high_price, high_bar, low_price, low_bar, confirmation_ratio, retrace_extreme, created_bar):
    return {
        "direction": direction,
        "high_price": float(high_price),
        "high_bar": int(high_bar),
        "low_price": float(low_price),
        "low_bar": int(low_bar),
        "level_0382": fib_level(direction, high_price, low_price, 0.382),
        "level_05": fib_level(direction, high_price, low_price, 0.5),
        "level_0618": fib_level(direction, high_price, low_price, 0.618),
        "level_086": fib_level(direction, high_price, low_price, 0.86),
        "confirmation_price": fib_level(direction, high_price, low_price, confirmation_ratio),
        "created_bar": int(created_bar),
        "retrace_extreme": retrace_extreme,
        "confirm_bar": None,
    }


def fib_big_enough(fib, atr_value):
    threshold = minimum_fib_size * (atr_value if np.isfinite(atr_value) else 0.0)
    return fib["high_price"] - fib["low_price"] >= threshold


true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(
            high[index] - low[index],
            abs(high[index] - close[index - 1]),
            abs(low[index] - close[index - 1]),
        )
atr = wilder_average(true_range, atr_length)

swings = []
drawn_fibs = []
last_fib_end_bar = None


def record_swing(is_high, price, swing_bar, confirm_bar):
    previous_same = swings[-2]["price"] if len(swings) >= 2 else None
    break_size = (atr[confirm_bar] if np.isfinite(atr[confirm_bar]) else 0.0) * minimum_break
    swing = {
        "is_high": bool(is_high),
        "price": float(price),
        "bar": int(swing_bar),
        "confirm_bar": int(confirm_bar),
        "tag": structure_tag(is_high, price, previous_same, break_size),
    }
    swings.append(swing)
    if len(swings) > 50:
        swings.pop(0)
    return swing


def publish_fib(fib, visible, confirm_bar, atr_method):
    global last_fib_end_bar
    fib["confirm_bar"] = int(confirm_bar)
    start_bar = min(fib["high_bar"], fib["low_bar"])
    end_bar = max(fib["high_bar"], fib["low_bar"]) if atr_method else int(confirm_bar)
    no_overlap = last_fib_end_bar is None or start_bar >= last_fib_end_bar
    if no_overlap:
        last_fib_end_bar = end_bar
        direction_visible = show_bullish if fib["direction"] == 1 else show_bearish
        if visible and direction_visible:
            drawn_fibs.append(dict(fib))
            if len(drawn_fibs) > maximum_fibs:
                drawn_fibs.pop(0)


def step_pending(fib, index):
    if fib is None:
        return None
    bullish = fib["direction"] == 1
    invalid = False
    if index > fib["created_bar"]:
        invalid = high[index] > fib["high_price"] if bullish else low[index] < fib["low_price"]
        if not invalid:
            if bullish:
                fib["retrace_extreme"] = min(float(fib["retrace_extreme"]), float(low[index]))
            else:
                fib["retrace_extreme"] = max(float(fib["retrace_extreme"]), float(high[index]))
    if invalid:
        return None
    confirmed = fib["retrace_extreme"] <= fib["confirmation_price"] if bullish else fib["retrace_extreme"] >= fib["confirmation_price"]
    if confirmed:
        publish_fib(fib, True, index, True)
        return None
    return fib


zz_direction = 0
zz_extreme = np.nan
zz_extreme_bar = None
zz_counter = np.nan
zz_counter_bar = None
pending_bull = None
pending_bear = None
reversal_level = np.full(n, np.nan, dtype=float)
leg_flipped = np.zeros(n, dtype=bool)

chain_direction = 0
chain_bull = None
chain_bear = None
chain_last_fib = None
chain_since_extreme = np.nan
chain_since_extreme_bar = None


def make_chain_move(direction, anchor, anchor_bar, extreme=None, extreme_bar=None, must_beat=None):
    return {
        "direction": int(direction),
        "anchor": float(anchor),
        "anchor_bar": int(anchor_bar),
        "extreme": None if extreme is None else float(extreme),
        "extreme_bar": None if extreme_bar is None else int(extreme_bar),
        "must_beat": must_beat,
    }


def advance_chain(move, index):
    bullish = move["direction"] == 1
    push_source = high[index] if bullish else low[index]
    pull_source = low[index] if bullish else high[index]
    formed = False
    if move["extreme"] is not None and (push_source <= move["extreme"] if bullish else push_source >= move["extreme"]):
        break_size = (atr[index] if np.isfinite(atr[index]) else 0.0) * minimum_break
        beyond_previous = move["must_beat"] is None or (move["extreme"] > move["must_beat"] + break_size if bullish else move["extreme"] < move["must_beat"] - break_size)
        high_price = move["extreme"] if bullish else move["anchor"]
        low_price = move["anchor"] if bullish else move["extreme"]
        confirmation_price = fib_level(move["direction"], high_price, low_price, chain_confirmation)
        formed = beyond_previous and (pull_source <= confirmation_price if bullish else pull_source >= confirmation_price)
    if not formed:
        if pull_source < move["anchor"] if bullish else pull_source > move["anchor"]:
            move["anchor"] = float(pull_source)
            move["anchor_bar"] = int(index)
            move["extreme"] = None
            move["extreme_bar"] = None
        elif move["extreme"] is None or (push_source > move["extreme"] if bullish else push_source < move["extreme"]):
            move["extreme"] = float(push_source)
            move["extreme_bar"] = int(index)
    return formed


def chain_to_fib(move, index):
    bullish = move["direction"] == 1
    return make_fib(
        move["direction"],
        move["extreme"] if bullish else move["anchor"],
        move["extreme_bar"] if bullish else move["anchor_bar"],
        move["anchor"] if bullish else move["extreme"],
        move["anchor_bar"] if bullish else move["extreme_bar"],
        chain_confirmation,
        np.nan,
        index,
    )


for index in range(n):
    previous_zz_direction = zz_direction
    new_swing = None
    retrace_so_far = np.nan

    if zz_direction == 0:
        zz_direction = 1
        zz_extreme = high[index]
        zz_extreme_bar = index
    else:
        rising_leg = zz_direction == 1
        push_source = high[index] if rising_leg else low[index]
        pull_source = low[index] if rising_leg else high[index]
        pushed = push_source > zz_extreme if rising_leg else push_source < zz_extreme
        if pushed:
            zz_extreme = float(push_source)
            zz_extreme_bar = index
            zz_counter = np.nan
            zz_counter_bar = None
        else:
            if not np.isfinite(zz_counter) or (pull_source < zz_counter if rising_leg else pull_source > zz_counter):
                zz_counter = float(pull_source)
                zz_counter_bar = index
            if np.isfinite(atr[index]) and np.isfinite(zz_counter) and abs(zz_extreme - zz_counter) >= atr[index] * atr_reversal_multiplier:
                new_swing = record_swing(rising_leg, zz_extreme, zz_extreme_bar, index)
                retrace_so_far = zz_counter
                zz_direction = -zz_direction
                zz_extreme = zz_counter
                zz_extreme_bar = zz_counter_bar
                zz_counter = np.nan
                zz_counter_bar = None

                if structure_method == "ATR_ZigZag" and len(swings) >= 2:
                    leg_start = swings[-2]
                    high_price = max(leg_start["price"], new_swing["price"])
                    low_price = min(leg_start["price"], new_swing["price"])
                    sized = high_price - low_price >= minimum_fib_size * atr[index]
                    bullish_leg = new_swing["is_high"] and new_swing["tag"] == "HH" and leg_start["tag"] in ("LL", "HL", "EQL")
                    bearish_leg = (not new_swing["is_high"]) and new_swing["tag"] == "LL" and leg_start["tag"] in ("HH", "LH", "EQH")
                    if sized and show_bullish and bullish_leg:
                        pending_bull = make_fib(1, new_swing["price"], new_swing["bar"], leg_start["price"], leg_start["bar"], atr_confirmation, retrace_so_far, index)
                    elif sized and show_bearish and bearish_leg:
                        pending_bear = make_fib(-1, leg_start["price"], leg_start["bar"], new_swing["price"], new_swing["bar"], atr_confirmation, retrace_so_far, index)

    leg_flipped[index] = index > 0 and zz_direction != previous_zz_direction
    if zz_direction == 1 and np.isfinite(atr[index]):
        reversal_level[index] = zz_extreme - atr[index] * atr_reversal_multiplier
    elif zz_direction == -1 and np.isfinite(atr[index]):
        reversal_level[index] = zz_extreme + atr[index] * atr_reversal_multiplier

    if structure_method == "ATR_ZigZag":
        pending_bull = step_pending(pending_bull, index)
        pending_bear = step_pending(pending_bear, index)

    if structure_method == "Retracement_Chain":
        if chain_bull is None:
            chain_bull = make_chain_move(1, low[index], index)
            chain_bear = make_chain_move(-1, high[index], index)
        else:
            bull_formed = chain_direction != -1 and advance_chain(chain_bull, index)
            bear_formed = chain_direction != 1 and advance_chain(chain_bear, index)
            if bull_formed and bear_formed:
                if chain_bull["extreme_bar"] >= chain_bear["extreme_bar"]:
                    bear_formed = False
                else:
                    bull_formed = False

            if bull_formed or bear_formed:
                move = chain_bull if bull_formed else chain_bear
                bullish = move["direction"] == 1
                fib = chain_to_fib(move, index)
                publish_fib(fib, fib_big_enough(fib, atr[index]), index, False)
                chain_last_fib = fib
                chain_direction = move["direction"]
                chain_since_extreme = high[index] if bullish else low[index]
                chain_since_extreme_bar = index
                next_move = make_chain_move(move["direction"], low[index] if bullish else high[index], index, must_beat=fib["high_price"] if bullish else fib["low_price"])
                if bullish:
                    chain_bull = next_move
                else:
                    chain_bear = next_move
            elif chain_direction != 0 and chain_last_fib is not None:
                bullish = chain_direction == 1
                if high[index] > chain_since_extreme if bullish else low[index] < chain_since_extreme:
                    chain_since_extreme = high[index] if bullish else low[index]
                    chain_since_extreme_bar = index
                level_one = chain_last_fib["low_price"] if bullish else chain_last_fib["high_price"]
                broken = low[index] < level_one if bullish else high[index] > level_one
                if broken:
                    chain_direction = 0
                    if bullish:
                        chain_bear = make_chain_move(-1, chain_since_extreme, chain_since_extreme_bar, low[index], index)
                        chain_bull = make_chain_move(1, low[index], index)
                    else:
                        chain_bull = make_chain_move(1, chain_since_extreme, chain_since_extreme_bar, high[index], index)
                        chain_bear = make_chain_move(-1, high[index], index)


def finite_line(values, visible):
    return [float(value) if visible and np.isfinite(value) else None for value in values]


layers = []
if show_swings:
    for swing_index in range(1, len(swings)):
        previous = swings[swing_index - 1]
        current = swings[swing_index]
        layers.append({
            "type": "line",
            "startIndex": previous["bar"],
            "endIndex": current["bar"],
            "startPrice": previous["price"],
            "endPrice": current["price"],
            "color": muted_color,
            "lineWidth": 1,
        })
    if swings and zz_extreme_bar is not None and np.isfinite(zz_extreme):
        latest = swings[-1]
        layers.append({
            "type": "line",
            "startIndex": latest["bar"],
            "endIndex": zz_extreme_bar,
            "startPrice": latest["price"],
            "endPrice": float(zz_extreme),
            "color": muted_color,
            "lineWidth": 1,
            "dashed": True,
            "text": "Forming",
        })

if show_swing_tags:
    for swing in swings:
        tag_color = bullish_structure_color if swing["tag"] in ("HH", "HL") else bearish_structure_color if swing["tag"] in ("LH", "LL") else muted_color
        layers.append({
            "type": "label",
            "index": swing["bar"],
            "price": swing["price"],
            "text": swing["tag"],
            "color": tag_color,
            "textColor": "#FFFFFF",
            "side": "above" if swing["is_high"] else "below",
            "fontSize": 10,
        })

level_specs = [
    (0.0, level_0_color, True),
    (0.382, level_0382_color, show_0382),
    (0.5, level_05_color, True),
    (0.618, level_0618_color, True),
    (0.86, level_086_color, show_086),
    (1.0, level_1_color, True),
]
for fib_index, fib in enumerate(drawn_fibs):
    bullish = fib["direction"] == 1
    is_latest_fib = fib_index == len(drawn_fibs) - 1
    start_index = min(fib["high_bar"], fib["low_bar"])
    confirmed_end = int(fib["confirm_bar"])
    visible_end = min(n - 1, confirmed_end + extension_bars)
    future_bars = max(0, confirmed_end + extension_bars - (n - 1))
    for ratio, color, visible in level_specs:
        if not visible:
            continue
        price = fib_level(fib["direction"], fib["high_price"], fib["low_price"], ratio)
        label = f"{ratio:g}"
        if show_prices:
            label += f" ({price:.2f})"
        layer = {
            "type": "line",
            "startIndex": start_index,
            "endIndex": visible_end,
            "price": float(price),
            "text": label if show_labels and is_latest_fib else "",
            "textPosition": "end",
            "fontSize": level_label_font_size,
            "color": color,
            "lineWidth": line_width,
            "dashed": line_style in ("Dashed", "Dotted"),
        }
        if future_bars > 0:
            layer["extendBars"] = future_bars
        layers.append(layer)
    if show_gold_zone:
        zone_end = min(n - 1, confirmed_end + extension_bars)
        layers.append({
            "type": "zone",
            "startIndex": start_index,
            "endIndex": zone_end,
            "top": float(max(fib["level_05"], fib["level_0618"])),
            "bottom": float(min(fib["level_05"], fib["level_0618"])),
            "fillColor": gold_zone_color,
            "borderColor": gold_zone_color,
            "opacity": 0.15,
            "text": "Golden Zone" if fib_index == len(drawn_fibs) - 1 else "",
        })

threshold = atr * atr_reversal_multiplier
reversal_plot = np.where(leg_flipped, np.nan, reversal_level)

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Runtime Anchor", "title": False, "data": finite_line(close, True), "color": "rgba(0,0,0,0)", "opacity": 0.0, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Reversal Level", "data": finite_line(reversal_plot, atr_display == "Reversal_Level"), "color": bullish_structure_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Upper ATR Band", "data": finite_line(close + threshold, atr_display == "ATR_Bands"), "color": muted_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Lower ATR Band", "data": finite_line(close - threshold, atr_display == "ATR_Bands"), "color": muted_color, "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [],
    "layers": layers,
}
