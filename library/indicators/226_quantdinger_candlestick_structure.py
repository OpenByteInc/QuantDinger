# ============================================================
# QuantDinger Candlestick Structure
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Candlestick Structure"
my_indicator_description = (
    "Detects sixteen bullish and bearish candlestick structures, measures their "
    "minor-trend alignment, and filters displayed patterns with a selectable major trend."
)

# @param hammer bool true Hammer
# @param inverted_hammer bool true Inverted hammer
# @param bullish_engulfing bool true Bullish engulfing
# @param rising_three bool true Rising three
# @param three_white_soldiers bool true Three white soldiers
# @param morning_star bool true Morning star
# @param bullish_harami bool true Bullish harami
# @param tweezer_bottom bool true Tweezer bottom
# @param hanging_man bool true Hanging man
# @param shooting_star bool true Shooting star
# @param bearish_engulfing bool true Bearish engulfing
# @param falling_three bool true Falling three
# @param three_black_crows bool true Three black crows
# @param evening_star bool true Evening star
# @param bearish_harami bool true Bearish harami
# @param tweezer_top bool true Tweezer top
# @param minor_trend_length int 10 Minor Donchian trend length range=1:500:1
# @param major_trend_method str SuperTrend Major trend method values=EMA,ChoCh,SuperTrend,Donchian_Channel
# @param major_swing_length int 20 ChoCh swing length range=2:500:1
# @param major_ema_short int 50 Short EMA length range=1:500:1
# @param major_ema_long int 200 Long EMA length range=1:1000:1
# @param supertrend_factor float 4.0 SuperTrend factor range=0.1:20.0:0.1
# @param supertrend_length int 20 SuperTrend length range=1:500:1
# @param major_donchian_length int 40 Major Donchian length range=1:500:1
# @param color_candles bool false Color candles by minor trend
# @param bullish_color str #089981 Bull color
# @param bearish_color str #F23645 Bear color
# @param show_fill bool true Show major trend fill
# @param label_size str tiny Label size values=tiny,small,normal,large
# @param show_dashboard bool true Show pattern dashboard
# @param dashboard_location str bottom-right Dashboard location values=top-right,bottom-right,bottom-left
# @param max_labels int 8 Recent pattern labels range=1:100:1
# @param label_spacing int 12 Minimum bars between pattern labels range=0:50:1
# @param label_lookback int 150 Label display lookback bars range=20:500:10

pattern_enabled = {
    "hammer": bool(params.get("hammer", True)),
    "invertedHammer": bool(params.get("inverted_hammer", True)),
    "bullishEngulfing": bool(params.get("bullish_engulfing", True)),
    "risingThree": bool(params.get("rising_three", True)),
    "threeWhiteSoldiers": bool(params.get("three_white_soldiers", True)),
    "morningStar": bool(params.get("morning_star", True)),
    "bullishHarami": bool(params.get("bullish_harami", True)),
    "tweezerBottom": bool(params.get("tweezer_bottom", True)),
    "hangingMan": bool(params.get("hanging_man", True)),
    "shootingStar": bool(params.get("shooting_star", True)),
    "bearishEngulfing": bool(params.get("bearish_engulfing", True)),
    "fallingThree": bool(params.get("falling_three", True)),
    "threeBlackCrows": bool(params.get("three_black_crows", True)),
    "eveningStar": bool(params.get("evening_star", True)),
    "bearishHarami": bool(params.get("bearish_harami", True)),
    "tweezerTop": bool(params.get("tweezer_top", True)),
}
minor_length = min(500, max(1, int(params.get("minor_trend_length", 10))))
major_method = str(params.get("major_trend_method", "SuperTrend"))
major_swing_length = min(500, max(2, int(params.get("major_swing_length", 20))))
major_ema_short = min(500, max(1, int(params.get("major_ema_short", 50))))
major_ema_long = min(1000, max(1, int(params.get("major_ema_long", 200))))
supertrend_factor = min(20.0, max(0.1, float(params.get("supertrend_factor", 4.0))))
supertrend_length = min(500, max(1, int(params.get("supertrend_length", 20))))
major_donchian_length = min(500, max(1, int(params.get("major_donchian_length", 40))))
color_candles = bool(params.get("color_candles", False))
bullish_color = str(params.get("bullish_color", "#089981"))
bearish_color = str(params.get("bearish_color", "#F23645"))
show_fill = bool(params.get("show_fill", True))
label_size = str(params.get("label_size", "tiny")).lower()
show_dashboard = bool(params.get("show_dashboard", True))
dashboard_location = str(params.get("dashboard_location", "bottom-right")).lower()
max_labels = min(100, max(1, int(params.get("max_labels", 8))))
label_spacing = min(50, max(0, int(params.get("label_spacing", 12))))
label_lookback = min(500, max(20, int(params.get("label_lookback", 150))))

frame = df.copy()
frame = frame.reset_index(drop=True)
n = len(frame)
if "time" not in frame.columns:
    frame["time"] = np.arange(n, dtype=float) * 3600.0
time_values = pd.to_numeric(frame["time"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
open_values = pd.to_numeric(frame["open"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
high_values = pd.to_numeric(frame["high"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
low_values = pd.to_numeric(frame["low"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
close_values = pd.to_numeric(frame["close"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)


def ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if not len(values):
        return result
    alpha = 2.0 / (float(length) + 1.0)
    result[0] = float(values[0])
    for index in range(1, len(values)):
        result[index] = alpha * float(values[index]) + (1.0 - alpha) * result[index - 1]
    return result


def wilder(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) < length:
        return result
    result[length - 1] = float(np.mean(values[:length]))
    for index in range(length, len(values)):
        result[index] = (result[index - 1] * (length - 1) + float(values[index])) / float(length)
    return result


def donchian_direction(length):
    lower = np.full(n, np.nan, dtype=float)
    upper = np.full(n, np.nan, dtype=float)
    direction = np.zeros(n, dtype=int)
    state = 0
    for index in range(n):
        start = max(0, index - length + 1)
        upper[index] = float(np.max(high_values[start:index + 1]))
        lower[index] = float(np.min(low_values[start:index + 1]))
        if high_values[index] == upper[index] and state != 1:
            state = 1
        if low_values[index] == lower[index] and state != -1:
            state = -1
        direction[index] = state
    return lower, upper, direction


def supertrend_state(factor, length):
    true_range = np.zeros(n, dtype=float)
    for index in range(n):
        if index == 0:
            true_range[index] = high_values[index] - low_values[index]
        else:
            true_range[index] = max(
                high_values[index] - low_values[index],
                abs(high_values[index] - close_values[index - 1]),
                abs(low_values[index] - close_values[index - 1]),
            )
    atr = wilder(true_range, length)
    midpoint = (high_values + low_values) * 0.5
    upper = np.full(n, np.nan, dtype=float)
    lower = np.full(n, np.nan, dtype=float)
    trend = np.full(n, np.nan, dtype=float)
    pine_direction = np.ones(n, dtype=int)
    for index in range(n):
        if not np.isfinite(atr[index]):
            continue
        raw_upper = midpoint[index] + factor * atr[index]
        raw_lower = midpoint[index] - factor * atr[index]
        if index == 0 or not np.isfinite(upper[index - 1]):
            upper[index] = raw_upper
            lower[index] = raw_lower
            pine_direction[index] = 1
        else:
            upper[index] = raw_upper if raw_upper < upper[index - 1] or close_values[index - 1] > upper[index - 1] else upper[index - 1]
            lower[index] = raw_lower if raw_lower > lower[index - 1] or close_values[index - 1] < lower[index - 1] else lower[index - 1]
            if np.isfinite(trend[index - 1]) and trend[index - 1] == upper[index - 1]:
                pine_direction[index] = -1 if close_values[index] > upper[index] else 1
            else:
                pine_direction[index] = 1 if close_values[index] < lower[index] else -1
        trend[index] = lower[index] if pine_direction[index] == -1 else upper[index]
    return trend, trend.copy(), -pine_direction


def choch_state(length):
    direction = np.zeros(n, dtype=int)
    top_level = np.full(n, np.nan, dtype=float)
    bottom_level = np.full(n, np.nan, dtype=float)
    bull_break = np.zeros(n, dtype=bool)
    bear_break = np.zeros(n, dtype=bool)
    oscillator = 0
    active_top = np.nan
    active_bottom = np.nan
    top_armed = False
    bottom_armed = False
    state = 0
    for index in range(n):
        previous_oscillator = oscillator
        if index >= length:
            start = max(0, index - length + 1)
            upper = float(np.max(high_values[start:index + 1]))
            lower = float(np.min(low_values[start:index + 1]))
            if high_values[index - length] > upper:
                oscillator = 0
            elif low_values[index - length] < lower:
                oscillator = 1
            if oscillator == 0 and previous_oscillator != 0:
                active_top = float(high_values[index - length])
                top_armed = True
            if oscillator == 1 and previous_oscillator != 1:
                active_bottom = float(low_values[index - length])
                bottom_armed = True
        if index > 0 and top_armed and np.isfinite(active_top) and close_values[index] > active_top and close_values[index - 1] <= active_top and state != 1:
            bull_break[index] = True
            top_armed = False
            state = 1
        if index > 0 and bottom_armed and np.isfinite(active_bottom) and close_values[index] < active_bottom and close_values[index - 1] >= active_bottom and state != -1:
            bear_break[index] = True
            bottom_armed = False
            state = -1
        direction[index] = state
        top_level[index] = active_top
        bottom_level[index] = active_bottom
    return direction, top_level, bottom_level, bull_break, bear_break


minor_lower, minor_upper, minor_direction = donchian_direction(minor_length)
major_choch_top = np.full(n, np.nan, dtype=float)
major_choch_bottom = np.full(n, np.nan, dtype=float)
major_choch_bull = np.zeros(n, dtype=bool)
major_choch_bear = np.zeros(n, dtype=bool)

if major_method == "EMA":
    major_first = ema(close_values, major_ema_short)
    major_second = ema(close_values, major_ema_long)
    major_direction = np.zeros(n, dtype=int)
    state = 0
    for index in range(n):
        if major_first[index] > major_second[index]:
            state = 1
        elif major_first[index] < major_second[index]:
            state = -1
        major_direction[index] = state
elif major_method == "ChoCh":
    major_direction, major_choch_top, major_choch_bottom, major_choch_bull, major_choch_bear = choch_state(major_swing_length)
    major_first = np.full(n, np.nan, dtype=float)
    major_second = np.full(n, np.nan, dtype=float)
elif major_method == "Donchian_Channel":
    major_first, major_second, major_direction = donchian_direction(major_donchian_length)
else:
    major_method = "SuperTrend"
    major_first, major_second, major_direction = supertrend_state(supertrend_factor, supertrend_length)

minor_bull = minor_direction > 0
minor_bear = minor_direction < 0
major_bull = major_direction > 0
major_bear = major_direction < 0
green_candle = close_values > open_values
red_candle = close_values < open_values
candle_top = np.maximum(open_values, close_values)
candle_bottom = np.minimum(open_values, close_values)
candle_range = high_values - low_values
body_width = candle_top - candle_bottom
upper_wick_percent = np.full(n, np.nan, dtype=float)
lower_wick_percent = np.full(n, np.nan, dtype=float)
body_percent = np.full(n, np.nan, dtype=float)
valid_range = candle_range > 0.0
upper_wick_percent[valid_range] = (high_values[valid_range] - candle_top[valid_range]) / candle_range[valid_range] * 100.0
lower_wick_percent[valid_range] = (candle_bottom[valid_range] - low_values[valid_range]) / candle_range[valid_range] * 100.0
body_percent[valid_range] = body_width[valid_range] / candle_range[valid_range] * 100.0
doji = np.isclose(close_values, open_values, rtol=0.0, atol=1e-12)
midpoint = (high_values + low_values) * 0.5

raw_patterns = {name: np.zeros(n, dtype=bool) for name in pattern_enabled}
minor_filters = {name: np.zeros(n, dtype=bool) for name in pattern_enabled}

for index in range(n):
    raw_patterns["hammer"][index] = bool(lower_wick_percent[index] > body_percent[index] * 2.0 and body_percent[index] < 50.0 and upper_wick_percent[index] < 2.0 and not doji[index])
    raw_patterns["invertedHammer"][index] = bool(upper_wick_percent[index] > body_percent[index] * 2.0 and body_percent[index] < 50.0 and lower_wick_percent[index] < 2.0 and not doji[index])
    raw_patterns["shootingStar"][index] = raw_patterns["invertedHammer"][index]
    raw_patterns["hangingMan"][index] = raw_patterns["hammer"][index]
    minor_filters["hammer"][index] = minor_bear[index]
    minor_filters["invertedHammer"][index] = minor_bear[index]
    minor_filters["shootingStar"][index] = minor_bull[index]
    minor_filters["hangingMan"][index] = minor_bull[index]

    if index >= 1:
        raw_patterns["bullishEngulfing"][index] = bool(
            red_candle[index - 1] and green_candle[index]
            and body_width[index] > body_width[index - 1] / 2.0
            and open_values[index] < close_values[index - 1]
            and candle_top[index] > candle_top[index - 1]
            and not doji[index - 1]
        )
        raw_patterns["bearishEngulfing"][index] = bool(
            green_candle[index - 1] and red_candle[index]
            and body_width[index] > body_width[index - 1] / 2.0
            and open_values[index] > close_values[index - 1]
            and candle_bottom[index] < candle_bottom[index - 1]
            and not doji[index - 1]
        )
        raw_patterns["bullishHarami"][index] = bool(green_candle[index] and high_values[index] <= candle_top[index - 1] and low_values[index] >= candle_bottom[index - 1] and red_candle[index - 1])
        raw_patterns["bearishHarami"][index] = bool(red_candle[index] and high_values[index] <= candle_top[index - 1] and low_values[index] >= candle_bottom[index - 1] and green_candle[index - 1])
        raw_patterns["tweezerBottom"][index] = bool(np.isclose(low_values[index], low_values[index - 1], rtol=0.0, atol=1e-12) and green_candle[index] and red_candle[index - 1])
        raw_patterns["tweezerTop"][index] = bool(np.isclose(high_values[index], high_values[index - 1], rtol=0.0, atol=1e-12) and red_candle[index] and green_candle[index - 1])
        minor_filters["bullishEngulfing"][index] = minor_bear[index]
        minor_filters["bearishEngulfing"][index] = minor_bull[index]
        minor_filters["bullishHarami"][index] = minor_bear[index - 1]
        minor_filters["bearishHarami"][index] = minor_bull[index - 1]
        minor_filters["tweezerBottom"][index] = minor_bear[index - 1]
        minor_filters["tweezerTop"][index] = minor_bull[index - 1]

    if index >= 2:
        raw_patterns["threeWhiteSoldiers"][index] = bool(
            green_candle[index - 2] and body_percent[index - 2] > 70.0
            and green_candle[index - 1] and body_percent[index - 1] > 70.0
            and candle_bottom[index - 1] >= candle_bottom[index - 2]
            and candle_bottom[index - 1] <= candle_top[index - 2]
            and close_values[index - 1] > high_values[index - 2]
            and green_candle[index] and body_percent[index] > 70.0
            and candle_bottom[index] >= candle_bottom[index - 1]
            and candle_bottom[index] <= candle_top[index - 1]
            and close_values[index] > high_values[index - 1]
        )
        raw_patterns["threeBlackCrows"][index] = bool(
            red_candle[index - 2] and body_percent[index - 2] > 70.0
            and red_candle[index - 1] and body_percent[index - 1] > 70.0
            and candle_top[index - 1] <= candle_top[index - 2]
            and candle_top[index - 1] >= candle_bottom[index - 2]
            and close_values[index - 1] < low_values[index - 2]
            and red_candle[index] and body_percent[index] > 70.0
            and candle_top[index] <= candle_top[index - 1]
            and candle_top[index] >= candle_bottom[index - 1]
            and close_values[index] < low_values[index - 1]
        )
        raw_patterns["morningStar"][index] = bool(
            red_candle[index - 2] and body_percent[index - 2] > 80.0
            and red_candle[index - 1] and body_width[index - 1] < body_width[index - 2] / 2.0
            and open_values[index - 1] < close_values[index - 2]
            and green_candle[index] and close_values[index] > midpoint[index - 2]
        )
        raw_patterns["eveningStar"][index] = bool(
            green_candle[index - 2] and body_percent[index - 2] > 80.0
            and green_candle[index - 1] and body_width[index - 1] < body_width[index - 2] / 2.0
            and open_values[index - 1] > close_values[index - 2]
            and red_candle[index] and close_values[index] < midpoint[index - 2]
        )
        minor_filters["threeWhiteSoldiers"][index] = minor_bear[index - 2]
        minor_filters["threeBlackCrows"][index] = minor_bull[index - 2]
        minor_filters["morningStar"][index] = minor_bear[index - 2]
        minor_filters["eveningStar"][index] = minor_bull[index - 2]

    if index >= 4:
        raw_patterns["risingThree"][index] = bool(
            green_candle[index - 4] and body_percent[index - 4] > 50.0
            and all(red_candle[index - offset] and candle_top[index - offset] <= high_values[index - 4] and candle_bottom[index - offset] >= low_values[index - 4] for offset in (3, 2, 1))
            and green_candle[index] and close_values[index] > high_values[index - 4] and body_percent[index] > 50.0
        )
        raw_patterns["fallingThree"][index] = bool(
            red_candle[index - 4] and body_percent[index - 4] > 50.0
            and all(green_candle[index - offset] and candle_top[index - offset] <= high_values[index - 4] and candle_bottom[index - offset] >= low_values[index - 4] for offset in (3, 2, 1))
            and red_candle[index] and close_values[index] < low_values[index - 4] and body_percent[index] > 50.0
        )
        minor_filters["risingThree"][index] = minor_bull[index - 4]
        minor_filters["fallingThree"][index] = minor_bear[index - 4]

raw_patterns["bullishEngulfing"] &= ~raw_patterns["risingThree"]
raw_patterns["bearishEngulfing"] &= ~raw_patterns["fallingThree"]
filtered_patterns = {name: raw_patterns[name] & minor_filters[name] for name in raw_patterns}

pattern_stats = {}
for name in raw_patterns:
    total = int(np.count_nonzero(raw_patterns[name]))
    aligned = int(np.count_nonzero(filtered_patterns[name]))
    pattern_stats[name] = {"aligned": aligned, "total": total, "percent": aligned * 100.0 / total if total else 0.0}

bullish_codes = {
    "hammer": "H",
    "invertedHammer": "IH",
    "bullishEngulfing": "EG+",
    "risingThree": "R3",
    "threeWhiteSoldiers": "3WS",
    "morningStar": "MS",
    "bullishHarami": "H+",
    "tweezerBottom": "TB",
}
bearish_codes = {
    "hangingMan": "HM",
    "shootingStar": "SS",
    "bearishEngulfing": "EG-",
    "fallingThree": "F3",
    "threeBlackCrows": "3BC",
    "eveningStar": "ES",
    "bearishHarami": "H-",
    "tweezerTop": "TT",
}

event_candidates = []
for index in range(max(0, n - label_lookback), n):
    bull_codes = [code for name, code in bullish_codes.items() if pattern_enabled[name] and filtered_patterns[name][index] and major_bull[index]]
    bear_codes = [code for name, code in bearish_codes.items() if pattern_enabled[name] and filtered_patterns[name][index] and major_bear[index]]
    if bull_codes:
        event_candidates.append((index, "/".join(bull_codes), bullish_color, low_values[index], "below"))
    if bear_codes:
        event_candidates.append((index, "/".join(bear_codes), bearish_color, high_values[index], "above"))
spaced_events = []
for event in event_candidates:
    if spaced_events and event[0] - spaced_events[-1][0] <= label_spacing:
        spaced_events[-1] = event
    else:
        spaced_events.append(event)
event_candidates = spaced_events[-max_labels:]
font_sizes = {"tiny": 7, "small": 8, "normal": 10, "large": 12}
layers = []
for index, text_value, color_value, price_value, side_value in event_candidates:
    layers.append({
        "type": "label",
        "timestamp": float(time_values[index]),
        "price": float(price_value),
        "text": text_value,
        "color": "rgba(0,0,0,0)",
        "fillColor": "rgba(0,0,0,0)",
        "borderColor": "rgba(0,0,0,0)",
        "textColor": color_value,
        "fontSize": font_sizes.get(label_size, 7),
        "side": side_value,
    })

for index in range(n):
    if major_choch_bull[index] and np.isfinite(major_choch_top[index]):
        layers.append({"type": "label", "timestamp": float(time_values[index]), "price": float(major_choch_top[index]), "text": "ChoCh+", "color": bullish_color, "fillColor": "rgba(0,0,0,0)", "borderColor": bullish_color, "textColor": bullish_color, "fontSize": 7, "side": "above"})
    if major_choch_bear[index] and np.isfinite(major_choch_bottom[index]):
        layers.append({"type": "label", "timestamp": float(time_values[index]), "price": float(major_choch_bottom[index]), "text": "ChoCh-", "color": bearish_color, "fillColor": "rgba(0,0,0,0)", "borderColor": bearish_color, "textColor": bearish_color, "fontSize": 7, "side": "below"})

def colored_line(values, visible_direction):
    result = []
    for index, value in enumerate(values):
        if not np.isfinite(value) or not visible_direction[index]:
            result.append(None)
        else:
            result.append({"value": float(value), "color": bullish_color if major_bull[index] else bearish_color})
    return result


plots = [{"name": "Candle Alignment Anchor", "title": False, "data": [float(value) for value in close_values], "color": "rgba(0,0,0,0)", "type": "line", "lineWidth": 1, "overlay": True}]
if major_method == "EMA":
    plots.extend([
        {"name": "Major EMA Short", "data": colored_line(major_first, np.ones(n, dtype=bool)), "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Major EMA Long", "data": colored_line(major_second, np.ones(n, dtype=bool)), "type": "line", "lineWidth": 1, "overlay": True},
    ])
elif major_method != "ChoCh":
    plots.extend([
        {"name": "Major Uptrend", "data": colored_line(major_first, major_bull), "type": "line", "lineWidth": 2, "overlay": True},
        {"name": "Major Downtrend", "data": colored_line(major_second, major_bear), "type": "line", "lineWidth": 2, "overlay": True},
    ])

if show_fill and major_method != "ChoCh":
    major_cloud = []
    for index in range(n):
        if np.isfinite(major_first[index]) and np.isfinite(major_second[index]):
            major_cloud.append({"upper": float(max(major_first[index], major_second[index])), "lower": float(min(major_first[index], major_second[index])), "color": bullish_color if major_bull[index] else bearish_color})
        else:
            major_cloud.append(None)
    plots.append({"name": "Major Trend Gradient", "title": False, "data": major_cloud, "type": "cloud", "color": bullish_color, "opacity": 0.10, "overlay": True})

bar_colors = [bullish_color if minor_direction[index] > 0 else bearish_color if minor_direction[index] < 0 else None for index in range(n)] if color_candles else [None] * n


def stat_text(name):
    stat = pattern_stats[name]
    return f"{stat['percent']:.1f}% ({stat['total']})"


tables = []
if show_dashboard:
    tables.append({
        "position": dashboard_location if dashboard_location in ("top-right", "bottom-right", "bottom-left") else "bottom-right",
        "backgroundColor": "rgba(30,34,45,0.94)",
        "borderColor": "#373A46",
        "textColor": "#F8FAFC",
        "accentColor": bullish_color,
        "rows": [
            ["PATTERN ALIGNMENT", "BULL / BEAR"],
            ["Hammer / Hanging", f"{stat_text('hammer')} / {stat_text('hangingMan')}"],
            ["Inv. Hammer / Shoot", f"{stat_text('invertedHammer')} / {stat_text('shootingStar')}"],
            ["Engulfing", f"{stat_text('bullishEngulfing')} / {stat_text('bearishEngulfing')}"],
            ["Rising / Falling 3", f"{stat_text('risingThree')} / {stat_text('fallingThree')}"],
            ["Soldiers / Crows", f"{stat_text('threeWhiteSoldiers')} / {stat_text('threeBlackCrows')}"],
            ["Morning / Evening", f"{stat_text('morningStar')} / {stat_text('eveningStar')}"],
            ["Bull / Bear Harami", f"{stat_text('bullishHarami')} / {stat_text('bearishHarami')}"],
            ["Tweezer Bottom / Top", f"{stat_text('tweezerBottom')} / {stat_text('tweezerTop')}"],
        ],
    })


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": layers,
    "tables": tables,
    "bar_colors": bar_colors,
    "background_colors": [None] * n,
    "calculatedVars": {
        "minorLower": finite(minor_lower),
        "minorUpper": finite(minor_upper),
        "minorDirection": [int(value) for value in minor_direction],
        "majorMethod": major_method,
        "majorFirst": finite(major_first),
        "majorSecond": finite(major_second),
        "majorDirection": [int(value) for value in major_direction],
        "majorChochTop": finite(major_choch_top),
        "majorChochBottom": finite(major_choch_bottom),
        "majorChochBull": [bool(value) for value in major_choch_bull],
        "majorChochBear": [bool(value) for value in major_choch_bear],
        "bodyPercent": finite(body_percent),
        "upperWickPercent": finite(upper_wick_percent),
        "lowerWickPercent": finite(lower_wick_percent),
        "rawPatterns": {name: [bool(value) for value in values] for name, values in raw_patterns.items()},
        "filteredPatterns": {name: [bool(value) for value in values] for name, values in filtered_patterns.items()},
        "patternStats": pattern_stats,
    },
}
