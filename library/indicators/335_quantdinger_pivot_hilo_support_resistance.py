# ============================================================
# QuantDinger Pivot HiLo Support and Resistance
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Pivot HiLo Support and Resistance"
my_indicator_description = "Detects configurable or ideal pivots, classifies swing structure, extends support/resistance, and flags filtered level breaks."

# @param show_pivots bool true Show pivot points
# @param auto_strength bool false Use automatic pivot strength
# @param left_length int 5 Pivot left length range=1:100:1
# @param right_length int 5 Pivot right length range=1:100:1
# @param show_levels bool true Show support/resistance extensions
# @param show_channel bool false Show levels as chaos channel
# @param confirmation_wait int 1 Wait for candle close range=0:1:1
# @param maximum_level_length int 0 Maximum level extension; zero is unlimited range=0:2000:1
# @param renko_pivots bool false Use candle bodies for pivots
# @param ideal_pivots bool false Require monotonic ideal pivots
# @param show_structure bool false Show HH/HL/LH/LL markers
# @param third_level_swings bool false Require comparison with three prior swings
# @param color_pivot_bars bool false Color pivot bars
# @param show_break_arrows bool false Show level-break arrows
# @param filter_breaks bool false Apply MA filter to breaks
# @param filter_pivots bool false Apply MA filter to pivots
# @param fast_ma_type str EMA Fast MA type values=SMA,EMA,WMA,VWMA,Smooth SMA,DEMA,TEMA,Hull MA,ZeroLag EMA,Triangular MA,SuperSmooth MA
# @param fast_ma_length int 21 Fast MA length range=1:500:1
# @param fast_source str close Fast MA source values=open,high,low,close,hl2,hlc3,ohlc4
# @param slow_ma_type str EMA Slow MA type values=SMA,EMA,WMA,VWMA,Smooth SMA,DEMA,TEMA,Hull MA,ZeroLag EMA,Triangular MA,SuperSmooth MA
# @param slow_ma_length int 55 Slow MA length range=1:500:1
# @param slow_source str close Slow MA source values=open,high,low,close,hl2,hlc3,ohlc4
# @param show_averages bool false Show moving averages

show_pivots = bool(params.get("show_pivots", True))
auto_strength = bool(params.get("auto_strength", False))
left_length = min(100, max(1, int(params.get("left_length", 5))))
right_length = min(100, max(1, int(params.get("right_length", 5))))
show_levels = bool(params.get("show_levels", True))
show_channel = bool(params.get("show_channel", False))
confirmation_wait = min(1, max(0, int(params.get("confirmation_wait", 1))))
maximum_level_length = max(0, int(params.get("maximum_level_length", 0)))
renko_pivots = bool(params.get("renko_pivots", False))
ideal_pivots = bool(params.get("ideal_pivots", False))
show_structure = bool(params.get("show_structure", False))
third_level_swings = bool(params.get("third_level_swings", False))
color_pivot_bars = bool(params.get("color_pivot_bars", False))
show_break_arrows = bool(params.get("show_break_arrows", False))
filter_breaks = bool(params.get("filter_breaks", False))
filter_pivots = bool(params.get("filter_pivots", False))
fast_ma_type = str(params.get("fast_ma_type", "EMA"))
fast_ma_length = max(1, int(params.get("fast_ma_length", 21)))
fast_source_name = str(params.get("fast_source", "close")).lower()
slow_ma_type = str(params.get("slow_ma_type", "EMA"))
slow_ma_length = max(1, int(params.get("slow_ma_length", 55)))
slow_source_name = str(params.get("slow_source", "close")).lower()
show_averages = bool(params.get("show_averages", False))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _wma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, period + 1, dtype=float)
    denominator = float(np.sum(weights))
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.sum(window * weights) / denominator)
    return result


def _vwma(values, volumes, period):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(period - 1, len(values)):
        source_window = values[index - period + 1:index + 1]
        volume_window = volumes[index - period + 1:index + 1]
        denominator = float(np.sum(volume_window))
        if np.all(np.isfinite(source_window)) and np.all(np.isfinite(volume_window)) and abs(denominator) > 1e-12:
            result[index] = float(np.sum(source_window * volume_window) / denominator)
    return result


def _smma(values, period):
    seed = _sma(values, period)
    result = np.full(len(values), np.nan, dtype=float)
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(previous):
            result[index] = seed[index]
        elif np.isfinite(value):
            result[index] = (previous * (period - 1) + value) / period
        previous = result[index]
    return result


def _supersmoother(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    a1 = math.exp(-1.414 * 3.14159 / period)
    b1 = 2.0 * a1 * math.cos(1.414 * 3.14159 / period)
    c2 = b1
    c3 = -(a1 * a1)
    c1 = 1.0 - c2 - c3
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        previous_source = values[index - 1] if index >= 1 and np.isfinite(values[index - 1]) else 0.0
        previous_one = result[index - 1] if index >= 1 and np.isfinite(result[index - 1]) else 0.0
        previous_two = result[index - 2] if index >= 2 and np.isfinite(result[index - 2]) else 0.0
        result[index] = c1 * (value + previous_source) / 2.0 + c2 * previous_one + c3 * previous_two
    return result


def _variant(kind, values, volumes, period):
    first = _ema(values, period)
    if kind == "EMA":
        return first
    if kind == "WMA":
        return _wma(values, period)
    if kind == "VWMA":
        return _vwma(values, volumes, period)
    if kind == "Smooth SMA":
        return _smma(values, period)
    if kind == "DEMA":
        return 2.0 * first - _ema(first, period)
    if kind == "TEMA":
        second = _ema(first, period)
        return 3.0 * (first - second) + _ema(second, period)
    if kind == "Hull MA":
        half = max(1, int(period / 2))
        root = max(1, int(np.floor(np.sqrt(period) + 0.5)))
        return _wma(2.0 * _wma(values, half) - _wma(values, period), root)
    if kind == "ZeroLag EMA":
        lag = max(0, int((period - 1) / 2))
        adjusted = np.full(len(values), np.nan, dtype=float)
        if lag == 0:
            adjusted = values.copy()
        else:
            for index in range(lag, len(values)):
                if np.all(np.isfinite([values[index], values[index - lag]])):
                    adjusted[index] = values[index] + values[index] - values[index - lag]
        return _ema(adjusted, period)
    if kind == "Triangular MA":
        return _sma(_sma(values, period), period)
    if kind == "SuperSmooth MA":
        return _supersmoother(values, period)
    return _sma(values, period)


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].fillna(0.0).to_numpy(dtype=float)
n = len(df)
sources = {
    "open": opened, "high": high, "low": low, "close": close,
    "hl2": (high + low) / 2.0, "hlc3": (high + low + close) / 3.0,
    "ohlc4": (opened + high + low + close) / 4.0,
}
fast_source = sources.get(fast_source_name, close)
slow_source = sources.get(slow_source_name, close)
fast_average = _variant(fast_ma_type, fast_source, volume, fast_ma_length)
slow_average = _variant(slow_ma_type, slow_source, volume, slow_ma_length)
ema_21 = _ema(close, 21)
pivot_high_source = np.maximum(close, opened) if renko_pivots else high.copy()
pivot_low_source = np.minimum(close, opened) if renko_pivots else low.copy()
left_strength = np.full(n, left_length, dtype=int)
right_strength = np.full(n, right_length, dtype=int)
if auto_strength:
    for index in range(n):
        if np.isfinite(ema_21[index]):
            left_strength[index] = left_length if close[index] > ema_21[index] else left_length + 1
            right_strength[index] = right_length if close[index] < ema_21[index] else right_length + 1

raw_high = np.full(n, np.nan, dtype=float)
raw_low = np.full(n, np.nan, dtype=float)
for index in range(n):
    left = int(left_strength[index])
    right = int(right_strength[index])
    center = index - right
    if center < left or center < 0:
        continue
    high_allowed = not filter_pivots or (
        fast_ma_length <= slow_ma_length
        and np.isfinite(fast_average[index]) and np.isfinite(slow_average[index])
        and fast_average[index] >= slow_average[index]
    )
    low_allowed = not filter_pivots or (
        fast_ma_length <= slow_ma_length
        and np.isfinite(fast_average[index]) and np.isfinite(slow_average[index])
        and fast_average[index] <= slow_average[index]
    )
    if ideal_pivots:
        high_match = all(pivot_high_source[position] > pivot_high_source[position - 1] for position in range(center - left + 1, center + 1))
        high_match = high_match and all(pivot_high_source[position] < pivot_high_source[position - 1] for position in range(center + 1, index + 1))
        low_match = all(pivot_low_source[position] < pivot_low_source[position - 1] for position in range(center - left + 1, center + 1))
        low_match = low_match and all(pivot_low_source[position] > pivot_low_source[position - 1] for position in range(center + 1, index + 1))
    else:
        high_match = all(pivot_high_source[center] >= pivot_high_source[position] for position in range(center - left, center))
        high_match = high_match and all(pivot_high_source[center] > pivot_high_source[position] for position in range(center + 1, index + 1))
        low_match = all(pivot_low_source[center] <= pivot_low_source[position] for position in range(center - left, center))
        low_match = low_match and all(pivot_low_source[center] < pivot_low_source[position] for position in range(center + 1, index + 1))
    if high_match and high_allowed:
        raw_high[index] = pivot_high_source[center]
    if low_match and low_allowed:
        raw_low[index] = pivot_low_source[center]

confirmed_high = np.full(n, np.nan, dtype=float)
confirmed_low = np.full(n, np.nan, dtype=float)
if confirmation_wait == 0:
    confirmed_high[:] = raw_high
    confirmed_low[:] = raw_low
elif n > confirmation_wait:
    confirmed_high[confirmation_wait:] = raw_high[:-confirmation_wait]
    confirmed_low[confirmation_wait:] = raw_low[:-confirmation_wait]

higher_high = np.zeros(n, dtype=bool)
lower_high = np.zeros(n, dtype=bool)
higher_low = np.zeros(n, dtype=bool)
lower_low = np.zeros(n, dtype=bool)
high_structure_values = []
low_structure_values = []
pivot_high_plot = np.full(n, np.nan, dtype=float)
pivot_low_plot = np.full(n, np.nan, dtype=float)
support = np.full(n, np.nan, dtype=float)
resistance = np.full(n, np.nan, dtype=float)
historical_resistance = np.full(n, np.nan, dtype=float)
historical_support = np.full(n, np.nan, dtype=float)
bar_colors = [None] * n
count_high = 0
count_low = 0
current_high = np.nan
current_low = np.nan
recent_lines = []

for index in range(n):
    right = int(right_strength[index])
    target = index - right - confirmation_wait
    high_event = np.isfinite(confirmed_high[index])
    low_event = np.isfinite(confirmed_low[index])
    high_level_changed = False
    low_level_changed = False
    if high_event:
        previous_high = current_high
        extracted = pivot_high_source[target] if 0 <= target < n else np.nan
        high_structure_values.append(float(extracted))
        comparisons = high_structure_values[:-1][-3 if third_level_swings else -1:]
        if len(comparisons) >= (3 if third_level_swings else 1):
            higher_high[index] = all(value < extracted for value in comparisons)
            lower_high[index] = all(value > extracted for value in comparisons)
        count_high = 0
        current_high = float(extracted)
        high_level_changed = np.isfinite(previous_high) and current_high != previous_high
        if 0 <= target < n:
            pivot_high_plot[target] = confirmed_high[index]
            if color_pivot_bars:
                bar_colors[target] = "#FFA500"
        if show_levels and not show_channel and np.isfinite(current_high) and current_high != (previous_high if np.isfinite(previous_high) else 0.0):
            recent_lines.append({"side": "resistance", "startIndex": int(target), "endIndex": int(index), "price": current_high})
    else:
        count_high += 1
    if low_event:
        previous_low = current_low
        extracted = pivot_low_source[target] if 0 <= target < n else np.nan
        low_structure_values.append(float(extracted))
        comparisons = low_structure_values[:-1][-3 if third_level_swings else -1:]
        if len(comparisons) >= (3 if third_level_swings else 1):
            higher_low[index] = all(value < extracted for value in comparisons)
            lower_low[index] = all(value > extracted for value in comparisons)
        count_low = 0
        current_low = float(extracted)
        low_level_changed = np.isfinite(previous_low) and current_low != previous_low
        if 0 <= target < n:
            pivot_low_plot[target] = confirmed_low[index]
            if color_pivot_bars:
                bar_colors[target] = "#FFA500"
        if show_levels and not show_channel and np.isfinite(current_low) and current_low != (previous_low if np.isfinite(previous_low) else 0.0):
            recent_lines.append({"side": "support", "startIndex": int(target), "endIndex": int(index), "price": current_low})
    else:
        count_low += 1

    for line in reversed(recent_lines):
        if line["side"] == "resistance":
            line["endIndex"] = int(index)
            break
    for line in reversed(recent_lines):
        if line["side"] == "support":
            line["endIndex"] = int(index)
            break

    if show_levels and show_channel:
        resistance[index] = current_high
        support[index] = current_low
    elif show_levels and not show_channel:
        if 0 <= target < n:
            if np.isfinite(current_high) and (maximum_level_length == 0 or count_high < maximum_level_length) and not high_level_changed:
                historical_resistance[target] = current_high
            if np.isfinite(current_low) and (maximum_level_length == 0 or count_low < maximum_level_length) and not low_level_changed:
                historical_support[target] = current_low

buy_break = np.zeros(n, dtype=bool)
sell_break = np.zeros(n, dtype=bool)
for index in range(n):
    current_resistance = np.nan
    current_support = np.nan
    for position in range(index, -1, -1):
        if np.isfinite(confirmed_high[position]):
            target = position - int(right_strength[position]) - confirmation_wait
            if 0 <= target < n:
                current_resistance = pivot_high_source[target]
            break
    for position in range(index, -1, -1):
        if np.isfinite(confirmed_low[position]):
            target = position - int(right_strength[position]) - confirmation_wait
            if 0 <= target < n:
                current_support = pivot_low_source[target]
            break
    base_buy = np.isfinite(current_resistance) and close[index] > current_resistance and opened[index] <= current_resistance
    base_sell = np.isfinite(current_support) and close[index] < current_support and opened[index] >= current_support
    if filter_breaks:
        buy_filter = (
            np.isfinite(fast_average[index]) and close[index] > fast_average[index]
            and (fast_ma_length > slow_ma_length or (np.isfinite(slow_average[index]) and fast_average[index] > slow_average[index]))
        )
        sell_filter = (
            np.isfinite(fast_average[index]) and close[index] < fast_average[index]
            and (fast_ma_length > slow_ma_length or (np.isfinite(slow_average[index]) and fast_average[index] < slow_average[index]))
        )
        base_buy = base_buy and buy_filter
        base_sell = base_sell and sell_filter
    buy_break[index] = bool(base_buy)
    sell_break[index] = bool(base_sell)

layers = []
if show_levels and not show_channel:
    for line in recent_lines[-500:]:
        layers.append({
            "type": "line", "role": "Resistance" if line["side"] == "resistance" else "Support",
            "startIndex": line["startIndex"], "endIndex": line["endIndex"], "y1": line["price"], "y2": line["price"],
            "color": "#800000" if line["side"] == "resistance" else "#008000", "lineWidth": 1,
        })


def _structure_points(events, role, bullish):
    result = [None] * n
    if not show_structure:
        return result
    for confirmation in np.flatnonzero(events):
        target = confirmation - int(right_strength[confirmation]) - confirmation_wait
        if 0 <= target < n:
            result[target] = {
                "active": True, "price": float(pivot_low_source[target] if role in ("HL", "LL") else pivot_high_source[target]),
                "text": role, "color": "#008000" if bullish else "#800000",
            }
    return result


signals = [
    {"type": "buy", "name": "Higher High", "data": _structure_points(higher_high, "HH", True), "color": "#008000", "renderMode": "events"},
    {"type": "buy", "name": "Higher Low", "data": _structure_points(higher_low, "HL", True), "color": "#008000", "renderMode": "events"},
    {"type": "sell", "name": "Lower High", "data": _structure_points(lower_high, "LH", False), "color": "#800000", "renderMode": "events"},
    {"type": "sell", "name": "Lower Low", "data": _structure_points(lower_low, "LL", False), "color": "#800000", "renderMode": "events"},
    {"type": "buy", "name": "Resistance Break", "data": [{"active": True, "price": float(low[index]), "text": "▲", "color": "#00FF00"} if show_break_arrows and buy_break[index] else None for index in range(n)], "color": "#00FF00", "renderMode": "events"},
    {"type": "sell", "name": "Support Break", "data": [{"active": True, "price": float(high[index]), "text": "▼", "color": "#FF0000"} if show_break_arrows and sell_break[index] else None for index in range(n)], "color": "#FF0000", "renderMode": "events"},
]

output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "Fast MA", "data": _finite(fast_average) if show_averages else [None] * n, "color": "#00FF00", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Slow MA", "data": _finite(slow_average) if show_averages and fast_ma_length <= slow_ma_length else [None] * n, "color": "#808080", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Pivot High", "data": _finite(pivot_high_plot) if show_pivots and not show_structure else [None] * n, "color": "#008000", "type": "scatter", "size": 3, "overlay": True},
        {"name": "Pivot Low", "data": _finite(pivot_low_plot) if show_pivots and not show_structure else [None] * n, "color": "#800000", "type": "scatter", "size": 3, "overlay": True},
        {"name": "Resistance Levels", "data": _finite(resistance if show_channel else historical_resistance), "color": "#008000" if show_channel else "#800000", "type": "step", "lineWidth": 1, "overlay": True},
        {"name": "Support Levels", "data": _finite(support if show_channel else historical_support), "color": "#800000" if show_channel else "#008000", "type": "step", "lineWidth": 1, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": signals, "layers": layers, "tables": [], "bar_colors": bar_colors,
    "background_colors": [None] * n,
    "calculatedVars": {
        "fastAverage": _finite(fast_average), "slowAverage": _finite(slow_average), "ema21": _finite(ema_21),
        "leftStrength": left_strength.tolist(), "rightStrength": right_strength.tolist(),
        "rawPivotHigh": _finite(raw_high), "rawPivotLow": _finite(raw_low),
        "confirmedPivotHigh": _finite(confirmed_high), "confirmedPivotLow": _finite(confirmed_low),
        "pivotHighPlot": _finite(pivot_high_plot), "pivotLowPlot": _finite(pivot_low_plot),
        "higherHigh": higher_high.tolist(), "lowerHigh": lower_high.tolist(),
        "higherLow": higher_low.tolist(), "lowerLow": lower_low.tolist(),
        "resistance": _finite(resistance), "support": _finite(support),
        "historicalResistance": _finite(historical_resistance), "historicalSupport": _finite(historical_support),
        "buyBreak": buy_break.tolist(), "sellBreak": sell_break.tolist(), "recentLines": recent_lines,
    },
}
