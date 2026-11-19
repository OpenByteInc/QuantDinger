# ============================================================
# QuantDinger ATR Levels
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger ATR Levels"
my_indicator_description = "Projects trigger, Fibonacci and extension levels from a selected higher-period close and ATR, with an EMA-ribbon trend summary."
indicator_timeframes = ["1D", "1W", "1M", "3M", "12M"]

# @param trading_type str Day Trading horizon values=Day,Multiday,Swing,Position,Long-term
# @param use_options_labels bool true Use Calls/Puts labels
# @param atr_length int 14 Higher-period ATR length range=1:500:1
# @param trigger_percentage float 0.236 Trigger ATR fraction range=0:10:0.001
# @param show_all_fibonacci_levels bool true Show intermediate Fibonacci levels
# @param show_extensions bool false Show ATR extensions
# @param level_size int 2 Level width range=1:5:1
# @param show_info bool true Show information panel
# @param use_current_close bool false Use current higher-period close
# @param fast_ema int 8 Fast EMA range=1:500:1
# @param pivot_ema int 21 Pivot EMA range=1:500:1
# @param slow_ema int 34 Slow EMA range=1:500:1
# @param previous_close_color str #FFFFFF Reference close color
# @param lower_trigger_color str #FFFF00 Lower trigger color
# @param upper_trigger_color str #00FFFF Upper trigger color
# @param key_target_color str #C0C0C0 Key target color
# @param atr_target_color str #FFFFFF Full ATR color
# @param intermediate_color str #808080 Intermediate target color

trading_type = str(params.get("trading_type", "Day"))
trading_type = trading_type if trading_type in ("Day", "Multiday", "Swing", "Position", "Long-term") else "Day"
use_options_labels = bool(params.get("use_options_labels", True))
atr_length = min(500, max(1, int(params.get("atr_length", 14))))
trigger_percentage = min(10.0, max(0.0, float(params.get("trigger_percentage", 0.236))))
show_all_fibonacci_levels = bool(params.get("show_all_fibonacci_levels", True))
show_extensions = bool(params.get("show_extensions", False))
level_size = min(5, max(1, int(params.get("level_size", 2))))
show_info = bool(params.get("show_info", True))
use_current_close = bool(params.get("use_current_close", False))
fast_ema_length = min(500, max(1, int(params.get("fast_ema", 8))))
pivot_ema_length = min(500, max(1, int(params.get("pivot_ema", 21))))
slow_ema_length = min(500, max(1, int(params.get("slow_ema", 34))))
previous_close_color = str(params.get("previous_close_color", "#FFFFFF"))
lower_trigger_color = str(params.get("lower_trigger_color", "#FFFF00"))
upper_trigger_color = str(params.get("upper_trigger_color", "#00FFFF"))
key_target_color = str(params.get("key_target_color", "#C0C0C0"))
atr_target_color = str(params.get("atr_target_color", "#FFFFFF"))
intermediate_color = str(params.get("intermediate_color", "#808080"))


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
    if len(times) and np.nanmax(np.abs(times)) > 10_000_000_000:
        result["time"] = times / 1000.0
    return result


def _ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    state = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            state = float(value) if not np.isfinite(state) else state + alpha * (float(value) - state)
            result[index] = state
    return result


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


def _aggregate(frame, timeframe):
    times = frame["time"].to_numpy(dtype=float)
    dates = pd.to_datetime(times, unit="s", utc=True)
    day_number = np.floor(times / 86400.0).astype(np.int64)
    if timeframe == "1D":
        keys = day_number
    elif timeframe == "1W":
        keys = np.floor((day_number + 3) / 7).astype(np.int64)
    else:
        months = dates.year.to_numpy() * 12 + dates.month.to_numpy() - 1
        divisor = 1 if timeframe == "1M" else 3 if timeframe == "3M" else 12
        keys = np.floor(months / divisor).astype(np.int64)
    rows = []
    current_key = None
    for index, key in enumerate(keys):
        if current_key is None or key != current_key:
            rows.append({"time": float(times[index]), "open": float(frame["open"].iloc[index]), "high": float(frame["high"].iloc[index]), "low": float(frame["low"].iloc[index]), "close": float(frame["close"].iloc[index]), "volume": float(frame["volume"].fillna(0.0).iloc[index])})
            current_key = key
        else:
            rows[-1]["high"] = max(rows[-1]["high"], float(frame["high"].iloc[index]))
            rows[-1]["low"] = min(rows[-1]["low"], float(frame["low"].iloc[index]))
            rows[-1]["close"] = float(frame["close"].iloc[index])
            rows[-1]["volume"] += float(frame["volume"].fillna(0.0).iloc[index])
    return pd.DataFrame(rows, columns=("time", "open", "high", "low", "close", "volume"))


df = df.copy()
base_frame = _normalize(df)
base_time = base_frame["time"].to_numpy(dtype=float)
close = base_frame["close"].to_numpy(dtype=float)
n = len(base_frame)
period_timeframe = {"Day": "1D", "Multiday": "1W", "Swing": "1M", "Position": "3M", "Long-term": "12M"}[trading_type]
runtime_ctx = globals().get("ctx")

raw_period = None
if runtime_ctx is not None and hasattr(runtime_ctx, "raw_series"):
    try:
        raw_period = runtime_ctx.raw_series(period_timeframe)
    except Exception:
        raw_period = None
if raw_period is not None and len(raw_period) > 0:
    period_frame = _normalize(raw_period)
    period_source = "context"
else:
    period_frame = _aggregate(base_frame, period_timeframe)
    period_source = "chart-aggregate"

period_time = period_frame["time"].to_numpy(dtype=float)
period_high_raw = period_frame["high"].to_numpy(dtype=float)
period_low_raw = period_frame["low"].to_numpy(dtype=float)
period_close_raw = period_frame["close"].to_numpy(dtype=float)
period_true_range = np.full(len(period_frame), np.nan, dtype=float)
for index in range(len(period_frame)):
    period_true_range[index] = period_high_raw[index] - period_low_raw[index] if index == 0 else max(period_high_raw[index] - period_low_raw[index], abs(period_high_raw[index] - period_close_raw[index - 1]), abs(period_low_raw[index] - period_close_raw[index - 1]))
period_atr_raw = _rma(period_true_range, atr_length)

period_index = np.full(n, -1, dtype=int)
reference_close = np.full(n, np.nan, dtype=float)
atr = np.full(n, np.nan, dtype=float)
period_high = np.full(n, np.nan, dtype=float)
period_low = np.full(n, np.nan, dtype=float)
shift = 0 if use_current_close else 1
for index in range(n):
    source_index = int(np.searchsorted(period_time, base_time[index], side="right") - 1)
    period_index[index] = source_index
    if source_index >= 0:
        period_high[index] = period_high_raw[source_index]
        period_low[index] = period_low_raw[source_index]
        reference_index = source_index - shift
        if reference_index >= 0:
            reference_close[index] = period_close_raw[reference_index]
            atr[index] = period_atr_raw[reference_index]

period_range = period_high - period_low
range_percent_of_atr = np.divide(period_range, atr, out=np.full(n, np.nan, dtype=float), where=np.isfinite(atr) & (atr != 0.0)) * 100.0
fast_ema = _ema(close, fast_ema_length)
pivot_ema = _ema(close, pivot_ema_length)
slow_ema = _ema(close, slow_ema_length)
bullish = np.isfinite(fast_ema) & np.isfinite(pivot_ema) & np.isfinite(slow_ema) & (close >= fast_ema) & (fast_ema >= pivot_ema) & (pivot_ema >= slow_ema)
bearish = np.isfinite(fast_ema) & np.isfinite(pivot_ema) & np.isfinite(slow_ema) & (close <= fast_ema) & (fast_ema <= pivot_ema) & (pivot_ema <= slow_ema)

level_ratios = (0.236, 0.382, 0.500, 0.618, 0.786, 1.000, 1.236, 1.382, 1.500, 1.618, 1.786, 2.000, 2.236, 2.382, 2.500, 2.618, 2.786, 3.000)
lower_levels = {ratio: reference_close - atr * ratio for ratio in level_ratios}
upper_levels = {ratio: reference_close + atr * ratio for ratio in level_ratios}
lower_trigger = reference_close - trigger_percentage * atr
upper_trigger = reference_close + trigger_percentage * atr

def _level_plot(name, values, color, visible, show_title=False):
    return {"name": name, "title": bool(show_title and visible), "data": _finite(values) if visible else [None] * n, "color": color, "type": "line", "lineWidth": level_size, "lineStyle": "step", "overlay": True}


plots = []
for ratio in reversed(level_ratios):
    if ratio in (2.786, 2.500, 2.382):
        continue
    visible = ratio in (0.618, 1.000) or (ratio in (0.382, 0.500, 0.786) and show_all_fibonacci_levels) or (ratio in (1.236, 1.618, 2.000, 2.236, 2.618, 3.000) and show_extensions) or (ratio in (1.382, 1.500, 1.786) and show_all_fibonacci_levels and show_extensions)
    color = atr_target_color if ratio in (1.000, 2.000, 3.000) else key_target_color if ratio in (0.618, 1.236, 1.618, 2.236, 2.618) else intermediate_color
    plots.append(_level_plot(f"Lower {ratio * 100:.1f}%", lower_levels[ratio], color, visible))
plots.extend([
    _level_plot("Lower Trigger", lower_trigger, lower_trigger_color, True, True),
    _level_plot("Reference Close", reference_close, previous_close_color, True, True),
    _level_plot("Upper Trigger", upper_trigger, upper_trigger_color, True, True),
])
for ratio in level_ratios:
    if ratio in (2.382, 2.500, 2.786):
        continue
    visible = ratio in (0.618, 1.000) or (ratio in (0.382, 0.500, 0.786) and show_all_fibonacci_levels) or (ratio in (1.236, 1.618, 2.000, 2.236, 2.618, 3.000) and show_extensions) or (ratio in (1.382, 1.500, 1.786) and show_all_fibonacci_levels and show_extensions)
    color = atr_target_color if ratio in (1.000, 2.000, 3.000) else key_target_color if ratio in (0.618, 1.236, 1.618, 2.236, 2.618) else intermediate_color
    plots.append(_level_plot(f"Upper {ratio * 100:.1f}%", upper_levels[ratio], color, visible))

tables = []
if show_info and n:
    last = n - 1
    trend_text = "Bullish" if bullish[last] else "Bearish" if bearish[last] else "Neutral"
    trend_color = "#008000" if bullish[last] else "#FF0000" if bearish[last] else "#FFA500"
    range_color = "#008000" if np.isfinite(range_percent_of_atr[last]) and range_percent_of_atr[last] <= 70.0 else "#FF0000" if np.isfinite(range_percent_of_atr[last]) and range_percent_of_atr[last] >= 90.0 else "#FFA500"
    long_label, short_label = ("Calls", "Puts") if use_options_labels else ("Long", "Short")
    def value_text(value): return "N/A" if not np.isfinite(value) else f"{value:.2f}"
    tables = [{
        "title": "ATR Levels", "position": "top-right", "compact": True,
        "rows": [
            [{"text": f"{trading_type} Trend"}, {"text": trend_text, "color": trend_color}],
            [{"text": "Range / ATR"}, {"text": "N/A" if not np.isfinite(range_percent_of_atr[last]) else f"{range_percent_of_atr[last]:.1f}%", "color": range_color}],
            [{"text": long_label}, {"text": f"> {value_text(upper_trigger[last])} | +1 ATR {value_text(upper_levels[1.0][last])}", "color": upper_trigger_color}],
            [{"text": short_label}, {"text": f"< {value_text(lower_trigger[last])} | -1 ATR {value_text(lower_levels[1.0][last])}", "color": lower_trigger_color}],
        ],
    }]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": [],
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "periodTimeframe": period_timeframe, "periodSource": period_source, "periodIndex": period_index.tolist(),
        "referenceClose": _finite(reference_close), "periodAtr": _finite(atr), "periodHigh": _finite(period_high), "periodLow": _finite(period_low),
        "periodRange": _finite(period_range), "rangePercentOfAtr": _finite(range_percent_of_atr),
        "fastEma": _finite(fast_ema), "pivotEma": _finite(pivot_ema), "slowEma": _finite(slow_ema), "bullish": bullish.tolist(), "bearish": bearish.tolist(),
        "lowerTrigger": _finite(lower_trigger), "upperTrigger": _finite(upper_trigger),
        "lowerLevels": {str(ratio): _finite(values) for ratio, values in lower_levels.items()},
        "upperLevels": {str(ratio): _finite(values) for ratio, values in upper_levels.items()},
    },
}
