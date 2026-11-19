# ============================================================
# QuantDinger Higher Timeframe High-Low
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Higher Timeframe High-Low"
my_indicator_description = (
    "Displays the last completed higher-timeframe high, low, and midpoint with "
    "breakout state, rejection markers, optional period separators, and projections."
)

indicator_timeframes = ["1H", "4H", "1D", "1W"]

# @param extension int 50 Projection bars range=0:300:1
# @param timeframe str 1D Higher timeframe values=1H,4H,1D,1W
# @param period_separator bool false Show higher-timeframe separators
# @param show_trend bool false Persist breakout trend candle colors
# @param show_breakouts bool false Show alternating breakout labels

extension = min(300, max(0, int(params.get("extension", 50))))
selected_timeframe = str(params.get("timeframe", "1D")).upper()
period_separator = bool(params.get("period_separator", False))
show_trend = bool(params.get("show_trend", False))
show_breakouts = bool(params.get("show_breakouts", False))

timeframe_seconds = {
    "1M": 60, "5M": 300, "15M": 900, "30M": 1800,
    "1H": 3600, "4H": 14400, "1D": 86400, "1W": 604800,
}
runtime_context = globals().get("ctx")
chart_timeframe = str(getattr(runtime_context, "timeframe", "1H") or "1H").upper()
chart_seconds = int(timeframe_seconds.get(chart_timeframe, 3600))
requested_seconds = int(timeframe_seconds.get(selected_timeframe, 86400))
timeframe_clamped = requested_seconds < chart_seconds
resolved_timeframe = chart_timeframe if timeframe_clamped else selected_timeframe
resolved_seconds = max(chart_seconds, requested_seconds)


def normalize(raw):
    if raw is None or len(raw) == 0:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    result = raw.copy().reset_index(drop=True)
    for column in ("time", "open", "high", "low", "close", "volume"):
        if column not in result.columns:
            result[column] = np.arange(len(result), dtype=float) * float(chart_seconds) if column == "time" else np.nan
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result["volume"] = result["volume"].fillna(0.0)
    return result.dropna(subset=["time", "open", "high", "low", "close"]).sort_values("time").drop_duplicates("time", keep="last").reset_index(drop=True)


def seconds(values):
    values = np.asarray(values, dtype=float)
    return values / 1000.0 if len(values) and np.nanmax(np.abs(values)) > 10000000000 else values.copy()


def group_starts(values, timeframe_name, duration):
    values = np.asarray(values, dtype=float)
    if timeframe_name == "1W":
        monday_epoch = 4.0 * 86400.0
        return np.floor((values - monday_epoch) / float(duration)) * float(duration) + monday_epoch
    return np.floor(values / float(duration)) * float(duration)


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


frame = normalize(df)
n = len(frame)
time_values = frame["time"].to_numpy(dtype=float)
time_seconds = seconds(time_values)
high_values = frame["high"].to_numpy(dtype=float)
low_values = frame["low"].to_numpy(dtype=float)
close_values = frame["close"].to_numpy(dtype=float)

raw_higher = (
    runtime_context.raw_series(resolved_timeframe)
    if runtime_context is not None and hasattr(runtime_context, "raw_series")
    else None
)
higher = normalize(raw_higher)
if len(higher) == 0 and n:
    fallback = frame.copy()
    fallback["_group"] = group_starts(time_seconds, resolved_timeframe, resolved_seconds)
    higher = fallback.groupby("_group", sort=True).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
    ).reset_index().rename(columns={"_group": "time"})
price_high = np.full(n, np.nan, dtype=float)
price_low = np.full(n, np.nan, dtype=float)
reference_index = np.full(n, -1, dtype=int)
if len(higher):
    higher_times = seconds(higher["time"].to_numpy(dtype=float))
    completed_at = higher_times + float(resolved_seconds)
    reference_index = np.searchsorted(completed_at, time_seconds + 1e-9, side="right") - 1
    valid = (reference_index >= 0) & (reference_index < len(higher))
    higher_high = higher["high"].to_numpy(dtype=float)
    higher_low = higher["low"].to_numpy(dtype=float)
    price_high[valid] = higher_high[reference_index[valid]]
    price_low[valid] = higher_low[reference_index[valid]]

price_average = (price_high + price_low) / 2.0
reference_changed = np.zeros(n, dtype=bool)
if n > 1:
    reference_changed[1:] = reference_index[1:] != reference_index[:-1]
display_high = price_high.copy()
display_low = price_low.copy()
display_average = price_average.copy()
if not show_trend:
    display_high[reference_changed] = np.nan
    display_low[reference_changed] = np.nan
    display_average[reference_changed] = np.nan

high_break = np.zeros(n, dtype=bool)
low_break = np.zeros(n, dtype=bool)
resistance = np.zeros(n, dtype=bool)
support = np.zeros(n, dtype=bool)
break_labels = []
bar_colors = [None] * n
background_colors = [None] * n
state = None
for index in range(1, n):
    if np.isfinite(price_high[index]) and np.isfinite(price_high[index - 1]):
        high_break[index] = close_values[index] > price_high[index] and close_values[index - 1] <= price_high[index - 1]
        resistance[index] = high_values[index] < price_high[index] and high_values[index - 1] >= price_high[index - 1]
    if np.isfinite(price_low[index]) and np.isfinite(price_low[index - 1]):
        low_break[index] = close_values[index] < price_low[index] and close_values[index - 1] >= price_low[index - 1]
        support[index] = low_values[index] > price_low[index] and low_values[index - 1] <= price_low[index - 1]
    previous_state = state
    if high_break[index]:
        state = True
        if show_breakouts and previous_state is False:
            break_labels.append({"type": "label", "index": index, "price": float(low_values[index] * 0.99), "text": f"{resolved_timeframe}\nHigh Break", "color": "#0E4E11", "textColor": "#FFFFFF", "side": "below", "fontSize": 9})
    elif low_break[index]:
        state = False
        if show_breakouts and previous_state is True:
            break_labels.append({"type": "label", "index": index, "price": float(high_values[index] * 1.01), "text": f"{resolved_timeframe}\nLow Break", "color": "#6E1414", "textColor": "#FFFFFF", "side": "above", "fontSize": 9})
    if show_trend:
        bar_colors[index] = "#13B818" if state is True else "#D41919" if state is False else None
    elif high_break[index]:
        bar_colors[index] = "#13B818"
    elif low_break[index]:
        bar_colors[index] = "#D41919"
    if period_separator and reference_changed[index]:
        background_colors[index] = "rgba(255,255,255,0.10)"

resistance_marks = [None] * n
support_marks = [None] * n
for index in range(1, n):
    target = index - 1
    if resistance[index]:
        resistance_marks[target] = float(high_values[target] * 1.002)
    if support[index]:
        support_marks[target] = float(low_values[target] * 0.998)

channel = []
for index in range(n):
    if not show_trend and np.isfinite(display_high[index]) and np.isfinite(display_low[index]):
        channel.append({"upper": float(display_high[index]), "lower": float(display_low[index]), "color": "rgba(153,21,255,0.08)"})
    else:
        channel.append(None)

layers = list(break_labels)
if n and np.isfinite(price_high[-1]) and np.isfinite(price_low[-1]):
    projection_start = max(0, n - 1 - extension)
    projection_end = n - 1
    for price, color, text, role in (
        (price_high[-1], "#D41919", f"↩ {resolved_timeframe} High", "HTF High Projection"),
        (price_low[-1], "#158D19", f"↘ {resolved_timeframe} Low", "HTF Low Projection"),
        (price_average[-1], "rgba(255,255,255,0.65)", "", "HTF Mid Projection"),
    ):
        layers.append({"type": "line", "role": role, "startIndex": projection_start, "endIndex": projection_end, "startPrice": float(price), "endPrice": float(price), "color": color, "width": 1 if show_trend and role == "HTF Mid Projection" else 2, "style": "solid" if show_trend or role != "HTF Mid Projection" else "dotted"})
        if text:
            layers.append({"type": "label", "index": projection_end, "price": float(price), "text": text, "color": color, "textColor": "#FFFFFF", "side": "right", "anchor": "right", "fontSize": 10})

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "HTF High", "data": finite(display_high), "color": "rgba(212,25,25,0.20)" if show_trend else "#D41919", "type": "line", "lineWidth": 2, "overlay": True, "connectNulls": False},
        {"name": "HTF Average", "data": finite(display_average), "color": "rgba(255,255,255,0.10)" if show_trend else "rgba(255,255,255,0.50)", "type": "line", "lineWidth": 1, "lineStyle": "solid" if show_trend else "dotted", "overlay": True, "connectNulls": False},
        {"name": "HTF Low", "data": finite(display_low), "color": "rgba(19,184,24,0.20)" if show_trend else "#13B818", "type": "line", "lineWidth": 2, "overlay": True, "connectNulls": False},
        {"name": "HTF Range", "title": False, "data": channel, "color": "#9915FF", "type": "cloud", "opacity": 0.08, "overlay": True},
        {"name": "Resistance", "title": False, "data": resistance_marks, "color": "#D41919", "type": "scatter", "size": 4, "overlay": True},
        {"name": "Support", "title": False, "data": support_marks, "color": "#13B818", "type": "scatter", "size": 4, "overlay": True},
    ],
    "signals": [
        {"type": "buy", "text": "High Break", "name": "HTF High Break", "data": [float(low_values[i]) if high_break[i] else None for i in range(n)], "color": "#13B818", "renderMode": "hidden"},
        {"type": "sell", "text": "Low Break", "name": "HTF Low Break", "data": [float(high_values[i]) if low_break[i] else None for i in range(n)], "color": "#D41919", "renderMode": "hidden"},
    ],
    "layers": layers,
    "tables": [],
    "bar_colors": bar_colors,
    "background_colors": background_colors,
    "calculatedVars": {
        "resolvedTimeframe": resolved_timeframe,
        "timeframeClamped": bool(timeframe_clamped),
        "referenceIndex": reference_index.tolist(),
        "priceHigh": finite(price_high), "priceLow": finite(price_low), "priceAverage": finite(price_average),
        "referenceChanged": reference_changed.tolist(),
        "highBreak": high_break.tolist(), "lowBreak": low_break.tolist(),
        "resistance": resistance.tolist(), "support": support.tolist(),
    },
}
