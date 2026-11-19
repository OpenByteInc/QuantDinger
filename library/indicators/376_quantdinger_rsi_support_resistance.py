# ============================================================
# QuantDinger RSI Support and Resistance
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger RSI Support and Resistance"
my_indicator_description = "Translates RSI threshold crossings into persistent price zones, reverse-RSI targets, and an optional price-scaled oscillator overlay."

# @param source str Price RSI source values=Price,OBV
# @param rsi_length int 14 RSI length range=1:500:1
# @param overbought int 70 Overbought threshold range=50:100:1
# @param bull_zone int 60 Bull zone threshold range=50:65:1
# @param bear_zone int 40 Bear zone threshold range=35:50:1
# @param oversold int 30 Oversold threshold range=1:50:1
# @param bullish_color str #008080 Bullish color
# @param neutral_color str #808080 Neutral color
# @param bearish_color str #FF0000 Bearish color
# @param show_support_resistance bool true Show RSI support/resistance
# @param show_zone_fills bool true Show zone fills
# @param mark_extremes bool false Mark overbought/oversold bars
# @param show_reverse_labels bool true Show reverse-RSI labels
# @param reverse_label_size str Small Reverse label size values=Tiny,Small,Normal
# @param show_reverse_bands bool false Show reverse-RSI bands
# @param show_rsi_overlay bool true Show RSI price overlay
# @param overlay_placement str Top Overlay placement values=Top,Bottom
# @param overlay_height int 7 Overlay height control range=3:10:1
# @param overlay_length int 200 Overlay display length range=10:495:1
# @param minimum_tick float 0.00000001 Instrument minimum tick range=0.00000001:100:0.00000001

source_name = str(params.get("source", "Price"))
source_name = source_name if source_name in ("Price", "OBV") else "Price"
rsi_length = min(500, max(1, int(params.get("rsi_length", 14))))
overbought = min(100, max(50, int(params.get("overbought", 70))))
bull_zone = min(65, max(50, int(params.get("bull_zone", 60))))
bear_zone = min(50, max(35, int(params.get("bear_zone", 40))))
oversold = min(50, max(1, int(params.get("oversold", 30))))
bullish_color = str(params.get("bullish_color", "#008080"))
neutral_color = str(params.get("neutral_color", "#808080"))
bearish_color = str(params.get("bearish_color", "#FF0000"))
show_support_resistance = bool(params.get("show_support_resistance", True))
show_zone_fills = bool(params.get("show_zone_fills", True))
mark_extremes = bool(params.get("mark_extremes", False))
show_reverse_labels = bool(params.get("show_reverse_labels", True))
reverse_label_size = str(params.get("reverse_label_size", "Small"))
reverse_label_size = reverse_label_size if reverse_label_size in ("Tiny", "Small", "Normal") else "Small"
show_reverse_bands = bool(params.get("show_reverse_bands", False))
show_rsi_overlay = bool(params.get("show_rsi_overlay", True))
overlay_placement = str(params.get("overlay_placement", "Top"))
overlay_placement = overlay_placement if overlay_placement in ("Top", "Bottom") else "Top"
overlay_height_input = min(10, max(3, int(params.get("overlay_height", 7))))
overlay_length = min(495, max(10, int(params.get("overlay_length", 200))))
minimum_tick = min(100.0, max(0.00000001, float(params.get("minimum_tick", 0.00000001))))


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


def _cross_over(first, second):
    result = np.zeros(len(first), dtype=bool)
    for index in range(1, len(first)):
        if np.all(np.isfinite([first[index], first[index - 1], second[index], second[index - 1]])):
            result[index] = first[index] > second[index] and first[index - 1] <= second[index - 1]
    return result


def _cross_under(first, second):
    result = np.zeros(len(first), dtype=bool)
    for index in range(1, len(first)):
        if np.all(np.isfinite([first[index], first[index - 1], second[index], second[index - 1]])):
            result[index] = first[index] < second[index] and first[index - 1] >= second[index - 1]
    return result


def _stable_visible(values):
    result = np.full(len(values), np.nan, dtype=float)
    if not show_support_resistance:
        return result
    for index in range(1, len(values)):
        if values[index] > 0.0 and values[index] == values[index - 1]:
            result[index] = values[index]
    return result


def _reverse_rsi(values, target, gains, losses, oscillator_values):
    result = np.full(len(values), np.nan, dtype=float)
    ratio = target / max(100.0 - target, 0.001)
    for index in range(len(values)):
        if not np.all(np.isfinite([values[index], gains[index], losses[index], oscillator_values[index]])):
            continue
        if target > oscillator_values[index]:
            required_gain = ratio * losses[index] * rsi_length - gains[index] * (rsi_length - 1.0)
            result[index] = values[index] + required_gain if required_gain > 0.0 else values[index]
        elif target < oscillator_values[index]:
            required_loss = gains[index] / max(ratio, 0.0001) * rsi_length - losses[index] * (rsi_length - 1.0)
            result[index] = values[index] - required_loss if required_loss > 0.0 else values[index]
        else:
            result[index] = values[index]
    return result


def _event(mask, prices, text, color, event_type):
    return {"type": event_type, "name": text, "text": text, "data": [{"active": True, "price": float(prices[index]), "text": text, "color": color} if mask[index] and np.isfinite(prices[index]) else None for index in range(len(mask))], "color": color, "renderMode": "events", "overlay": True}


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = np.nan if column == "volume" else 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].fillna(0.0).to_numpy(dtype=float)
n = len(df)

obv = np.zeros(n, dtype=float)
for index in range(1, n):
    direction = 1.0 if close[index] > close[index - 1] else -1.0 if close[index] < close[index - 1] else 0.0
    obv[index] = obv[index - 1] + direction * volume[index]
rsi_source = close if source_name == "Price" else obv
source_change = np.diff(rsi_source, prepend=np.nan)
average_gain = _rma(np.where(np.isfinite(source_change), np.maximum(source_change, 0.0), np.nan), rsi_length)
average_loss = _rma(np.where(np.isfinite(source_change), np.maximum(-source_change, 0.0), np.nan), rsi_length)
oscillator = np.full(n, np.nan, dtype=float)
for index in range(n):
    if np.all(np.isfinite([average_gain[index], average_loss[index]])):
        oscillator[index] = 100.0 if average_loss[index] == 0.0 else 0.0 if average_gain[index] == 0.0 else 100.0 - 100.0 / (1.0 + average_gain[index] / average_loss[index])

thresholds = {
    "overbought": np.full(n, float(overbought)), "bull": np.full(n, float(bull_zone)),
    "bear": np.full(n, float(bear_zone)), "oversold": np.full(n, float(oversold)),
}
cross_over_ob = _cross_over(oscillator, thresholds["overbought"])
cross_under_ob = _cross_under(oscillator, thresholds["overbought"])
cross_over_bull = _cross_over(oscillator, thresholds["bull"])
cross_under_bull = _cross_under(oscillator, thresholds["bull"])
cross_over_bear = _cross_over(oscillator, thresholds["bear"])
cross_under_bear = _cross_under(oscillator, thresholds["bear"])
cross_over_os = _cross_over(oscillator, thresholds["oversold"])
cross_under_os = _cross_under(oscillator, thresholds["oversold"])

average_high = (high + close) / 2.0
average_low = (low + close) / 2.0
level_ob_over = np.zeros(n, dtype=float)
level_ob_under = np.zeros(n, dtype=float)
level_bull = np.zeros(n, dtype=float)
level_bear = np.zeros(n, dtype=float)
level_os_over = np.zeros(n, dtype=float)
level_os_under = np.zeros(n, dtype=float)
states = [0.0] * 6
for index in range(n):
    if cross_over_ob[index]:
        states[0] = average_high[index]
    if cross_under_ob[index]:
        states[1] = average_low[index]
    if cross_over_bull[index]:
        states[2] = average_high[index]
    if cross_under_bull[index]:
        states[2] = average_low[index]
    if cross_over_bear[index]:
        states[3] = average_high[index]
    if cross_under_bear[index]:
        states[3] = average_low[index]
    if cross_over_os[index]:
        states[4] = average_high[index]
    if cross_under_os[index]:
        states[5] = average_low[index]
    level_ob_over[index], level_ob_under[index], level_bull[index], level_bear[index], level_os_over[index], level_os_under[index] = states

visible_ob_over = _stable_visible(level_ob_over)
visible_ob_under = _stable_visible(level_ob_under)
visible_bull = _stable_visible(level_bull)
visible_bear = _stable_visible(level_bear)
visible_os_over = _stable_visible(level_os_over)
visible_os_under = _stable_visible(level_os_under)

price_cross_above_ob = _cross_over(close, level_ob_over)
price_cross_below_os = _cross_under(close, level_os_under)
price_ob = _reverse_rsi(rsi_source, float(overbought), average_gain, average_loss, oscillator)
price_bull = _reverse_rsi(rsi_source, float(bull_zone), average_gain, average_loss, oscillator)
price_mid = _reverse_rsi(rsi_source, 50.0, average_gain, average_loss, oscillator)
price_bear = _reverse_rsi(rsi_source, float(bear_zone), average_gain, average_loss, oscillator)
price_os = _reverse_rsi(rsi_source, float(oversold), average_gain, average_loss, oscillator)

bull_zone_cloud = [{"upper": float(max(visible_ob_over[index], visible_bull[index])), "lower": float(min(visible_ob_over[index], visible_bull[index])), "color": bullish_color, "opacity": 0.10} if show_zone_fills and np.all(np.isfinite([visible_ob_over[index], visible_bull[index]])) else None for index in range(n)]
bear_zone_cloud = [{"upper": float(max(visible_os_under[index], visible_bear[index])), "lower": float(min(visible_os_under[index], visible_bear[index])), "color": bearish_color, "opacity": 0.10} if show_zone_fills and np.all(np.isfinite([visible_os_under[index], visible_bear[index]])) else None for index in range(n)]
reverse_bull_cloud = [{"upper": float(max(price_ob[index], price_bull[index])), "lower": float(min(price_ob[index], price_bull[index])), "color": bullish_color, "opacity": 0.10} if show_reverse_bands and np.all(np.isfinite([price_ob[index], price_bull[index]])) else None for index in range(n)]
reverse_bear_cloud = [{"upper": float(max(price_bear[index], price_os[index])), "lower": float(min(price_bear[index], price_os[index])), "color": bearish_color, "opacity": 0.10} if show_reverse_bands and np.all(np.isfinite([price_bear[index], price_os[index]])) else None for index in range(n)]

overlay_rsi = np.full(n, np.nan, dtype=float)
overlay_ob = overlay_bull = overlay_mid = overlay_bear = overlay_os = np.nan
overlay_start = -1
overlay_rate = np.nan
layers = []
if show_rsi_overlay and n:
    end_index = n - 1
    overlay_start = max(0, end_index - overlay_length)
    highest_price = float(np.max(high[max(0, n - overlay_length):n]))
    lowest_price = float(np.min(low[max(0, n - overlay_length):n]))
    oscillator_height = 11 - overlay_height_input
    if highest_price != 0.0:
        overlay_rate = (highest_price - lowest_price) / highest_price / oscillator_height / 100.0
        base = highest_price if overlay_placement == "Top" else lowest_price
        def mapped_level(value):
            return base * (1.0 + (value if overlay_placement == "Top" else -(100.0 - value)) * overlay_rate)
        overlay_ob, overlay_bull, overlay_mid, overlay_bear, overlay_os = [mapped_level(value) for value in (overbought, bull_zone, 50.0, bear_zone, oversold)]
        for index in range(overlay_start, n):
            if np.isfinite(oscillator[index]):
                overlay_rsi[index] = mapped_level(oscillator[index])
        for role, value, color, width in (
            ("Overlay Overbought", overlay_ob, bullish_color, 2), ("Overlay Bull Zone", overlay_bull, bullish_color, 1),
            ("Overlay Center", overlay_mid, neutral_color, 1), ("Overlay Bear Zone", overlay_bear, bearish_color, 1), ("Overlay Oversold", overlay_os, bearish_color, 2),
        ):
            layers.append({"type": "line", "role": role, "startIndex": overlay_start, "endIndex": end_index, "y1": float(value), "y2": float(value), "color": color, "lineWidth": width, "lineStyle": "dotted"})
        layers.append({"type": "zone", "role": "Overlay Bullish Zone", "startIndex": overlay_start, "endIndex": end_index, "top": float(max(overlay_ob, overlay_bull)), "bottom": float(min(overlay_ob, overlay_bull)), "fillColor": bullish_color, "opacity": 0.10, "borderColor": "rgba(0,0,0,0)", "borderSize": 0})
        layers.append({"type": "zone", "role": "Overlay Bearish Zone", "startIndex": overlay_start, "endIndex": end_index, "top": float(max(overlay_bear, overlay_os)), "bottom": float(min(overlay_bear, overlay_os)), "fillColor": bearish_color, "opacity": 0.10, "borderColor": "rgba(0,0,0,0)", "borderSize": 0})

reverse_targets = (("OB", overbought, price_ob, bullish_color), ("Bull", bull_zone, price_bull, bullish_color), ("Mid", 50, price_mid, neutral_color), ("Bear", bear_zone, price_bear, bearish_color), ("OS", oversold, price_os, bearish_color))
if show_reverse_labels and source_name == "Price" and n:
    for role, target, values, color in reverse_targets:
        value = values[-1]
        if not np.isfinite(value):
            continue
        distance = value - close[-1]
        direction = "✓" if abs(distance) < minimum_tick else "↑" if distance > 0.0 else "↓"
        layers.append({"type": "label", "role": f"Reverse RSI {role}", "index": n + 1, "price": float(value), "text": f"RSI {target} {direction} {value:.8g}", "color": color, "textColor": color, "size": reverse_label_size.lower()})

overbought_mark = np.isfinite(oscillator) & (oscillator > overbought) & mark_extremes
oversold_mark = np.isfinite(oscillator) & (oscillator < oversold) & mark_extremes
overbought_entry = overbought_mark & ~np.r_[False, overbought_mark[:-1]]
oversold_entry = oversold_mark & ~np.r_[False, oversold_mark[:-1]]


def _limited_event_masks(masks, recent_bars=120, total_cap=8, minimum_gap=10):
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


display_overbought, display_oversold, display_above_ob, display_below_os = _limited_event_masks([
    overbought_entry, oversold_entry, price_cross_above_ob, price_cross_below_os,
])
signals = [
    _event(display_overbought, high, "RSI Overbought", bearish_color, "sell"),
    _event(display_oversold, low, "RSI Oversold", bullish_color, "buy"),
    _event(display_above_ob, close, "Above OB", bullish_color, "buy"),
    _event(display_below_os, close, "Below OS", bearish_color, "sell"),
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Bullish Momentum Zone", "title": False, "data": bull_zone_cloud, "color": bullish_color, "type": "cloud", "overlay": True},
        {"name": "Bearish Momentum Zone", "title": False, "data": bear_zone_cloud, "color": bearish_color, "type": "cloud", "overlay": True},
        {"name": "Overbought Crossover Level", "title": True, "data": _finite(visible_ob_over), "color": bullish_color, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Overbought Exit Level", "title": True, "data": _finite(visible_ob_under), "color": bullish_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Bullish Threshold Level", "title": True, "data": _finite(visible_bull), "color": bullish_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Bearish Threshold Level", "title": True, "data": _finite(visible_bear), "color": bearish_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Oversold Rebound Level", "title": True, "data": _finite(visible_os_over), "color": bearish_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Oversold Breakdown Level", "title": True, "data": _finite(visible_os_under), "color": bearish_color, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Reverse RSI Bullish Zone", "title": False, "data": reverse_bull_cloud, "color": bullish_color, "type": "cloud", "overlay": True},
        {"name": "Reverse RSI Bearish Zone", "title": False, "data": reverse_bear_cloud, "color": bearish_color, "type": "cloud", "overlay": True},
        {"name": "Reverse RSI Overbought", "title": True, "data": _finite(price_ob) if show_reverse_bands else [None] * n, "color": bullish_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Reverse RSI Bull", "title": True, "data": _finite(price_bull) if show_reverse_bands else [None] * n, "color": bullish_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Reverse RSI Mid", "title": True, "data": _finite(price_mid) if show_reverse_bands else [None] * n, "color": neutral_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Reverse RSI Bear", "title": True, "data": _finite(price_bear) if show_reverse_bands else [None] * n, "color": bearish_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Reverse RSI Oversold", "title": True, "data": _finite(price_os) if show_reverse_bands else [None] * n, "color": bearish_color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "RSI Price Overlay", "title": True, "data": _finite(overlay_rsi), "color": "#7E57C2", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": signals,
    "layers": layers,
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "obv": _finite(obv), "rsiSource": _finite(rsi_source), "sourceChange": _finite(source_change), "averageGain": _finite(average_gain), "averageLoss": _finite(average_loss), "oscillator": _finite(oscillator),
        "crossOverOverbought": cross_over_ob.tolist(), "crossUnderOverbought": cross_under_ob.tolist(), "crossOverBull": cross_over_bull.tolist(), "crossUnderBull": cross_under_bull.tolist(),
        "crossOverBear": cross_over_bear.tolist(), "crossUnderBear": cross_under_bear.tolist(), "crossOverOversold": cross_over_os.tolist(), "crossUnderOversold": cross_under_os.tolist(),
        "overboughtCrossoverLevel": _finite(level_ob_over), "overboughtExitLevel": _finite(level_ob_under), "bullZoneLevel": _finite(level_bull), "bearZoneLevel": _finite(level_bear), "oversoldReboundLevel": _finite(level_os_over), "oversoldBreakdownLevel": _finite(level_os_under),
        "visibleOverboughtCrossover": _finite(visible_ob_over), "visibleOverboughtExit": _finite(visible_ob_under), "visibleBullZone": _finite(visible_bull), "visibleBearZone": _finite(visible_bear), "visibleOversoldRebound": _finite(visible_os_over), "visibleOversoldBreakdown": _finite(visible_os_under),
        "priceCrossAboveObLevel": price_cross_above_ob.tolist(), "priceCrossBelowOsLevel": price_cross_below_os.tolist(),
        "reverseOverbought": _finite(price_ob), "reverseBull": _finite(price_bull), "reverseMid": _finite(price_mid), "reverseBear": _finite(price_bear), "reverseOversold": _finite(price_os),
        "overlayRsi": _finite(overlay_rsi), "overlayStart": int(overlay_start), "overlayRate": None if not np.isfinite(overlay_rate) else float(overlay_rate),
        "overlayLevels": [None if not np.isfinite(value) else float(value) for value in (overlay_ob, overlay_bull, overlay_mid, overlay_bear, overlay_os)],
    },
}
