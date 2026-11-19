# ============================================================
# QuantDinger Market Structure Trailing Stop
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Market Structure Trailing Stop"
my_indicator_description = "Turns confirmed pivot touches and signed structure volume into optional market-structure trailing stops."

# @param pivot_length int 8 High and low pivot length range=2:200:1
# @param use_trailing_stop bool false Enable trailing stop
# @param trailing_length int 25 Trailing stop length range=2:500:1
# @param volume_threshold int 500 Volume threshold range=0:100000000:1
# @param show_bos bool false Show break-of-structure labels
# @param show_sweeps bool false Show liquidity sweep labels
# @param color_trend bool false Color candles by market trend

pivot_length = min(200, max(2, int(params.get("pivot_length", 8))))
use_trailing_stop = bool(params.get("use_trailing_stop", False))
trailing_length = min(500, max(2, int(params.get("trailing_length", 25))))
volume_threshold = max(0, int(params.get("volume_threshold", 500)))
show_bos = bool(params.get("show_bos", False))
show_sweeps = bool(params.get("show_sweeps", False))
color_trend = bool(params.get("color_trend", False))
up_color = "#1FC0B0"
down_color = "#D11C43"


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


frame = df.copy().reset_index(drop=True)
if "time" not in frame.columns:
    frame["time"] = np.arange(len(frame), dtype=float) * 3600000.0
if "volume" not in frame.columns:
    frame["volume"] = 0.0
for column in ("time", "open", "high", "low", "close", "volume"):
    frame[column] = pd.to_numeric(frame[column], errors="coerce")
frame["volume"] = frame["volume"].fillna(0.0)
frame = frame.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
times = frame["time"].to_numpy(dtype=float)
if len(times) and np.nanmax(np.abs(times)) < 100000000000.0: times = times * 1000.0
open_price = frame["open"].to_numpy(dtype=float); high = frame["high"].to_numpy(dtype=float); low = frame["low"].to_numpy(dtype=float); close = frame["close"].to_numpy(dtype=float); volume = frame["volume"].to_numpy(dtype=float)
n = len(frame)

pivot_high = np.full(n, np.nan, dtype=float); pivot_low = np.full(n, np.nan, dtype=float)
for index in range(2 * pivot_length, n):
    candidate = index - pivot_length
    if high[candidate] > np.max(high[candidate - pivot_length:candidate]) and high[candidate] >= np.max(high[candidate + 1:candidate + pivot_length + 1]): pivot_high[index] = high[candidate]
    if low[candidate] < np.min(low[candidate - pivot_length:candidate]) and low[candidate] <= np.min(low[candidate + 1:candidate + pivot_length + 1]): pivot_low[index] = low[candidate]

active_upper = None; active_lower = None; count_up = 0; count_down = 0; trend_up_counter = 0; trend_down_counter = 0; signed_volume_up = 0.0; signed_volume_down = 0.0
upper_touch = np.zeros(n, dtype=bool); lower_touch = np.zeros(n, dtype=bool); count_up_history = np.zeros(n, dtype=int); count_down_history = np.zeros(n, dtype=int); trend_up_history = np.zeros(n, dtype=int); trend_down_history = np.zeros(n, dtype=int); signed_up_history = np.zeros(n); signed_down_history = np.zeros(n)
structure_lines = []; structure_labels = []
for index in range(n):
    if np.isfinite(pivot_high[index]): active_upper = {"index": index - pivot_length, "level": float(pivot_high[index])}
    if np.isfinite(pivot_low[index]): active_lower = {"index": index - pivot_length, "level": float(pivot_low[index])}
    if active_upper is not None and high[index] > active_upper["level"] and low[index] < active_upper["level"]:
        upper_touch[index] = True; count_up += 1; count_down = 0; start = active_upper["index"]; level = active_upper["level"]
        signed_volume_up = float(np.sum(np.where(close[start:index + 1] > open_price[start:index + 1], volume[start:index + 1], -volume[start:index + 1])))
        structure_lines.append((start, index, level, up_color, "solid" if count_up == 1 or show_bos else "dashed"))
        if count_up == 1: structure_labels.append((start, level, f"{signed_volume_up:.0f}\nCHoCH", "#D0D0D0", "down"))
        elif show_bos:
            midpoint = int(index - (index - start) / 2); sweep = show_sweeps and close[index] < open_price[index] and open_price[index] < level
            structure_labels.append((midpoint, level, f"{signed_volume_up:.0f}\n{'X' if sweep else 'BoS'}", up_color, "down"))
        active_upper = None
    if active_lower is not None and high[index] > active_lower["level"] and low[index] < active_lower["level"]:
        lower_touch[index] = True; count_down += 1; count_up = 0; start = active_lower["index"]; level = active_lower["level"]
        signed_volume_down = float(np.sum(np.where(close[start:index + 1] > open_price[start:index + 1], volume[start:index + 1], -volume[start:index + 1])))
        structure_lines.append((start, index, level, down_color, "solid" if count_down == 1 or show_bos else "dashed"))
        if count_down == 1: structure_labels.append((start, level, f"CHoCH\n{signed_volume_down:.0f}", "#D0D0D0", "up"))
        elif show_bos:
            midpoint = int(index - (index - start) / 2); sweep = show_sweeps and close[index] < open_price[index] and close[index] > level
            structure_labels.append((midpoint, level, f"{'X' if sweep else 'BoS'}\n{signed_volume_down:.0f}", down_color, "up"))
        active_lower = None
    if count_up > 0: trend_down_counter = 0; trend_up_counter += 1
    if count_down > 0: trend_up_counter = 0; trend_down_counter += 1
    count_up_history[index] = count_up; count_down_history[index] = count_down; trend_up_history[index] = trend_up_counter; trend_down_history[index] = trend_down_counter; signed_up_history[index] = signed_volume_up; signed_down_history[index] = signed_volume_down

rolling_high = pd.Series(high).rolling(trailing_length, min_periods=1).max().to_numpy(dtype=float); rolling_low = pd.Series(low).rolling(trailing_length, min_periods=1).min().to_numpy(dtype=float)
band = np.full(n, np.nan, dtype=float)
for index in range(5, n):
    rising_floor = rolling_low[index - 5] <= rolling_low[index - 4] <= rolling_low[index - 3] <= rolling_low[index - 2] <= rolling_low[index - 1] <= rolling_low[index]
    falling_ceiling = rolling_high[index - 5] >= rolling_high[index - 4] >= rolling_high[index - 3] >= rolling_high[index - 2] >= rolling_high[index - 1] >= rolling_high[index]
    if use_trailing_stop and trend_up_history[index] > 0 and rising_floor and signed_up_history[index] > volume_threshold: band[index] = rolling_low[index]
    elif use_trailing_stop and trend_down_history[index] > 0 and falling_ceiling and signed_down_history[index] < -volume_threshold: band[index] = rolling_high[index]

cross_up = np.zeros(n, dtype=bool); cross_down = np.zeros(n, dtype=bool); any_cross = np.zeros(n, dtype=bool); buy_signal = np.zeros(n, dtype=bool); sell_signal = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.all(np.isfinite([close[index], band[index], close[index - 1], band[index - 1]])):
        cross_up[index] = close[index] > band[index] and close[index - 1] <= band[index - 1]; cross_down[index] = close[index] < band[index] and close[index - 1] >= band[index - 1]
    any_cross[index] = cross_up[index] or cross_down[index]
    buy_signal[index] = use_trailing_stop and (cross_up[index] or (close[index] > band[index] and not np.isfinite(band[index - 1]) and np.isfinite(band[index])))
    sell_signal[index] = use_trailing_stop and (cross_down[index] or (close[index] < band[index] and not np.isfinite(band[index - 1]) and np.isfinite(band[index])))

pivot_high_marker = [None] * n; pivot_low_marker = [None] * n
for index in range(2 * pivot_length, n):
    if np.isfinite(pivot_high[index]): pivot_high_marker[index - pivot_length] = float(pivot_high[index])
    if np.isfinite(pivot_low[index]): pivot_low_marker[index - pivot_length] = float(pivot_low[index])
band_data = [None if any_cross[index] or not np.isfinite(band[index]) else float(band[index]) for index in range(n)]
band_colors = ["rgba(31,192,176,0.80)" if np.isfinite(band[index]) and close[index] > band[index] else "rgba(209,28,67,0.80)" for index in range(n)]
layers = []
for start, end, level, color, style in structure_lines: layers.append({"type": "line", "role": "Market Structure", "start": float(times[start]), "end": float(times[end]), "y1": level, "y2": level, "color": color, "lineWidth": 1, "dashed": style == "dashed"})
visible_label_start = max(0, n - 180)
for index, price, text, color, direction in structure_labels:
    if index >= visible_label_start: layers.append({"type": "label", "index": index, "price": price, "text": text, "textColor": color, "color": "rgba(0,0,0,0)", "direction": direction, "size": "small"})
plots = [
    {"name": "Trailing Stop", "data": band_data, "color": band_colors, "type": "line", "style": "linebr", "lineWidth": 1, "overlay": True},
    {"name": "Trailing Stop Glow", "title": False, "data": band_data, "color": [color.replace("0.80", "0.20") for color in band_colors], "type": "line", "style": "linebr", "lineWidth": 5, "overlay": True},
    {"name": "Pivot High", "data": pivot_high_marker, "color": up_color, "type": "scatter", "size": 2, "overlay": True},
    {"name": "Pivot Low", "data": pivot_low_marker, "color": down_color, "type": "scatter", "size": 2, "overlay": True},
]
signals = [
    {"type": "buy", "name": "Trailing Buy", "text": "B", "data": [float(band[index]) if buy_signal[index] else None for index in range(n)], "color": "rgba(0,255,0,0.50)", "renderMode": "events"},
    {"type": "sell", "name": "Trailing Sell", "text": "S", "data": [float(band[index]) if sell_signal[index] else None for index in range(n)], "color": "rgba(223,30,30,0.50)", "renderMode": "events"},
]
bar_colors = [up_color if trend_up_history[index] > 0 else down_color if trend_down_history[index] > 0 else None for index in range(n)] if color_trend else [None] * n
output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": signals, "layers": layers, "tables": [], "bar_colors": bar_colors, "background_colors": [None] * n,
    "calculatedVars": {"pivotHigh": _finite(pivot_high), "pivotLow": _finite(pivot_low), "upperTouch": upper_touch.tolist(), "lowerTouch": lower_touch.tolist(), "countUp": count_up_history.tolist(), "countDown": count_down_history.tolist(), "trendUpCounter": trend_up_history.tolist(), "trendDownCounter": trend_down_history.tolist(), "signedVolumeUp": _finite(signed_up_history), "signedVolumeDown": _finite(signed_down_history), "rollingHigh": _finite(rolling_high), "rollingLow": _finite(rolling_low), "band": _finite(band), "crossUp": cross_up.tolist(), "crossDown": cross_down.tolist(), "buySignal": buy_signal.tolist(), "sellSignal": sell_signal.tolist()}
}
