# ============================================================
# QuantDinger Opening Range Breakout Algo
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Opening Range Breakout Algo"
my_indicator_description = "Builds a configurable intraday opening range, confirms breakout retests, and tracks three take-profit stages plus an adaptive stop."

# @param orb_minutes int 30 Opening-range duration in minutes range=1:240:1
# @param sensitivity str Medium Required breakout retests values=High,Medium,Low,Lowest
# @param breakout_condition str Close Breakout source values=Close,EMA
# @param take_profit_method str Dynamic Take-profit method values=Dynamic,ATR
# @param ema_length int 9 EMA length values=4,9,13,20,34
# @param stop_loss_method str Balanced Initial stop placement values=Safer,Balanced,Risky
# @param adaptive_stop bool true Move stop to entry after TP1
# @param custom_session_enabled bool false Use a bounded custom session
# @param custom_session_start str 1000 Custom session start in HHMM
# @param custom_session_end str 1100 Custom session end in HHMM
# @param regular_session_start str 0000 Regular daily session start in HHMM
# @param session_timezone_offset_minutes int 0 Offset from UTC for session calculations range=-840:840:15
# @param backtesting_exit_ratios str 90-5-5 TP1-TP2-TP3 exit percentages values=75-15-10,50-25-25,34-33-33,90-5-5,100-0-0,0-100-0,0-0-100
# @param show_orb_zones bool true Draw opening-range levels
# @param plot_ema bool false Plot the breakout EMA
# @param show_historic_zones bool true Retain historical opening ranges
# @param show_dashboard bool true Show current ORB dashboard
# @param show_backtest bool true Show backtest summary

orb_minutes = min(240, max(1, int(params.get("orb_minutes", 30))))
sensitivity = str(params.get("sensitivity", "Medium")).title()
if sensitivity not in ("High", "Medium", "Low", "Lowest"):
    sensitivity = "Medium"
breakout_condition = str(params.get("breakout_condition", "Close")).upper()
if breakout_condition not in ("CLOSE", "EMA"):
    breakout_condition = "CLOSE"
take_profit_method = str(params.get("take_profit_method", "Dynamic")).upper()
if take_profit_method not in ("DYNAMIC", "ATR"):
    take_profit_method = "DYNAMIC"
ema_length = int(params.get("ema_length", 9))
if ema_length not in (4, 9, 13, 20, 34):
    ema_length = 9
stop_loss_method = str(params.get("stop_loss_method", "Balanced")).title()
if stop_loss_method not in ("Safer", "Balanced", "Risky"):
    stop_loss_method = "Balanced"
adaptive_stop = bool(params.get("adaptive_stop", True))
custom_session_enabled = bool(params.get("custom_session_enabled", False))
session_timezone_offset_minutes = min(840, max(-840, int(params.get("session_timezone_offset_minutes", 0))))
show_orb_zones = bool(params.get("show_orb_zones", True))
plot_ema = bool(params.get("plot_ema", False))
show_historic_zones = bool(params.get("show_historic_zones", True))
show_dashboard = bool(params.get("show_dashboard", True))
show_backtest = bool(params.get("show_backtest", True))


def _clock_minutes(value, default):
    text = str(value).strip().replace(":", "")
    if not text.isdigit() or len(text) > 4:
        text = default
    text = text.zfill(4)
    hour, minute = int(text[:2]), int(text[2:])
    if hour > 23 or minute > 59:
        return int(default[:2]) * 60 + int(default[2:])
    return hour * 60 + minute


custom_session_start = _clock_minutes(params.get("custom_session_start", "1000"), "1000")
custom_session_end = _clock_minutes(params.get("custom_session_end", "1100"), "1100")
regular_session_start = _clock_minutes(params.get("regular_session_start", "0000"), "0000")


def _exit_ratios(value):
    pieces = str(value).replace("%", "").replace("|", "-").split("-")
    try:
        ratios = [max(0.0, float(piece.strip())) / 100.0 for piece in pieces if piece.strip()]
    except Exception:
        ratios = []
    if len(ratios) != 3 or abs(sum(ratios) - 1.0) > 0.011:
        return [0.90, 0.05, 0.05]
    return ratios


backtest_exit_ratios = _exit_ratios(params.get("backtesting_exit_ratios", "90-5-5"))
retests_needed = {"High": 0, "Medium": 1, "Low": 2, "Lowest": 3}[sensitivity]


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
    if len(values) < period:
        return result
    for index in range(period - 1, len(values)):
        if index == period - 1:
            window = values[:period]
            if np.all(np.isfinite(window)):
                result[index] = float(np.mean(window))
        elif np.isfinite(values[index]) and np.isfinite(result[index - 1]):
            result[index] = (result[index - 1] * (period - 1) + values[index]) / period
    return result


def _timeframe_seconds(value):
    text = str(value or "").strip().upper()
    if not text:
        return None
    multipliers = {"S": 1, "M": 60, "H": 3600, "D": 86400, "W": 604800}
    suffix = text[-1]
    try:
        if suffix in multipliers:
            return max(1, int(float(text[:-1] or "1") * multipliers[suffix]))
        return max(1, int(float(text) * 60))
    except Exception:
        return None


def _difference_percent(first, second):
    return abs(first - second) / abs(second) * 100.0 if np.isfinite(second) and abs(second) > 1e-12 else 0.0


df = df.copy().reset_index(drop=True)
if "time" not in df.columns:
    df["time"] = np.arange(len(df), dtype=float) * 60.0
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
time_raw = df["time"].to_numpy(dtype=float)
time_seconds = time_raw / 1000.0 if len(time_raw) and np.nanmax(np.abs(time_raw)) > 1.0e11 else time_raw.copy()
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)

mid_price = (high + low) / 2.0
ema = _ema(mid_price, ema_length)
true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
atr = _rma(true_range, 12)

local_time = time_seconds + session_timezone_offset_minutes * 60.0
local_minute = (np.floor(local_time / 60.0).astype(np.int64) % 1440) if n else np.asarray([], dtype=np.int64)
regular_bucket = np.floor((local_time - regular_session_start * 60.0) / 86400.0).astype(np.int64) if n else np.asarray([], dtype=np.int64)
custom_bucket = np.floor((local_time - custom_session_start * 60.0) / 86400.0).astype(np.int64) if n else np.asarray([], dtype=np.int64)
if custom_session_start < custom_session_end:
    custom_inside = (local_minute >= custom_session_start) & (local_minute < custom_session_end)
else:
    custom_inside = (local_minute >= custom_session_start) | (local_minute < custom_session_end)

entry_event = np.zeros(n, dtype=bool)
entry_bullish = np.zeros(n, dtype=bool)
tp1_event = np.zeros(n, dtype=bool)
tp2_event = np.zeros(n, dtype=bool)
tp3_event = np.zeros(n, dtype=bool)
stop_event = np.zeros(n, dtype=bool)
exit_event = np.zeros(n, dtype=bool)
orb_high = np.full(n, np.nan, dtype=float)
orb_low = np.full(n, np.nan, dtype=float)
stop_level = np.full(n, np.nan, dtype=float)
state_code = np.zeros(n, dtype=int)
session_number = np.full(n, -1, dtype=int)
orbs = []
last_orb = None


def _new_orb(index):
    return {
        "high": None, "low": None, "startTime": float(time_seconds[index]), "startIndex": int(index), "endIndex": None,
        "state": "Opening Range", "breakouts": [], "entryBullish": None, "entryIndex": None, "entryPrice": None,
        "entryATR": None, "tp1Index": None, "tp1Price": None, "tp2Index": None, "tp2Price": None,
        "tp3Index": None, "tp3Price": None, "slIndex": None, "slPrice": None,
    }


for index in range(n):
    if custom_session_enabled:
        new_session = bool(custom_inside[index] and (index == 0 or not custom_inside[index - 1] or custom_bucket[index] != custom_bucket[index - 1]))
    else:
        new_session = bool(index == 0 or regular_bucket[index] != regular_bucket[index - 1])

    if new_session:
        if last_orb is not None:
            last_orb["endIndex"] = index - 1
            if last_orb["entryIndex"] is not None and last_orb["slIndex"] is None and index > 0:
                last_orb["slPrice"] = float(close[index - 1])
                last_orb["slIndex"] = index - 1
                exit_event[index - 1] = True
        last_orb = _new_orb(index)
        orbs.insert(0, last_orb)

    if last_orb is not None:
        opening_end = last_orb["startTime"] + orb_minutes * 60.0
        if last_orb["state"] == "Opening Range" and time_seconds[index] < opening_end:
            last_orb["high"] = float(max(0.0 if last_orb["high"] is None else last_orb["high"], high[index]))
            last_orb["low"] = float(low[index] if last_orb["low"] is None else min(last_orb["low"], low[index]))
        if last_orb["state"] == "Opening Range" and time_seconds[index] >= opening_end:
            last_orb["state"] = "Waiting For Breakouts"

        if last_orb["state"] == "Waiting For Breakouts" and last_orb["high"] is not None and last_orb["low"] is not None:
            condition_price = ema[index] if breakout_condition == "EMA" else close[index]
            if np.isfinite(condition_price) and (condition_price > last_orb["high"] or condition_price < last_orb["low"]):
                last_orb["breakouts"].insert(0, {"isBullish": bool(condition_price > last_orb["high"]), "startIndex": int(index), "endIndex": None, "failed": False, "retests": 0})
                last_orb["state"] = "In Breakout"

        if last_orb["state"] == "In Breakout":
            breakout = last_orb["breakouts"][0]
            bullish = breakout["isBullish"]
            if (bullish and close[index] < last_orb["high"]) or ((not bullish) and close[index] > last_orb["low"]):
                breakout["failed"] = True
                breakout["endIndex"] = int(index)
                last_orb["state"] = "Waiting For Breakouts"
            if index > breakout["startIndex"] and ((bullish and close[index] > last_orb["high"] and low[index] < last_orb["high"]) or ((not bullish) and close[index] < last_orb["low"] and high[index] > last_orb["low"])):
                breakout["retests"] += 1
            if breakout["retests"] >= retests_needed:
                breakout["endIndex"] = int(index)
                last_orb["state"] = "Entry Taken"
                last_orb["entryATR"] = float(atr[index]) if np.isfinite(atr[index]) else None
                last_orb["entryIndex"] = int(index)
                last_orb["entryPrice"] = float(close[index])
                last_orb["entryBullish"] = bool(bullish)
                center = (last_orb["high"] + last_orb["low"]) / 2.0
                if stop_loss_method == "Safer":
                    last_orb["slPrice"] = (center + (last_orb["high"] if bullish else last_orb["low"])) / 2.0
                elif stop_loss_method == "Risky":
                    last_orb["slPrice"] = (center + (last_orb["low"] if bullish else last_orb["high"])) / 2.0
                else:
                    last_orb["slPrice"] = center
                entry_event[index] = True
                entry_bullish[index] = bullish

        if last_orb["state"] == "Entry Taken":
            bullish = bool(last_orb["entryBullish"])
            entry_price = last_orb["entryPrice"]
            profitable = (bullish and ema[index] > entry_price) or ((not bullish) and ema[index] < entry_price)
            if take_profit_method == "DYNAMIC":
                reversal = (bullish and close[index] < ema[index]) or ((not bullish) and close[index] > ema[index])
                if profitable and _difference_percent(ema[index], entry_price) >= 0.20:
                    if last_orb["slIndex"] is None and last_orb["tp1Index"] is None and reversal:
                        last_orb["tp1Index"], last_orb["tp1Price"] = int(index), float(ema[index])
                        tp1_event[index] = True
                        if adaptive_stop:
                            last_orb["slPrice"] = entry_price
                    elif last_orb["slIndex"] is None and last_orb["tp1Index"] is not None and last_orb["tp2Index"] is None and reversal:
                        correct_side = ema[index] > last_orb["tp1Price"] if bullish else ema[index] < last_orb["tp1Price"]
                        if correct_side and _difference_percent(ema[index], last_orb["tp1Price"]) >= 0.075:
                            last_orb["tp2Index"], last_orb["tp2Price"] = int(index), float(ema[index])
                            tp2_event[index] = True
                    elif last_orb["slIndex"] is None and last_orb["tp2Index"] is not None and last_orb["tp3Index"] is None and reversal:
                        correct_side = ema[index] > last_orb["tp2Price"] if bullish else ema[index] < last_orb["tp2Price"]
                        if correct_side and _difference_percent(ema[index], last_orb["tp2Price"]) >= 0.075:
                            last_orb["tp3Index"], last_orb["tp3Price"], last_orb["slIndex"] = int(index), float(ema[index]), -1
                            tp3_event[index] = True
            else:
                entry_atr = last_orb["entryATR"]
                if entry_atr is not None and np.isfinite(entry_atr):
                    direction = 1.0 if bullish else -1.0
                    targets = [entry_price + direction * entry_atr * multiple for multiple in (0.75, 1.50, 2.25)]
                    if last_orb["slIndex"] is None and last_orb["tp1Index"] is None and ((bullish and high[index] >= targets[0]) or ((not bullish) and low[index] <= targets[0])):
                        last_orb["tp1Index"], last_orb["tp1Price"] = int(index), float(targets[0])
                        tp1_event[index] = True
                        if adaptive_stop:
                            last_orb["slPrice"] = entry_price
                    elif last_orb["slIndex"] is None and last_orb["tp1Index"] is not None and last_orb["tp2Index"] is None and ((bullish and high[index] >= targets[1]) or ((not bullish) and low[index] <= targets[1])):
                        last_orb["tp2Index"], last_orb["tp2Price"] = int(index), float(targets[1])
                        tp2_event[index] = True
                    elif last_orb["slIndex"] is None and last_orb["tp2Index"] is not None and last_orb["tp3Index"] is None and ((bullish and high[index] >= targets[2]) or ((not bullish) and low[index] <= targets[2])):
                        last_orb["tp3Index"], last_orb["tp3Price"], last_orb["slIndex"] = int(index), float(targets[2]), -1
                        tp3_event[index] = True

            if last_orb["slPrice"] is not None and last_orb["slIndex"] is None and ((bullish and low[index] < last_orb["slPrice"]) or ((not bullish) and high[index] > last_orb["slPrice"])):
                if not (tp1_event[index] or tp2_event[index] or tp3_event[index]):
                    last_orb["slIndex"] = int(index)
                    stop_event[index] = True

        orb_high[index] = last_orb["high"] if last_orb["high"] is not None else np.nan
        orb_low[index] = last_orb["low"] if last_orb["low"] is not None else np.nan
        stop_level[index] = last_orb["slPrice"] if last_orb["slPrice"] is not None else np.nan
        state_code[index] = {"Opening Range": 1, "Waiting For Breakouts": 2, "In Breakout": 3, "Entry Taken": 4}.get(last_orb["state"], 0)
        session_number[index] = len(orbs) - 1

if last_orb is not None and last_orb["endIndex"] is None:
    last_orb["endIndex"] = n - 1

layers = []
visible_orbs = orbs if show_historic_zones else orbs[:1]
if show_orb_zones:
    for orb in visible_orbs[:250]:
        if orb["high"] is None or orb["low"] is None or orb["startIndex"] <= n - 5001:
            continue
        end_index = n - 1 if orb is last_orb else int(orb["endIndex"])
        layers.append({"type": "line", "role": "ORB High", "startIndex": int(orb["startIndex"]), "endIndex": end_index, "y1": float(orb["high"]), "y2": float(orb["high"]), "color": "#22C55E", "lineWidth": 2})
        layers.append({"type": "line", "role": "ORB Low", "startIndex": int(orb["startIndex"]), "endIndex": end_index, "y1": float(orb["low"]), "y2": float(orb["low"]), "color": "#EF4444", "lineWidth": 2})


def _event_points(events, label, color):
    data = [None] * n
    for index, active in enumerate(events):
        if active:
            data[index] = {"active": True, "price": float(close[index]), "text": label, "color": color}
    return data


buy_entries = np.asarray(entry_event & entry_bullish, dtype=bool)
sell_entries = np.asarray(entry_event & ~entry_bullish, dtype=bool)
signals = [
    {"type": "buy", "name": "Long Entry", "data": _event_points(buy_entries, "Buy", "#22C55E"), "color": "#22C55E", "renderMode": "events"},
    {"type": "sell", "name": "Short Entry", "data": _event_points(sell_entries, "Sell", "#EF4444"), "color": "#EF4444", "renderMode": "events"},
    {"type": "sell", "name": "Take Profit 1", "data": _event_points(tp1_event, "TP 1", "#0284C7"), "color": "#0284C7", "renderMode": "events"},
    {"type": "sell", "name": "Take Profit 2", "data": _event_points(tp2_event, "TP 2", "#7C3AED"), "color": "#7C3AED", "renderMode": "events"},
    {"type": "sell", "name": "Take Profit 3", "data": _event_points(tp3_event, "TP 3", "#0F766E"), "color": "#0F766E", "renderMode": "events"},
    {"type": "sell", "name": "Stop Loss", "data": _event_points(stop_event, "SL", "#EF4444"), "color": "#EF4444", "renderMode": "events"},
    {"type": "sell", "name": "Session Exit", "data": _event_points(exit_event, "Exit", "#F97316"), "color": "#F97316", "renderMode": "events"},
]

total_profit_percent = 0.0
wins = 0
losses = 0
for orb in orbs:
    if orb["entryIndex"] is None:
        continue
    successful = False
    profit_taken = 0.0
    for key, ratio in zip(("tp1Price", "tp2Price", "tp3Price"), backtest_exit_ratios):
        if orb[key] is not None:
            successful = True
            total_profit_percent += _difference_percent(orb[key], orb["entryPrice"]) * ratio
            profit_taken += ratio
    if orb["slIndex"] is not None and orb["slIndex"] != -1:
        total_profit_percent -= _difference_percent(orb["slPrice"], orb["entryPrice"]) * (1.0 - profit_taken)
        if profit_taken == 0.0:
            losses += 1
    if successful:
        wins += 1

closed_count = wins + losses
tables = []
if show_dashboard:
    if last_orb is None or last_orb["entryIndex"] is None:
        dashboard_rows = [[{"text": "Signal"}, {"text": "No Signal", "color": "#94A3B8"}]]
    else:
        bullish = bool(last_orb["entryBullish"])
        current_profit = (close[-1] - last_orb["entryPrice"]) * (1.0 if bullish else -1.0) if n else 0.0
        current_percent = current_profit / last_orb["entryPrice"] * 100.0 if abs(last_orb["entryPrice"]) > 1e-12 else 0.0
        dashboard_rows = [
            [{"text": "Signal"}, {"text": "Bullish" if bullish else "Bearish", "color": "#22C55E" if bullish else "#EF4444"}],
            [{"text": "Entry"}, {"text": f"{last_orb['entryPrice']:.6g}"}],
            [{"text": "Current Profit"}, {"text": f"{current_percent:.2f}%", "color": "#22C55E" if current_profit > 0 else "#EF4444"}],
            [{"text": "Stop"}, {"text": f"{last_orb['slPrice']:.6g}"}],
        ]
        if take_profit_method == "ATR" and last_orb["entryATR"] is not None:
            direction = 1.0 if bullish else -1.0
            for number, multiple in enumerate((0.75, 1.50, 2.25), 1):
                dashboard_rows.append([{"text": f"TP {number}"}, {"text": f"{last_orb['entryPrice'] + direction * last_orb['entryATR'] * multiple:.6g}"}])
    tables.append({"title": "ORB Dashboard", "position": "top-right", "compact": True, "rows": dashboard_rows})
if show_backtest:
    tables.append({
        "title": "ORB Backtesting", "position": "top-center", "compact": True,
        "rows": [
            [{"text": "Total Days"}, {"text": str(len(orbs))}],
            [{"text": "Wins"}, {"text": str(wins), "color": "#22C55E"}],
            [{"text": "Losses"}, {"text": str(losses), "color": "#EF4444"}],
            [{"text": "Win Rate"}, {"text": f"{100.0 * wins / closed_count if closed_count else 0.0:.2f}%"}],
            [{"text": "Average Profit"}, {"text": f"{total_profit_percent / closed_count if closed_count else 0.0:.2f}%"}],
            [{"text": "Total Profit"}, {"text": f"{total_profit_percent:.2f}%"}],
        ],
    })

runtime_ctx = ctx
chart_seconds = _timeframe_seconds(runtime_ctx.timeframe)
if chart_seconds is None and n >= 2:
    differences = np.diff(time_seconds)
    positive = differences[differences > 0]
    chart_seconds = int(np.median(positive)) if len(positive) else None
timeframe_supported = chart_seconds is None or chart_seconds <= 900
if not timeframe_supported:
    tables.append({"title": "Timeframe", "position": "right-center", "compact": True, "rows": [[{"text": "Use 15m or lower", "color": "#F59E0B"}]]})

orb_audit = []
for orb in reversed(orbs):
    orb_audit.append({
        "startIndex": orb["startIndex"], "endIndex": orb["endIndex"], "high": orb["high"], "low": orb["low"], "state": orb["state"],
        "entryIndex": orb["entryIndex"], "entryPrice": orb["entryPrice"], "entryBullish": orb["entryBullish"], "entryATR": orb["entryATR"],
        "tp1Index": orb["tp1Index"], "tp1Price": orb["tp1Price"], "tp2Index": orb["tp2Index"], "tp2Price": orb["tp2Price"],
        "tp3Index": orb["tp3Index"], "tp3Price": orb["tp3Price"], "slIndex": orb["slIndex"], "slPrice": orb["slPrice"],
        "breakouts": orb["breakouts"],
    })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "EMA", "data": _finite(ema) if plot_ema else [None] * n, "color": "#F59E0B", "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": signals,
    "layers": layers,
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "ema": _finite(ema), "atr": _finite(atr), "orbHigh": _finite(orb_high), "orbLow": _finite(orb_low), "stopLevel": _finite(stop_level),
        "stateCode": state_code.tolist(), "sessionNumber": session_number.tolist(), "entryEvent": entry_event.tolist(), "entryBullish": entry_bullish.tolist(),
        "tp1Event": tp1_event.tolist(), "tp2Event": tp2_event.tolist(), "tp3Event": tp3_event.tolist(), "stopEvent": stop_event.tolist(), "exitEvent": exit_event.tolist(),
        "orbs": orb_audit, "wins": wins, "losses": losses, "totalProfitPercent": total_profit_percent, "exitRatios": backtest_exit_ratios,
        "timeframeSupported": bool(timeframe_supported), "chartSeconds": chart_seconds, "sessionTimezoneOffsetMinutes": session_timezone_offset_minutes,
        "regularSessionStartMinutes": regular_session_start, "customSessionStartMinutes": custom_session_start, "customSessionEndMinutes": custom_session_end,
    },
}
