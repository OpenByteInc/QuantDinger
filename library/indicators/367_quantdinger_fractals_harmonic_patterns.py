# ============================================================
# QuantDinger Fractals and Harmonic Patterns
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Fractals and Harmonic Patterns"
my_indicator_description = "Builds alternating fractal zigzags, classifies pivot structure, and detects twelve harmonic or geometric patterns."
indicator_timeframes = ["1m", "5m", "15m", "30m", "1H", "4H", "1D", "1W"]

# @param show_patterns bool true Show harmonic pattern labels
# @param show_bar_colors bool false Color confirmed zigzag pivot bars
# @param regular_fractal_filter bool false Require strictly rising/falling regular fractals
# @param show_pivot_labels bool false Show HH/LH/HL/LL labels
# @param higher_timeframe str 4H Higher timeframe values=1m,5m,15m,30m,1H,4H,1D,1W
# @param show_higher_timeframe_fractals bool false Filter pivots against higher-timeframe extremes
# @param repaint_higher_timeframe bool true Use developing higher-timeframe values
# @param show_channel_1 bool false Show all fractal points
# @param show_channel_2 bool false Show HH and LL points
# @param show_channel_3 bool false Show higher-timeframe HH and LL points
# @param show_zigzag bool true Show alternating zigzag

show_patterns = bool(params.get("show_patterns", True))
show_bar_colors = bool(params.get("show_bar_colors", False))
regular_fractal_filter = bool(params.get("regular_fractal_filter", False))
show_pivot_labels = bool(params.get("show_pivot_labels", False))
higher_timeframe = str(params.get("higher_timeframe", "4H"))
higher_timeframe = higher_timeframe if higher_timeframe in indicator_timeframes else "4H"
show_higher_timeframe_fractals = bool(params.get("show_higher_timeframe_fractals", False))
repaint_higher_timeframe = bool(params.get("repaint_higher_timeframe", True))
show_channel_1 = bool(params.get("show_channel_1", False))
show_channel_2 = bool(params.get("show_channel_2", False))
show_channel_3 = bool(params.get("show_channel_3", False))
show_zigzag = bool(params.get("show_zigzag", True))

timeframe_seconds = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800}


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _normalize(frame):
    result = frame.copy().reset_index(drop=True)
    for column in ("time", "open", "high", "low", "close", "volume"):
        if column not in result.columns:
            result[column] = np.nan if column == "volume" else 0.0
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
    times = result["time"].to_numpy(dtype=float)
    if len(times) and max(abs(float(value)) for value in times) > 10_000_000_000:
        result["time"] = times / 1000.0
    return result


def _shift_back(values, bars=2):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) > bars:
        result[:-bars] = values[bars:]
    return result


df = df.copy()
base = _normalize(df)
opened = base["open"].to_numpy(dtype=float)
high = base["high"].to_numpy(dtype=float)
low = base["low"].to_numpy(dtype=float)
close = base["close"].to_numpy(dtype=float)
times = base["time"].to_numpy(dtype=float)
n = len(base)

top_fractal = np.zeros(n, dtype=bool)
bottom_fractal = np.zeros(n, dtype=bool)
for index in range(4, n):
    if regular_fractal_filter:
        top_fractal[index] = high[index - 4] < high[index - 3] < high[index - 2] and high[index - 2] > high[index - 1] > high[index]
        bottom_fractal[index] = low[index - 4] > low[index - 3] > low[index - 2] and low[index - 2] < low[index - 1] < low[index]
    else:
        top_fractal[index] = high[index - 4] < high[index - 2] and high[index - 3] <= high[index - 2] and high[index - 2] >= high[index - 1] and high[index - 2] > high[index]
        bottom_fractal[index] = low[index - 4] > low[index - 2] and low[index - 3] >= low[index - 2] and low[index - 2] <= low[index - 1] and low[index - 2] < low[index]

higher_high = np.zeros(n, dtype=bool)
lower_high = np.zeros(n, dtype=bool)
higher_low = np.zeros(n, dtype=bool)
lower_low = np.zeros(n, dtype=bool)
latest_top_values = []
latest_bottom_values = []
last_higher_high_value = np.nan
last_lower_low_value = np.nan
for index in range(n):
    if top_fractal[index]:
        latest_top_values.append(float(high[index - 2]))
        if len(latest_top_values) >= 3:
            current, previous, older = latest_top_values[-1], latest_top_values[-2], latest_top_values[-3]
            higher_high[index] = previous < current and older < current
            lower_high[index] = previous > current and older > current
        if higher_high[index]:
            last_higher_high_value = high[index - 2]
    if bottom_fractal[index]:
        latest_bottom_values.append(float(low[index - 2]))
        if len(latest_bottom_values) >= 3:
            current, previous, older = latest_bottom_values[-1], latest_bottom_values[-2], latest_bottom_values[-3]
            higher_low[index] = previous < current and older < current
            lower_low[index] = previous > current and older > current
        if lower_low[index]:
            last_lower_low_value = low[index - 2]

htf_available = True
aligned_htf_high = np.full(n, np.nan, dtype=float)
aligned_htf_low = np.full(n, np.nan, dtype=float)
need_higher_timeframe = show_higher_timeframe_fractals or show_channel_3
if need_higher_timeframe:
    raw = ctx.raw_series(higher_timeframe)
    if raw is None or len(raw) == 0:
        htf_available = False
    else:
        higher_frame = _normalize(raw)
        higher_times = higher_frame["time"].to_numpy(dtype=float)
        higher_high_values = higher_frame["high"].to_numpy(dtype=float)
        higher_low_values = higher_frame["low"].to_numpy(dtype=float)
        higher_seconds = timeframe_seconds[higher_timeframe]
        chart_name = str(ctx.timeframe or "1H")
        aliases = {"1": "1m", "5": "5m", "15": "15m", "30": "30m", "60": "1H", "240": "4H", "D": "1D", "W": "1W"}
        chart_seconds = timeframe_seconds.get(aliases.get(chart_name, chart_name), 3600)
        for index in range(n):
            if repaint_higher_timeframe:
                source_index = int(np.searchsorted(higher_times, times[index], side="right") - 1)
            else:
                source_index = int(np.searchsorted(higher_times + higher_seconds, times[index] + chart_seconds, side="right") - 1)
            if 0 <= source_index < len(higher_frame):
                aligned_htf_high[index] = higher_high_values[source_index]
                aligned_htf_low[index] = higher_low_values[source_index]

higher_timeframe_top = np.zeros(n, dtype=bool)
higher_timeframe_bottom = np.zeros(n, dtype=bool)
last_higher_high = np.nan
last_lower_low = np.nan
for index in range(n):
    if higher_high[index]:
        last_higher_high = high[index - 2]
    if lower_low[index]:
        last_lower_low = low[index - 2]
    higher_timeframe_top[index] = higher_high[index] and np.all(np.isfinite([last_higher_high, aligned_htf_high[index]])) and last_higher_high >= aligned_htf_high[index]
    higher_timeframe_bottom[index] = lower_low[index] and np.all(np.isfinite([last_lower_low, aligned_htf_low[index]])) and last_lower_low <= aligned_htf_low[index]

selected_top = higher_timeframe_top if show_higher_timeframe_fractals else top_fractal
selected_bottom = higher_timeframe_bottom if show_higher_timeframe_fractals else bottom_fractal
zigzag = np.full(n, np.nan, dtype=float)
zigzag_is_top = np.zeros(n, dtype=bool)
last_top_index = -1
last_bottom_index = -1
for index in range(n):
    prior_top, prior_bottom = last_top_index, last_bottom_index
    if selected_top[index] and prior_bottom > prior_top:
        zigzag[index] = high[index - 2]
        zigzag_is_top[index] = True
    elif selected_bottom[index] and prior_top > prior_bottom:
        zigzag[index] = low[index - 2]
    if selected_top[index]:
        last_top_index = index
    if selected_bottom[index]:
        last_bottom_index = index


def _pattern_flags(x, a, b, c, d, mode):
    result = {name: False for name in ("AB=CD", "Bat", "Alt Bat", "Butterfly", "Gartley", "Crab", "Shark", "5-O", "Wolf Wave", "Head & Shoulders", "Contracting Triangle", "Expanding Triangle")}
    denominators = (abs(x - a), abs(a - b), abs(b - c))
    if not np.all(np.isfinite([x, a, b, c, d])) or any(value == 0.0 for value in denominators):
        return result
    xab = abs(b - a) / denominators[0]
    xad = abs(a - d) / denominators[0]
    abc = abs(b - c) / denominators[1]
    bcd = abs(c - d) / denominators[2]
    direction = d < c if mode == 1 else d > c
    result["AB=CD"] = 0.382 <= abc <= 0.886 and 1.13 <= bcd <= 2.618 and direction
    result["Bat"] = 0.382 <= xab <= 0.5 and 0.382 <= abc <= 0.886 and 1.618 <= bcd <= 2.618 and xad <= 0.886 and direction
    result["Alt Bat"] = xab <= 0.382 and 0.382 <= abc <= 0.886 and 2.0 <= bcd <= 3.618 and xad <= 1.13 and direction
    result["Butterfly"] = xab <= 0.786 and 0.382 <= abc <= 0.886 and 1.618 <= bcd <= 2.618 and 1.27 <= xad <= 1.618 and direction
    result["Gartley"] = 0.5 <= xab <= 0.618 and 0.382 <= abc <= 0.886 and 1.13 <= bcd <= 2.618 and 0.75 <= xad <= 0.875 and direction
    result["Crab"] = 0.75 <= xab <= 0.875 and 0.382 <= abc <= 0.886 and 2.0 <= bcd <= 3.618 and 1.5 <= xad <= 1.625 and direction
    result["Shark"] = 0.5 <= xab <= 0.875 and 1.13 <= abc <= 1.618 and 1.27 <= bcd <= 2.24 and 0.88 <= xad <= 1.13 and direction
    result["5-O"] = 1.13 <= xab <= 1.618 and 1.618 <= abc <= 2.24 and 0.5 <= bcd <= 0.625 and 0.0 <= xad <= 0.236 and direction
    result["Wolf Wave"] = 1.27 <= xab <= 1.618 and 0.0 <= abc <= 5.0 and 1.27 <= bcd <= 1.618 and 0.0 <= xad <= 5.0 and direction
    result["Head & Shoulders"] = 2.0 <= xab <= 10.0 and 0.9 <= abc <= 1.1 and 0.236 <= bcd <= 0.88 and 0.9 <= xad <= 1.1 and direction
    result["Contracting Triangle"] = 0.382 <= xab <= 0.618 and 0.382 <= abc <= 0.618 and 0.382 <= bcd <= 0.618 and 0.236 <= xad <= 0.764 and direction
    result["Expanding Triangle"] = 1.236 <= xab <= 1.618 and 1.0 <= abc <= 1.618 and 1.236 <= bcd <= 2.0 and 2.0 <= xad <= 2.236 and direction
    return result


pattern_names = ("AB=CD", "Bat", "Alt Bat", "Butterfly", "Gartley", "Crab", "Shark", "5-O", "Wolf Wave", "Head & Shoulders", "Contracting Triangle", "Expanding Triangle")
bull_pattern_masks = {name: np.zeros(n, dtype=bool) for name in pattern_names}
bear_pattern_masks = {name: np.zeros(n, dtype=bool) for name in pattern_names}
previous_bull_state = {name: False for name in pattern_names}
previous_bear_state = {name: False for name in pattern_names}
zigzag_values = []
for index in range(n):
    if np.isfinite(zigzag[index]):
        zigzag_values.append(float(zigzag[index]))
    if len(zigzag_values) >= 5:
        x, a, b, c, d = zigzag_values[-5:]
        bull_state = _pattern_flags(x, a, b, c, d, 1)
        bear_state = _pattern_flags(x, a, b, c, d, -1)
    else:
        bull_state = {name: False for name in pattern_names}
        bear_state = {name: False for name in pattern_names}
    for name in pattern_names:
        bull_pattern_masks[name][index] = bull_state[name] and not previous_bull_state[name]
        bear_pattern_masks[name][index] = bear_state[name] and not previous_bear_state[name]
        previous_bull_state[name] = bull_state[name]
        previous_bear_state[name] = bear_state[name]

top_event = np.zeros(n, dtype=bool)
bottom_event = np.zeros(n, dtype=bool)
hh_event = np.zeros(n, dtype=bool)
lh_event = np.zeros(n, dtype=bool)
hl_event = np.zeros(n, dtype=bool)
ll_event = np.zeros(n, dtype=bool)
zigzag_plot = np.full(n, np.nan, dtype=float)
channel1_top = np.full(n, np.nan, dtype=float)
channel1_bottom = np.full(n, np.nan, dtype=float)
channel2_top = np.full(n, np.nan, dtype=float)
channel2_bottom = np.full(n, np.nan, dtype=float)
channel3_top = np.full(n, np.nan, dtype=float)
channel3_bottom = np.full(n, np.nan, dtype=float)
bar_colors = [None] * n
bull_pattern_text = [""] * n
bear_pattern_text = [""] * n
for index in range(2, n):
    pivot = index - 2
    top_event[pivot] = top_fractal[index]
    bottom_event[pivot] = bottom_fractal[index]
    hh_event[pivot] = higher_high[index]
    lh_event[pivot] = lower_high[index]
    hl_event[pivot] = higher_low[index]
    ll_event[pivot] = lower_low[index]
    if np.isfinite(zigzag[index]):
        zigzag_plot[pivot] = zigzag[index]
        if show_bar_colors:
            bar_colors[pivot] = "#FF0000" if zigzag_is_top[index] else "#00FF00"
    if top_fractal[index]: channel1_top[pivot] = high[pivot]
    if bottom_fractal[index]: channel1_bottom[pivot] = low[pivot]
    if higher_high[index]: channel2_top[pivot] = high[pivot]
    if lower_low[index]: channel2_bottom[pivot] = low[pivot]
    if higher_timeframe_top[index]: channel3_top[pivot] = high[pivot]
    if higher_timeframe_bottom[index]: channel3_bottom[pivot] = low[pivot]
    bull_hits = [name for name in pattern_names if bull_pattern_masks[name][index]]
    bear_hits = [name for name in pattern_names if bear_pattern_masks[name][index]]
    if bull_hits: bull_pattern_text[pivot] = "Bull: " + ", ".join(bull_hits)
    if bear_hits: bear_pattern_text[pivot] = "Bear: " + ", ".join(bear_hits)


zigzag_visible = np.full(n, np.nan, dtype=float)
zigzag_indices = np.flatnonzero(np.isfinite(zigzag_plot))
if len(zigzag_indices) >= 2:
    zigzag_visible = np.interp(
        np.arange(n, dtype=float),
        zigzag_indices.astype(float),
        zigzag_plot[zigzag_indices],
        left=np.nan,
        right=np.nan,
    )


def _events(mask, prices, text, color, event_type):
    return {"type": event_type, "name": text, "data": [{"active": True, "price": float(prices[index]), "text": text, "color": color} if active else None for index, active in enumerate(mask)], "color": color, "renderMode": "events"}


def _text_events(texts, prices, color, event_type):
    return {"type": event_type, "name": event_type.replace("_", " ").title(), "data": [{"active": True, "price": float(prices[index]), "text": texts[index], "color": color} if texts[index] else None for index in range(n)], "color": color, "renderMode": "events"}


def _limited_event_masks(masks, recent_bars=120, total_cap=14, minimum_gap=4):
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


display_top_event, display_bottom_event = _limited_event_masks([top_event, bottom_event])


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "ZigZag", "title": show_zigzag, "data": _finite(zigzag_visible) if show_zigzag else [None] * n, "color": "#BDBDBD", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Channel 1 Top", "title": show_channel_1, "data": _finite(channel1_top) if show_channel_1 else [None] * n, "color": "#000000", "type": "line", "overlay": True},
        {"name": "Channel 1 Bottom", "title": show_channel_1, "data": _finite(channel1_bottom) if show_channel_1 else [None] * n, "color": "#000000", "type": "line", "overlay": True},
        {"name": "Channel 2 Top", "title": show_channel_2, "data": _finite(channel2_top) if show_channel_2 else [None] * n, "color": "#808080", "type": "line", "overlay": True},
        {"name": "Channel 2 Bottom", "title": show_channel_2, "data": _finite(channel2_bottom) if show_channel_2 else [None] * n, "color": "#808080", "type": "line", "overlay": True},
        {"name": "Channel 3 Top", "title": show_channel_3, "data": _finite(channel3_top) if show_channel_3 else [None] * n, "color": "#C0C0C0", "type": "line", "overlay": True},
        {"name": "Channel 3 Bottom", "title": show_channel_3, "data": _finite(channel3_bottom) if show_channel_3 else [None] * n, "color": "#C0C0C0", "type": "line", "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "overlay": True},
    ],
    "signals": [
        _events(display_top_event, high, "Top Fractal", "#DC2626", "sell"), _events(display_bottom_event, low, "Bottom Fractal", "#16A34A", "buy"),
        _events(hh_event & show_pivot_labels, high, "HH", "#DC2626", "sell"), _events(lh_event & show_pivot_labels, high, "LH", "#DC2626", "sell"),
        _events(hl_event & show_pivot_labels, low, "HL", "#16A34A", "buy"), _events(ll_event & show_pivot_labels, low, "LL", "#16A34A", "buy"),
        _text_events(bull_pattern_text if show_patterns else [""] * n, low, "#16A34A", "buy"),
        _text_events(bear_pattern_text if show_patterns else [""] * n, high, "#DC2626", "sell"),
    ],
    "layers": [],
    "tables": [{"title": "Higher Timeframe", "position": "top-right", "compact": True, "rows": [["Status", "Requested data unavailable"]]}] if need_higher_timeframe and not htf_available else [],
    "bar_colors": bar_colors, "background_colors": [None] * n,
    "calculatedVars": {
        "topFractal": top_fractal.tolist(), "bottomFractal": bottom_fractal.tolist(), "higherHigh": higher_high.tolist(), "lowerHigh": lower_high.tolist(), "higherLow": higher_low.tolist(), "lowerLow": lower_low.tolist(),
        "alignedHtfHigh": _finite(aligned_htf_high), "alignedHtfLow": _finite(aligned_htf_low), "higherTimeframeTop": higher_timeframe_top.tolist(), "higherTimeframeBottom": higher_timeframe_bottom.tolist(),
        "selectedTop": selected_top.tolist(), "selectedBottom": selected_bottom.tolist(), "zigzag": _finite(zigzag), "zigzagIsTop": zigzag_is_top.tolist(),
        **{"bull" + name.replace(" ", "").replace("&", "And").replace("=", "Eq").replace("-", ""): values.tolist() for name, values in bull_pattern_masks.items()},
        **{"bear" + name.replace(" ", "").replace("&", "And").replace("=", "Eq").replace("-", ""): values.tolist() for name, values in bear_pattern_masks.items()},
    },
}
