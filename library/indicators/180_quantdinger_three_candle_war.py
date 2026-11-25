# ============================================================
# QuantDinger Three Candle War
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Three Candle War"
my_indicator_description = (
    "Tracks a three-candle sweep, reclaim, and confirmation pattern with a multi-timeframe SMA, "
    "displacement order blocks, first-touch confluence, warnings, and fixed risk/reward zones."
)

indicator_timeframes = ["5m", "15m", "1H", "4H", "1D"]

# @param show_c1_lines bool true Show C1 range lines
# @param max_history int 50 Confirmed setups kept range=5:200:1
# @param waiting_line_color str #667085 Waiting line color
# @param setup_line_color str #FF00FF Setup line color
# @param show_buy bool true Show buy signals
# @param show_sell bool true Show sell signals
# @param show_pending_setups bool false Show pending C2 setup labels
# @param buy_color str #FFD600 Buy signal color
# @param sell_color str #F44336 Sell signal color
# @param close_tolerance_atr float 0.01 C3 close tolerance range=0.0:5.0:0.01
# @param use_ma_filter bool true Enable MA filter
# @param ma_timeframe str Current MA timeframe values=Current,5m,15m,1H,4H,1D
# @param ma_length int 20 SMA period range=1:500:1
# @param ma_source str close MA source values=close,open,high,low,hl2,hlc3,ohlc4
# @param show_ma bool true Show SMA
# @param dynamic_ma_color bool true Dynamic SMA color
# @param ma_up_color str #00E676 MA up color
# @param ma_down_color str #F44336 MA down color
# @param ma_flat_color str #00BCD4 Fixed SMA color
# @param order_blocks bool true Enable order blocks
# @param show_order_blocks bool true Show order block zones
# @param require_displacement bool true Require displacement
# @param atr_length int 14 ATR length range=1:500:1
# @param displacement_mult float 1.2 Displacement ATR multiplier range=0.1:20.0:0.1
# @param order_block_count int 5 Maximum blocks per side range=1:20:1
# @param max_block_distance_atr float 8.0 Maximum visible block distance in ATR range=1.0:100.0:1.0
# @param mitigate_on_close bool true Mitigate blocks on close
# @param proximity_atr float 0.5 Nearby margin in ATR range=0.0:10.0:0.1
# @param bypass_ma_on_touch bool true Bypass MA when C1/C2 touches matching block
# @param bullish_ob_color str #00897B Bullish block color
# @param bearish_ob_color str #FB8C00 Bearish block color
# @param show_first_touch bool true Show first important block touch
# @param important_size_atr float 1.0 Important block size range=0.0:10.0:0.1
# @param show_trade_zones bool true Show SL and TP zones
# @param reward_risk float 2.0 Take profit ratio range=0.5:20.0:0.5
# @param minimum_stop_atr float 0.5 Stop padding in ATR range=0.0:10.0:0.1
# @param trade_zone_bars int 30 Trade zone length range=1:500:1
# @param max_trade_zones int 10 Maximum trade zones range=1:50:1
# @param show_trade_labels bool false Show trade details

show_c1_lines = bool(params.get("show_c1_lines", True))
max_history = min(200, max(5, int(params.get("max_history", 50))))
waiting_line_color = str(params.get("waiting_line_color", "#667085"))
setup_line_color = str(params.get("setup_line_color", "#FF00FF"))
show_buy = bool(params.get("show_buy", True))
show_sell = bool(params.get("show_sell", True))
show_pending_setups = bool(params.get("show_pending_setups", False))
buy_color = str(params.get("buy_color", "#FFD600"))
sell_color = str(params.get("sell_color", "#F44336"))
close_tolerance_atr = min(5.0, max(0.0, float(params.get("close_tolerance_atr", 0.01))))
use_ma_filter = bool(params.get("use_ma_filter", True))
ma_timeframe = str(params.get("ma_timeframe", "Current"))
if ma_timeframe not in ("Current", "5m", "15m", "1H", "4H", "1D"):
    ma_timeframe = "5m"
ma_length = min(500, max(1, int(params.get("ma_length", 20))))
ma_source = str(params.get("ma_source", "close"))
if ma_source not in ("close", "open", "high", "low", "hl2", "hlc3", "ohlc4"):
    ma_source = "close"
show_ma = bool(params.get("show_ma", True))
dynamic_ma_color = bool(params.get("dynamic_ma_color", True))
ma_up_color = str(params.get("ma_up_color", "#00E676"))
ma_down_color = str(params.get("ma_down_color", "#F44336"))
ma_flat_color = str(params.get("ma_flat_color", "#00BCD4"))
order_blocks_enabled = bool(params.get("order_blocks", True))
show_order_blocks = bool(params.get("show_order_blocks", True))
require_displacement = bool(params.get("require_displacement", True))
atr_length = min(500, max(1, int(params.get("atr_length", 14))))
displacement_mult = min(20.0, max(0.1, float(params.get("displacement_mult", 1.2))))
order_block_count = min(20, max(1, int(params.get("order_block_count", 5))))
max_block_distance_atr = min(100.0, max(1.0, float(params.get("max_block_distance_atr", 8.0))))
mitigate_on_close = bool(params.get("mitigate_on_close", True))
proximity_atr = min(10.0, max(0.0, float(params.get("proximity_atr", 0.5))))
bypass_ma_on_touch = bool(params.get("bypass_ma_on_touch", True))
bullish_ob_color = str(params.get("bullish_ob_color", "#00897B"))
bearish_ob_color = str(params.get("bearish_ob_color", "#FB8C00"))
show_first_touch = bool(params.get("show_first_touch", True))
important_size_atr = min(10.0, max(0.0, float(params.get("important_size_atr", 1.0))))
show_trade_zones = bool(params.get("show_trade_zones", True))
reward_risk = min(20.0, max(0.5, float(params.get("reward_risk", 2.0))))
minimum_stop_atr = min(10.0, max(0.0, float(params.get("minimum_stop_atr", 0.5))))
trade_zone_bars = min(500, max(1, int(params.get("trade_zone_bars", 30))))
max_trade_zones = min(50, max(1, int(params.get("max_trade_zones", 10))))
show_trade_labels = bool(params.get("show_trade_labels", False))

timeframe_seconds = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1H": 3600, "2H": 7200, "4H": 14400, "6H": 21600,
    "8H": 28800, "12H": 43200, "1D": 86400, "1W": 604800,
}

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
raw_time = df["time"].astype(float).to_numpy(dtype=float) if "time" in df.columns else np.arange(len(df), dtype=float)
base_time = raw_time / 1000.0 if len(raw_time) and np.nanmax(np.abs(raw_time)) > 10_000_000_000 else raw_time
n = len(df)
runtime_context = ctx
chart_timeframe = str(runtime_context.timeframe or "1H")
if chart_timeframe not in timeframe_seconds:
    chart_timeframe = "1H"
chart_seconds = timeframe_seconds[chart_timeframe]

def normalized_frame(frame):
    if frame is None or len(frame) == 0:
        frame = df.copy()
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

def sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result

def rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    result[length - 1] = float(np.mean(values[:length]))
    for index in range(length, len(values)):
        result[index] = (result[index - 1] * (length - 1) + values[index]) / length
    return result

def atr_series():
    true_range = np.zeros(n, dtype=float)
    for index in range(n):
        if index == 0:
            true_range[index] = high[index] - low[index]
        else:
            true_range[index] = max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
    return rma(true_range, atr_length)

def frame_source(frame, source_name):
    source_open = frame["open"].to_numpy(dtype=float)
    source_high = frame["high"].to_numpy(dtype=float)
    source_low = frame["low"].to_numpy(dtype=float)
    source_close = frame["close"].to_numpy(dtype=float)
    if source_name == "open":
        return source_open
    if source_name == "high":
        return source_high
    if source_name == "low":
        return source_low
    if source_name == "hl2":
        return (source_high + source_low) / 2.0
    if source_name == "hlc3":
        return (source_high + source_low + source_close) / 3.0
    if source_name == "ohlc4":
        return (source_open + source_high + source_low + source_close) / 4.0
    return source_close

def align_completed(source_time, source_values, source_seconds):
    result = np.full(n, np.nan, dtype=float)
    source_close_time = source_time + source_seconds
    base_close_time = base_time + chart_seconds
    for index in range(n):
        source_index = int(np.searchsorted(source_close_time, base_close_time[index], side="right") - 1)
        if 0 <= source_index < len(source_values) and np.isfinite(source_values[source_index]):
            result[index] = float(source_values[source_index])
    return result

if ma_timeframe == "Current" or ma_timeframe == chart_timeframe:
    ma_frame = normalized_frame(df)
    resolved_ma_timeframe = chart_timeframe
else:
    requested_ma_frame = runtime_context.raw_series(ma_timeframe) if runtime_context is not None and hasattr(runtime_context, "raw_series") else None
    if requested_ma_frame is None or len(requested_ma_frame) == 0:
        ma_frame = normalized_frame(df)
        resolved_ma_timeframe = chart_timeframe
    else:
        ma_frame = normalized_frame(requested_ma_frame)
        resolved_ma_timeframe = ma_timeframe
ma_source_values = frame_source(ma_frame, ma_source)
ma_source_time = ma_frame["time"].to_numpy(dtype=float)
ma_raw = sma(ma_source_values, ma_length)
ma_values = align_completed(ma_source_time, ma_raw, timeframe_seconds[resolved_ma_timeframe])
atr_values = atr_series()

def overlaps(blocks, candle_low, candle_high, margin):
    for block in blocks:
        if block["active"] and candle_high >= block["bottom"] - margin and candle_low <= block["top"] + margin:
            return True
    return False

def near_price(blocks, price, margin):
    for block in blocks:
        if block["active"] and price <= block["top"] + margin and price >= block["bottom"] - margin:
            return True
    return False

bull_blocks = []
bear_blocks = []
first_bull_touch = np.zeros(n, dtype=bool)
first_bear_touch = np.zeros(n, dtype=bool)
buy_signal = np.zeros(n, dtype=bool)
sell_signal = np.zeros(n, dtype=bool)
buy_near_ob = np.zeros(n, dtype=bool)
sell_near_ob = np.zeros(n, dtype=bool)
buy_warn_ob = np.zeros(n, dtype=bool)
sell_warn_ob = np.zeros(n, dtype=bool)
pending_buy = np.zeros(n, dtype=bool)
pending_sell = np.zeros(n, dtype=bool)
confirmed_segments = []
trade_zones = []
state = 0
c1_high = np.nan
c1_low = np.nan
c1_index = -1
is_buy_setup = False
is_sell_setup = False
c1c2_touched_bull = False
c1c2_touched_bear = False

for index in range(n):
    current_atr = atr_values[index] if np.isfinite(atr_values[index]) else 0.0

    if order_blocks_enabled:
        for block in bull_blocks:
            if block["active"] and (close[index] < block["bottom"] if mitigate_on_close else low[index] < block["bottom"]):
                block["active"] = False
                block["end"] = index - 1
        for block in bear_blocks:
            if block["active"] and (close[index] > block["top"] if mitigate_on_close else high[index] > block["top"]):
                block["active"] = False
                block["end"] = index - 1

        if index > 0:
            bullish_ob = close[index - 1] < open_price[index - 1] and close[index] > high[index - 1]
            bearish_ob = close[index - 1] > open_price[index - 1] and close[index] < low[index - 1]
            if require_displacement:
                bullish_ob = bullish_ob and close[index] - open_price[index] > current_atr * displacement_mult
                bearish_ob = bearish_ob and open_price[index] - close[index] > current_atr * displacement_mult
            if bullish_ob:
                bull_blocks.append({"top": float(high[index - 1]), "bottom": float(low[index - 1]), "start": index - 1, "end": None, "active": True, "touched": False})
                active_bulls = [block for block in bull_blocks if block["active"]]
                if len(active_bulls) > order_block_count:
                    oldest = active_bulls[0]
                    oldest["active"] = False
                    oldest["end"] = index - 1
            if bearish_ob:
                bear_blocks.append({"top": float(high[index - 1]), "bottom": float(low[index - 1]), "start": index - 1, "end": None, "active": True, "touched": False})
                active_bears = [block for block in bear_blocks if block["active"]]
                if len(active_bears) > order_block_count:
                    oldest = active_bears[0]
                    oldest["active"] = False
                    oldest["end"] = index - 1

        if show_first_touch and show_order_blocks:
            for block in bull_blocks:
                if block["active"] and not block["touched"] and block["top"] - block["bottom"] >= current_atr * important_size_atr and high[index] >= block["bottom"] and low[index] <= block["top"]:
                    block["touched"] = True
                    first_bull_touch[index] = True
            for block in bear_blocks:
                if block["active"] and not block["touched"] and block["top"] - block["bottom"] >= current_atr * important_size_atr and high[index] >= block["bottom"] and low[index] <= block["top"]:
                    block["touched"] = True
                    first_bear_touch[index] = True

    above_ma = not use_ma_filter or (np.isfinite(ma_values[index]) and close[index] > ma_values[index])
    below_ma = not use_ma_filter or (np.isfinite(ma_values[index]) and close[index] < ma_values[index])

    if state == 0:
        c1_high = high[index]
        c1_low = low[index]
        c1_index = index
        c1c2_touched_bull = order_blocks_enabled and overlaps(bull_blocks, low[index], high[index], current_atr * proximity_atr)
        c1c2_touched_bear = order_blocks_enabled and overlaps(bear_blocks, low[index], high[index], current_atr * proximity_atr)
        state = 1
    elif state == 1:
        broke_down = low[index] < c1_low
        broke_up = high[index] > c1_high
        reclaimed = close[index] >= c1_low and close[index] <= c1_high
        buy_setup = broke_down and not broke_up and reclaimed
        sell_setup = broke_up and not broke_down and reclaimed
        if buy_setup or sell_setup:
            if order_blocks_enabled:
                margin = current_atr * proximity_atr
                c1c2_touched_bull = c1c2_touched_bull or overlaps(bull_blocks, low[index], high[index], margin)
                c1c2_touched_bear = c1c2_touched_bear or overlaps(bear_blocks, low[index], high[index], margin)
            is_buy_setup = buy_setup
            is_sell_setup = sell_setup
            pending_buy[index] = buy_setup
            pending_sell[index] = sell_setup
            state = 2
        else:
            c1_high = high[index]
            c1_low = low[index]
            c1_index = index
            c1c2_touched_bull = order_blocks_enabled and overlaps(bull_blocks, low[index], high[index], current_atr * proximity_atr)
            c1c2_touched_bear = order_blocks_enabled and overlaps(bear_blocks, low[index], high[index], current_atr * proximity_atr)
            state = 1
    else:
        margin = current_atr * proximity_atr
        tolerance = current_atr * close_tolerance_atr
        buy_base = is_buy_setup and low[index] >= c1_low and high[index] >= c1_high and close[index] > c1_high - tolerance
        sell_base = is_sell_setup and high[index] <= c1_high and low[index] <= c1_low and close[index] < c1_low + tolerance
        buy_near = order_blocks_enabled and buy_base and near_price(bull_blocks, close[index], margin)
        sell_near = order_blocks_enabled and sell_base and near_price(bear_blocks, close[index], margin)
        buy_warning = order_blocks_enabled and buy_base and near_price(bear_blocks, close[index], margin)
        sell_warning = order_blocks_enabled and sell_base and near_price(bull_blocks, close[index], margin)
        buy_bypass = order_blocks_enabled and bypass_ma_on_touch and c1c2_touched_bull
        sell_bypass = order_blocks_enabled and bypass_ma_on_touch and c1c2_touched_bear
        buy_confirm = buy_base and (above_ma or buy_near or buy_bypass)
        sell_confirm = sell_base and (below_ma or sell_near or sell_bypass)
        buy_signal[index] = buy_confirm and show_buy
        sell_signal[index] = sell_confirm and show_sell
        buy_near_ob[index] = buy_signal[index] and buy_near
        sell_near_ob[index] = sell_signal[index] and sell_near
        buy_warn_ob[index] = buy_signal[index] and buy_warning
        sell_warn_ob[index] = sell_signal[index] and sell_warning

        if buy_confirm or sell_confirm:
            confirmed_segments.append({"start": c1_index, "end": index, "high": float(c1_high), "low": float(c1_low)})
            if show_trade_zones:
                is_long = buy_confirm
                entry = float(close[index])
                stop = float(c1_low - current_atr * minimum_stop_atr) if is_long else float(c1_high + current_atr * minimum_stop_atr)
                risk = abs(entry - stop)
                target = entry + risk * reward_risk if is_long else entry - risk * reward_risk
                trade_zones.append({"start": index, "end": min(n - 1, index + trade_zone_bars), "entry": entry, "stop": stop, "target": target, "is_long": is_long})
            state = 0
            is_buy_setup = False
            is_sell_setup = False
        else:
            c1_high = high[index]
            c1_low = low[index]
            c1_index = index
            c1c2_touched_bull = order_blocks_enabled and overlaps(bull_blocks, low[index], high[index], margin)
            c1c2_touched_bear = order_blocks_enabled and overlaps(bear_blocks, low[index], high[index], margin)
            is_buy_setup = False
            is_sell_setup = False
            state = 1

plots = [
    {
        "name": "SMA Filter",
        "title": show_ma,
        "data": [
            {
                "value": float(ma_values[index]),
                "color": ma_up_color if dynamic_ma_color and close[index] > ma_values[index] else ma_down_color if dynamic_ma_color and close[index] < ma_values[index] else ma_flat_color,
            }
            if np.isfinite(ma_values[index]) else None
            for index in range(n)
        ],
        "color": ma_flat_color if show_ma else "rgba(0,188,212,0.001)",
        "type": "line",
        "lineWidth": 2,
        "overlay": True,
    }
]

if show_c1_lines:
    for segment_index, segment in enumerate(confirmed_segments[-max_history:], 1):
        high_data = [None for _ in range(n)]
        low_data = [None for _ in range(n)]
        for index in range(segment["start"], min(n, segment["start"] + 3)):
            high_data[index] = segment["high"]
            low_data[index] = segment["low"]
        plots.append({"name": f"Confirmed C1 High {segment_index}", "title": False, "data": high_data, "color": setup_line_color, "type": "line", "overlay": True})
        plots.append({"name": f"Confirmed C1 Low {segment_index}", "title": False, "data": low_data, "color": setup_line_color, "type": "line", "overlay": True})
    if c1_index >= 0:
        active_high = [None for _ in range(n)]
        active_low = [None for _ in range(n)]
        for index in range(c1_index, min(n, c1_index + 3)):
            active_high[index] = float(c1_high)
            active_low[index] = float(c1_low)
        active_color = setup_line_color if state == 2 else waiting_line_color
        plots.append({"name": "Active C1 High", "data": active_high, "color": active_color, "type": "line", "overlay": True})
        plots.append({"name": "Active C1 Low", "data": active_low, "color": active_color, "type": "line", "overlay": True})

if order_blocks_enabled and show_order_blocks:
    current_atr = float(atr_values[-1]) if n and np.isfinite(atr_values[-1]) else 0.0
    current_price = float(close[-1]) if n else 0.0
    maximum_distance = current_atr * max_block_distance_atr
    def visible_block(block):
        if not block["active"]:
            return False
        distance = max(float(block["bottom"]) - current_price, current_price - float(block["top"]), 0.0)
        return maximum_distance <= 0.0 or distance <= maximum_distance
    active_bulls = [block for block in bull_blocks if visible_block(block)][-order_block_count:]
    active_bears = [block for block in bear_blocks if visible_block(block)][-order_block_count:]
    for block_index, block in enumerate(active_bulls, 1):
        data = [None for _ in range(n)]
        for index in range(block["start"], n):
            data[index] = {"upper": block["top"], "lower": block["bottom"]}
        plots.append({"name": f"Bullish Order Block {block_index}", "title": False, "data": data, "color": "rgba(0,137,123,0.20)", "type": "cloud", "overlay": True})
    for block_index, block in enumerate(active_bears, 1):
        data = [None for _ in range(n)]
        for index in range(block["start"], n):
            data[index] = {"upper": block["top"], "lower": block["bottom"]}
        plots.append({"name": f"Bearish Order Block {block_index}", "title": False, "data": data, "color": "rgba(251,140,0,0.20)", "type": "cloud", "overlay": True})

for zone_index, zone in enumerate(trade_zones[-max_trade_zones:], 1):
    reward_data = [None for _ in range(n)]
    risk_data = [None for _ in range(n)]
    entry_data = [None for _ in range(n)]
    for index in range(zone["start"], zone["end"] + 1):
        reward_data[index] = {"upper": max(zone["entry"], zone["target"]), "lower": min(zone["entry"], zone["target"])}
        risk_data[index] = {"upper": max(zone["entry"], zone["stop"]), "lower": min(zone["entry"], zone["stop"])}
        entry_data[index] = zone["entry"]
    plots.append({"name": f"Trade {zone_index} Target", "title": False, "data": reward_data, "color": "rgba(0,200,83,0.15)", "type": "cloud", "overlay": True})
    plots.append({"name": f"Trade {zone_index} Stop", "title": False, "data": risk_data, "color": "rgba(244,67,54,0.15)", "type": "cloud", "overlay": True})
    plots.append({"name": f"Trade {zone_index} Entry", "title": False, "data": entry_data, "color": "rgba(255,255,255,0.70)", "type": "line", "overlay": True})

def event_points(mask, values, text, color):
    return [{"value": float(values[index]), "text": text, "color": color} if mask[index] else None for index in range(n)]

signals = [
    {"type": "buy", "name": "Buy Star", "text": "★", "color": buy_color, "data": event_points(buy_signal, low, "★", buy_color), "renderMode": "events"},
    {"type": "sell", "name": "Sell Star", "text": "★", "color": sell_color, "data": event_points(sell_signal, high, "★", sell_color), "renderMode": "events"},
    {"type": "buy", "name": "Buy With Order Block", "text": "OB", "color": bullish_ob_color, "data": event_points(buy_near_ob, low, "OB", bullish_ob_color), "renderMode": "events"},
    {"type": "sell", "name": "Sell With Order Block", "text": "OB", "color": bearish_ob_color, "data": event_points(sell_near_ob, high, "OB", bearish_ob_color), "renderMode": "events"},
    {"type": "sell", "name": "Buy Opposite Block Warning", "text": "WARN OB", "color": setup_line_color, "data": event_points(buy_warn_ob, high, "WARN OB", setup_line_color), "renderMode": "events"},
    {"type": "buy", "name": "Sell Opposite Block Warning", "text": "WARN OB", "color": setup_line_color, "data": event_points(sell_warn_ob, low, "WARN OB", setup_line_color), "renderMode": "events"},
]
if show_pending_setups:
    signals.append({"type": "buy", "name": "Buy Setup Pending", "text": "C2 BUY", "color": setup_line_color, "data": event_points(pending_buy, low, "C2 BUY", setup_line_color), "renderMode": "events"})
    signals.append({"type": "sell", "name": "Sell Setup Pending", "text": "C2 SELL", "color": setup_line_color, "data": event_points(pending_sell, high, "C2 SELL", setup_line_color), "renderMode": "events"})
if show_first_touch:
    signals.append({"type": "buy", "name": "Bullish Block First Touch", "text": "OB", "color": bullish_ob_color, "data": event_points(first_bull_touch, high, "OB", bullish_ob_color), "renderMode": "events"})
    signals.append({"type": "sell", "name": "Bearish Block First Touch", "text": "OB", "color": bearish_ob_color, "data": event_points(first_bear_touch, high, "OB", bearish_ob_color), "renderMode": "events"})

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
}
