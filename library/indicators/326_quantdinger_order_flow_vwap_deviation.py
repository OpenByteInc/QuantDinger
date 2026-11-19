# ============================================================
# QuantDinger Order Flow VWAP Deviation
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

import math

my_indicator_name = "QuantDinger Order Flow VWAP Deviation"
my_indicator_description = "Combines anchored VWAP deviation bands, volume-confirmed pivot sweeps, inversion gaps, and an anchored close-volume profile."

# @param vwap_anchor str Session VWAP reset period values=Session,Week,Month,Year
# @param vwap_timezone_offset_minutes int 0 VWAP calendar offset from UTC range=-840:840:15
# @param deviation_multiplier float 2 VWAP deviation multiplier range=0.5:10:0.1
# @param pivot_lookback int 50 Symmetric stop-zone pivot length range=10:250:1
# @param max_active_lines int 10 Maximum unresolved lines per side range=1:50:1
# @param profile_rows int 50 Volume-profile rows range=10:100:1
# @param profile_width int 30 Maximum projected profile width range=5:100:1
# @param profile_offset int 10 Profile right offset range=0:100:1
# @param session_anchor_start str 1800 Profile reset time in HHMM
# @param session_timezone_offset_minutes int -240 Profile session offset from UTC range=-840:840:15
# @param stop_zone_multiplier float 1.5 Profile stop-zone volume multiplier range=1:10:0.1
# @param show_ifvg bool false Show inversion fair-value gaps
# @param ifvg_volatility_filter float 1.2 IFVG body filter range=0.5:10:0.1
# @param ifvg_history int 5 Maximum active IFVGs range=1:20:1

vwap_anchor = str(params.get("vwap_anchor", "Session")).title()
if vwap_anchor not in ("Session", "Week", "Month", "Year"):
    vwap_anchor = "Session"
vwap_timezone_offset_minutes = min(840, max(-840, int(params.get("vwap_timezone_offset_minutes", 0))))
deviation_multiplier = min(10.0, max(0.5, float(params.get("deviation_multiplier", 2.0))))
pivot_lookback = min(250, max(10, int(params.get("pivot_lookback", 50))))
max_active_lines = min(50, max(1, int(params.get("max_active_lines", 10))))
profile_rows = min(100, max(10, int(params.get("profile_rows", 50))))
profile_width = min(100, max(5, int(params.get("profile_width", 30))))
profile_offset = min(100, max(0, int(params.get("profile_offset", 10))))
session_timezone_offset_minutes = min(840, max(-840, int(params.get("session_timezone_offset_minutes", -240))))
stop_zone_multiplier = min(10.0, max(1.0, float(params.get("stop_zone_multiplier", 1.5))))
show_ifvg = bool(params.get("show_ifvg", False))
ifvg_volatility_filter = min(10.0, max(0.5, float(params.get("ifvg_volatility_filter", 1.2))))
ifvg_history = min(20, max(1, int(params.get("ifvg_history", 5))))


def _clock_minutes(value, default):
    text = str(value).strip().replace(":", "").zfill(4)
    if not text.isdigit() or len(text) != 4:
        text = default
    hour, minute = int(text[:2]), int(text[2:])
    return hour * 60 + minute if hour < 24 and minute < 60 else int(default[:2]) * 60 + int(default[2:])


session_anchor_start = _clock_minutes(params.get("session_anchor_start", "1800"), "1800")


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    running = 0.0
    for index, value in enumerate(values):
        running += float(value)
        if index >= period:
            running -= float(values[index - period])
        if index >= period - 1:
            result[index] = running / period
    return result


df = df.copy().reset_index(drop=True)
if "time" not in df.columns:
    df["time"] = np.arange(len(df), dtype=float) * 60.0
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close", "volume"]).reset_index(drop=True)
time_raw = df["time"].to_numpy(dtype=float)
time_seconds = time_raw / 1000.0 if len(time_raw) and np.nanmax(np.abs(time_raw)) > 1.0e11 else time_raw.copy()
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].to_numpy(dtype=float)
n = len(df)

local_seconds = time_seconds + vwap_timezone_offset_minutes * 60.0
calendar = pd.to_datetime(local_seconds, unit="s", utc=True) if n else pd.DatetimeIndex([])
if vwap_anchor == "Week":
    period_key = (np.floor((local_seconds + 3.0 * 86400.0) / 604800.0).astype(np.int64) if n else np.asarray([], dtype=np.int64))
elif vwap_anchor == "Month":
    period_key = (calendar.year.to_numpy(dtype=np.int64) * 12 + calendar.month.to_numpy(dtype=np.int64)) if n else np.asarray([], dtype=np.int64)
elif vwap_anchor == "Year":
    period_key = calendar.year.to_numpy(dtype=np.int64) if n else np.asarray([], dtype=np.int64)
else:
    period_key = np.floor(local_seconds / 86400.0).astype(np.int64) if n else np.asarray([], dtype=np.int64)

source = (high + low + close) / 3.0
vwap = np.full(n, np.nan, dtype=float)
upper_band = np.full(n, np.nan, dtype=float)
lower_band = np.full(n, np.nan, dtype=float)
sum_volume = sum_price_volume = sum_square_volume = 0.0
for index in range(n):
    if index == 0 or period_key[index] != period_key[index - 1]:
        sum_volume = sum_price_volume = sum_square_volume = 0.0
    sum_volume += volume[index]
    sum_price_volume += source[index] * volume[index]
    sum_square_volume += source[index] * source[index] * volume[index]
    if sum_volume > 0.0:
        vwap[index] = sum_price_volume / sum_volume
        variance = max(0.0, sum_square_volume / sum_volume - vwap[index] * vwap[index])
        deviation = math.sqrt(variance) * deviation_multiplier
        upper_band[index], lower_band[index] = vwap[index] + deviation, vwap[index] - deviation

average_volume = _sma(volume, 20)
upper_stop_event = np.zeros(n, dtype=bool)
lower_stop_event = np.zeros(n, dtype=bool)
active_upper, active_lower, stop_lines = [], [], []
for index in range(n):
    center = index - pivot_lookback
    if index >= pivot_lookback * 2:
        high_window = high[center - pivot_lookback:center + pivot_lookback + 1]
        low_window = low[center - pivot_lookback:center + pivot_lookback + 1]
        if high[center] == np.max(high_window):
            active_upper.insert(0, {"side": "upper", "startIndex": int(center), "endIndex": int(index), "price": float(high[center]), "triggered": False})
            if len(active_upper) > max_active_lines:
                active_upper.pop()
        if low[center] == np.min(low_window):
            active_lower.insert(0, {"side": "lower", "startIndex": int(center), "endIndex": int(index), "price": float(low[center]), "triggered": False})
            if len(active_lower) > max_active_lines:
                active_lower.pop()
    kept = []
    for line in active_upper:
        line["endIndex"] = int(index)
        if high[index] >= line["price"]:
            line["triggered"] = True
            stop_lines.append(line.copy())
            if np.isfinite(average_volume[index]) and volume[index] > average_volume[index] * 1.2:
                upper_stop_event[index] = True
        else:
            kept.append(line)
    active_upper = kept
    kept = []
    for line in active_lower:
        line["endIndex"] = int(index)
        if low[index] <= line["price"]:
            line["triggered"] = True
            stop_lines.append(line.copy())
            if np.isfinite(average_volume[index]) and volume[index] > average_volume[index] * 1.2:
                lower_stop_event[index] = True
        else:
            kept.append(line)
    active_lower = kept

body_average = _sma(np.abs(close - opened), 50)
bear_fvg_event = np.zeros(n, dtype=bool)
bull_fvg_event = np.zeros(n, dtype=bool)
bull_ifvg_event = np.zeros(n, dtype=bool)
bear_ifvg_event = np.zeros(n, dtype=bool)
pending_bear, pending_bull, active_ifvgs = [], [], []
if show_ifvg:
    for index in range(2, n):
        valid = np.isfinite(body_average[index]) and abs(close[index - 1] - opened[index - 1]) > body_average[index] * ifvg_volatility_filter
        if valid and high[index] < low[index - 2]:
            pending_bear.insert(0, {"top": float(low[index - 2]), "bottom": float(high[index]), "startIndex": int(index - 1), "createdIndex": int(index)})
            bear_fvg_event[index] = True
        if valid and low[index] > high[index - 2]:
            pending_bull.insert(0, {"top": float(low[index]), "bottom": float(high[index - 2]), "startIndex": int(index - 1), "createdIndex": int(index)})
            bull_fvg_event[index] = True
        kept = []
        for gap in pending_bear:
            if close[index] > gap["top"]:
                item = {**gap, "side": "bull", "invertedIndex": int(index), "endIndex": int(index + 10)}
                active_ifvgs.insert(0, item)
                bull_ifvg_event[index] = True
            else:
                kept.append(gap)
        pending_bear = kept
        kept = []
        for gap in pending_bull:
            if close[index] < gap["bottom"]:
                item = {**gap, "side": "bear", "invertedIndex": int(index), "endIndex": int(index + 10)}
                active_ifvgs.insert(0, item)
                bear_ifvg_event[index] = True
            else:
                kept.append(gap)
        pending_bull = kept
        active_ifvgs = active_ifvgs[:ifvg_history]
        kept = []
        for gap in active_ifvgs:
            mitigated = close[index] < gap["bottom"] if gap["side"] == "bull" else close[index] > gap["top"]
            if not mitigated:
                gap["endIndex"] = int(index + 5) if index < gap["endIndex"] else gap["endIndex"]
                kept.append(gap)
        active_ifvgs = kept

profile_local = time_seconds + session_timezone_offset_minutes * 60.0
profile_minute = np.floor(profile_local / 60.0).astype(np.int64) % 1440 if n else np.asarray([], dtype=np.int64)
profile_bucket = np.floor((profile_local - session_anchor_start * 60.0) / 86400.0).astype(np.int64) if n else np.asarray([], dtype=np.int64)
profile_prices, profile_volumes, profile_buys, profile_sells = [], [], [], []
anchor_event = np.zeros(n, dtype=bool)
for index in range(n):
    if index == 0 or profile_bucket[index] != profile_bucket[index - 1]:
        if profile_minute[index] >= session_anchor_start or index == 0:
            profile_prices, profile_volumes, profile_buys, profile_sells = [], [], [], []
            anchor_event[index] = True
    span = max(high[index] - low[index], 1e-12)
    profile_prices.append(float(close[index]))
    profile_volumes.append(float(volume[index]))
    profile_buys.append(float(volume[index] * (close[index] - low[index]) / span))
    profile_sells.append(float(volume[index] * (high[index] - close[index]) / span))

profile_bins = []
if profile_prices:
    session_high, session_low = max(profile_prices), min(profile_prices)
    bin_size = (session_high - session_low) / profile_rows
    if bin_size > 0.0:
        totals = np.zeros(profile_rows, dtype=float)
        buys = np.zeros(profile_rows, dtype=float)
        sells = np.zeros(profile_rows, dtype=float)
        for price, amount, buy_amount, sell_amount in zip(profile_prices, profile_volumes, profile_buys, profile_sells):
            bin_index = min(profile_rows - 1, max(0, int(math.floor((price - session_low) / bin_size))))
            totals[bin_index] += amount
            buys[bin_index] += buy_amount
            sells[bin_index] += sell_amount
        maximum = float(np.max(totals))
        average = float(np.sum(totals) / profile_rows)
        point_of_control = int(np.argmax(totals))
        for index in range(profile_rows):
            if totals[index] <= 0.0:
                continue
            profile_bins.append({
                "index": index, "top": session_low + (index + 1) * bin_size, "bottom": session_low + index * bin_size,
                "volume": float(totals[index]), "buyVolume": float(buys[index]), "sellVolume": float(sells[index]),
                "width": float(totals[index] / maximum * profile_width) if maximum > 0.0 else 0.0,
                "pointOfControl": index == point_of_control, "stopZone": bool(totals[index] > average * stop_zone_multiplier),
            })

layers = []
for line in (stop_lines + active_upper + active_lower)[-500:]:
    layers.append({"type": "line", "role": f"{line['side']} stop level", "startIndex": line["startIndex"], "endIndex": line["endIndex"], "y1": line["price"], "y2": line["price"], "color": "rgba(242,54,69,0.40)" if line["side"] == "upper" else "rgba(8,153,129,0.40)", "lineWidth": 1, "dashed": True})
for gap in active_ifvgs:
    bullish = gap["side"] == "bull"
    layers.append({"type": "zone", "role": "Bullish IFVG" if bullish else "Bearish IFVG", "startIndex": gap["startIndex"], "endIndex": gap["endIndex"], "top": gap["top"], "bottom": gap["bottom"], "fillColor": "rgba(8,153,129,0.15)" if bullish else "rgba(242,54,69,0.15)", "borderColor": "rgba(8,153,129,0.40)" if bullish else "rgba(242,54,69,0.40)"})
for item in profile_bins:
    start = n - 1 + profile_offset
    end = start + int(math.floor(item["width"] + 0.5))
    color = "rgba(255,235,59,0.90)" if item["pointOfControl"] else "rgba(255,152,0,0.70)" if item["stopZone"] else "rgba(91,156,246,0.40)"
    layers.append({"type": "zone", "role": "Volume Profile", "startIndex": start, "endIndex": end, "top": item["top"], "bottom": item["bottom"], "fillColor": color, "borderColor": "rgba(0,0,0,0)"})

def _event_points(events, bullish, text):
    return [{"active": True, "price": float(high[index] if bullish else low[index]), "text": text, "color": "#F23645" if bullish else "#089981"} if events[index] else None for index in range(n)]

upper_cloud = [{"upper": float(upper_band[i]), "lower": float(vwap[i]), "color": "rgba(242,54,69,0.10)"} if np.all(np.isfinite([upper_band[i], vwap[i]])) else None for i in range(n)]
lower_cloud = [{"upper": float(vwap[i]), "lower": float(lower_band[i]), "color": "rgba(8,153,129,0.10)"} if np.all(np.isfinite([lower_band[i], vwap[i]])) else None for i in range(n)]

output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "VWAP", "data": _finite(vwap), "color": "rgba(255,255,255,0.50)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Upper Band", "data": _finite(upper_band), "color": "#F23645", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Lower Band", "data": _finite(lower_band), "color": "#089981", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Upper Deviation Fill", "title": False, "data": upper_cloud, "color": "#F23645", "type": "cloud", "overlay": True},
        {"name": "Lower Deviation Fill", "title": False, "data": lower_cloud, "color": "#089981", "type": "cloud", "overlay": True},
    ],
    "signals": [
        {"type": "sell", "name": "Stops Triggered High", "data": _event_points(upper_stop_event, True, "Stop"), "color": "#F23645", "renderMode": "events"},
        {"type": "buy", "name": "Stops Triggered Low", "data": _event_points(lower_stop_event, False, "Stop"), "color": "#089981", "renderMode": "events"},
    ],
    "layers": layers, "tables": [], "bar_colors": [None] * n,
    "background_colors": ["rgba(255,235,59,0.20)" if anchor_event[i] else None for i in range(n)],
    "calculatedVars": {
        "vwap": _finite(vwap), "upperBand": _finite(upper_band), "lowerBand": _finite(lower_band), "periodKey": period_key.tolist(),
        "upperStopEvent": upper_stop_event.tolist(), "lowerStopEvent": lower_stop_event.tolist(), "activeUpper": active_upper, "activeLower": active_lower, "stopLines": stop_lines,
        "bodyAverage": _finite(body_average), "bearFvgEvent": bear_fvg_event.tolist(), "bullFvgEvent": bull_fvg_event.tolist(),
        "bullIfvgEvent": bull_ifvg_event.tolist(), "bearIfvgEvent": bear_ifvg_event.tolist(), "pendingBearFvg": pending_bear, "pendingBullFvg": pending_bull, "activeIfvgs": active_ifvgs,
        "anchorEvent": anchor_event.tolist(), "profileBins": profile_bins, "profileSampleCount": len(profile_prices),
        "vwapTimezoneOffsetMinutes": vwap_timezone_offset_minutes, "sessionTimezoneOffsetMinutes": session_timezone_offset_minutes,
    },
}
