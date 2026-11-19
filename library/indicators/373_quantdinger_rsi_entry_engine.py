# ============================================================
# QuantDinger RSI Entry Engine
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger RSI Entry Engine"
my_indicator_description = "Builds smoothed RSI reclaim signals and manages bounded take-profit and stop-loss trade projections."

# @param rsi_source str Close RSI source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param rsi_length int 14 RSI length range=1:500:1
# @param smoothing_mode str EMA RSI smoothing values=None,EMA,SMA,RMA
# @param smoothing_length int 5 RSI smoothing length range=1:500:1
# @param signal_length int 9 Signal length range=1:500:1
# @param signal_mode str EMA Signal smoothing values=EMA,SMA,RMA
# @param overbought int 70 Overbought level range=50:100:1
# @param oversold int 30 Oversold level range=0:50:1
# @param extreme_high int 80 Extreme high level range=60:100:1
# @param extreme_low int 20 Extreme low level range=0:40:1
# @param show_trade_projections bool true Show trade projections
# @param entry_mode str Close Projected entry source values=Close,Open,HLC3
# @param stop_mode str ATR Stop-loss mode values=Signal Candle,ATR,Percent
# @param risk_reward float 5 Risk/reward target range=0.1:100:0.1
# @param atr_length int 14 ATR length range=1:500:1
# @param atr_multiplier float 1 ATR multiplier range=0.1:100:0.1
# @param percent_stop float 0.5 Percent stop distance range=0.01:100:0.01
# @param same_bar_priority str SL Same-bar hit priority values=SL,TP
# @param maximum_stored_trades int 20 Maximum stored projections range=1:100:1
# @param minimum_tick float 0.00000001 Instrument minimum tick range=0.00000001:100:0.00000001
# @param show_extreme_background bool true Highlight extreme zones
# @param show_panel bool true Show status panel
# @param panel_position str Top Right Panel position values=Top Right,Top Left,Bottom Right,Bottom Left
# @param panel_text_size str Small Panel text size values=Tiny,Small,Normal
# @param label_text_size str Normal Event label size values=Tiny,Small,Normal,Large,Huge
# @param enable_cross_alerts bool true Enable RSI/signal alert states
# @param enable_zone_alerts bool true Enable reclaim alert states
# @param enable_center_alerts bool true Enable centerline alert states
# @param enable_entry_alerts bool true Enable entry alert states
# @param enable_trade_alerts bool true Enable trade-hit alert states


def _choice(name, default, allowed):
    value = str(params.get(name, default))
    return value if value in allowed else default


def _bounded_int(name, default, lower, upper):
    return min(upper, max(lower, int(params.get(name, default))))


def _bounded_float(name, default, lower, upper):
    return min(upper, max(lower, float(params.get(name, default))))


rsi_source_name = _choice("rsi_source", "Close", ("Close", "Open", "High", "Low", "HL2", "HLC3", "OHLC4"))
rsi_length = _bounded_int("rsi_length", 14, 1, 500)
smoothing_mode = _choice("smoothing_mode", "EMA", ("None", "EMA", "SMA", "RMA"))
smoothing_length = _bounded_int("smoothing_length", 5, 1, 500)
signal_length = _bounded_int("signal_length", 9, 1, 500)
signal_mode = _choice("signal_mode", "EMA", ("EMA", "SMA", "RMA"))
overbought = _bounded_int("overbought", 70, 50, 100)
oversold = _bounded_int("oversold", 30, 0, 50)
extreme_high = _bounded_int("extreme_high", 80, 60, 100)
extreme_low = _bounded_int("extreme_low", 20, 0, 40)
show_trade_projections = bool(params.get("show_trade_projections", True))
entry_mode = _choice("entry_mode", "Close", ("Close", "Open", "HLC3"))
stop_mode = _choice("stop_mode", "ATR", ("Signal Candle", "ATR", "Percent"))
risk_reward = _bounded_float("risk_reward", 5.0, 0.1, 100.0)
atr_length = _bounded_int("atr_length", 14, 1, 500)
atr_multiplier = _bounded_float("atr_multiplier", 1.0, 0.1, 100.0)
percent_stop = _bounded_float("percent_stop", 0.5, 0.01, 100.0)
same_bar_priority = _choice("same_bar_priority", "SL", ("SL", "TP"))
maximum_stored_trades = _bounded_int("maximum_stored_trades", 20, 1, 100)
minimum_tick = _bounded_float("minimum_tick", 0.00000001, 0.00000001, 100.0)
show_extreme_background = bool(params.get("show_extreme_background", True))
show_panel = bool(params.get("show_panel", True))
panel_position = _choice("panel_position", "Top Right", ("Top Right", "Top Left", "Bottom Right", "Bottom Left"))
panel_text_size = _choice("panel_text_size", "Small", ("Tiny", "Small", "Normal"))
label_text_size = _choice("label_text_size", "Normal", ("Tiny", "Small", "Normal", "Large", "Huge"))
enable_cross_alerts = bool(params.get("enable_cross_alerts", True))
enable_zone_alerts = bool(params.get("enable_zone_alerts", True))
enable_center_alerts = bool(params.get("enable_center_alerts", True))
enable_entry_alerts = bool(params.get("enable_entry_alerts", True))
enable_trade_alerts = bool(params.get("enable_trade_alerts", True))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    state = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            state = float(value) if not np.isfinite(state) else alpha * float(value) + (1.0 - alpha) * state
            result[index] = state
    return result


def _sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    history = []
    for index, value in enumerate(values):
        if np.isfinite(value):
            history.append(float(value))
            if len(history) >= length:
                result[index] = float(np.mean(history[-length:]))
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


def _average(values, length, mode):
    if mode == "EMA":
        return _ema(values, length)
    if mode == "SMA":
        return _sma(values, length)
    if mode == "RMA":
        return _rma(values, length)
    return values.copy()


def _cross_over(first, second):
    result = np.zeros(len(first), dtype=bool)
    for index in range(1, len(first)):
        if np.all(np.isfinite([first[index], first[index - 1], second[index], second[index - 1]])):
            result[index] = first[index] > second[index] and first[index - 1] <= second[index - 1]
    return result


def _cross_under(first, second):
    result = np.zeros(len(first), dtype=bool)
    for index in range(1, len(first)):
        if np.all(np.isfinite([first[index], first[index - 1], second[index], second[index - 1]])):
            result[index] = first[index] < second[index] and first[index - 1] >= second[index - 1]
    return result


def _event(mask, prices, text, color):
    return [{"active": True, "price": float(prices[index]), "text": text, "color": color, "size": label_text_size.lower()} if mask[index] and np.isfinite(prices[index]) else None for index in range(len(mask))]


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

source_map = {
    "Close": close,
    "Open": opened,
    "High": high,
    "Low": low,
    "HL2": (high + low) / 2.0,
    "HLC3": (high + low + close) / 3.0,
    "OHLC4": (opened + high + low + close) / 4.0,
}
rsi_source = source_map[rsi_source_name]
change = np.diff(rsi_source, prepend=np.nan)
average_gain = _rma(np.where(np.isfinite(change), np.maximum(change, 0.0), np.nan), rsi_length)
average_loss = _rma(np.where(np.isfinite(change), np.maximum(-change, 0.0), np.nan), rsi_length)
rsi_raw = np.full(n, np.nan, dtype=float)
for index in range(n):
    if np.all(np.isfinite([average_gain[index], average_loss[index]])):
        rsi_raw[index] = 100.0 if average_loss[index] == 0.0 else 0.0 if average_gain[index] == 0.0 else 100.0 - 100.0 / (1.0 + average_gain[index] / average_loss[index])
rsi_smooth = rsi_raw.copy() if smoothing_mode == "None" else _average(rsi_raw, smoothing_length, smoothing_mode)
signal = _average(rsi_smooth, signal_length, signal_mode)
spread = rsi_smooth - signal
delta = np.diff(rsi_smooth, prepend=np.nan)

true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    true_range[index] = high[index] - low[index] if index == 0 else max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
atr = _rma(true_range, atr_length)

extreme_high_state = np.isfinite(rsi_smooth) & (rsi_smooth >= extreme_high)
overbought_state = np.isfinite(rsi_smooth) & (rsi_smooth >= overbought)
bullish_state = np.isfinite(rsi_smooth) & (rsi_smooth > 50.0) & (rsi_smooth < overbought)
neutral_state = np.isfinite(rsi_smooth) & (rsi_smooth >= 45.0) & (rsi_smooth <= 55.0)
bearish_state = np.isfinite(rsi_smooth) & (rsi_smooth < 50.0) & (rsi_smooth > oversold)
oversold_state = np.isfinite(rsi_smooth) & (rsi_smooth <= oversold)
extreme_low_state = np.isfinite(rsi_smooth) & (rsi_smooth <= extreme_low)

up_signal = _cross_over(rsi_smooth, np.full(n, float(extreme_low)))
down_signal = _cross_under(rsi_smooth, np.full(n, float(extreme_high)))
bull_cross = _cross_over(rsi_smooth, signal)
bear_cross = _cross_under(rsi_smooth, signal)
bull_reclaim = _cross_over(rsi_smooth, np.full(n, float(oversold)))
bear_reject = _cross_under(rsi_smooth, np.full(n, float(overbought)))
center_bull = _cross_over(rsi_smooth, np.full(n, 50.0))
center_bear = _cross_under(rsi_smooth, np.full(n, 50.0))

entry_base = opened if entry_mode == "Open" else (high + low + close) / 3.0 if entry_mode == "HLC3" else close
long_created = np.zeros(n, dtype=bool)
short_created = np.zeros(n, dtype=bool)
long_tp_hit = np.zeros(n, dtype=bool)
long_sl_hit = np.zeros(n, dtype=bool)
short_tp_hit = np.zeros(n, dtype=bool)
short_sl_hit = np.zeros(n, dtype=bool)
stored_count = np.zeros(n, dtype=int)
active_count = np.zeros(n, dtype=int)
trade_store = []
trade_identifier = 0

for index in range(n):
    for direction, triggered in ((1, up_signal[index]), (-1, down_signal[index])):
        if not show_trade_projections or not triggered:
            continue
        entry = float(entry_base[index])
        if stop_mode == "Signal Candle":
            raw_stop = float(low[index] if direction == 1 else high[index])
        elif stop_mode == "ATR":
            if not np.isfinite(atr[index]):
                continue
            raw_stop = float(entry - direction * atr[index] * atr_multiplier)
        else:
            raw_stop = float(entry * (1.0 - direction * percent_stop / 100.0))
        stop = min(raw_stop, entry - minimum_tick) if direction == 1 else max(raw_stop, entry + minimum_tick)
        risk = direction * (entry - stop)
        if risk <= 0.0:
            continue
        target = entry + direction * risk * risk_reward
        record = {
            "id": int(trade_identifier), "direction": int(direction), "startIndex": int(index), "endIndex": int(index),
            "entry": float(entry), "target": float(target), "stop": float(stop), "active": True,
            "result": "Active", "hitIndex": None,
        }
        trade_identifier += 1
        trade_store.append(record)
        if direction == 1:
            long_created[index] = True
        else:
            short_created[index] = True
    while len(trade_store) > maximum_stored_trades:
        trade_store.pop(0)

    for record in trade_store:
        if not record["active"]:
            continue
        record["endIndex"] = int(index)
        if index <= record["startIndex"]:
            continue
        if record["direction"] == 1:
            tp_hit = high[index] >= record["target"]
            sl_hit = low[index] <= record["stop"]
        else:
            tp_hit = low[index] <= record["target"]
            sl_hit = high[index] >= record["stop"]
        if tp_hit and sl_hit:
            if same_bar_priority == "SL":
                tp_hit = False
            else:
                sl_hit = False
        if not (tp_hit or sl_hit):
            continue
        record["active"] = False
        record["result"] = "TP" if tp_hit else "SL"
        record["hitIndex"] = int(index)
        if record["direction"] == 1:
            long_tp_hit[index] |= tp_hit
            long_sl_hit[index] |= sl_hit
        else:
            short_tp_hit[index] |= tp_hit
            short_sl_hit[index] |= sl_hit
    stored_count[index] = len(trade_store)
    active_count[index] = sum(1 for record in trade_store if record["active"])

display_first_index = max(0, n - 160)
display_trade_store = [record for record in trade_store if record["active"] or record["endIndex"] >= display_first_index]
layers = []
for record in display_trade_store:
    start_index, end_index = max(record["startIndex"], display_first_index), record["endIndex"]
    entry, target, stop = record["entry"], record["target"], record["stop"]
    layers.extend([
        {"type": "zone", "role": "Take Profit", "startIndex": start_index, "endIndex": end_index, "top": float(max(entry, target)), "bottom": float(min(entry, target)), "fillColor": "rgba(34,197,94,0.35)", "borderColor": "#22C55E", "borderSize": 1},
        {"type": "zone", "role": "Stop Loss", "startIndex": start_index, "endIndex": end_index, "top": float(max(entry, stop)), "bottom": float(min(entry, stop)), "fillColor": "rgba(239,68,68,0.45)", "borderColor": "#EF4444", "borderSize": 1},
        {"type": "line", "role": "Entry", "startIndex": start_index, "endIndex": end_index, "y1": entry, "y2": entry, "color": "#141414", "lineWidth": 2},
        {"type": "label", "role": "Projected Entry", "index": start_index, "price": entry, "text": "BUY" if record["direction"] == 1 else "SELL", "color": "#22C55E" if record["direction"] == 1 else "#EF4444", "textColor": "#FFFFFF", "size": label_text_size.lower()},
    ])
    if record["hitIndex"] is not None:
        hit_price = target if record["result"] == "TP" else stop
        layers.append({"type": "label", "role": "Trade Result", "index": record["hitIndex"], "price": hit_price, "text": f"{record['result']} HIT", "color": "#22C55E" if record["result"] == "TP" else "#EF4444", "textColor": "#FFFFFF", "size": label_text_size.lower()})

status = []
momentum = []
structure = []
for index in range(n):
    status.append("Extreme High" if extreme_high_state[index] else "Overbought" if overbought_state[index] else "Bullish" if bullish_state[index] else "Neutral" if neutral_state[index] else "Bearish" if bearish_state[index] else "Oversold" if oversold_state[index] else "Extreme Low")
    momentum.append("Strong Up" if np.isfinite(delta[index]) and delta[index] >= 1.5 else "Up" if np.isfinite(delta[index]) and delta[index] > 0.0 else "Strong Down" if np.isfinite(delta[index]) and delta[index] <= -1.5 else "Down" if np.isfinite(delta[index]) and delta[index] < 0.0 else "Flat")
    structure.append("RSI > Signal" if np.isfinite(spread[index]) and spread[index] > 0.0 else "RSI < Signal" if np.isfinite(spread[index]) and spread[index] < 0.0 else "At Signal")

position_map = {"Top Right": "top-right", "Top Left": "top-left", "Bottom Right": "bottom-right", "Bottom Left": "bottom-left"}
tables = []
if show_panel and n:
    last = n - 1
    tables = [{
        "title": "RSI Panel", "position": position_map[panel_position], "compact": panel_text_size != "Normal",
        "columns": [{"text": "RSI Panel"}, {"text": "Value"}],
        "rows": [
            [{"text": "RSI"}, {"text": "N/A" if not np.isfinite(rsi_smooth[last]) else f"{rsi_smooth[last]:.2f}"}],
            [{"text": "Signal"}, {"text": "N/A" if not np.isfinite(signal[last]) else f"{signal[last]:.2f}"}],
            [{"text": "Status"}, {"text": status[last]}],
            [{"text": "Momentum"}, {"text": momentum[last]}],
            [{"text": "Structure"}, {"text": structure[last]}],
        ],
    }]

overbought_cloud = [{"upper": 100.0, "lower": float(overbought), "color": "#EF4444", "opacity": 0.30} for _ in range(n)]
oversold_cloud = [{"upper": float(oversold), "lower": 0.0, "color": "#22C55E", "opacity": 0.30} for _ in range(n)]
signals = [
    {"type": "buy", "name": "Extreme Low Reclaim", "text": "UP", "data": _event(up_signal & (np.arange(n) >= display_first_index), rsi_smooth, "UP", "#22C55E"), "color": "#22C55E", "renderMode": "events", "overlay": False},
    {"type": "sell", "name": "Extreme High Rejection", "text": "DOWN", "data": _event(down_signal & (np.arange(n) >= display_first_index), rsi_smooth, "DOWN", "#EF4444"), "color": "#EF4444", "renderMode": "events", "overlay": False},
    {"type": "buy", "name": "Projected Long", "text": "BUY", "data": _event(long_created & (np.arange(n) >= display_first_index), entry_base, "BUY", "#22C55E"), "color": "#22C55E", "renderMode": "events", "overlay": True},
    {"type": "sell", "name": "Projected Short", "text": "SELL", "data": _event(short_created & (np.arange(n) >= display_first_index), entry_base, "SELL", "#EF4444"), "color": "#EF4444", "renderMode": "events", "overlay": True},
    {"type": "sell", "name": "Take Profit Hit", "text": "TP", "data": _event((long_tp_hit | short_tp_hit) & (np.arange(n) >= display_first_index), np.where(long_tp_hit, high, low), "TP", "#22C55E"), "color": "#22C55E", "renderMode": "events", "overlay": True},
    {"type": "sell", "name": "Stop Loss Hit", "text": "SL", "data": _event((long_sl_hit | short_sl_hit) & (np.arange(n) >= display_first_index), np.where(long_sl_hit, low, high), "SL", "#EF4444"), "color": "#EF4444", "renderMode": "events", "overlay": True},
]

background_colors = ["rgba(239,68,68,0.20)" if show_extreme_background and extreme_high_state[index] else "rgba(34,197,94,0.20)" if show_extreme_background and extreme_low_state[index] else None for index in range(n)]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Overbought Zone", "title": False, "data": overbought_cloud, "color": "#EF4444", "type": "cloud", "overlay": False},
        {"name": "Oversold Zone", "title": False, "data": oversold_cloud, "color": "#22C55E", "type": "cloud", "overlay": False},
        {"name": "Center", "title": True, "data": [50.0] * n, "color": "rgba(139,148,158,0.40)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Overbought", "title": True, "data": [float(overbought)] * n, "color": "#EF4444", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Oversold", "title": True, "data": [float(oversold)] * n, "color": "#22C55E", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Extreme High", "title": True, "data": [float(extreme_high)] * n, "color": "rgba(239,68,68,0.55)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Extreme Low", "title": True, "data": [float(extreme_low)] * n, "color": "rgba(34,197,94,0.55)", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "RSI", "title": True, "data": _finite(rsi_smooth), "color": "#58A6FF", "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "RSI Extreme High", "title": False, "data": [float(rsi_smooth[index]) if extreme_high_state[index] else None for index in range(n)], "color": "#EF4444", "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "RSI Extreme Low", "title": False, "data": [float(rsi_smooth[index]) if extreme_low_state[index] else None for index in range(n)], "color": "#22C55E", "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "Signal", "title": True, "data": _finite(signal), "color": "rgba(255,255,255,0.65)", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": signals,
    "layers": layers,
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": background_colors,
    "calculatedVars": {
        "rsiSource": _finite(rsi_source), "averageGain": _finite(average_gain), "averageLoss": _finite(average_loss),
        "rsiRaw": _finite(rsi_raw), "rsiSmooth": _finite(rsi_smooth), "signal": _finite(signal), "spread": _finite(spread), "delta": _finite(delta), "atr": _finite(atr),
        "isExtremeHigh": extreme_high_state.tolist(), "isOverbought": overbought_state.tolist(), "isBullish": bullish_state.tolist(), "isNeutral": neutral_state.tolist(), "isBearish": bearish_state.tolist(), "isOversold": oversold_state.tolist(), "isExtremeLow": extreme_low_state.tolist(),
        "upSignal": up_signal.tolist(), "downSignal": down_signal.tolist(), "bullCross": bull_cross.tolist(), "bearCross": bear_cross.tolist(), "bullReclaim": bull_reclaim.tolist(), "bearReject": bear_reject.tolist(), "centerBull": center_bull.tolist(), "centerBear": center_bear.tolist(),
        "longCreated": long_created.tolist(), "shortCreated": short_created.tolist(), "longTpHit": long_tp_hit.tolist(), "longSlHit": long_sl_hit.tolist(), "shortTpHit": short_tp_hit.tolist(), "shortSlHit": short_sl_hit.tolist(),
        "storedTradeCount": stored_count.tolist(), "activeTradeCount": active_count.tolist(), "tradeStore": trade_store,
        "status": status, "momentum": momentum, "crossState": structure,
        "crossAlert": ((bull_cross | bear_cross) & enable_cross_alerts).tolist(), "zoneAlert": ((bull_reclaim | bear_reject) & enable_zone_alerts).tolist(), "centerAlert": ((center_bull | center_bear) & enable_center_alerts).tolist(),
        "entryAlert": ((up_signal | down_signal) & enable_entry_alerts).tolist(), "tradeAlert": ((long_tp_hit | long_sl_hit | short_tp_hit | short_sl_hit) & enable_trade_alerts).tolist(),
    },
}
