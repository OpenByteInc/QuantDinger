# ============================================================
# QuantDinger Multimodal Trailing Stop
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Multimodal Trailing Stop"
my_indicator_description = "Runs ATR, Chandelier, Donchian, standard-deviation, or fixed-percent candidates through one ratcheting stop and safety-zone engine."
indicator_timeframe_param = "atr_timeframe"
indicator_timeframes = ["15m", "30m", "1H", "2H", "4H", "6H", "8H", "12H", "1D", "1W"]

# @param trailing_method str ATR-Supertrend Trailing method values=ATR-Supertrend,Chandelier,Donchian,StdDev-Channel,Fixed-Percent
# @param anchor_source str HL2 Anchor source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param atr_length int 10 ATR length range=1:10000:1
# @param atr_multiplier float 3.0 ATR multiplier range=0.1:100:0.1
# @param use_higher_atr bool false Use last-closed higher-timeframe ATR
# @param atr_timeframe str 4H ATR timeframe values=15m,30m,1H,2H,4H,6H,8H,12H,1D,1W
# @param chandelier_lookback int 22 Chandelier lookback range=1:10000:1
# @param chandelier_atr_length int 14 Chandelier ATR length range=1:10000:1
# @param chandelier_multiplier float 3.0 Chandelier multiplier range=0.1:100:0.1
# @param donchian_lookback int 20 Donchian lookback range=1:10000:1
# @param deviation_length int 20 Standard deviation length range=2:10000:1
# @param deviation_multiplier float 2.0 Standard deviation multiplier range=0.2:100:0.1
# @param fixed_percent float 5.0 Fixed trailing percent range=0.1:100:0.1
# @param show_safe_bands bool true Show colored safety bands
# @param show_band_borders bool true Show safety band borders
# @param safety_atr_length int 14 Safety ATR length range=1:10000:1
# @param safe_zone_multiplier float 2.0 Safe-zone ATR multiplier range=0.5:100:0.1
# @param up_color str #00897B Uptrend stop color
# @param down_color str #FF9800 Downtrend stop color
# @param show_flips bool true Show flip markers

method_aliases = {"ATR-Supertrend": "ATR", "Chandelier": "Chandelier", "Donchian": "Donchian", "StdDev-Channel": "StdDev", "Fixed-Percent": "Fixed"}
trailing_method = method_aliases.get(str(params.get("trailing_method", "ATR-Supertrend")), "ATR")
anchor_source = str(params.get("anchor_source", "HL2"))
atr_length = min(10000, max(1, int(params.get("atr_length", 10))))
atr_multiplier = min(100.0, max(0.1, float(params.get("atr_multiplier", 3.0))))
use_higher_atr = bool(params.get("use_higher_atr", False))
atr_timeframe = str(params.get("atr_timeframe", "4H"))
chandelier_lookback = min(10000, max(1, int(params.get("chandelier_lookback", 22))))
chandelier_atr_length = min(10000, max(1, int(params.get("chandelier_atr_length", 14))))
chandelier_multiplier = min(100.0, max(0.1, float(params.get("chandelier_multiplier", 3.0))))
donchian_lookback = min(10000, max(1, int(params.get("donchian_lookback", 20))))
deviation_length = min(10000, max(2, int(params.get("deviation_length", 20))))
deviation_multiplier = min(100.0, max(0.2, float(params.get("deviation_multiplier", 2.0))))
fixed_percent = min(100.0, max(0.1, float(params.get("fixed_percent", 5.0))))
show_safe_bands = bool(params.get("show_safe_bands", True))
show_band_borders = bool(params.get("show_band_borders", True))
safety_atr_length = min(10000, max(1, int(params.get("safety_atr_length", 14))))
safe_zone_multiplier = min(100.0, max(0.5, float(params.get("safe_zone_multiplier", 2.0))))
up_color = str(params.get("up_color", "#00897B"))
down_color = str(params.get("down_color", "#FF9800"))
show_flips = bool(params.get("show_flips", True))

timeframe_seconds = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1H": 3600, "2H": 7200, "4H": 14400, "6H": 21600, "8H": 28800, "12H": 43200, "1D": 86400, "1W": 604800}


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _timeframe_key(value):
    text = str(value or "1H").strip()
    aliases = {"1": "1m", "5": "5m", "15": "15m", "30": "30m", "60": "1H", "120": "2H", "240": "4H", "360": "6H", "480": "8H", "720": "12H", "D": "1D", "W": "1W"}
    return aliases.get(text, text)


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


def _true_range(high, low, close):
    result = np.empty(len(close), dtype=float)
    for index in range(len(close)):
        result[index] = high[index] - low[index] if index == 0 else max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
    return result


def _rolling(values, length, maximum):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(len(values)):
        window = values[max(0, index - length + 1):index + 1]
        result[index] = float(np.max(window) if maximum else np.min(window))
    return result


def _deviation(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        result[index] = float(np.std(window, ddof=0))
    return result


def _source(name, opened, high, low, close):
    key = name.upper()
    if key == "OPEN":
        return opened.copy()
    if key == "HIGH":
        return high.copy()
    if key == "LOW":
        return low.copy()
    if key == "HLC3":
        return (high + low + close) / 3.0
    if key == "OHLC4":
        return (opened + high + low + close) / 4.0
    if key == "CLOSE":
        return close.copy()
    return (high + low) / 2.0


df = df.copy()
base = _normalize(df)
times = base["time"].to_numpy(dtype=float)
opened = base["open"].to_numpy(dtype=float)
high = base["high"].to_numpy(dtype=float)
low = base["low"].to_numpy(dtype=float)
close = base["close"].to_numpy(dtype=float)
n = len(base)
anchor = _source(anchor_source, opened, high, low, close)
true_range = _true_range(high, low, close)
local_atr = _rma(true_range, atr_length)

runtime_ctx = ctx
chart_timeframe = _timeframe_key(runtime_ctx.timeframe or "1H")
selected_timeframe = _timeframe_key(atr_timeframe)
chart_seconds = timeframe_seconds.get(chart_timeframe, 3600)
selected_seconds = timeframe_seconds.get(selected_timeframe, 14400)
higher_invalid = use_higher_atr and selected_seconds < chart_seconds
higher_available = not use_higher_atr
higher_atr_aligned = np.full(n, np.nan, dtype=float)
if use_higher_atr and not higher_invalid:
    raw_higher = runtime_ctx.raw_series(selected_timeframe)
    higher_available = raw_higher is not None and len(raw_higher) > 0
    if higher_available:
        higher = _normalize(raw_higher)
        higher_time = higher["time"].to_numpy(dtype=float)
        higher_high = higher["high"].to_numpy(dtype=float)
        higher_low = higher["low"].to_numpy(dtype=float)
        higher_close = higher["close"].to_numpy(dtype=float)
        higher_atr = _rma(_true_range(higher_high, higher_low, higher_close), atr_length)
        for index, timestamp in enumerate(times):
            current_higher = int(np.searchsorted(higher_time, timestamp, side="right") - 1)
            closed_index = current_higher - 1
            if 0 <= closed_index < len(higher_atr) and np.isfinite(higher_atr[closed_index]):
                higher_atr_aligned[index] = higher_atr[closed_index]
atr_value = higher_atr_aligned if use_higher_atr else local_atr

distance_atr = atr_value * atr_multiplier
distance_deviation = _deviation(anchor, deviation_length) * deviation_multiplier
distance_percent = anchor * fixed_percent / 100.0
chandelier_atr = _rma(true_range, chandelier_atr_length)
chandelier_high = _rolling(high, chandelier_lookback, True)
chandelier_low = _rolling(low, chandelier_lookback, False)
chandelier_long = chandelier_high - chandelier_atr * chandelier_multiplier
chandelier_short = chandelier_low + chandelier_atr * chandelier_multiplier
donchian_long = _rolling(low, donchian_lookback, False)
donchian_short = _rolling(high, donchian_lookback, True)

if trailing_method == "Chandelier":
    long_candidate, short_candidate = chandelier_long, chandelier_short
elif trailing_method == "Donchian":
    long_candidate, short_candidate = donchian_long, donchian_short
elif trailing_method == "StdDev":
    long_candidate, short_candidate = anchor - distance_deviation, anchor + distance_deviation
elif trailing_method == "Fixed":
    long_candidate, short_candidate = anchor - distance_percent, anchor + distance_percent
else:
    long_candidate, short_candidate = anchor - distance_atr, anchor + distance_atr

long_trail = np.full(n, np.nan, dtype=float)
short_trail = np.full(n, np.nan, dtype=float)
trend = np.ones(n, dtype=int)
for index in range(n):
    previous_long = long_trail[index - 1] if index else np.nan
    previous_short = short_trail[index - 1] if index else np.nan
    previous_close = close[index - 1] if index else np.nan
    prior_long_zero = previous_long if np.isfinite(previous_long) else 0.0
    prior_short_zero = previous_short if np.isfinite(previous_short) else 0.0
    long_trail[index] = max(long_candidate[index], prior_long_zero) if np.isfinite(previous_close) and previous_close > prior_long_zero and np.isfinite(long_candidate[index]) else long_candidate[index]
    short_trail[index] = min(short_candidate[index], prior_short_zero) if np.isfinite(previous_close) and previous_close < prior_short_zero and np.isfinite(short_candidate[index]) else short_candidate[index]
    previous_trend = trend[index - 1] if index else 1
    short_gate = previous_short if np.isfinite(previous_short) else short_trail[index]
    long_gate = previous_long if np.isfinite(previous_long) else long_trail[index]
    if np.isfinite(short_gate) and close[index] > short_gate:
        trend[index] = 1
    elif np.isfinite(long_gate) and close[index] < long_gate:
        trend[index] = -1
    else:
        trend[index] = previous_trend

stop_line = np.where(trend == 1, long_trail, short_trail)
safety_atr = _rma(true_range, safety_atr_length)
gap_atr = np.full(n, np.nan, dtype=float)
valid_gap = np.isfinite(safety_atr) & (safety_atr > 0.0) & np.isfinite(stop_line)
gap_atr[valid_gap] = np.abs(close[valid_gap] - stop_line[valid_gap]) / safety_atr[valid_gap]
watch_zone = safe_zone_multiplier / 2.0
zone = []
for value in gap_atr:
    zone.append("safe" if not np.isfinite(value) or value >= safe_zone_multiplier else "watch" if value >= watch_zone else "critical")
direction = np.where(trend == 1, 1.0, -1.0)
critical_bound = stop_line + direction * watch_zone * safety_atr
safe_bound = stop_line + direction * safe_zone_multiplier * safety_atr
flip_up = (trend == 1) & (np.r_[1, trend[:-1]] == -1)
flip_down = (trend == -1) & (np.r_[1, trend[:-1]] == 1)


def _cross_above(first, second):
    return np.asarray([index > 0 and np.all(np.isfinite([first[index], first[index - 1], second[index], second[index - 1]])) and first[index] > second[index] and first[index - 1] <= second[index - 1] for index in range(len(first))], dtype=bool)


def _cross_below(first, second):
    return np.asarray([index > 0 and np.all(np.isfinite([first[index], first[index - 1], second[index], second[index - 1]])) and first[index] < second[index] and first[index - 1] >= second[index - 1] for index in range(len(first))], dtype=bool)


close_over = _cross_above(close, stop_line)
close_under = _cross_below(close, stop_line)
entered_critical = np.asarray([value == "critical" and (index == 0 or zone[index - 1] != "critical") for index, value in enumerate(zone)], dtype=bool)
stop_colors = [up_color if value == 1 else down_color for value in trend]
critical_cloud = []
watch_cloud = []
for index in range(n):
    if show_safe_bands and not higher_invalid and np.all(np.isfinite([stop_line[index], critical_bound[index], safe_bound[index]])):
        critical_cloud.append({"upper": float(max(stop_line[index], critical_bound[index])), "lower": float(min(stop_line[index], critical_bound[index])), "color": "#FF1111", "opacity": 0.40})
        watch_cloud.append({"upper": float(max(critical_bound[index], safe_bound[index])), "lower": float(min(critical_bound[index], safe_bound[index])), "color": "#FDD835", "opacity": 0.30})
    else:
        critical_cloud.append(None)
        watch_cloud.append(None)


def _event(mask, prices, text, color, event_type):
    return {"type": event_type, "name": text, "data": [{"active": True, "price": float(prices[index]), "text": text, "color": color} if active and np.isfinite(prices[index]) else None for index, active in enumerate(mask)], "color": color, "renderMode": "events"}


def _limited_event_masks(masks, recent_bars=120, total_cap=12, minimum_gap=5):
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


method_text = {
    "ATR": f"ATR / Supertrend · {atr_multiplier:.2f}x ATR({atr_length})",
    "Chandelier": f"Chandelier · {chandelier_multiplier:.2f}x ATR({chandelier_atr_length})",
    "Donchian": f"Donchian · {donchian_lookback} bars",
    "StdDev": f"StdDev · {deviation_multiplier:.2f}x",
    "Fixed": f"Fixed% · {fixed_percent:.2f}%",
}[trailing_method]
warning = "Selected ATR timeframe is lower than chart timeframe" if higher_invalid else "Higher-timeframe ATR data unavailable" if use_higher_atr and not higher_available else ""
last = n - 1
tables = [{
    "title": "Multimodal Trailing Stop", "position": "top-right", "compact": True,
    "columns": [{"text": "Metric"}, {"text": "Value"}],
    "rows": [
        [{"text": "Method"}, {"text": method_text}],
        [{"text": "Side"}, {"text": "Long" if not n or trend[last] == 1 else "Short"}],
        [{"text": "Stop"}, {"text": "—" if not n or not np.isfinite(stop_line[last]) else f"{stop_line[last]:.6g}"}],
        [{"text": "Safety"}, {"text": "—" if not n or not np.isfinite(gap_atr[last]) else f"{zone[last].title()} · {gap_atr[last]:.2f}x ATR"}],
        [{"text": "Warning"}, {"text": warning}],
    ],
}]

visible_stop = stop_line.copy()
visible_critical = critical_bound.copy()
visible_safe = safe_bound.copy()
if higher_invalid:
    visible_stop[:] = np.nan
    visible_critical[:] = np.nan
    visible_safe[:] = np.nan
display_flip_up, display_flip_down, display_close_over, display_close_under, display_entered_critical = _limited_event_masks([
    flip_up & show_flips & (~higher_invalid),
    flip_down & show_flips & (~higher_invalid),
    close_over,
    close_under,
    entered_critical,
])
output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Trail Stop", "data": _finite(visible_stop), "color": stop_colors, "type": "line", "lineWidth": 3, "overlay": True},
        {"name": "Critical Boundary", "title": show_band_borders, "data": _finite(visible_critical), "color": "rgba(120,123,134,0.60)" if show_band_borders else "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Safe Boundary", "title": show_band_borders, "data": _finite(visible_safe), "color": "rgba(120,123,134,0.60)" if show_band_borders else "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Critical Band", "title": show_safe_bands, "data": critical_cloud, "color": "#FF1111", "type": "cloud", "overlay": True},
        {"name": "Watch Band", "title": show_safe_bands, "data": watch_cloud, "color": "#FDD835", "type": "cloud", "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [
        _event(display_flip_up, stop_line - safety_atr, "FLIP", up_color, "buy"),
        _event(display_flip_down, stop_line + safety_atr, "FLIP", down_color, "sell"),
        _event(display_close_over, close, "Close Over Stop", up_color, "buy"),
        _event(display_close_under, close, "Close Under Stop", down_color, "sell"),
        _event(display_entered_critical, close, "Critical Zone", "#FF1111", "sell"),
    ],
    "layers": [], "tables": tables, "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "trueRange": _finite(true_range), "localAtr": _finite(local_atr), "higherAtr": _finite(higher_atr_aligned), "atrValue": _finite(atr_value),
        "anchor": _finite(anchor), "distanceAtr": _finite(distance_atr), "distanceDeviation": _finite(distance_deviation), "distancePercent": _finite(distance_percent),
        "chandelierAtr": _finite(chandelier_atr), "chandelierHigh": _finite(chandelier_high), "chandelierLow": _finite(chandelier_low),
        "donchianLong": _finite(donchian_long), "donchianShort": _finite(donchian_short), "longCandidate": _finite(long_candidate), "shortCandidate": _finite(short_candidate),
        "longTrail": _finite(long_trail), "shortTrail": _finite(short_trail), "trend": [int(value) for value in trend], "stopLine": _finite(stop_line),
        "safetyAtr": _finite(safety_atr), "gapAtr": _finite(gap_atr), "zone": zone, "criticalBound": _finite(critical_bound), "safeBound": _finite(safe_bound),
        "flipUp": flip_up.tolist(), "flipDown": flip_down.tolist(), "closeOver": close_over.tolist(), "closeUnder": close_under.tolist(), "enteredCritical": entered_critical.tolist(),
        "higherTimeframeInvalid": bool(higher_invalid), "higherTimeframeAvailable": bool(higher_available), "method": trailing_method,
    },
}
