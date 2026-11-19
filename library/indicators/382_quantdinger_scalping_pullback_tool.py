# ============================================================
# QuantDinger Scalping Pullback Tool
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Scalping Pullback Tool"
my_indicator_description = "Combines a price-action EMA channel, trend ribbon, fractal structure and pullback-recovery alerts for short-term chart analysis."

# @param pac_length int 34 Price-action channel length range=2:1000:1
# @param fast_ema_length int 89 Fast EMA length range=2:2000:1
# @param medium_ema_length int 200 Medium EMA length range=2:2000:1
# @param slow_ema_length int 600 Slow EMA length range=2:3000:1
# @param show_fast_ema bool true Show fast EMA
# @param show_medium_ema bool true Show medium EMA
# @param show_slow_ema bool false Show slow EMA
# @param show_hhll bool false Show HH/LH/HL/LL labels
# @param show_fractals bool true Show fractal markers
# @param ideal_fractals_only bool false Require regular five-bar fractals
# @param show_bar_colors bool true Color bars around the channel
# @param show_buy_sell bool true Show pullback recovery arrows
# @param lookback int 3 Pullback center-line lookback range=1:100:1
# @param delay_arrow bool false Wait for the next candle before showing an arrow
# @param show_trend_background bool true Show trend background
# @param use_heikin_ashi bool true Use Heikin Ashi values for calculations

pac_length = min(1000, max(2, int(params.get("pac_length", 34))))
fast_length = min(2000, max(2, int(params.get("fast_ema_length", 89))))
medium_length = min(2000, max(2, int(params.get("medium_ema_length", 200))))
slow_length = min(3000, max(2, int(params.get("slow_ema_length", 600))))
show_fast = bool(params.get("show_fast_ema", True))
show_medium = bool(params.get("show_medium_ema", True))
show_slow = bool(params.get("show_slow_ema", False))
show_hhll = bool(params.get("show_hhll", False))
show_fractals = bool(params.get("show_fractals", True))
ideal_only = bool(params.get("ideal_fractals_only", False))
show_bar_colors = bool(params.get("show_bar_colors", True))
show_buy_sell = bool(params.get("show_buy_sell", True))
lookback = min(100, max(1, int(params.get("lookback", 3))))
delay_arrow = bool(params.get("delay_arrow", False))
show_background = bool(params.get("show_trend_background", True))
use_heikin_ashi = bool(params.get("use_heikin_ashi", True))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    state = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            state = float(value) if not np.isfinite(state) else state + alpha * (float(value) - state)
            result[index] = state
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

ha_close_all = (opened + high + low + close) / 4.0
ha_open_all = np.full(n, np.nan, dtype=float)
for index in range(n):
    ha_open_all[index] = (opened[index] + close[index]) / 2.0 if index == 0 else (ha_open_all[index - 1] + ha_close_all[index - 1]) / 2.0
ha_high_all = np.maximum.reduce([high, ha_open_all, ha_close_all]) if n else np.asarray([], dtype=float)
ha_low_all = np.minimum.reduce([low, ha_open_all, ha_close_all]) if n else np.asarray([], dtype=float)

calc_open = ha_open_all if use_heikin_ashi else opened
calc_high = ha_high_all if use_heikin_ashi else high
calc_low = ha_low_all if use_heikin_ashi else low
calc_close = ha_close_all if use_heikin_ashi else close

fast_ema = _ema(calc_close, fast_length)
medium_ema = _ema(calc_close, medium_length)
slow_ema = _ema(calc_close, slow_length)
pac_close = _ema(calc_close, pac_length)
pac_low = _ema(calc_low, pac_length)
pac_high = _ema(calc_high, pac_length)
trend = np.zeros(n, dtype=int)
trend[(fast_ema > medium_ema) & (pac_low > medium_ema)] = 1
trend[(fast_ema < medium_ema) & (pac_high < medium_ema)] = -1

top_detected = np.zeros(n, dtype=bool)
bottom_detected = np.zeros(n, dtype=bool)
higher_high = np.zeros(n, dtype=bool)
lower_high = np.zeros(n, dtype=bool)
higher_low = np.zeros(n, dtype=bool)
lower_low = np.zeros(n, dtype=bool)
top_pivots = []
bottom_pivots = []
for index in range(4, n):
    if ideal_only:
        top = high[index - 4] < high[index - 3] < high[index - 2] and high[index - 2] > high[index - 1] > high[index]
        bottom = low[index - 4] > low[index - 3] > low[index - 2] and low[index - 2] < low[index - 1] < low[index]
    else:
        top = high[index - 4] < high[index - 2] and high[index - 3] <= high[index - 2] and high[index - 2] >= high[index - 1] and high[index - 2] > high[index]
        bottom = low[index - 4] > low[index - 2] and low[index - 3] >= low[index - 2] and low[index - 2] <= low[index - 1] and low[index - 2] < low[index]
    pivot = index - 2
    if top:
        top_detected[pivot] = True
        current = float(high[pivot])
        if len(top_pivots) >= 2:
            higher_high[pivot] = top_pivots[-1] < current and top_pivots[-2] < current
            lower_high[pivot] = top_pivots[-1] > current and top_pivots[-2] > current
        top_pivots.append(current)
    if bottom:
        bottom_detected[pivot] = True
        current = float(low[pivot])
        if len(bottom_pivots) >= 2:
            higher_low[pivot] = bottom_pivots[-1] < current and bottom_pivots[-2] < current
            lower_low[pivot] = bottom_pivots[-1] > current and bottom_pivots[-2] > current
        bottom_pivots.append(current)

bars_since_below = np.full(n, np.nan, dtype=float)
bars_since_above = np.full(n, np.nan, dtype=float)
last_below = -1
last_above = -1
for index in range(n):
    if calc_close[index] < pac_close[index]:
        last_below = index
    if calc_close[index] > pac_close[index]:
        last_above = index
    if last_below >= 0:
        bars_since_below[index] = index - last_below
    if last_above >= 0:
        bars_since_above[index] = index - last_above

exit_upper = (calc_open < pac_high) & (calc_close > pac_high) & np.isfinite(bars_since_below) & (bars_since_below <= lookback)
exit_lower = (calc_open > pac_low) & (calc_close < pac_low) & np.isfinite(bars_since_above) & (bars_since_above <= lookback)
buy_condition = (trend == 1) & exit_upper
sell_condition = (trend == -1) & exit_lower
trade_direction = np.zeros(n, dtype=int)
long_entry = np.zeros(n, dtype=bool)
short_entry = np.zeros(n, dtype=bool)
for index in range(n):
    previous = trade_direction[index - 1] if index else 0
    current = previous
    if previous == 1 and calc_close[index] < pac_close[index]:
        current = 0
    elif previous == -1 and calc_close[index] > pac_close[index]:
        current = 0
    elif previous == 0 and buy_condition[index]:
        current = 1
    elif previous == 0 and sell_condition[index]:
        current = -1
    trade_direction[index] = current
    long_entry[index] = previous == 0 and current == 1
    short_entry[index] = previous == 0 and current == -1

visible_long = long_entry.copy()
visible_short = short_entry.copy()
if delay_arrow and n:
    visible_long[-1] = False
    visible_short[-1] = False
if not show_buy_sell:
    visible_long[:] = False
    visible_short[:] = False

bar_colors = [None] * n
if show_bar_colors:
    bar_colors = ["#0000FF" if calc_close[index] > pac_high[index] else "#FF0000" if calc_close[index] < pac_low[index] else "#808080" for index in range(n)]
background_colors = [None] * n
if show_background:
    background_colors = ["rgba(0,128,0,0.10)" if value == 1 else "rgba(255,0,0,0.10)" if value == -1 else "rgba(255,255,0,0.10)" for value in trend]

pac_cloud = [{"upper": float(max(pac_high[index], pac_low[index])), "lower": float(min(pac_high[index], pac_low[index])), "color": "#808080", "opacity": 0.10} if np.all(np.isfinite([pac_high[index], pac_low[index]])) else None for index in range(n)]


def _event_data(mask, prices, text, color):
    return [{"active": True, "price": float(prices[index]), "text": text, "color": color} if mask[index] else None for index in range(n)]


def _limited_event_masks(masks, recent_bars=120, total_cap=16, minimum_gap=5):
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


display_top, display_bottom, display_hh, display_lh, display_hl, display_ll = _limited_event_masks([
    top_detected if show_fractals else np.zeros(n, bool),
    bottom_detected if show_fractals else np.zeros(n, bool),
    higher_high if show_hhll else np.zeros(n, bool),
    lower_high if show_hhll else np.zeros(n, bool),
    higher_low if show_hhll else np.zeros(n, bool),
    lower_low if show_hhll else np.zeros(n, bool),
])
recent_mask = np.arange(n) >= max(0, n - 120)
display_long = visible_long & recent_mask
display_short = visible_short & recent_mask


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "PAC Low", "title": True, "data": _finite(pac_low), "color": "#808080", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "PAC High", "title": True, "data": _finite(pac_high), "color": "#808080", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "PAC Close", "title": True, "data": _finite(pac_close), "color": "#FF0000", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "PAC Fill", "title": False, "data": pac_cloud, "color": "#808080", "type": "cloud", "overlay": True},
        {"name": "Fast EMA", "title": True, "data": _finite(fast_ema) if show_fast else [None] * n, "color": "#008000", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Medium EMA", "title": True, "data": _finite(medium_ema) if show_medium else [None] * n, "color": "#0000FF", "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "Slow EMA", "title": True, "data": _finite(slow_ema) if show_slow else [None] * n, "color": "#000000", "type": "line", "lineWidth": 4, "overlay": True},
    ],
    "signals": [
        {"type": "sell", "name": "Top Fractal", "text": "▼", "data": _event_data(display_top, high, "▼", "#FF0000"), "color": "#FF0000", "renderMode": "events", "overlay": True},
        {"type": "buy", "name": "Bottom Fractal", "text": "▲", "data": _event_data(display_bottom, low, "▲", "#00FF00"), "color": "#00FF00", "renderMode": "events", "overlay": True},
        {"type": "sell", "name": "Higher High", "text": "HH", "data": _event_data(display_hh, high, "HH", "#800000"), "color": "#800000", "renderMode": "events", "overlay": True},
        {"type": "sell", "name": "Lower High", "text": "LH", "data": _event_data(display_lh, high, "LH", "#800000"), "color": "#800000", "renderMode": "events", "overlay": True},
        {"type": "buy", "name": "Higher Low", "text": "HL", "data": _event_data(display_hl, low, "HL", "#008000"), "color": "#008000", "renderMode": "events", "overlay": True},
        {"type": "buy", "name": "Lower Low", "text": "LL", "data": _event_data(display_ll, low, "LL", "#008000"), "color": "#008000", "renderMode": "events", "overlay": True},
        {"type": "buy", "name": "Pullback Buy", "text": "BUY", "data": _event_data(display_long, low, "BUY", "#008000"), "color": "#008000", "renderMode": "events", "overlay": True},
        {"type": "sell", "name": "Pullback Sell", "text": "SELL", "data": _event_data(display_short, high, "SELL", "#800000"), "color": "#800000", "renderMode": "events", "overlay": True},
    ],
    "layers": [], "tables": [], "bar_colors": bar_colors, "background_colors": background_colors,
    "calculatedVars": {
        "calcOpen": _finite(calc_open), "calcHigh": _finite(calc_high), "calcLow": _finite(calc_low), "calcClose": _finite(calc_close),
        "fastEma": _finite(fast_ema), "mediumEma": _finite(medium_ema), "slowEma": _finite(slow_ema),
        "pacClose": _finite(pac_close), "pacLow": _finite(pac_low), "pacHigh": _finite(pac_high), "trend": trend.tolist(),
        "topFractal": top_detected.tolist(), "bottomFractal": bottom_detected.tolist(),
        "higherHigh": higher_high.tolist(), "lowerHigh": lower_high.tolist(), "higherLow": higher_low.tolist(), "lowerLow": lower_low.tolist(),
        "barsSinceBelow": _finite(bars_since_below), "barsSinceAbove": _finite(bars_since_above),
        "exitUpper": exit_upper.tolist(), "exitLower": exit_lower.tolist(), "buyCondition": buy_condition.tolist(), "sellCondition": sell_condition.tolist(),
        "tradeDirection": trade_direction.tolist(), "longEntry": long_entry.tolist(), "shortEntry": short_entry.tolist(),
    },
}
