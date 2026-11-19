# ============================================================
# QuantDinger Auto Structure Fibonacci
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; alerts do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Auto Structure Fibonacci"
my_indicator_description = (
    "Builds fixed Fibonacci structures from either ATR-confirmed market swings or a "
    "directional retracement chain, with optional structure tags, ATR guides, and gold zones."
)

# @param structure_method str ATR_ZigZag Structure method values=ATR_ZigZag,Retracement_Chain
# @param atr_confirmation float 0.5 ATR ZigZag confirmation ratio range=0.1:1.0:0.001
# @param chain_confirmation float 0.382 Retracement Chain confirmation ratio range=0.1:1.0:0.001
# @param atr_length int 14 ATR length range=1:200:1
# @param atr_multiplier float 2.6 ATR reversal multiplier range=0.1:20.0:0.1
# @param minimum_fib_atr float 0.0 Minimum Fibonacci size in ATR range=0.0:20.0:0.5
# @param minimum_break_atr float 0.0 Minimum structural break in ATR range=0.0:10.0:0.05
# @param show_swings bool false Show swing structure
# @param show_swing_tags bool false Show swing labels
# @param atr_display str Off ATR display values=Off,Reversal Level,ATR Bands
# @param maximum_fibs int 10 Maximum historical Fibonacci structures range=1:50:1
# @param show_bullish bool true Show bullish structures
# @param show_bearish bool true Show bearish structures
# @param show_labels bool false Show level values
# @param show_prices bool false Include prices in labels
# @param extension_bars int 30 Fibonacci extension bars range=0:400:1
# @param line_width int 1 Line width range=1:4:1
# @param line_style str Solid Line style values=Solid,Dashed,Dotted
# @param label_size str Small Label size values=Tiny,Small,Normal
# @param level_0_color str #787B86 Level 0 color
# @param show_0382 bool true Show 0.382 level
# @param level_0382_color str #5B9CF6 Level 0.382 color
# @param level_05_color str #F5B400 Level 0.5 color
# @param level_0618_color str #F5B400 Level 0.618 color
# @param show_086 bool true Show 0.86 level
# @param level_086_color str #F23645 Level 0.86 color
# @param level_1_color str #787B86 Level 1 color
# @param show_gold_zone bool true Show 0.5 to 0.618 gold zone
# @param gold_zone_color str rgba(245,180,0,0.15) Gold zone color

structure_method = str(params.get("structure_method", "ATR_ZigZag"))
atr_confirmation = min(1.0, max(0.1, float(params.get("atr_confirmation", 0.5))))
chain_confirmation = min(1.0, max(0.1, float(params.get("chain_confirmation", 0.382))))
atr_length = min(200, max(1, int(params.get("atr_length", 14))))
atr_multiplier = min(20.0, max(0.1, float(params.get("atr_multiplier", 2.6))))
minimum_fib_atr = min(20.0, max(0.0, float(params.get("minimum_fib_atr", 0.0))))
minimum_break_atr = min(10.0, max(0.0, float(params.get("minimum_break_atr", 0.0))))
show_swings = bool(params.get("show_swings", False))
show_swing_tags = bool(params.get("show_swing_tags", False))
atr_display = str(params.get("atr_display", "Off"))
maximum_fibs = min(50, max(1, int(params.get("maximum_fibs", 10))))
show_bullish = bool(params.get("show_bullish", True))
show_bearish = bool(params.get("show_bearish", True))
show_labels = bool(params.get("show_labels", False))
show_prices = bool(params.get("show_prices", False))
extension_bars = min(400, max(0, int(params.get("extension_bars", 30))))
line_width = min(4, max(1, int(params.get("line_width", 1))))
line_style = str(params.get("line_style", "Solid"))
label_size = str(params.get("label_size", "Small"))
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

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

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

atr = np.full(n, np.nan, dtype=float)
if n >= atr_length:
    atr[atr_length - 1] = float(np.mean(true_range[:atr_length]))
    for index in range(atr_length, n):
        atr[index] = (atr[index - 1] * (atr_length - 1) + true_range[index]) / atr_length

def nz(value):
    return float(value) if np.isfinite(value) else 0.0

def fib_level(direction, high_price, low_price, ratio):
    price_range = high_price - low_price
    return high_price - price_range * ratio if direction == 1 else low_price + price_range * ratio

def make_fib(direction, high_price, high_bar, low_price, low_bar, confirm_ratio, retrace_extreme, created_bar):
    return {
        "direction": int(direction),
        "high": float(high_price),
        "high_bar": int(high_bar),
        "low": float(low_price),
        "low_bar": int(low_bar),
        "level_0382": fib_level(direction, high_price, low_price, 0.382),
        "level_05": fib_level(direction, high_price, low_price, 0.5),
        "level_0618": fib_level(direction, high_price, low_price, 0.618),
        "level_086": fib_level(direction, high_price, low_price, 0.86),
        "confirm_price": fib_level(direction, high_price, low_price, confirm_ratio),
        "created_bar": int(created_bar),
        "retrace_extreme": retrace_extreme,
        "confirm_bar": None,
    }

def structure_tag(is_high, price, previous_same, minimum_break):
    if previous_same is None:
        return "H" if is_high else "L"
    if is_high:
        if price > previous_same + minimum_break:
            return "HH"
        if price >= previous_same:
            return "EQH"
        return "LH"
    if price < previous_same - minimum_break:
        return "LL"
    if price <= previous_same:
        return "EQL"
    return "HL"

swings = []
drawn_fibs = []
last_fib_end = None

def record_swing(is_high, price, bar, confirm_bar):
    previous_same = swings[-2]["price"] if len(swings) >= 2 else None
    tag = structure_tag(is_high, price, previous_same, nz(atr[confirm_bar]) * minimum_break_atr)
    swings.append(
        {
            "is_high": bool(is_high),
            "price": float(price),
            "bar": int(bar),
            "confirm_bar": int(confirm_bar),
            "tag": tag,
        }
    )
    if len(swings) > 50:
        swings.pop(0)
    return swings[-1]

def big_enough(high_price, low_price, index):
    return high_price - low_price >= minimum_fib_atr * nz(atr[index])

def publish_fib(fib, visible, index, atr_method):
    global last_fib_end
    fib["confirm_bar"] = int(index)
    start_bar = min(fib["high_bar"], fib["low_bar"])
    end_bar = max(fib["high_bar"], fib["low_bar"]) if atr_method else index
    no_overlap = last_fib_end is None or start_bar >= last_fib_end
    direction_visible = show_bullish if fib["direction"] == 1 else show_bearish
    if no_overlap and visible and direction_visible:
        last_fib_end = int(end_bar)
        drawn_fibs.append(dict(fib))
        if len(drawn_fibs) > maximum_fibs:
            drawn_fibs.pop(0)

is_atr_method = structure_method in ("ATR_ZigZag", "ATR ZigZag")
zz_direction = 0
zz_extreme = None
zz_extreme_bar = None
zz_counter = None
zz_counter_bar = None
pending_bull = None
pending_bear = None
reversal_level = [None for _ in range(n)]
leg_flipped = [False for _ in range(n)]
zz_direction_series = [0 for _ in range(n)]

def resolve_pending(fib, index):
    if fib is None:
        return None
    bullish = fib["direction"] == 1
    status = 0
    if index > fib["created_bar"]:
        if (bullish and high[index] > fib["high"]) or ((not bullish) and low[index] < fib["low"]):
            status = -1
        else:
            if bullish:
                fib["retrace_extreme"] = min(float(fib["retrace_extreme"]), float(low[index]))
            else:
                fib["retrace_extreme"] = max(float(fib["retrace_extreme"]), float(high[index]))
    if status == 0:
        confirmed = fib["retrace_extreme"] <= fib["confirm_price"] if bullish else fib["retrace_extreme"] >= fib["confirm_price"]
        if confirmed:
            status = 1
    if status == 1:
        publish_fib(fib, True, index, True)
        return None
    if status == -1:
        return None
    return fib

for index in range(n):
    previous_direction = zz_direction
    if zz_direction == 0:
        zz_direction = 1
        zz_extreme = float(high[index])
        zz_extreme_bar = index
    else:
        rising = zz_direction == 1
        push_source = float(high[index] if rising else low[index])
        pull_source = float(low[index] if rising else high[index])
        extended = push_source > zz_extreme if rising else push_source < zz_extreme
        if extended:
            zz_extreme = push_source
            zz_extreme_bar = index
            zz_counter = None
            zz_counter_bar = None
        else:
            if zz_counter is None or (pull_source < zz_counter if rising else pull_source > zz_counter):
                zz_counter = pull_source
                zz_counter_bar = index
            if np.isfinite(atr[index]) and abs(zz_extreme - zz_counter) >= atr[index] * atr_multiplier:
                new_swing = record_swing(rising, zz_extreme, zz_extreme_bar, index)
                retrace_so_far = float(zz_counter)
                zz_direction = -zz_direction
                zz_extreme = float(zz_counter)
                zz_extreme_bar = int(zz_counter_bar)
                zz_counter = None
                zz_counter_bar = None
                if is_atr_method and len(swings) >= 2:
                    start = swings[-2]
                    end = new_swing
                    sized = big_enough(max(start["price"], end["price"]), min(start["price"], end["price"]), index)
                    bullish_leg = end["tag"] == "HH" and start["tag"] in ("LL", "HL", "EQL")
                    bearish_leg = end["tag"] == "LL" and start["tag"] in ("HH", "LH", "EQH")
                    if sized and show_bullish and end["is_high"] and bullish_leg:
                        pending_bull = make_fib(1, end["price"], end["bar"], start["price"], start["bar"], atr_confirmation, retrace_so_far, index)
                    elif sized and show_bearish and (not end["is_high"]) and bearish_leg:
                        pending_bear = make_fib(-1, start["price"], start["bar"], end["price"], end["bar"], atr_confirmation, retrace_so_far, index)
    leg_flipped[index] = previous_direction != 0 and zz_direction != previous_direction
    zz_direction_series[index] = int(zz_direction)
    if zz_direction == 1 and zz_extreme is not None and np.isfinite(atr[index]):
        reversal_level[index] = float(zz_extreme - atr[index] * atr_multiplier)
    elif zz_direction == -1 and zz_extreme is not None and np.isfinite(atr[index]):
        reversal_level[index] = float(zz_extreme + atr[index] * atr_multiplier)
    pending_bull = resolve_pending(pending_bull, index)
    pending_bear = resolve_pending(pending_bear, index)

def make_chain_move(direction, anchor, anchor_bar, extreme=None, extreme_bar=None, must_beat=None):
    return {
        "direction": int(direction),
        "anchor": float(anchor),
        "anchor_bar": int(anchor_bar),
        "extreme": None if extreme is None else float(extreme),
        "extreme_bar": None if extreme_bar is None else int(extreme_bar),
        "must_beat": None if must_beat is None else float(must_beat),
    }

def advance_chain(move, index):
    bullish = move["direction"] == 1
    push_source = float(high[index] if bullish else low[index])
    pull_source = float(low[index] if bullish else high[index])
    formed = False
    if move["extreme"] is not None and (push_source <= move["extreme"] if bullish else push_source >= move["extreme"]):
        minimum_break = nz(atr[index]) * minimum_break_atr
        beyond_previous = move["must_beat"] is None or (
            move["extreme"] > move["must_beat"] + minimum_break
            if bullish
            else move["extreme"] < move["must_beat"] - minimum_break
        )
        high_price = move["extreme"] if bullish else move["anchor"]
        low_price = move["anchor"] if bullish else move["extreme"]
        confirmation_price = fib_level(move["direction"], high_price, low_price, chain_confirmation)
        formed = beyond_previous and (pull_source <= confirmation_price if bullish else pull_source >= confirmation_price)
    if not formed:
        if pull_source < move["anchor"] if bullish else pull_source > move["anchor"]:
            move["anchor"] = pull_source
            move["anchor_bar"] = index
            move["extreme"] = None
            move["extreme_bar"] = None
        elif move["extreme"] is None or (push_source > move["extreme"] if bullish else push_source < move["extreme"]):
            move["extreme"] = push_source
            move["extreme_bar"] = index
    return formed

if not is_atr_method and n > 0:
    chain_direction = 0
    chain_bull = make_chain_move(1, low[0], 0)
    chain_bear = make_chain_move(-1, high[0], 0)
    chain_last_fib = None
    since_extreme = None
    since_extreme_bar = None
    for index in range(1, n):
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
            fib = make_fib(
                move["direction"],
                move["extreme"] if bullish else move["anchor"],
                move["extreme_bar"] if bullish else move["anchor_bar"],
                move["anchor"] if bullish else move["extreme"],
                move["anchor_bar"] if bullish else move["extreme_bar"],
                chain_confirmation,
                None,
                index,
            )
            publish_fib(fib, big_enough(fib["high"], fib["low"], index), index, False)
            chain_last_fib = fib
            chain_direction = move["direction"]
            since_extreme = float(high[index] if bullish else low[index])
            since_extreme_bar = index
            next_move = make_chain_move(
                move["direction"],
                low[index] if bullish else high[index],
                index,
                must_beat=fib["high"] if bullish else fib["low"],
            )
            if bullish:
                chain_bull = next_move
            else:
                chain_bear = next_move
        elif chain_direction != 0 and chain_last_fib is not None:
            bullish = chain_direction == 1
            current_extreme = float(high[index] if bullish else low[index])
            if (current_extreme > since_extreme if bullish else current_extreme < since_extreme):
                since_extreme = current_extreme
                since_extreme_bar = index
            last_level_1 = chain_last_fib["low"] if bullish else chain_last_fib["high"]
            broken = low[index] < last_level_1 if bullish else high[index] > last_level_1
            if broken:
                chain_direction = 0
                if bullish:
                    chain_bear = make_chain_move(-1, since_extreme, since_extreme_bar, low[index], index)
                    chain_bull = make_chain_move(1, low[index], index)
                else:
                    chain_bull = make_chain_move(1, since_extreme, since_extreme_bar, high[index], index)
                    chain_bear = make_chain_move(-1, high[index], index)

plots = [
    {
        "name": "Price Guide",
        "title": False,
        "data": [float(value) if np.isfinite(value) else None for value in close],
        "color": "rgba(0,0,0,0)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    }
]
if atr_display == "Reversal Level":
    reversal_plot = []
    for index in range(n):
        if leg_flipped[index] or reversal_level[index] is None:
            reversal_plot.append(None)
        else:
            reversal_plot.append(
                {
                    "value": float(reversal_level[index]),
                    "color": "#26A69A" if zz_direction_series[index] == 1 else "#EF5350",
                }
            )
    plots.append(
        {
            "name": "ATR Reversal Level",
            "data": reversal_plot,
            "color": "#26A69A",
            "type": "line",
            "lineWidth": 1,
            "overlay": True,
        }
    )
elif atr_display == "ATR Bands":
    threshold = [float(atr[i] * atr_multiplier) if np.isfinite(atr[i]) else None for i in range(n)]
    plots.extend(
        [
            {
                "name": "Upper ATR Band",
                "data": [float(close[i] + threshold[i]) if threshold[i] is not None else None for i in range(n)],
                "color": "#787B86",
                "type": "line",
                "lineWidth": 1,
                "overlay": True,
            },
            {
                "name": "Lower ATR Band",
                "data": [float(close[i] - threshold[i]) if threshold[i] is not None else None for i in range(n)],
                "color": "#787B86",
                "type": "line",
                "lineWidth": 1,
                "overlay": True,
            },
        ]
    )

layers = []
if show_swings:
    for index in range(1, len(swings)):
        previous = swings[index - 1]
        current = swings[index]
        layers.append(
            {
                "type": "line",
                "startIndex": previous["bar"],
                "endIndex": current["bar"],
                "y1": previous["price"],
                "y2": current["price"],
                "color": "rgba(120,123,134,0.80)",
                "lineWidth": 1,
                "dashed": False,
                "extendRight": False,
            }
        )
    if swings and zz_extreme_bar is not None:
        layers.append(
            {
                "type": "line",
                "startIndex": swings[-1]["bar"],
                "endIndex": int(zz_extreme_bar),
                "y1": swings[-1]["price"],
                "y2": float(zz_extreme),
                "color": "#787B86",
                "lineWidth": 1,
                "dashed": True,
                "extendRight": False,
            }
        )

if show_swing_tags:
    for swing in swings:
        tag_color = "#26A69A" if swing["tag"] in ("HH", "HL") else "#EF5350" if swing["tag"] in ("LH", "LL") else "#787B86"
        layers.append(
            {
                "type": "label",
                "index": swing["bar"],
                "price": swing["price"],
                "text": swing["tag"],
                "color": tag_color,
                "textColor": "#FFFFFF",
                "fontSize": 10,
            }
        )

dashed = line_style != "Solid"
font_size = {"Tiny": 8, "Small": 10, "Normal": 12}.get(label_size, 10)
for fib in drawn_fibs:
    bullish = fib["direction"] == 1
    level_0 = fib["high"] if bullish else fib["low"]
    level_1 = fib["low"] if bullish else fib["high"]
    start_index = min(fib["high_bar"], fib["low_bar"])
    end_index = min(n - 1, fib["confirm_bar"] + extension_bars)
    levels = [
        ("0", level_0, level_0_color, True),
        ("0.382", fib["level_0382"], level_0382_color, show_0382),
        ("0.5", fib["level_05"], level_05_color, True),
        ("0.618", fib["level_0618"], level_0618_color, True),
        ("0.86", fib["level_086"], level_086_color, show_086),
        ("1", level_1, level_1_color, True),
    ]
    if show_gold_zone:
        layers.append(
            {
                "type": "zone",
                "startIndex": int(start_index),
                "endIndex": int(end_index),
                "top": float(max(fib["level_05"], fib["level_0618"])),
                "bottom": float(min(fib["level_05"], fib["level_0618"])),
                "fillColor": gold_zone_color,
                "borderColor": "rgba(0,0,0,0)",
                "borderSize": 0,
                "extendRight": False,
            }
        )
    for text_value, price, color, enabled in levels:
        if not enabled:
            continue
        layers.append(
            {
                "type": "line",
                "startIndex": int(start_index),
                "endIndex": int(end_index),
                "y1": float(price),
                "y2": float(price),
                "color": color,
                "lineWidth": int(line_width),
                "dashed": dashed,
                "extendRight": False,
            }
        )
        if show_labels:
            label_text = f"{text_value} ({price:.5f})" if show_prices else text_value
            layers.append(
                {
                    "type": "label",
                    "index": int(end_index),
                    "price": float(price),
                    "text": label_text,
                    "color": "rgba(0,0,0,0)",
                    "textColor": color,
                    "fontSize": font_size,
                }
            )

output = {"name": my_indicator_name, "plots": plots, "signals": [], "layers": layers[-800:]}
