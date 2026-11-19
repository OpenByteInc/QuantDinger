# ============================================================
# QuantDinger Momentum Trade Planner
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

import math

my_indicator_name = "QuantDinger Momentum Trade Planner"
my_indicator_description = "EMA/RSI transition plans with stop and three targets, plus confirmed order blocks and ATR-filtered fair-value gaps."

# @param ema_length int 9 EMA length range=1:500:1
# @param rsi_length int 14 RSI length range=1:500:1
# @param rsi_level float 50.0 RSI level range=0:100:0.1
# @param one_at_a_time bool false Hide same-direction signals while active
# @param stop_lookback int 4 Stop lookback range=1:500:1
# @param stop_buffer float 0.0 Stop buffer range=0:100000:0.1
# @param target_step float 10.0 Target step range=0.01:100000:0.5
# @param show_levels bool true Show active plan
# @param show_hits bool true Show target and stop hits
# @param extension_bars int 10 Projection bars range=1:100:1
# @param zone_transparency int 80 Zone transparency range=0:100:1
# @param fade_bars int 15 Fade length range=0:100:1
# @param color_bars bool true Color candles
# @param show_ema bool true Show EMA
# @param stop_color str #F44336 Stop color
# @param target_color str #43D443 Target color
# @param entry_color str #9E9E9E Entry color
# @param ema_color str #2962FF EMA color
# @param show_order_blocks bool true Show order blocks
# @param swing_length int 10 Swing length range=2:50:1
# @param maximum_order_blocks int 3 Maximum order blocks per side range=1:20:1
# @param bullish_ob_color str #2962FF Bullish order block color
# @param bearish_ob_color str #E040FB Bearish order block color
# @param show_fvg bool true Show fair value gaps
# @param minimum_fvg_atr float 0.25 Minimum gap ATR multiple range=0:10:0.05
# @param maximum_fvg int 3 Maximum gaps per side range=1:20:1
# @param bullish_fvg_color str #00BCD4 Bullish gap color
# @param bearish_fvg_color str #FF9800 Bearish gap color
# @param remove_mitigated bool true Remove mitigated zones

ema_length = min(500, max(1, int(params.get("ema_length", 9))))
rsi_length = min(500, max(1, int(params.get("rsi_length", 14))))
rsi_level = min(100.0, max(0.0, float(params.get("rsi_level", 50.0))))
one_at_a_time = bool(params.get("one_at_a_time", False))
stop_lookback = min(500, max(1, int(params.get("stop_lookback", 4))))
stop_buffer = max(0.0, float(params.get("stop_buffer", 0.0)))
target_step = max(0.01, float(params.get("target_step", 10.0)))
show_levels = bool(params.get("show_levels", True))
show_hits = bool(params.get("show_hits", True))
extension_bars = min(100, max(1, int(params.get("extension_bars", 10))))
zone_transparency = min(100, max(0, int(params.get("zone_transparency", 80))))
fade_bars = min(100, max(0, int(params.get("fade_bars", 15))))
color_bars = bool(params.get("color_bars", True))
show_ema = bool(params.get("show_ema", True))
stop_color = str(params.get("stop_color", "#F44336"))
target_color = str(params.get("target_color", "#43D443"))
entry_color = str(params.get("entry_color", "#9E9E9E"))
ema_color = str(params.get("ema_color", "#2962FF"))
show_order_blocks = bool(params.get("show_order_blocks", True))
swing_length = min(50, max(2, int(params.get("swing_length", 10))))
maximum_order_blocks = min(20, max(1, int(params.get("maximum_order_blocks", 3))))
bullish_ob_color = str(params.get("bullish_ob_color", "#2962FF"))
bearish_ob_color = str(params.get("bearish_ob_color", "#E040FB"))
show_fvg = bool(params.get("show_fvg", True))
minimum_fvg_atr = max(0.0, float(params.get("minimum_fvg_atr", 0.25)))
maximum_fvg = min(20, max(1, int(params.get("maximum_fvg", 3))))
bullish_fvg_color = str(params.get("bullish_fvg_color", "#00BCD4"))
bearish_fvg_color = str(params.get("bearish_fvg_color", "#FF9800"))
remove_mitigated = bool(params.get("remove_mitigated", True))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _ema(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (period + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result


def _rma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    finite_indices = np.flatnonzero(np.isfinite(values))
    if len(finite_indices) < period:
        return result
    seed_indices = finite_indices[:period]
    seed_index = int(seed_indices[-1])
    previous = float(np.mean(values[seed_indices]))
    result[seed_index] = previous
    for index in range(seed_index + 1, len(values)):
        if np.isfinite(values[index]):
            previous = (previous * (period - 1) + float(values[index])) / period
            result[index] = previous
    return result


def _rsi(values, period):
    gains = np.full(len(values), np.nan, dtype=float)
    losses = np.full(len(values), np.nan, dtype=float)
    for index in range(1, len(values)):
        change = values[index] - values[index - 1]
        gains[index], losses[index] = max(change, 0.0), max(-change, 0.0)
    average_gain, average_loss = _rma(gains, period), _rma(losses, period)
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(len(values)):
        if not np.all(np.isfinite([average_gain[index], average_loss[index]])):
            continue
        result[index] = 100.0 if average_loss[index] == 0.0 and average_gain[index] > 0.0 else 50.0 if average_loss[index] == 0.0 else 0.0 if average_gain[index] == 0.0 else 100.0 - 100.0 / (1.0 + average_gain[index] / average_loss[index])
    return result


def _atr(high_values, low_values, close_values, period):
    true_range = np.full(len(close_values), np.nan, dtype=float)
    for index in range(len(close_values)):
        true_range[index] = high_values[index] - low_values[index] if index == 0 else max(high_values[index] - low_values[index], abs(high_values[index] - close_values[index - 1]), abs(low_values[index] - close_values[index - 1]))
    return _rma(true_range, period)


def _rgba(color, transparency):
    text = str(color).strip()
    if text.startswith("#") and len(text) >= 7:
        try:
            channels = [int(text[index:index + 2], 16) for index in (1, 3, 5)]
            return f"rgba({channels[0]},{channels[1]},{channels[2]},{max(0.0, min(1.0, 1.0 - transparency / 100.0)):.2f})"
        except ValueError:
            return text
    return text


df = df.copy().reset_index(drop=True)
if "time" not in df.columns:
    df["time"] = np.arange(len(df), dtype=float) * 3600000.0
if "volume" not in df.columns:
    df["volume"] = 0.0
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
times = df["time"].to_numpy(dtype=float)
if len(times) and max(abs(float(value)) for value in times) < 10_000_000_000:
    times = times * 1000.0
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)
ema_value = _ema(close, ema_length)
rsi_value = _rsi(close, rsi_length)
atr_value = _atr(high, low, close, 14)

previous_low = np.full(n, np.nan, dtype=float)
previous_high = np.full(n, np.nan, dtype=float)
for index in range(stop_lookback, n):
    previous_low[index] = float(np.min(low[index - stop_lookback:index]))
    previous_high[index] = float(np.max(high[index - stop_lookback:index]))
bull_condition = np.isfinite(ema_value) & np.isfinite(rsi_value) & (close > ema_value) & (rsi_value > rsi_level)
bear_condition = np.isfinite(ema_value) & np.isfinite(rsi_value) & (close < ema_value) & (rsi_value < rsi_level)

pivot_high = np.full(n, np.nan, dtype=float)
pivot_low = np.full(n, np.nan, dtype=float)
for index in range(2 * swing_length, n):
    pivot_index = index - swing_length
    high_window = high[pivot_index - swing_length:pivot_index + swing_length + 1]
    low_window = low[pivot_index - swing_length:pivot_index + swing_length + 1]
    if high[pivot_index] == np.max(high_window) and np.count_nonzero(high_window == high[pivot_index]) == 1:
        pivot_high[index] = high[pivot_index]
    if low[pivot_index] == np.min(low_window) and np.count_nonzero(low_window == low[pivot_index]) == 1:
        pivot_low[index] = low[pivot_index]

zone_id = 0
active_bull_ob, active_bear_ob, active_bull_fvg, active_bear_fvg = [], [], [], []
historical_zones = []
zone_snapshots = []
last_swing_high = last_swing_low = np.nan
last_swing_high_bar = last_swing_low_bar = -1
swing_high_used = swing_low_used = True


def _update_zones(items, bullish, price_down, price_up, index):
    for item in list(items)[::-1]:
        broken = price_down < item["bottom"] if bullish else price_up > item["top"]
        if broken:
            items.remove(item)
            if not remove_mitigated:
                item["end"] = index
                item["mitigated"] = True
                historical_zones.append(item)
        else:
            item["end"] = index + extension_bars


def _push_zone(items, item, maximum):
    items.append(item)
    if len(items) > maximum:
        items.pop(0)


for index in range(n):
    if np.isfinite(pivot_high[index]):
        last_swing_high = pivot_high[index]
        last_swing_high_bar = index - swing_length
        swing_high_used = False
    if np.isfinite(pivot_low[index]):
        last_swing_low = pivot_low[index]
        last_swing_low_bar = index - swing_length
        swing_low_used = False
    _update_zones(active_bull_ob, True, close[index], close[index], index)
    _update_zones(active_bear_ob, False, close[index], close[index], index)
    if show_order_blocks and not swing_high_used and np.isfinite(last_swing_high) and close[index] > last_swing_high:
        swing_high_used = True
        span = min(index - last_swing_high_bar, 200)
        if index >= 1 and span >= 1:
            selected_offset = 1
            for offset in range(1, span + 1):
                if low[index - offset] < low[index - selected_offset]:
                    selected_offset = offset
            zone_id += 1
            _push_zone(active_bull_ob, {"id": zone_id, "kind": "Bull OB", "bullish": True, "start": index - selected_offset, "end": index + extension_bars, "top": float(high[index - selected_offset]), "bottom": float(low[index - selected_offset]), "color": bullish_ob_color, "mitigated": False}, maximum_order_blocks)
    if show_order_blocks and not swing_low_used and np.isfinite(last_swing_low) and close[index] < last_swing_low:
        swing_low_used = True
        span = min(index - last_swing_low_bar, 200)
        if index >= 1 and span >= 1:
            selected_offset = 1
            for offset in range(1, span + 1):
                if high[index - offset] > high[index - selected_offset]:
                    selected_offset = offset
            zone_id += 1
            _push_zone(active_bear_ob, {"id": zone_id, "kind": "Bear OB", "bullish": False, "start": index - selected_offset, "end": index + extension_bars, "top": float(high[index - selected_offset]), "bottom": float(low[index - selected_offset]), "color": bearish_ob_color, "mitigated": False}, maximum_order_blocks)

    _update_zones(active_bull_fvg, True, low[index], high[index], index)
    _update_zones(active_bear_fvg, False, low[index], high[index], index)
    minimum_gap = atr_value[index] * minimum_fvg_atr if np.isfinite(atr_value[index]) else np.nan
    if show_fvg and index >= 2 and np.isfinite(minimum_gap) and low[index] > high[index - 2] and low[index] - high[index - 2] >= minimum_gap:
        zone_id += 1
        _push_zone(active_bull_fvg, {"id": zone_id, "kind": "Bull FVG", "bullish": True, "start": index - 2, "end": index + extension_bars, "top": float(low[index]), "bottom": float(high[index - 2]), "color": bullish_fvg_color, "mitigated": False}, maximum_fvg)
    if show_fvg and index >= 2 and np.isfinite(minimum_gap) and high[index] < low[index - 2] and low[index - 2] - high[index] >= minimum_gap:
        zone_id += 1
        _push_zone(active_bear_fvg, {"id": zone_id, "kind": "Bear FVG", "bullish": False, "start": index - 2, "end": index + extension_bars, "top": float(low[index - 2]), "bottom": float(high[index]), "color": bearish_fvg_color, "mitigated": False}, maximum_fvg)
    zone_snapshots.append([len(active_bull_ob), len(active_bear_ob), len(active_bull_fvg), len(active_bear_fvg), len(historical_zones)])

direction = np.zeros(n, dtype=int)
entry_bar = np.full(n, -1, dtype=int)
entry = np.full(n, np.nan, dtype=float)
stop = np.full(n, np.nan, dtype=float)
target_1 = np.full(n, np.nan, dtype=float)
target_2 = np.full(n, np.nan, dtype=float)
target_3 = np.full(n, np.nan, dtype=float)
hit_1_state = np.zeros(n, dtype=bool)
hit_2_state = np.zeros(n, dtype=bool)
hit_3_state = np.zeros(n, dtype=bool)
buy_signal = np.zeros(n, dtype=bool)
sell_signal = np.zeros(n, dtype=bool)
target_1_hit = np.zeros(n, dtype=bool)
target_2_hit = np.zeros(n, dtype=bool)
target_3_hit = np.zeros(n, dtype=bool)
stop_hit = np.zeros(n, dtype=bool)
current_direction = 0
current_entry_bar = -1
current_entry = current_stop = current_target_1 = current_target_2 = current_target_3 = np.nan
current_hit_1 = current_hit_2 = current_hit_3 = False
plan_end = -1

for index in range(n):
    prior_bull = bull_condition[index - 1] if index >= 1 else False
    prior_bear = bear_condition[index - 1] if index >= 1 else False
    buy_signal[index] = bull_condition[index] and not prior_bull and (not one_at_a_time or current_direction != 1)
    sell_signal[index] = bear_condition[index] and not prior_bear and (not one_at_a_time or current_direction != -1)
    if current_direction != 0 and index > current_entry_bar:
        is_long = current_direction == 1
        if (is_long and low[index] <= current_stop) or ((not is_long) and high[index] >= current_stop):
            stop_hit[index] = True
        else:
            if not current_hit_1 and ((is_long and high[index] >= current_target_1) or ((not is_long) and low[index] <= current_target_1)):
                current_hit_1 = True
                target_1_hit[index] = True
            if not current_hit_2 and ((is_long and high[index] >= current_target_2) or ((not is_long) and low[index] <= current_target_2)):
                current_hit_2 = True
                target_2_hit[index] = True
            if not current_hit_3 and ((is_long and high[index] >= current_target_3) or ((not is_long) and low[index] <= current_target_3)):
                current_hit_3 = True
                target_3_hit[index] = True
        if stop_hit[index] or current_hit_3:
            current_direction = 0
            plan_end = index
    if buy_signal[index] or sell_signal[index]:
        current_direction = 1 if buy_signal[index] else -1
        current_entry_bar = index
        current_entry = close[index]
        current_stop = previous_low[index] - stop_buffer if buy_signal[index] else previous_high[index] + stop_buffer
        current_target_1 = current_entry + current_direction * target_step
        current_target_2 = current_entry + current_direction * target_step * 2.0
        current_target_3 = current_entry + current_direction * target_step * 3.0
        current_hit_1 = current_hit_2 = current_hit_3 = False
        plan_end = index + extension_bars
    if current_direction != 0:
        plan_end = index + extension_bars
    direction[index] = current_direction
    entry_bar[index] = current_entry_bar
    entry[index], stop[index] = current_entry, current_stop
    target_1[index], target_2[index], target_3[index] = current_target_1, current_target_2, current_target_3
    hit_1_state[index], hit_2_state[index], hit_3_state[index] = current_hit_1, current_hit_2, current_hit_3

layers = []
all_zones = historical_zones + active_bull_ob + active_bear_ob + active_bull_fvg + active_bear_fvg
for item in all_zones:
    layers.append({"type": "zone", "role": item["kind"], "startIndex": int(item["start"]), "endIndex": int(item["end"]), "top": item["top"], "bottom": item["bottom"], "fillColor": _rgba(item["color"], 82), "borderColor": _rgba(item["color"], 40), "borderSize": 1, "mitigated": bool(item["mitigated"])})

if show_levels and current_entry_bar >= 0 and np.all(np.isfinite([current_entry, current_stop, current_target_1, current_target_2, current_target_3])):
    end_index = max(current_entry_bar, plan_end)
    levels = (("Stop Loss", current_stop, stop_color), ("Entry", current_entry, entry_color), ("Take Profit 1", current_target_1, target_color), ("Take Profit 2", current_target_2, target_color), ("Take Profit 3", current_target_3, target_color))
    for role, price, color in levels:
        layers.append({"type": "line", "role": role, "startIndex": int(current_entry_bar), "endIndex": int(end_index), "y1": float(price), "y2": float(price), "color": color, "lineWidth": 1})
        layers.append({"type": "label", "role": role, "index": int(end_index), "price": float(price), "text": f"{role}: {price:.6g}", "color": color, "textColor": "#FFFFFF"})
    for role, first, second, color in (("Risk Zone", current_entry, current_stop, stop_color), ("Target Zone", current_entry, current_target_3, target_color)):
        fade_start = max(current_entry_bar, end_index - fade_bars)
        if fade_start > current_entry_bar:
            layers.append({"type": "zone", "role": role, "startIndex": int(current_entry_bar), "endIndex": int(fade_start), "top": float(max(first, second)), "bottom": float(min(first, second)), "fillColor": _rgba(color, zone_transparency), "borderColor": "rgba(0,0,0,0)", "borderSize": 0})
        span = end_index - fade_start
        for segment in range(6):
            left = fade_start + int(math.floor(span * segment / 6.0 + 0.5))
            right = fade_start + int(math.floor(span * (segment + 1) / 6.0 + 0.5))
            if right > left:
                transparency = zone_transparency + (100.0 - zone_transparency) * (segment + 1) / 7.0
                layers.append({"type": "zone", "role": f"{role} Fade {segment + 1}", "startIndex": int(left), "endIndex": int(right), "top": float(max(first, second)), "bottom": float(min(first, second)), "fillColor": _rgba(color, transparency), "borderColor": "rgba(0,0,0,0)", "borderSize": 0})

visual_events = []
for index in range(n):
    event_kind = None
    if stop_hit[index]:
        event_kind = "stop"
    elif buy_signal[index]:
        event_kind = "buy"
    elif sell_signal[index]:
        event_kind = "sell"
    elif target_1_hit[index] or target_2_hit[index] or target_3_hit[index]:
        event_kind = "target"
    if event_kind is not None and (not visual_events or index - visual_events[-1][0] >= 5):
        visual_events.append((index, event_kind))
visual_event_map = dict(visual_events[-14:])

signals = [
    {"type": "buy", "name": "Buy", "text": "Buy", "data": [float(low[index]) if visual_event_map.get(index) == "buy" else None for index in range(n)], "color": target_color, "renderMode": "events"},
    {"type": "sell", "name": "Sell", "text": "Sell", "data": [float(high[index]) if visual_event_map.get(index) == "sell" else None for index in range(n)], "color": stop_color, "renderMode": "events"},
]
if show_hits:
    signals.extend([
        {"type": "sell", "name": "Target Hit", "text": "TP", "data": [float(high[index] if direction[index - 1] == 1 else low[index]) if index >= 1 and visual_event_map.get(index) == "target" else None for index in range(n)], "color": target_color, "renderMode": "events"},
        {"type": "sell", "name": "Stop Hit", "text": "SL", "data": [float(low[index] if direction[index - 1] == 1 else high[index]) if index >= 1 and visual_event_map.get(index) == "stop" else None for index in range(n)], "color": stop_color, "renderMode": "events"},
    ])

runtime_ctx = globals().get("ctx")
chart_timeframe = str(getattr(runtime_ctx, "timeframe", "") or "")
tables = []
if chart_timeframe not in ("15", "15m", "15M"):
    tables = [{"title": "Timeframe", "position": "top-right", "compact": True, "rows": [[{"text": "Designed for 15m", "color": "#FFFFFF"}, {"text": f"Current: {chart_timeframe}", "color": "#FFA500"}]]}]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "EMA", "data": _finite(ema_value) if show_ema else [None] * n, "color": ema_color, "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": signals,
    "layers": layers,
    "tables": tables,
    "bar_colors": [target_color if color_bars and bull_condition[index] else stop_color if color_bars and bear_condition[index] else "#808080" if color_bars else None for index in range(n)],
    "background_colors": [None] * n,
    "calculatedVars": {
        "ema": _finite(ema_value), "rsi": _finite(rsi_value), "atr": _finite(atr_value), "previousLow": _finite(previous_low), "previousHigh": _finite(previous_high),
        "bullCondition": bull_condition.tolist(), "bearCondition": bear_condition.tolist(), "buySignal": buy_signal.tolist(), "sellSignal": sell_signal.tolist(),
        "direction": direction.tolist(), "entryBar": entry_bar.tolist(), "entry": _finite(entry), "stop": _finite(stop), "target1": _finite(target_1), "target2": _finite(target_2), "target3": _finite(target_3),
        "hit1State": hit_1_state.tolist(), "hit2State": hit_2_state.tolist(), "hit3State": hit_3_state.tolist(),
        "target1Hit": target_1_hit.tolist(), "target2Hit": target_2_hit.tolist(), "target3Hit": target_3_hit.tolist(), "stopHit": stop_hit.tolist(),
        "pivotHigh": _finite(pivot_high), "pivotLow": _finite(pivot_low), "zoneSnapshots": [[int(value) for value in snapshot] for snapshot in zone_snapshots],
        "activeBullOB": len(active_bull_ob), "activeBearOB": len(active_bear_ob), "activeBullFVG": len(active_bull_fvg), "activeBearFVG": len(active_bear_fvg), "historicalZones": len(historical_zones),
        "timeframe": chart_timeframe, "designedTimeframe": "15m",
    },
}
