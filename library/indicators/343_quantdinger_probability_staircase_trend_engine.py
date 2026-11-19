# ============================================================
# QuantDinger Probability Staircase Trend Engine
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Probability Staircase Trend Engine"
my_indicator_description = "Tracks every adaptive-trend flip as a shadow trade, learns empirical R-level survival and time-to-reach, and issues calibrated TAKE, SKIP, or CALIBRATING staircase plans."

# @param sensitivity str Balanced Trend sensitivity values=Fast,Balanced,Slow
# @param stop_lookback int 10 Stop swing lookback range=3:50:1
# @param plan_horizon int 60 Plan horizon in bars range=10:300:1
# @param minimum_sample int 20 Minimum resolved sample range=8:150:1
# @param target_ladder str Balanced Target ladder values=Conservative,Balanced,Aggressive
# @param split_by_efficiency bool true Split evidence by trend efficiency
# @param show_plan bool true Show probability staircase
# @param past_plans_kept int 2 Past plans kept range=0:5:1
# @param show_skip_flips bool true Show SKIP flips
# @param bull_color str #00A8C6 Bull color
# @param bear_color str #F0457A Bear color
# @param show_dashboard bool true Show dashboard
# @param table_size str Small Table size values=Tiny,Small,Normal

sensitivity = str(params.get("sensitivity", "Balanced"))
stop_lookback = min(50, max(3, int(params.get("stop_lookback", 10))))
plan_horizon = min(300, max(10, int(params.get("plan_horizon", 60))))
minimum_sample = min(150, max(8, int(params.get("minimum_sample", 20))))
target_ladder = str(params.get("target_ladder", "Balanced"))
split_by_efficiency = bool(params.get("split_by_efficiency", True))
show_plan = bool(params.get("show_plan", True))
past_plans_kept = min(5, max(0, int(params.get("past_plans_kept", 2))))
show_skip_flips = bool(params.get("show_skip_flips", True))
bull_color = str(params.get("bull_color", "#00A8C6"))
bear_color = str(params.get("bear_color", "#F0457A"))
show_dashboard = bool(params.get("show_dashboard", True))
table_size = str(params.get("table_size", "Small"))

if sensitivity == "Fast":
    efficiency_length, slow_length, band_factor = 8, 20, 1.0
elif sensitivity == "Slow":
    efficiency_length, slow_length, band_factor = 14, 40, 2.0
else:
    efficiency_length, slow_length, band_factor = 10, 30, 1.5
if target_ladder == "Conservative":
    ladder_probabilities = (0.70, 0.50, 0.30)
elif target_ladder == "Aggressive":
    ladder_probabilities = (0.50, 0.30, 0.15)
else:
    ladder_probabilities = (0.60, 0.40, 0.20)

grid = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]


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


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    seed = []
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            seed.append(float(value))
            if len(seed) == length:
                previous = sum(seed) / length
                result[index] = previous
        else:
            previous = (previous * (length - 1.0) + float(value)) / length
            result[index] = previous
    return result


def _rolling_sum(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    running = 0.0
    valid = 0
    for index, value in enumerate(values):
        if np.isfinite(value):
            running += float(value)
            valid += 1
        if index >= length and np.isfinite(values[index - length]):
            running -= float(values[index - length])
            valid -= 1
        if index >= length - 1 and valid == length:
            result[index] = running
    return result


def _rolling_extreme(values, length, maximum):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.max(window) if maximum else np.min(window))
    return result


def _rolling_percentile(values, length, percentile):
    result = np.full(len(values), np.nan, dtype=float)
    rank = (length - 1) * percentile / 100.0
    lower_rank = int(np.floor(rank))
    upper_rank = int(np.ceil(rank))
    weight = rank - lower_rank
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            ordered = np.sort(window)
            result[index] = float(ordered[lower_rank] * (1.0 - weight) + ordered[upper_rank] * weight)
    return result


df = df.copy()
frame = _normalize(df)
time_values = frame["time"].to_numpy(dtype=float)
high = frame["high"].to_numpy(dtype=float)
low = frame["low"].to_numpy(dtype=float)
close = frame["close"].to_numpy(dtype=float)
n = len(frame)

true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    true_range[index] = high[index] - low[index] if index == 0 else max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
atr = _rma(true_range, 14)
absolute_change = np.full(n, np.nan, dtype=float)
if n > 1:
    absolute_change[1:] = np.abs(close[1:] - close[:-1])
path_length = _rolling_sum(absolute_change, efficiency_length)
net_move = np.full(n, np.nan, dtype=float)
if n > efficiency_length:
    net_move[efficiency_length:] = np.abs(close[efficiency_length:] - close[:-efficiency_length])
efficiency = np.divide(net_move, path_length, out=np.zeros(n, dtype=float), where=np.isfinite(path_length) & (path_length > 0.0))
efficiency_median = _rolling_percentile(efficiency, 200, 50.0)
clean_context = np.where(np.isfinite(efficiency_median), efficiency >= efficiency_median, efficiency >= 0.30)
fast_constant = 2.0 / 3.0
slow_constant = 2.0 / (slow_length + 1.0)
smoothing_constant = (efficiency * (fast_constant - slow_constant) + slow_constant) ** 2
kama = np.full(n, np.nan, dtype=float)
previous_kama = np.nan
for index in range(n):
    previous_kama = close[index] if not np.isfinite(previous_kama) else previous_kama + smoothing_constant[index] * (close[index] - previous_kama)
    kama[index] = previous_kama
swing_low = _rolling_extreme(low, stop_lookback, False)
swing_high = _rolling_extreme(high, stop_lookback, True)

trail = np.full(n, np.nan, dtype=float)
trend = np.zeros(n, dtype=int)
flip_up = np.zeros(n, dtype=bool)
flip_down = np.zeros(n, dtype=bool)
trend_state = 0
trail_state = np.nan
for index in range(n):
    if not np.isfinite(atr[index]):
        continue
    upper_trail = kama[index] - band_factor * atr[index]
    lower_trail = kama[index] + band_factor * atr[index]
    if trend_state == 0:
        trend_state = 1 if close[index] >= kama[index] else -1
        trail_state = upper_trail if trend_state == 1 else lower_trail
    elif trend_state == 1:
        trail_state = max(trail_state, upper_trail)
        if close[index] < trail_state:
            trend_state = -1
            trail_state = lower_trail
            flip_down[index] = True
    else:
        trail_state = min(trail_state, lower_trail)
        if close[index] > trail_state:
            trend_state = 1
            trail_state = upper_trail
            flip_up[index] = True
    trend[index] = trend_state
    trail[index] = trail_state


def _in_pool(outcome, level, bucket, direction_index):
    return outcome["bucket"] == bucket if level == 0 else outcome["dir"] == direction_index if level == 1 else True


def _statistics(store, bucket, direction_index):
    bucket_count = sum(item["bucket"] == bucket for item in store)
    direction_count = sum(item["dir"] == direction_index for item in store)
    all_count = len(store)
    level = 0 if bucket_count >= minimum_sample else 1 if direction_count >= minimum_sample else 2 if all_count >= minimum_sample else 3
    sample_count = bucket_count if level == 0 else direction_count if level == 1 else all_count
    result = {"level": level, "count": sample_count, "g1": -1, "g2": -1, "g3": -1, "s1": None, "s2": None, "s3": None, "edge": None, "survival": [0.0] * 8}
    if level >= 3 or sample_count <= 0:
        return result
    selected = [item for item in store if _in_pool(item, level, bucket, direction_index)]
    survival = [sum(item["mfe_r"] >= target for item in selected) / sample_count for target in grid]
    g1 = g2 = g3 = -1
    for grid_index, probability in enumerate(survival):
        if probability >= ladder_probabilities[0]:
            g1 = grid_index
        if probability >= ladder_probabilities[1]:
            g2 = grid_index
        if probability >= ladder_probabilities[2]:
            g3 = grid_index
    if g2 == g1:
        g2 = -1
    previous_grid = g2 if g2 >= 0 else g1
    if g3 == previous_grid:
        g3 = -1
    target = grid[max(g1, 0)]
    edge = sum(target if item["mfe_r"] >= target else item["exit_r"] for item in selected) / sample_count
    result.update({"g1": g1, "g2": g2, "g3": g3, "s1": survival[g1] if g1 >= 0 else None, "s2": survival[g2] if g2 >= 0 else None, "s3": survival[g3] if g3 >= 0 else None, "edge": edge, "survival": survival})
    return result


def _median_bars(store, level, bucket, direction_index, grid_index):
    if grid_index < 0 or level >= 3:
        return None
    values = [item["reach"][grid_index] for item in store if _in_pool(item, level, bucket, direction_index) and item["reach"][grid_index] >= 0]
    return float(np.median(values)) if values else None


store = []
plans = []
open_trade = None
ties = 0
resolved = 0
last_exit = "—"
last_direction = 0
last_verdict = -1
last_signal_bar = -1
snapshot = {"level": 3, "count": 0, "g1": -1, "g2": -1, "g3": -1, "s1": None, "s2": None, "s3": None, "m1": None, "m2": None, "m3": None, "edge": None, "clean": True, "survival": [0.0] * 8}
verdict_series = np.full(n, -1, dtype=int)
signal_code = np.zeros(n, dtype=float)
edge_series = np.full(n, np.nan, dtype=float)
probability_series = np.full(n, np.nan, dtype=float)
bars_since_flip = np.full(n, np.nan, dtype=float)
take_up = np.zeros(n, dtype=bool)
take_down = np.zeros(n, dtype=bool)
skip_up = np.zeros(n, dtype=bool)
skip_down = np.zeros(n, dtype=bool)
calibrate_up = np.zeros(n, dtype=bool)
calibrate_down = np.zeros(n, dtype=bool)

for index in range(n):
    resolve_now = False
    stopped = False
    if open_trade is not None and index > open_trade["start"] and open_trade["risk"] > 0.0:
        stop_hit = low[index] <= open_trade["stop"] if open_trade["dir"] == 1 else high[index] >= open_trade["stop"]
        bar_mfe = (high[index] - open_trade["entry"]) / open_trade["risk"] if open_trade["dir"] == 1 else (open_trade["entry"] - low[index]) / open_trade["risk"]
        if stop_hit:
            if any(open_trade["reach"][grid_index] < 0 and bar_mfe >= grid[grid_index] for grid_index in range(8)):
                ties += 1
            resolve_now = True
            stopped = True
        else:
            if bar_mfe > open_trade["mfe"]:
                open_trade["mfe"] = float(bar_mfe)
                for grid_index in range(8):
                    if open_trade["reach"][grid_index] < 0 and open_trade["mfe"] >= grid[grid_index]:
                        open_trade["reach"][grid_index] = index - open_trade["start"]
            if index - open_trade["start"] >= plan_horizon:
                resolve_now = True
        if flip_up[index] or flip_down[index]:
            resolve_now = True
    if resolve_now and open_trade is not None:
        exit_r = -1.0 if stopped else open_trade["dir"] * (close[index] - open_trade["entry"]) / open_trade["risk"]
        outcome = {"bucket": open_trade["bucket"], "dir": 0 if open_trade["dir"] == 1 else 1, "mfe_r": max(open_trade["mfe"], 0.0), "exit_r": float(exit_r), "reach": list(open_trade["reach"]), "start": open_trade["start"], "end": index, "stopped": bool(stopped)}
        store.append(outcome)
        store = store[-300:]
        resolved += 1
        last_exit = "stopped" if stopped else "reversed" if flip_up[index] or flip_down[index] else "horizon"
        if plans and plans[-1]["live"]:
            plans[-1]["live"] = False
            plans[-1]["end"] = min(plans[-1]["end"], index)
            plans[-1]["reached"] = [open_trade["reach"][grid_index] >= 0 if grid_index >= 0 else False for grid_index in plans[-1]["display_grids"]]
            plans[-1]["stopped"] = bool(stopped)
        open_trade = None

    if (flip_up[index] or flip_down[index]) and np.isfinite(atr[index]) and atr[index] > 0.0:
        direction = 1 if flip_up[index] else -1
        direction_index = 0 if direction == 1 else 1
        swing = swing_low[index] if direction == 1 else swing_high[index]
        raw_risk = atr[index] if not np.isfinite(swing) else close[index] - swing if direction == 1 else swing - close[index]
        risk = max(0.5 * atr[index], min(3.0 * atr[index], raw_risk))
        context_index = (0 if clean_context[index] else 1) if split_by_efficiency else 0
        bucket = direction_index * 2 + context_index
        stats = _statistics(store, bucket, direction_index)
        medians = [_median_bars(store, stats["level"], bucket, direction_index, stats[key]) for key in ("g1", "g2", "g3")]
        verdict = 2 if stats["level"] == 3 else 1 if stats["g1"] >= 0 and stats["edge"] is not None and stats["edge"] > 0.0 else 0
        open_trade = {"dir": direction, "bucket": bucket, "start": index, "entry": float(close[index]), "risk": float(risk), "stop": float(close[index] - direction * risk), "mfe": 0.0, "reach": [-1] * 8}
        last_direction = direction
        last_verdict = verdict
        last_signal_bar = index
        snapshot = {**stats, "m1": medians[0], "m2": medians[1], "m3": medians[2], "clean": bool(clean_context[index])}
        verdict_series[index] = verdict
        signal_code[index] = direction * (1.0 if verdict == 1 else 0.5)
        if verdict == 1:
            take_up[index] = direction == 1
            take_down[index] = direction == -1
        elif verdict == 0:
            skip_up[index] = direction == 1
            skip_down[index] = direction == -1
        else:
            calibrate_up[index] = direction == 1
            calibrate_down[index] = direction == -1
        if show_plan:
            if plans:
                plans[-1]["faded"] = True
            display_grids = [1, 3, 5] if verdict == 2 else [stats["g1"], stats["g2"], stats["g3"]]
            widths = []
            for target_index, median in enumerate(medians):
                if verdict == 2:
                    width = int(np.floor(plan_horizon * (0.25, 0.50, 0.75)[target_index] + 0.5))
                else:
                    width = int(np.floor((plan_horizon * 0.5 if median is None else median) + 0.5))
                widths.append(max(2, min(width, 480)))
            plans.append({"start": index, "end": index + max(widths), "entry": float(close[index]), "stop": float(open_trade["stop"]), "risk": float(risk), "dir": direction, "verdict": verdict, "level": stats["level"], "count": stats["count"], "display_grids": display_grids, "probabilities": [None, None, None] if verdict == 2 else [stats["s1"], stats["s2"], stats["s3"]], "widths": widths, "live": True, "faded": False, "reached": [False, False, False], "stopped": False})
            plans = plans[-(past_plans_kept + 1):]
    if last_signal_bar >= 0:
        bars_since_flip[index] = index - last_signal_bar
    if snapshot["edge"] is not None:
        edge_series[index] = snapshot["edge"]
    if snapshot["s1"] is not None:
        probability_series[index] = snapshot["s1"] * 100.0

trail_display = trail.copy()
for index in range(1, n):
    if trend[index] != trend[index - 1]:
        trail_display[index] = np.nan
trend_cloud = []
for index in range(n):
    if np.isfinite(trail_display[index]):
        midpoint = (high[index] + low[index]) / 2.0
        trend_cloud.append({"upper": float(max(trail_display[index], midpoint)), "lower": float(min(trail_display[index], midpoint)), "color": "rgba(0,168,198,0.10)" if trend[index] == 1 else "rgba(240,69,122,0.10)"})
    else:
        trend_cloud.append(None)

layers = []
for plan in plans:
    color = "#8A93A6" if plan["verdict"] == 0 else bull_color if plan["dir"] == 1 else bear_color
    previous_level = plan["entry"]
    maximum_end = plan["start"] + 2
    for target_index, grid_index in enumerate(plan["display_grids"]):
        if grid_index < 0:
            continue
        target = plan["entry"] + plan["dir"] * grid[grid_index] * plan["risk"]
        target_end = min(plan["start"] + plan["widths"][target_index], plan["end"])
        maximum_end = max(maximum_end, target_end)
        probability = plan["probabilities"][target_index]
        alpha = 0.12 if plan["faded"] else 0.28 if probability is None else min(0.48, 0.18 + probability * 0.20)
        layers.append({"type": "zone", "role": f"T{target_index + 1}", "startIndex": plan["start"], "endIndex": target_end, "top": float(max(previous_level, target)), "bottom": float(min(previous_level, target)), "fillColor": color, "opacity": alpha, "borderColor": color})
        layers.append({"type": "label", "role": f"T{target_index + 1} Label", "index": target_end, "price": float(target), "text": f"T{target_index + 1} +{grid[grid_index]:g}R" + (" ✓" if plan["reached"][target_index] else ""), "color": "rgba(0,0,0,0)", "textColor": color, "size": "small"})
        previous_level = target
    layers.append({"type": "zone", "role": "Stop", "startIndex": plan["start"], "endIndex": min(maximum_end, plan["end"]), "top": float(max(plan["entry"], plan["stop"])), "bottom": float(min(plan["entry"], plan["stop"])), "fillColor": "#8A93A6", "opacity": 0.12, "borderColor": "#8A93A6", "dashed": True})
    layers.append({"type": "line", "role": "Entry", "startIndex": plan["start"], "endIndex": min(maximum_end, plan["end"]), "y1": plan["entry"], "y2": plan["entry"], "color": "rgba(138,147,166,0.65)", "dashed": True, "lineWidth": 1})


def _signal_data(mask, prices, text, color):
    return [{"active": True, "price": float(prices[index]), "text": text, "color": color} if mask[index] and np.isfinite(prices[index]) else None for index in range(n)]


offset = np.where(np.isfinite(atr), 0.35 * atr, 0.0)
tables = []
if show_dashboard:
    trend_text = ("▲ BULL" if n and trend[-1] == 1 else "▼ BEAR" if n and trend[-1] == -1 else "—") + (" · clean" if n and clean_context[-1] else " · choppy")
    verdict_text = "TAKE" if last_verdict == 1 else "SKIP" if last_verdict == 0 else "CALIBRATING" if last_verdict == 2 else "—"
    pool_text = "Direction + context" if snapshot["level"] == 0 else "Direction pool" if snapshot["level"] == 1 else "All flips" if snapshot["level"] == 2 else "Calibrating"
    def target_text(key_grid, key_survival, key_median, required):
        grid_index = snapshot[key_grid]
        if snapshot["level"] == 3 or grid_index < 0:
            return f"none ≥ {required:.0%}" if key_grid == "g1" else "—"
        median = snapshot[key_median]
        return f"+{grid[grid_index]:g}R · {snapshot[key_survival]:.0%} · ~{median:.0f} bars" if median is not None else f"+{grid[grid_index]:g}R · {snapshot[key_survival]:.0%}"
    live_text = f"Open · MFE +{open_trade['mfe']:.1f}R · {n - 1 - open_trade['start']}/{plan_horizon}" if open_trade is not None else "—" if last_signal_bar < 0 else f"Closed · {last_exit}"
    tables.append({"title": "Probability Staircase", "position": "top-right", "size": table_size.lower(), "compact": True, "rows": [
        [{"text": "Trend"}, {"text": trend_text}],
        [{"text": "Last flip"}, {"text": "—" if last_signal_bar < 0 else f"{'LONG' if last_direction == 1 else 'SHORT'} · {verdict_text} · {n - 1 - last_signal_bar} bars ago"}],
        [{"text": "Evidence"}, {"text": f"{pool_text} · n={snapshot['count']}" if snapshot["level"] < 3 else f"Calibrating {len(store)}/{minimum_sample}"}],
        [{"text": "T1"}, {"text": target_text("g1", "s1", "m1", ladder_probabilities[0])}],
        [{"text": "T2"}, {"text": target_text("g2", "s2", "m2", ladder_probabilities[1])}],
        [{"text": "T3"}, {"text": target_text("g3", "s3", "m3", ladder_probabilities[2])}],
        [{"text": "Edge at T1"}, {"text": "—" if snapshot["edge"] is None else f"{snapshot['edge']:+.2f}R / trade"}],
        [{"text": "Live plan"}, {"text": live_text}],
        [{"text": "Record"}, {"text": f"{resolved} resolved · {ties} stop-first ties"}],
        [{"text": "Data mode"}, {"text": "ALL PLANS · closed-bar OHLC"}],
    ]})

output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "Trend Trail", "data": _finite(trail_display), "color": [bull_color if value == 1 else bear_color if value == -1 else "#8A93A6" for value in trend], "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Trend Fill", "title": False, "data": trend_cloud, "color": "rgba(0,168,198,0.10)", "type": "cloud", "overlay": True},
        {"name": "Scale Anchor High", "title": False, "data": _finite(high), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Scale Anchor Low", "title": False, "data": _finite(low), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Signal Code", "data": _finite(signal_code), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Trend State", "data": [int(value) for value in trend], "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Edge at T1", "data": _finite(edge_series), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "T1 Reach Probability", "data": _finite(probability_series), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Bars Since Flip", "data": _finite(bars_since_flip), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": False},
    ],
    "signals": [
        {"type": "buy", "name": "Long TAKE", "data": _signal_data(take_up, low - offset, "TAKE", bull_color), "color": bull_color, "renderMode": "events"},
        {"type": "sell", "name": "Short TAKE", "data": _signal_data(take_down, high + offset, "TAKE", bear_color), "color": bear_color, "renderMode": "events"},
        {"type": "buy", "name": "Long SKIP", "data": _signal_data(skip_up & show_skip_flips, low - offset, "×", "#8A93A6"), "color": "#8A93A6", "renderMode": "events"},
        {"type": "sell", "name": "Short SKIP", "data": _signal_data(skip_down & show_skip_flips, high + offset, "×", "#8A93A6"), "color": "#8A93A6", "renderMode": "events"},
        {"type": "buy", "name": "Long Calibrating", "data": _signal_data(calibrate_up, low - offset, "CAL", bull_color), "color": bull_color, "renderMode": "events"},
        {"type": "sell", "name": "Short Calibrating", "data": _signal_data(calibrate_down, high + offset, "CAL", bear_color), "color": bear_color, "renderMode": "events"},
    ],
    "layers": layers, "tables": tables, "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "sensitivity": sensitivity, "efficiencyLength": efficiency_length, "slowLength": slow_length, "bandFactor": band_factor, "ladderProbabilities": list(ladder_probabilities),
        "trueRange": _finite(true_range), "atr": _finite(atr), "pathLength": _finite(path_length), "netMove": _finite(net_move), "efficiency": _finite(efficiency), "efficiencyMedian": _finite(efficiency_median), "cleanContext": [bool(value) for value in clean_context],
        "smoothingConstant": _finite(smoothing_constant), "kama": _finite(kama), "swingLow": _finite(swing_low), "swingHigh": _finite(swing_high), "trail": _finite(trail), "trend": [int(value) for value in trend], "flipUp": flip_up.tolist(), "flipDown": flip_down.tolist(),
        "verdict": [int(value) for value in verdict_series], "signalCode": _finite(signal_code), "edge": _finite(edge_series), "t1Probability": _finite(probability_series), "barsSinceFlip": _finite(bars_since_flip),
        "outcomes": store, "plans": plans, "activeTrade": open_trade, "resolved": resolved, "ties": ties, "lastExit": last_exit, "lastDirection": last_direction, "lastVerdict": last_verdict, "lastSignalBar": last_signal_bar, "snapshot": snapshot,
    },
}
