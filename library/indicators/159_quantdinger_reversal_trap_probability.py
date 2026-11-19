# ============================================================
# QuantDinger Reversal Trap Probability Bands
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; tracked outcomes do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Reversal Trap Probability Bands"
my_indicator_description = (
    "Detects failed excursions beyond an EMA/ATR envelope, tracks target-versus-stop outcomes "
    "by RSI bucket, and displays entry-time win probability with projected trade paths."
)

# @param envelope_length int 55 Envelope smoothness range=1:300:1
# @param envelope_width float 4.0 Envelope width range=0.1:20.0:0.1
# @param trap_window int 10 Trap window bars range=1:100:1
# @param signal_gap int 10 Minimum bars between signals range=1:200:1
# @param stop_multiplier float 0.5 ATR stop multiplier range=0.1:10.0:0.1
# @param max_lookback int 5000 Maximum lookback bars range=1:20000:1
# @param max_trades int 500 Maximum tracked trades range=1:5000:1
# @param label_size str Small Label size values=Auto,Tiny,Small,Normal,Large,Huge
# @param bullish_color str #12AF9F Bullish color
# @param bearish_color str #EC3333 Bearish color
# @param midpoint_color str rgba(120,123,134,0.50) Midpoint color

envelope_length = min(300, max(1, int(params.get("envelope_length", 55))))
envelope_width = min(20.0, max(0.1, float(params.get("envelope_width", 4.0))))
trap_window = min(100, max(1, int(params.get("trap_window", 10))))
signal_gap = min(200, max(1, int(params.get("signal_gap", 10))))
stop_multiplier = min(10.0, max(0.1, float(params.get("stop_multiplier", 0.5))))
max_lookback = min(20000, max(1, int(params.get("max_lookback", 5000))))
max_trades = min(5000, max(1, int(params.get("max_trades", 500))))
label_size = str(params.get("label_size", "Small"))
bullish_color = str(params.get("bullish_color", "#12AF9F"))
bearish_color = str(params.get("bearish_color", "#EC3333"))
midpoint_color = str(params.get("midpoint_color", "rgba(120,123,134,0.50)"))

font_sizes = {"Auto": 10, "Tiny": 8, "Small": 10, "Normal": 12, "Large": 14, "Huge": 18}
font_size = font_sizes.get(label_size, 10)

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

def pine_ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    result[length - 1] = float(np.mean(values[:length]))
    alpha = 2.0 / (length + 1.0)
    for index in range(length, len(values)):
        result[index] = alpha * values[index] + (1.0 - alpha) * result[index - 1]
    return result

def wilder(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    result[length - 1] = float(np.mean(values[:length]))
    for index in range(length, len(values)):
        result[index] = (result[index - 1] * (length - 1) + values[index]) / length
    return result

def rgba(color, opacity):
    value = str(color).strip()
    if value.startswith("#") and len(value) in (4, 7):
        if len(value) == 4:
            red = int(value[1] * 2, 16)
            green = int(value[2] * 2, 16)
            blue = int(value[3] * 2, 16)
        else:
            red = int(value[1:3], 16)
            green = int(value[3:5], 16)
            blue = int(value[5:7], 16)
        return f"rgba({red},{green},{blue},{min(1.0, max(0.0, opacity)):.3f})"
    return value

basis = pine_ema(close, envelope_length)
true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
envelope_atr = wilder(true_range, envelope_length)
atr_100 = wilder(true_range, 100) * stop_multiplier
upper_band = basis + envelope_width * envelope_atr
lower_band = basis - envelope_width * envelope_atr

changes = np.full(n, np.nan, dtype=float)
for index in range(1, n):
    changes[index] = close[index] - close[index - 1]
gains = np.where(np.isfinite(changes), np.maximum(changes, 0.0), 0.0)
losses = np.where(np.isfinite(changes), np.maximum(-changes, 0.0), 0.0)
average_gain = wilder(gains, 20)
average_loss = wilder(losses, 20)
rsi = np.full(n, np.nan, dtype=float)
for index in range(n):
    if np.isfinite(average_gain[index]) and np.isfinite(average_loss[index]):
        if average_loss[index] == 0.0:
            rsi[index] = 100.0 if average_gain[index] > 0.0 else 50.0
        else:
            rs = average_gain[index] / average_loss[index]
            rsi[index] = 100.0 - 100.0 / (1.0 + rs)

bull_total = np.zeros(11, dtype=int)
bull_wins = np.zeros(11, dtype=int)
bear_total = np.zeros(11, dtype=int)
bear_wins = np.zeros(11, dtype=int)
total_trades = 0
above_count = 0
below_count = 0
last_signal = -1000
active_bull = None
active_bear = None
layers = []

def add_line(start, end, y1, y2, color, dashed=True):
    layers.append(
        {
            "type": "line",
            "startIndex": int(start),
            "endIndex": int(end),
            "y1": float(y1),
            "y2": float(y2),
            "color": color,
            "lineWidth": 1,
            "dashed": bool(dashed),
        }
    )

for index in range(n):
    previous_above = above_count
    previous_below = below_count
    raw_bear = False
    raw_bull = False
    if (
        index > 0
        and np.isfinite(upper_band[index])
        and np.isfinite(upper_band[index - 1])
        and close[index] < upper_band[index]
    ):
        raw_bear = (
            (high[index] > upper_band[index] and close[index - 1] < upper_band[index - 1] and previous_above <= trap_window)
            or (close[index - 1] > upper_band[index - 1] and previous_above <= trap_window)
        )
    if (
        index > 0
        and np.isfinite(lower_band[index])
        and np.isfinite(lower_band[index - 1])
        and close[index] > lower_band[index]
    ):
        raw_bull = (
            (low[index] < lower_band[index] and close[index - 1] > lower_band[index - 1] and previous_below <= trap_window)
            or (close[index - 1] < lower_band[index - 1] and previous_below <= trap_window)
        )

    above_count = previous_above + 1 if np.isfinite(upper_band[index]) and high[index] > upper_band[index] else 0
    below_count = previous_below + 1 if np.isfinite(lower_band[index]) and low[index] < lower_band[index] else 0
    can_fire = index - last_signal >= signal_gap
    bull_trap = raw_bull and can_fire
    bear_trap = raw_bear and can_fire
    if bull_trap or bear_trap:
        last_signal = index

    allowed = (n - 1 - index) <= max_lookback and total_trades < max_trades
    bucket = int(max(0, min(10, np.floor((rsi[index] if np.isfinite(rsi[index]) else 0.0) / 10.0 + 0.5))))

    if bull_trap and active_bull is None and allowed and np.isfinite(basis[index]) and np.isfinite(atr_100[index]):
        target = float(basis[index])
        stop = float(min(low[max(0, index - 1):index + 1]) - atr_100[index])
        attempts = int(bull_total[bucket])
        rate = bull_wins[bucket] / attempts * 100.0 if attempts > 0 else None
        text = "Tracking..." if rate is None else f"{rate:.0f}% WIN PROB"
        active_bull = {"start": index, "target": target, "stop": stop, "bucket": bucket, "low": float(low[index])}
        total_trades += 1
        layers.append(
            {
                "type": "label",
                "index": index,
                "price": float(low[index] - atr_100[index]),
                "text": f"⬆\nTRAP\n{text}\n(RSI: {rsi[index]:.0f})",
                "color": "rgba(0,0,0,0)",
                "textColor": bullish_color,
                "fontSize": font_size,
                "side": "up",
            }
        )

    if bear_trap and active_bear is None and allowed and np.isfinite(basis[index]) and np.isfinite(atr_100[index]):
        target = float(basis[index])
        stop = float(max(high[max(0, index - 1):index + 1]) + atr_100[index])
        attempts = int(bear_total[bucket])
        rate = bear_wins[bucket] / attempts * 100.0 if attempts > 0 else None
        text = "Tracking..." if rate is None else f"{rate:.0f}% WIN PROB"
        active_bear = {"start": index, "target": target, "stop": stop, "bucket": bucket, "high": float(high[index])}
        total_trades += 1
        layers.append(
            {
                "type": "label",
                "index": index,
                "price": float(high[index] + atr_100[index]),
                "text": f"{text}\n(RSI: {rsi[index]:.0f})\nTRAP\n⬇",
                "color": "rgba(0,0,0,0)",
                "textColor": bearish_color,
                "fontSize": font_size,
                "side": "down",
            }
        )

    if active_bull is not None and index > active_bull["start"]:
        won = high[index] >= active_bull["target"]
        lost = low[index] < active_bull["stop"]
        if won or lost:
            end = index
            add_line(active_bull["start"], end, active_bull["target"], active_bull["target"], bullish_color)
            add_line(active_bull["start"], end, active_bull["stop"], active_bull["stop"], bullish_color)
            add_line(active_bull["start"], end, active_bull["low"], active_bull["target"], bullish_color, False)
            bull_total[active_bull["bucket"]] += 1
            if won:
                bull_wins[active_bull["bucket"]] += 1
                layers.append({"type": "label", "index": end, "price": float(high[index]), "text": "✓", "color": "rgba(0,0,0,0)", "textColor": bullish_color, "fontSize": font_size, "side": "down"})
            active_bull = None

    if active_bear is not None and index > active_bear["start"]:
        won = low[index] <= active_bear["target"]
        lost = high[index] > active_bear["stop"]
        if won or lost:
            end = index
            add_line(active_bear["start"], end, active_bear["target"], active_bear["target"], bearish_color)
            add_line(active_bear["start"], end, active_bear["stop"], active_bear["stop"], bearish_color)
            add_line(active_bear["start"], end, active_bear["high"], active_bear["target"], bearish_color, False)
            bear_total[active_bear["bucket"]] += 1
            if won:
                bear_wins[active_bear["bucket"]] += 1
                layers.append({"type": "label", "index": end, "price": float(low[index]), "text": "✓", "color": "rgba(0,0,0,0)", "textColor": bearish_color, "fontSize": font_size, "side": "up"})
            active_bear = None

if active_bull is not None:
    end = max(0, n - 1)
    add_line(active_bull["start"], end, active_bull["target"], active_bull["target"], bullish_color)
    add_line(active_bull["start"], end, active_bull["stop"], active_bull["stop"], bullish_color)
    add_line(active_bull["start"], end, active_bull["low"], active_bull["target"], bullish_color, False)
if active_bear is not None:
    end = max(0, n - 1)
    add_line(active_bear["start"], end, active_bear["target"], active_bear["target"], bearish_color)
    add_line(active_bear["start"], end, active_bear["stop"], active_bear["stop"], bearish_color)
    add_line(active_bear["start"], end, active_bear["high"], active_bear["target"], bearish_color, False)

def clean(values):
    return [float(value) if np.isfinite(value) else None for value in values]

plots = [
    {"name": "Envelope Ceiling", "data": clean(upper_band), "color": rgba(bearish_color, 0.50), "type": "line", "lineWidth": 3, "overlay": True},
    {"name": "Envelope Floor", "data": clean(lower_band), "color": rgba(bullish_color, 0.50), "type": "line", "lineWidth": 3, "overlay": True},
    {"name": "Envelope Baseline", "data": clean(basis), "color": midpoint_color, "type": "line", "lineWidth": 1, "dashed": True, "overlay": True},
]
for band_index in range(5):
    fraction = (band_index + 1.0) / 6.0
    upper_level = basis + (upper_band - basis) * fraction
    lower_level = basis - (basis - lower_band) * fraction
    opacity = 0.10 + 0.05 * band_index
    plots.append({"name": f"Upper Gradient {band_index + 1}", "title": False, "data": clean(upper_level), "color": rgba(bearish_color, opacity), "type": "line", "lineWidth": 1, "overlay": True})
    plots.append({"name": f"Lower Gradient {band_index + 1}", "title": False, "data": clean(lower_level), "color": rgba(bullish_color, opacity), "type": "line", "lineWidth": 1, "overlay": True})

output = {"name": my_indicator_name, "plots": plots, "signals": [], "layers": layers[-500:]}
