# ============================================================
# QuantDinger Reversal Master
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Reversal Master"
my_indicator_description = "Combines a relative-volatility extreme cycle, three stretch bands, signal spacing, strength tiers, and ATR-normalized trend-angle candle colors."

# @param show_reversal_signals bool true Show reversal signals
# @param show_ready_alerts bool true Show ready markers
# @param momentum_length int 10 Momentum length range=2:100:1
# @param momentum_smoothing int 14 Momentum smoothing range=1:100:1
# @param extreme_high int 80 Extreme high range=51:99:1
# @param extreme_low int 20 Extreme low range=1:49:1
# @param signal_gap int 5 Minimum same-direction signal gap range=0:200:1
# @param ready_gap int 8 Minimum ready-marker gap range=0:200:1
# @param show_bands bool true Show reversal bands
# @param band_length int 34 Band length range=5:300:1
# @param band_multiplier float 2.0 Band distance range=0.5:10:0.1
# @param color_trend_candles bool true Color candles by trend angle
# @param angle_length int 34 Trend angle length range=5:300:1
# @param up_color str #3DDC97 Up color
# @param down_color str #F2607A Down color
# @param flat_color str #6B7280 Flat color

show_reversal_signals = bool(params.get("show_reversal_signals", True))
show_ready_alerts = bool(params.get("show_ready_alerts", True))
momentum_length = min(100, max(2, int(params.get("momentum_length", 10))))
momentum_smoothing = min(100, max(1, int(params.get("momentum_smoothing", 14))))
extreme_high = min(99, max(51, int(params.get("extreme_high", 80))))
extreme_low = min(49, max(1, int(params.get("extreme_low", 20))))
signal_gap = min(200, max(0, int(params.get("signal_gap", 5))))
ready_gap = min(200, max(0, int(params.get("ready_gap", 8))))
show_bands = bool(params.get("show_bands", True))
band_length = min(300, max(5, int(params.get("band_length", 34))))
band_multiplier = min(10.0, max(0.5, float(params.get("band_multiplier", 2.0))))
color_trend_candles = bool(params.get("color_trend_candles", True))
angle_length = min(300, max(5, int(params.get("angle_length", 34))))
up_color = str(params.get("up_color", "#3DDC97"))
down_color = str(params.get("down_color", "#F2607A"))
flat_color = str(params.get("flat_color", "#6B7280"))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def _rma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    result[length - 1] = float(np.mean(values[:length]))
    for index in range(length, len(values)):
        result[index] = (result[index - 1] * (length - 1.0) + values[index]) / length
    return result


def _deviation(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        result[index] = float(np.std(values[index - length + 1:index + 1], ddof=0))
    return result


def _true_range(high, low, close):
    result = np.empty(len(close), dtype=float)
    for index in range(len(close)):
        result[index] = high[index] - low[index] if index == 0 else max(high[index] - low[index], abs(high[index] - close[index - 1]), abs(low[index] - close[index - 1]))
    return result


def _regression_slope(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    x = np.arange(length, dtype=float)
    x_centered = x - float(np.mean(x))
    denominator = float(np.sum(x_centered * x_centered))
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        result[index] = float(np.sum(x_centered * (window - np.mean(window))) / denominator)
    return result


def _hex_rgb(value):
    text = str(value).lstrip("#")
    if len(text) != 6:
        return 107, 114, 128
    return tuple(int(text[position:position + 2], 16) for position in (0, 2, 4))


def _blend(first, second, amount):
    a = _hex_rgb(first)
    b = _hex_rgb(second)
    ratio = min(1.0, max(0.0, float(amount)))
    values = [round(a[index] + (b[index] - a[index]) * ratio) for index in range(3)]
    return "#" + "".join(f"{value:02X}" for value in values)


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close"]).reset_index(drop=True)
opened = df["open"].to_numpy(dtype=float)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
n = len(df)

standard_deviation = _deviation(close, momentum_length)
change = np.full(n, np.nan, dtype=float)
if n > 1:
    change[1:] = np.diff(close)
up_input = np.where(change > 0.0, standard_deviation, 0.0)
down_input = np.where(change <= 0.0, standard_deviation, 0.0)
up_volatility = _ema(up_input, momentum_smoothing)
down_volatility = _ema(down_input, momentum_smoothing)
rvi = np.full(n, 50.0, dtype=float)
valid_rvi = np.isfinite(up_volatility) & np.isfinite(down_volatility) & (up_volatility + down_volatility > 0.0)
rvi[valid_rvi] = 100.0 * up_volatility[valid_rvi] / (up_volatility[valid_rvi] + down_volatility[valid_rvi])

true_range = _true_range(high, low, close)
typical = (high + low + close) / 3.0
basis = _ema(typical, band_length)
band_deviation = _deviation(close, band_length)
band_atr = _rma(true_range, band_length)
unit = _ema((band_deviation + band_atr) / 2.0, 5)
upper_one = basis + unit * band_multiplier
upper_two = basis + unit * (band_multiplier + 0.75)
upper_three = basis + unit * (band_multiplier + 1.50)
lower_one = basis - unit * band_multiplier
lower_two = basis - unit * (band_multiplier + 0.75)
lower_three = basis - unit * (band_multiplier + 1.50)

slope = _regression_slope(close, angle_length)
angle_atr = _rma(true_range, angle_length)
steep_input = np.zeros(n, dtype=float)
valid_steep = np.isfinite(slope) & np.isfinite(angle_atr) & (angle_atr > 0.0)
steep_input[valid_steep] = slope[valid_steep] / angle_atr[valid_steep] * angle_length
steep = _ema(steep_input, 5)
degrees = np.degrees(np.arctan(steep))
bar_colors = []
for index, degree in enumerate(degrees):
    angle_color = _blend(flat_color, up_color, degree / 60.0) if degree >= 0.0 else _blend(down_color, flat_color, (degree + 60.0) / 60.0)
    bar_colors.append(angle_color if close[index] >= opened[index] else _blend(angle_color, "#0F1117", 0.45))
if not color_trend_candles:
    bar_colors = [None] * n


def _cross_above(values, level):
    return np.asarray([index > 0 and values[index] > level and values[index - 1] <= level for index in range(n)], dtype=bool)


def _cross_below(values, level):
    return np.asarray([index > 0 and values[index] < level and values[index - 1] >= level for index in range(n)], dtype=bool)


enter_high = _cross_above(rvi, extreme_high)
leave_high = _cross_below(rvi, extreme_high)
enter_low = _cross_below(rvi, extreme_low)
leave_low = _cross_above(rvi, extreme_low)
atr30 = _rma(true_range, 30)
ready_down_state = np.zeros(n, dtype=bool)
ready_up_state = np.zeros(n, dtype=bool)
down_tier = np.zeros(n, dtype=int)
up_tier = np.zeros(n, dtype=int)
ready_down_event = np.zeros(n, dtype=bool)
ready_up_event = np.zeros(n, dtype=bool)
ready_down_visible = np.zeros(n, dtype=bool)
ready_up_visible = np.zeros(n, dtype=bool)
sell_signal = np.zeros(n, dtype=bool)
buy_signal = np.zeros(n, dtype=bool)
sell_strong = np.zeros(n, dtype=bool)
buy_strong = np.zeros(n, dtype=bool)
last_down_bar = -100000
last_up_bar = -100000
last_ready_bar = -100000
last_signal_bar = -100000
ready_down = False
ready_up = False
tier_down = 0
tier_up = 0


def _upper_tier(index):
    if not np.all(np.isfinite([upper_one[index], upper_two[index], upper_three[index]])):
        return 0
    return 3 if high[index] > upper_three[index] else 2 if high[index] > upper_two[index] else 1 if high[index] > upper_one[index] else 0


def _lower_tier(index):
    if not np.all(np.isfinite([lower_one[index], lower_two[index], lower_three[index]])):
        return 0
    return 3 if low[index] < lower_three[index] else 2 if low[index] < lower_two[index] else 1 if low[index] < lower_one[index] else 0


for index in range(n):
    if np.isfinite(atr30[index]):
        if ready_down:
            tier_down = max(tier_down, _upper_tier(index))
        if ready_up:
            tier_up = max(tier_up, _lower_tier(index))
        if enter_high[index]:
            ready_down = True
            tier_down = _upper_tier(index)
            ready_down_event[index] = True
            if show_ready_alerts and index - last_ready_bar >= ready_gap:
                ready_down_visible[index] = True
                last_ready_bar = index
        if enter_low[index]:
            ready_up = True
            tier_up = _lower_tier(index)
            ready_up_event[index] = True
            if show_ready_alerts and index - last_ready_bar >= ready_gap:
                ready_up_visible[index] = True
                last_ready_bar = index
        if leave_high[index] and ready_down:
            if show_reversal_signals and index - last_down_bar >= signal_gap:
                sell_signal[index] = True
                sell_strong[index] = tier_down >= 2
                last_down_bar = index
                last_signal_bar = index
            ready_down = False
        if leave_low[index] and ready_up:
            if show_reversal_signals and index - last_up_bar >= signal_gap:
                buy_signal[index] = True
                buy_strong[index] = tier_up >= 2
                last_up_bar = index
                last_signal_bar = index
            ready_up = False
    ready_down_state[index] = ready_down
    ready_up_state[index] = ready_up
    down_tier[index] = tier_down
    up_tier[index] = tier_up

upper_zone_one = [{"upper": float(upper_two[index]), "lower": float(upper_one[index]), "color": down_color, "opacity": 0.07} if show_bands and np.all(np.isfinite([upper_one[index], upper_two[index]])) else None for index in range(n)]
upper_zone_two = [{"upper": float(upper_three[index]), "lower": float(upper_two[index]), "color": down_color, "opacity": 0.14} if show_bands and np.all(np.isfinite([upper_two[index], upper_three[index]])) else None for index in range(n)]
lower_zone_one = [{"upper": float(lower_one[index]), "lower": float(lower_two[index]), "color": up_color, "opacity": 0.07} if show_bands and np.all(np.isfinite([lower_one[index], lower_two[index]])) else None for index in range(n)]
lower_zone_two = [{"upper": float(lower_two[index]), "lower": float(lower_three[index]), "color": up_color, "opacity": 0.14} if show_bands and np.all(np.isfinite([lower_two[index], lower_three[index]])) else None for index in range(n)]


def _events(mask, prices, texts, color, event_type):
    return {"type": event_type, "name": texts if isinstance(texts, str) else "Reversal", "data": [{"active": True, "price": float(prices[index]), "text": texts if isinstance(texts, str) else texts[index], "color": color} if active and np.isfinite(prices[index]) else None for index, active in enumerate(mask)], "color": color, "renderMode": "events"}


buy_text = ["▲ Strong" if value else "▲" for value in buy_strong]
sell_text = ["▼ Strong" if value else "▼" for value in sell_strong]
output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Upper 1", "title": show_bands, "data": _finite(upper_one) if show_bands else [None] * n, "color": down_color, "type": "line", "overlay": True},
        {"name": "Upper 2", "title": show_bands, "data": _finite(upper_two) if show_bands else [None] * n, "color": down_color, "type": "line", "overlay": True},
        {"name": "Upper 3", "title": show_bands, "data": _finite(upper_three) if show_bands else [None] * n, "color": down_color, "type": "line", "overlay": True},
        {"name": "Lower 1", "title": show_bands, "data": _finite(lower_one) if show_bands else [None] * n, "color": up_color, "type": "line", "overlay": True},
        {"name": "Lower 2", "title": show_bands, "data": _finite(lower_two) if show_bands else [None] * n, "color": up_color, "type": "line", "overlay": True},
        {"name": "Lower 3", "title": show_bands, "data": _finite(lower_three) if show_bands else [None] * n, "color": up_color, "type": "line", "overlay": True},
        {"name": "Upper Zone 1", "title": False, "data": upper_zone_one, "color": down_color, "type": "cloud", "overlay": True},
        {"name": "Upper Zone 2", "title": False, "data": upper_zone_two, "color": down_color, "type": "cloud", "overlay": True},
        {"name": "Lower Zone 1", "title": False, "data": lower_zone_one, "color": up_color, "type": "cloud", "overlay": True},
        {"name": "Lower Zone 2", "title": False, "data": lower_zone_two, "color": up_color, "type": "cloud", "overlay": True},
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "overlay": True},
    ],
    "signals": [
        _events(buy_signal, low - atr30 * 0.8, buy_text, up_color, "buy"), _events(sell_signal, high + atr30 * 0.8, sell_text, down_color, "sell"),
        _events(ready_up_visible, low - atr30 * 0.15, "Ready ▲", up_color, "buy"), _events(ready_down_visible, high + atr30 * 0.15, "Ready ▼", down_color, "sell"),
    ],
    "layers": [], "tables": [], "bar_colors": bar_colors, "background_colors": [None] * n,
    "calculatedVars": {
        "standardDeviation": _finite(standard_deviation), "change": _finite(change), "upVolatility": _finite(up_volatility), "downVolatility": _finite(down_volatility), "rvi": _finite(rvi),
        "trueRange": _finite(true_range), "basis": _finite(basis), "bandDeviation": _finite(band_deviation), "bandAtr": _finite(band_atr), "unit": _finite(unit),
        "upperOne": _finite(upper_one), "upperTwo": _finite(upper_two), "upperThree": _finite(upper_three), "lowerOne": _finite(lower_one), "lowerTwo": _finite(lower_two), "lowerThree": _finite(lower_three),
        "slope": _finite(slope), "angleAtr": _finite(angle_atr), "steep": _finite(steep), "degrees": _finite(degrees),
        "enterHigh": enter_high.tolist(), "leaveHigh": leave_high.tolist(), "enterLow": enter_low.tolist(), "leaveLow": leave_low.tolist(), "atr30": _finite(atr30),
        "readyDownState": ready_down_state.tolist(), "readyUpState": ready_up_state.tolist(), "downTier": [int(value) for value in down_tier], "upTier": [int(value) for value in up_tier],
        "readyDownEvent": ready_down_event.tolist(), "readyUpEvent": ready_up_event.tolist(), "readyDownVisible": ready_down_visible.tolist(), "readyUpVisible": ready_up_visible.tolist(),
        "sellSignal": sell_signal.tolist(), "buySignal": buy_signal.tolist(), "sellStrong": sell_strong.tolist(), "buyStrong": buy_strong.tolist(),
    },
}
