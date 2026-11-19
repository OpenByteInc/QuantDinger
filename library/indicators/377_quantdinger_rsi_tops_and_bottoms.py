# ============================================================
# QuantDinger RSI Tops and Bottoms
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger RSI Tops and Bottoms"
my_indicator_description = "Finds short overbought and oversold excursions, then flags price/RSI divergence between successive zone extremes."

# @param rsi_length int 14 RSI length range=1:500:1
# @param source str Close RSI source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param upper_band float 70 Upper RSI band range=0:100:1
# @param lower_band float 30 Lower RSI band range=0:100:1
# @param maximum_zone_bars int 10 Maximum bars inside extreme zone range=1:300:1
# @param minimum_distance int 5 Minimum bars between extremes range=0:1000:1
# @param maximum_distance int 100 Maximum bars between extremes range=1:1000:1
# @param top_color str #FF0000 Top divergence color
# @param bottom_color str #00FF00 Bottom divergence color
# @param line_width int 2 Divergence line width range=1:4:1
# @param color_signal_bars bool true Color divergence bars

rsi_length = min(500, max(1, int(params.get("rsi_length", 14))))
source_name = str(params.get("source", "Close"))
source_name = source_name if source_name in ("Close", "Open", "High", "Low", "HL2", "HLC3", "OHLC4") else "Close"
upper_band = min(100.0, max(0.0, float(params.get("upper_band", 70.0))))
lower_band = min(100.0, max(0.0, float(params.get("lower_band", 30.0))))
maximum_zone_bars = min(300, max(1, int(params.get("maximum_zone_bars", 10))))
minimum_distance = min(1000, max(0, int(params.get("minimum_distance", 5))))
maximum_distance = min(1000, max(1, int(params.get("maximum_distance", 100))))
top_color = str(params.get("top_color", "#FF0000"))
bottom_color = str(params.get("bottom_color", "#00FF00"))
line_width = min(4, max(1, int(params.get("line_width", 2))))
color_signal_bars = bool(params.get("color_signal_bars", True))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


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


def _event(mask, values, text, color, side):
    return {"type": side, "name": text, "text": text, "data": [{"active": True, "price": float(values[index]), "text": text, "color": color} if mask[index] and np.isfinite(values[index]) else None for index in range(len(mask))], "color": color, "renderMode": "events", "overlay": False}


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

sources = {
    "Close": close, "Open": opened, "High": high, "Low": low,
    "HL2": (high + low) / 2.0, "HLC3": (high + low + close) / 3.0,
    "OHLC4": (opened + high + low + close) / 4.0,
}
source = sources[source_name]
change = np.diff(source, prepend=np.nan)
average_gain = _rma(np.where(np.isfinite(change), np.maximum(change, 0.0), np.nan), rsi_length)
average_loss = _rma(np.where(np.isfinite(change), np.maximum(-change, 0.0), np.nan), rsi_length)
rsi = np.full(n, np.nan, dtype=float)
for index in range(n):
    if np.all(np.isfinite([average_gain[index], average_loss[index]])):
        rsi[index] = 100.0 if average_loss[index] == 0.0 else 0.0 if average_gain[index] == 0.0 else 100.0 - 100.0 / (1.0 + average_gain[index] / average_loss[index])

below_oversold = np.zeros(n, dtype=bool)
oversold_count = np.zeros(n, dtype=int)
last_low_rsi = np.full(n, np.nan, dtype=float)
last_low_price = np.full(n, np.nan, dtype=float)
last_low_index = np.full(n, -1, dtype=int)
valid_oversold = np.zeros(n, dtype=bool)
may_go_up = np.zeros(n, dtype=bool)
above_overbought = np.zeros(n, dtype=bool)
overbought_count = np.zeros(n, dtype=int)
last_high_rsi = np.full(n, np.nan, dtype=float)
last_high_price = np.full(n, np.nan, dtype=float)
last_high_index = np.full(n, -1, dtype=int)
valid_overbought = np.zeros(n, dtype=bool)
may_go_down = np.zeros(n, dtype=bool)

below_state = False
below_count_state = 0
low_rsi_state = np.nan
low_price_state = np.nan
low_index_state = -1
low_valid_state = False
above_state = False
above_count_state = 0
high_rsi_state = np.nan
high_price_state = np.nan
high_index_state = -1
high_valid_state = False
divergence_lines = []

for index in range(n):
    previous_rsi = rsi[index - 1] if index > 0 else np.nan
    previous_below = below_state
    previous_below_count = below_count_state
    prior_low_rsi, prior_low_price, prior_low_index, prior_low_valid = low_rsi_state, low_price_state, low_index_state, low_valid_state
    if np.all(np.isfinite([previous_rsi, rsi[index]])) and previous_rsi >= lower_band and rsi[index] < lower_band:
        below_state = True
    elif np.isfinite(rsi[index]) and rsi[index] > lower_band:
        below_state = False
    below_count_state = below_count_state + 1 if below_state else 0
    if previous_below and not below_state and previous_below_count > 0:
        low_rsi_state = 101.0
        low_index_state = index
        low_valid_state = True
        for offset in range(1, previous_below_count + 1):
            if offset > maximum_zone_bars:
                low_valid_state = False
            candidate = rsi[index - offset] if index - offset >= 0 else np.nan
            if np.isfinite(candidate) and candidate < low_rsi_state:
                low_rsi_state = float(candidate)
                low_index_state = int(index - offset)
                low_price_state = float(low[index - offset])
        changed = np.isfinite(prior_low_rsi) and low_rsi_state != prior_low_rsi
        distance = index - prior_low_index if prior_low_index >= 0 else -1
        if changed and low_rsi_state != 0.0 and prior_low_rsi != 0.0 and low_rsi_state > prior_low_rsi and np.isfinite(prior_low_price) and low_price_state < prior_low_price and distance < maximum_distance and low_valid_state and prior_low_valid and distance > minimum_distance:
            may_go_up[index] = True
            divergence_lines.append({"bullish": True, "startIndex": int(prior_low_index), "endIndex": int(index), "startRsi": float(prior_low_rsi), "endRsi": float(low_rsi_state)})
    below_oversold[index] = below_state
    oversold_count[index] = below_count_state
    last_low_rsi[index], last_low_price[index], last_low_index[index], valid_oversold[index] = low_rsi_state, low_price_state, low_index_state, low_valid_state

    previous_above = above_state
    previous_above_count = above_count_state
    prior_high_rsi, prior_high_price, prior_high_index, prior_high_valid = high_rsi_state, high_price_state, high_index_state, high_valid_state
    if np.all(np.isfinite([previous_rsi, rsi[index]])) and previous_rsi <= upper_band and rsi[index] > upper_band:
        above_state = True
    elif np.isfinite(rsi[index]) and rsi[index] < upper_band:
        above_state = False
    above_count_state = above_count_state + 1 if above_state else 0
    if previous_above and not above_state and previous_above_count > 0:
        high_rsi_state = -1.0
        high_index_state = index
        high_valid_state = True
        for offset in range(1, previous_above_count + 1):
            if offset > maximum_zone_bars:
                high_valid_state = False
            candidate = rsi[index - offset] if index - offset >= 0 else np.nan
            if np.isfinite(candidate) and candidate > high_rsi_state:
                high_rsi_state = float(candidate)
                high_index_state = int(index - offset)
                high_price_state = float(high[index - offset])
        changed = np.isfinite(prior_high_rsi) and high_rsi_state != prior_high_rsi
        distance = index - prior_high_index if prior_high_index >= 0 else -1
        if changed and high_rsi_state != 0.0 and prior_high_rsi != 0.0 and high_rsi_state < prior_high_rsi and np.isfinite(prior_high_price) and high_price_state > prior_high_price and distance < maximum_distance and high_valid_state and prior_high_valid and distance > minimum_distance:
            may_go_down[index] = True
            divergence_lines.append({"bullish": False, "startIndex": int(prior_high_index), "endIndex": int(index), "startRsi": float(prior_high_rsi), "endRsi": float(high_rsi_state)})
    above_overbought[index] = above_state
    overbought_count[index] = above_count_state
    last_high_rsi[index], last_high_price[index], last_high_index[index], valid_overbought[index] = high_rsi_state, high_price_state, high_index_state, high_valid_state

layers = [{"type": "line", "role": "Bottom Divergence" if item["bullish"] else "Top Divergence", "startIndex": item["startIndex"], "endIndex": item["endIndex"], "y1": item["startRsi"], "y2": item["endRsi"], "color": bottom_color if item["bullish"] else top_color, "lineWidth": line_width, "arrow": "left"} for item in divergence_lines]
rsi_band = [{"upper": float(max(upper_band, lower_band)), "lower": float(min(upper_band, lower_band)), "color": "#9915FF", "opacity": 0.10} for _ in range(n)]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "RSI Band", "title": False, "data": rsi_band, "color": "#9915FF", "type": "cloud", "overlay": False},
        {"name": "Upper Band", "title": True, "data": [float(upper_band)] * n, "color": "#C0C0C0", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Lower Band", "title": True, "data": [float(lower_band)] * n, "color": "#C0C0C0", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "RSI", "title": True, "data": _finite(rsi), "color": "#8E1599", "type": "line", "lineWidth": 2, "overlay": False},
    ],
    "signals": [
        _event(may_go_up, rsi, "Bottom Divergence", bottom_color, "buy"),
        _event(may_go_down, rsi, "Top Divergence", top_color, "sell"),
    ],
    "layers": layers,
    "tables": [],
    "bar_colors": ["#0000FF" if color_signal_bars and may_go_up[index] else "#000000" if color_signal_bars and may_go_down[index] else None for index in range(n)],
    "background_colors": [None] * n,
    "calculatedVars": {
        "source": _finite(source), "averageGain": _finite(average_gain), "averageLoss": _finite(average_loss), "rsi": _finite(rsi),
        "belowOversold": below_oversold.tolist(), "oversoldCount": oversold_count.tolist(), "lastLowRsi": _finite(last_low_rsi), "lastLowPrice": _finite(last_low_price), "lastLowIndex": last_low_index.tolist(), "validOversold": valid_oversold.tolist(), "mayGoUp": may_go_up.tolist(),
        "aboveOverbought": above_overbought.tolist(), "overboughtCount": overbought_count.tolist(), "lastHighRsi": _finite(last_high_rsi), "lastHighPrice": _finite(last_high_price), "lastHighIndex": last_high_index.tolist(), "validOverbought": valid_overbought.tolist(), "mayGoDown": may_go_down.tolist(),
        "divergenceLines": divergence_lines,
    },
}
