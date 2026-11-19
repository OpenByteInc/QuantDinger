# ============================================================
# QuantDinger Market Sessions
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Market Sessions"
my_indicator_description = "Draws four configurable fixed-offset market sessions with ranges, regression, means, VWAP, statistics and day dividers."

# @param session_a_enabled bool true Enable session A
# @param session_a_name str New_York Session A name
# @param session_a_hours str 1300-2200 Session A hours
# @param session_a_range bool true Draw session A range
# @param session_a_trendline bool false Draw session A regression
# @param session_a_mean bool false Draw session A mean
# @param session_a_vwap bool false Draw session A VWAP
# @param session_a_max_min bool false Extend session A maximum and minimum
# @param session_a_color str #FF5D00 Session A color
# @param session_b_enabled bool true Enable session B
# @param session_b_name str London Session B name
# @param session_b_hours str 0700-1600 Session B hours
# @param session_b_range bool true Draw session B range
# @param session_b_trendline bool false Draw session B regression
# @param session_b_mean bool false Draw session B mean
# @param session_b_vwap bool false Draw session B VWAP
# @param session_b_max_min bool false Extend session B maximum and minimum
# @param session_b_color str #2157F3 Session B color
# @param session_c_enabled bool true Enable session C
# @param session_c_name str Tokyo Session C name
# @param session_c_hours str 0000-0900 Session C hours
# @param session_c_range bool true Draw session C range
# @param session_c_trendline bool false Draw session C regression
# @param session_c_mean bool false Draw session C mean
# @param session_c_vwap bool false Draw session C VWAP
# @param session_c_max_min bool false Extend session C maximum and minimum
# @param session_c_color str #E91E63 Session C color
# @param session_d_enabled bool true Enable session D
# @param session_d_name str Sydney Session D name
# @param session_d_hours str 2100-0600 Session D hours
# @param session_d_range bool true Draw session D range
# @param session_d_trendline bool false Draw session D regression
# @param session_d_mean bool false Draw session D mean
# @param session_d_vwap bool false Draw session D VWAP
# @param session_d_max_min bool false Extend session D maximum and minimum
# @param session_d_color str #FFEB3B Session D color
# @param timezone_offset int 0 Fixed UTC offset range=-12:14:1
# @param use_exchange_timezone bool false Use manual exchange offset
# @param exchange_timezone_offset int 0 Manual exchange UTC offset range=-12:14:1
# @param range_transparency int 90 Range transparency range=0:100:1
# @param show_range_outline bool true Show range outline
# @param show_range_label bool true Show range labels
# @param show_dashboard bool false Show dashboard
# @param advanced_dashboard bool false Show advanced statistics
# @param dashboard_position str top-right Dashboard position values=top-right,bottom-right,bottom-left
# @param show_session_divider bool false Show session dividers
# @param show_day_divider bool true Show daily dividers


def _bounded(name, default, low_value, high_value):
    return min(high_value, max(low_value, int(params.get(name, default))))


timezone_offset = _bounded("timezone_offset", 0, -12, 14)
use_exchange_timezone = bool(params.get("use_exchange_timezone", False))
exchange_offset = _bounded("exchange_timezone_offset", 0, -12, 14)
active_offset = exchange_offset if use_exchange_timezone else timezone_offset
range_transparency = _bounded("range_transparency", 90, 0, 100)
show_outline = bool(params.get("show_range_outline", True))
show_labels = bool(params.get("show_range_label", True))
show_dashboard = bool(params.get("show_dashboard", False))
advanced_dashboard = bool(params.get("advanced_dashboard", False))
dashboard_position = str(params.get("dashboard_position", "top-right"))
show_session_divider = bool(params.get("show_session_divider", False))
show_day_divider = bool(params.get("show_day_divider", True))


configs = [
    {"letter": "A", "enabled": bool(params.get("session_a_enabled", True)), "name": str(params.get("session_a_name", "New_York")).replace("_", " "), "hours": str(params.get("session_a_hours", "1300-2200")), "range": bool(params.get("session_a_range", True)), "trendline": bool(params.get("session_a_trendline", False)), "mean": bool(params.get("session_a_mean", False)), "vwap": bool(params.get("session_a_vwap", False)), "maxMin": bool(params.get("session_a_max_min", False)), "color": str(params.get("session_a_color", "#FF5D00"))},
    {"letter": "B", "enabled": bool(params.get("session_b_enabled", True)), "name": str(params.get("session_b_name", "London")), "hours": str(params.get("session_b_hours", "0700-1600")), "range": bool(params.get("session_b_range", True)), "trendline": bool(params.get("session_b_trendline", False)), "mean": bool(params.get("session_b_mean", False)), "vwap": bool(params.get("session_b_vwap", False)), "maxMin": bool(params.get("session_b_max_min", False)), "color": str(params.get("session_b_color", "#2157F3"))},
    {"letter": "C", "enabled": bool(params.get("session_c_enabled", True)), "name": str(params.get("session_c_name", "Tokyo")), "hours": str(params.get("session_c_hours", "0000-0900")), "range": bool(params.get("session_c_range", True)), "trendline": bool(params.get("session_c_trendline", False)), "mean": bool(params.get("session_c_mean", False)), "vwap": bool(params.get("session_c_vwap", False)), "maxMin": bool(params.get("session_c_max_min", False)), "color": str(params.get("session_c_color", "#E91E63"))},
    {"letter": "D", "enabled": bool(params.get("session_d_enabled", True)), "name": str(params.get("session_d_name", "Sydney")), "hours": str(params.get("session_d_hours", "2100-0600")), "range": bool(params.get("session_d_range", True)), "trendline": bool(params.get("session_d_trendline", False)), "mean": bool(params.get("session_d_mean", False)), "vwap": bool(params.get("session_d_vwap", False)), "maxMin": bool(params.get("session_d_max_min", False)), "color": str(params.get("session_d_color", "#FFEB3B"))},
]


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _parse_hours(text):
    clean = str(text).replace(":", "")
    if len(clean) != 9 or clean[4] != "-" or not clean[:4].isdigit() or not clean[5:].isdigit():
        clean = "0000-0000"
    start = int(clean[:2]) * 60 + int(clean[2:4])
    end = int(clean[5:7]) * 60 + int(clean[7:9])
    return min(1439, max(0, start)), min(1440, max(0, end))


def _active_mask(minutes, hours):
    start, end = _parse_hours(hours)
    if start == end:
        return np.ones(len(minutes), dtype=bool)
    return (minutes >= start) & (minutes < end) if start < end else (minutes >= start) | (minutes < end)


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = np.nan if column == "volume" else 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
times = df["time"].to_numpy(dtype=float)
if len(times) and np.nanmax(np.abs(times)) > 10_000_000_000:
    times = times / 1000.0
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].to_numpy(dtype=float)
n = len(df)
bar_seconds = float(np.median(np.diff(times))) if n > 1 else 3600.0
show_session_labels = show_labels and bar_seconds < 14400.0
local_seconds = times + active_offset * 3600.0
local_minutes = (np.floor(local_seconds / 60.0).astype(np.int64) % 1440).astype(int) if n else np.asarray([], dtype=int)
local_days = np.floor(local_seconds / 86400.0).astype(np.int64) if n else np.asarray([], dtype=np.int64)


def _calculate_session(config):
    active = _active_mask(local_minutes, config["hours"]) & config["enabled"]
    started = active & ~np.r_[False, active[:-1]]
    ended = ~active & np.r_[False, active[:-1]]
    session_high = np.full(n, np.nan, dtype=float)
    session_low = np.full(n, np.nan, dtype=float)
    completed_high = np.full(n, np.nan, dtype=float)
    completed_low = np.full(n, np.nan, dtype=float)
    average = np.full(n, np.nan, dtype=float)
    vwap = np.full(n, np.nan, dtype=float)
    reg_first = np.full(n, np.nan, dtype=float)
    reg_last = np.full(n, np.nan, dtype=float)
    deviation = np.full(n, np.nan, dtype=float)
    correlation = np.full(n, np.nan, dtype=float)
    records = []
    current = None
    last_high = last_low = np.nan
    for index in range(n):
        if started[index]:
            current = {"startIndex": index, "endIndex": index, "high": float(high[index]), "low": float(low[index]), "count": 1, "sum": float(close[index]), "weighted": float(close[index]), "squared": float(close[index] * close[index]), "volume": float(volume[index]) if np.isfinite(volume[index]) else 0.0, "pv": float(close[index] * volume[index]) if np.isfinite(volume[index]) else 0.0, "first": float(close[index]), "last": float(close[index]), "deviation": 0.0, "correlation": None}
            records.append(current)
        elif active[index] and current is not None:
            current["endIndex"] = index
            current["high"] = max(current["high"], float(high[index]))
            current["low"] = min(current["low"], float(low[index]))
            current["count"] += 1
            current["sum"] += float(close[index])
            current["weighted"] += float(close[index]) * current["count"]
            current["squared"] += float(close[index] * close[index])
            if np.isfinite(volume[index]):
                current["volume"] += float(volume[index])
                current["pv"] += float(close[index] * volume[index])
            length = current["count"]
            mean = current["sum"] / length
            weighted_mean = current["weighted"] / (length * (length + 1) / 2.0)
            covariance = (weighted_mean - mean) * (length + 1) / 2.0
            variance = max(current["squared"] / length - mean * mean, 0.0)
            denominator = np.sqrt(variance) * (np.sqrt(length * length - 1.0) / (2.0 * np.sqrt(3.0)))
            current["first"] = 4.0 * mean - 3.0 * weighted_mean
            current["last"] = 3.0 * weighted_mean - 2.0 * mean
            current["deviation"] = float(np.sqrt(variance))
            current["correlation"] = float(covariance / denominator) if denominator != 0.0 else None
        if active[index] and current is not None:
            session_high[index], session_low[index] = current["high"], current["low"]
            average[index] = current["sum"] / current["count"]
            vwap[index] = current["pv"] / current["volume"] if current["volume"] != 0.0 else np.nan
            reg_first[index], reg_last[index] = current["first"], current["last"]
            deviation[index] = current["deviation"]
            correlation[index] = current["correlation"] if current["correlation"] is not None else np.nan
        else:
            if index and active[index - 1] and current is not None:
                last_high, last_low = current["high"], current["low"]
            completed_high[index], completed_low[index] = last_high, last_low
    clean_records = [{key: value for key, value in record.items() if key not in ("count", "sum", "weighted", "squared", "pv")} for record in records]
    return {"active": active, "started": started, "ended": ended, "high": session_high, "low": session_low, "completedHigh": completed_high, "completedLow": completed_low, "average": average, "vwap": vwap, "regFirst": reg_first, "regLast": reg_last, "deviation": deviation, "correlation": correlation, "records": clean_records}


results = [_calculate_session(config) for config in configs]
plots = [{"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0)", "type": "line", "overlay": True}]
layers = []
signals = []
opacity = (100.0 - range_transparency) / 100.0
for config, result in zip(configs, results):
    letter, color = config["letter"], config["color"]
    plots.extend([
        {"name": f"Session {letter} Maximum", "title": bool(config["maxMin"]), "data": _finite(result["completedHigh"]) if config["maxMin"] else [None] * n, "color": color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": f"Session {letter} Minimum", "title": bool(config["maxMin"]), "data": _finite(result["completedLow"]) if config["maxMin"] else [None] * n, "color": color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": f"Session {letter} Mean", "title": bool(config["mean"]), "data": _finite(result["average"]) if config["mean"] else [None] * n, "color": color, "type": "line", "lineWidth": 1, "overlay": True},
        {"name": f"Session {letter} VWAP", "title": bool(config["vwap"]), "data": _finite(result["vwap"]) if config["vwap"] else [None] * n, "color": color, "type": "line", "lineWidth": 1, "overlay": True},
    ])
    for record in result["records"]:
        if config["range"]:
            layers.append({"type": "zone", "role": f"Session {letter} Range", "startIndex": record["startIndex"], "endIndex": record["endIndex"], "top": record["high"], "bottom": record["low"], "fillColor": color, "opacity": opacity, "borderColor": color if show_outline else "rgba(0,0,0,0)", "borderSize": 1 if show_outline else 0})
            if show_session_labels:
                layers.append({"type": "label", "role": f"Session {letter} Label", "index": int((record["startIndex"] + record["endIndex"]) / 2), "price": record["high"], "text": config["name"], "color": "rgba(0,0,0,0)", "textColor": color, "size": "tiny"})
        if config["trendline"]:
            layers.append({"type": "line", "role": f"Session {letter} Regression", "startIndex": record["startIndex"], "endIndex": record["endIndex"], "y1": record["first"], "y2": record["last"], "color": color, "lineWidth": 1})
    divider_mask = result["active"] if show_session_divider else np.zeros(n, dtype=bool)
    boundary_mask = (result["started"] | result["ended"]) if show_session_divider else np.zeros(n, dtype=bool)
    signals.append({"type": "buy", "name": f"Session {letter} Active", "text": ".", "data": [{"active": True, "price": float(low[index]), "text": ".", "color": color} if divider_mask[index] else None for index in range(n)], "color": color, "renderMode": "events", "overlay": True})
    signals.append({"type": "buy", "name": f"Session {letter} Boundary", "text": "|", "data": [{"active": True, "price": float(low[index]), "text": "|", "color": color} if boundary_mask[index] else None for index in range(n)], "color": color, "renderMode": "events", "overlay": True})

new_day = np.zeros(n, dtype=bool)
if n > 1:
    new_day[1:] = local_days[1:] != local_days[:-1]
if show_day_divider and n:
    chart_low, chart_high = float(np.min(low)), float(np.max(high))
    day_names = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    day_indexes = np.flatnonzero(new_day)
    if bar_seconds >= 14400.0:
        day_indexes = day_indexes[::7]
    for index in day_indexes:
        layers.append({"type": "line", "role": "Daily Divider", "startIndex": int(index), "endIndex": int(index), "y1": chart_low, "y2": chart_high, "color": "#808080", "lineWidth": 1, "lineStyle": "dashed"})
        layers.append({"type": "label", "role": "Day Label", "index": int(index), "price": chart_high, "text": day_names[int((local_days[index] + 3) % 7)], "color": "rgba(0,0,0,0)", "textColor": "#808080", "size": "tiny"})

tables = []
if show_dashboard and n:
    rows = []
    for config, result in zip(configs, results):
        active_now = bool(result["active"][-1])
        cells = [{"text": config["name"], "color": config["color"]}, {"text": "Active" if active_now else "Inactive" if config["enabled"] else "Disabled", "color": "#089981" if active_now else "#F23645" if config["enabled"] else "#808080"}]
        if advanced_dashboard:
            correlation = result["correlation"][-1]
            deviation = result["deviation"][-1]
            volume_total = result["records"][-1].get("volume", None) if result["records"] else None
            cells.extend([{"text": "n/a" if not np.isfinite(correlation) else f"{correlation:.2f}"}, {"text": "n/a" if volume_total is None else f"{volume_total:.0f}"}, {"text": "n/a" if not np.isfinite(deviation) else f"{deviation:.4f}"}])
        rows.append(cells)
    tables.append({"title": "Market Sessions", "position": dashboard_position, "compact": True, "rows": rows})
if use_exchange_timezone:
    tables.append({"title": "Timezone", "position": "bottom-left", "compact": True, "rows": [[{"text": "Manual exchange UTC offset"}, {"text": f"UTC{active_offset:+d}", "color": "#FFB300"}]]})

calculated = {"activeOffset": int(active_offset), "localDay": local_days.astype(int).tolist(), "newDay": new_day.tolist()}
for config, result in zip(configs, results):
    prefix = f"session{config['letter']}"
    calculated[prefix + "Active"] = result["active"].tolist()
    calculated[prefix + "Started"] = result["started"].tolist()
    calculated[prefix + "Ended"] = result["ended"].tolist()
    calculated[prefix + "High"] = _finite(result["high"])
    calculated[prefix + "Low"] = _finite(result["low"])
    calculated[prefix + "CompletedHigh"] = _finite(result["completedHigh"])
    calculated[prefix + "CompletedLow"] = _finite(result["completedLow"])
    calculated[prefix + "Average"] = _finite(result["average"])
    calculated[prefix + "Vwap"] = _finite(result["vwap"])
    calculated[prefix + "RegressionFirst"] = _finite(result["regFirst"])
    calculated[prefix + "RegressionLast"] = _finite(result["regLast"])
    calculated[prefix + "Deviation"] = _finite(result["deviation"])
    calculated[prefix + "Correlation"] = _finite(result["correlation"])
    calculated[prefix + "Records"] = result["records"]

output = {"schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers, "tables": tables, "bar_colors": [None] * n, "background_colors": [None] * n, "calculatedVars": calculated}
