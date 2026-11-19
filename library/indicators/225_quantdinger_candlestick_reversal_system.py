# ============================================================
# QuantDinger Candlestick Reversal System
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Candlestick Reversal System"
my_indicator_description = (
    "Combines wick, extreme-candle, outside-bar, and doji reversal systems "
    "behind rolling high/low pivot gates."
)

# @param pivot_length int 5 Length to highest or lowest range=1:100:1
# @param show_sma bool false Show SMA
# @param sma_length int 10 SMA length range=1:500:1
# @param sma_color str #3B82F6 SMA color
# @param enable_wick bool true Enable wick reversal system
# @param wick_multiplier float 2.5 Wick multiplier range=0.0:20.0:0.5
# @param body_percentage float 0.25 Wick body percentage range=0.0:1.0:0.05
# @param enable_extreme bool true Enable extreme reversal system
# @param extreme_body_size float 0.525 Extreme body fraction range=0.0:1.0:0.025
# @param extreme_bars_back int 50 Extreme average window range=1:50:1
# @param extreme_multiplier float 2.0 Extreme range multiplier range=0.0:5.0:0.25
# @param enable_outside bool true Enable outside reversal system
# @param outside_multiplier float 1.25 Outside range multiplier range=0.0:3.5:0.05
# @param outside_bars_back int 50 Outside average window range=1:250:1
# @param enable_doji bool true Enable doji reversal system
# @param doji_percentage float 0.1 Doji body percentage range=0.1:2.0:0.1
# @param long_color str #3B82F6 Long marker color
# @param short_color str #EF4444 Short marker color

pivot_length = min(100, max(1, int(params.get("pivot_length", 5))))
show_sma = bool(params.get("show_sma", False))
sma_length = min(500, max(1, int(params.get("sma_length", 10))))
sma_color = str(params.get("sma_color", "#3B82F6"))
enable_wick = bool(params.get("enable_wick", True))
wick_multiplier = min(20.0, max(0.0, float(params.get("wick_multiplier", 2.5))))
body_percentage = min(1.0, max(0.0, float(params.get("body_percentage", 0.25))))
enable_extreme = bool(params.get("enable_extreme", True))
extreme_body_size = min(1.0, max(0.0, float(params.get("extreme_body_size", 0.525))))
extreme_bars_back = min(50, max(1, int(params.get("extreme_bars_back", 50))))
extreme_multiplier = min(5.0, max(0.0, float(params.get("extreme_multiplier", 2.0))))
enable_outside = bool(params.get("enable_outside", True))
outside_multiplier = min(3.5, max(0.0, float(params.get("outside_multiplier", 1.25))))
outside_bars_back = min(250, max(1, int(params.get("outside_bars_back", 50))))
enable_doji = bool(params.get("enable_doji", True))
doji_percentage = min(2.0, max(0.1, float(params.get("doji_percentage", 0.1))))
long_color = str(params.get("long_color", "#3B82F6"))
short_color = str(params.get("short_color", "#EF4444"))

frame = df.copy()
frame = frame.reset_index(drop=True)
n = len(frame)
open_values = pd.to_numeric(frame["open"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
high_values = pd.to_numeric(frame["high"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
low_values = pd.to_numeric(frame["low"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)
close_values = pd.to_numeric(frame["close"], errors="coerce").ffill().fillna(0.0).to_numpy(dtype=float)


def rolling_mean(values, length):
    return pd.Series(values, dtype=float).rolling(length, min_periods=length).mean().to_numpy(dtype=float)


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


high_pivot = np.zeros(n, dtype=bool)
low_pivot = np.zeros(n, dtype=bool)
for index in range(pivot_length, n):
    high_pivot[index] = high_values[index] > float(np.max(high_values[index - pivot_length:index]))
    low_pivot[index] = low_values[index] < float(np.min(low_values[index - pivot_length:index]))

candle_range = high_values - low_values
body_size = np.abs(close_values - open_values)
average_range_50 = rolling_mean(candle_range, 50)
average_body = rolling_mean(body_size, extreme_bars_back)
average_extreme_range = rolling_mean(candle_range, extreme_bars_back)
average_outside_range = rolling_mean(candle_range, outside_bars_back)
sma_10 = rolling_mean(close_values, 10)
display_sma = rolling_mean(close_values, sma_length)

wick_long = np.zeros(n, dtype=bool)
wick_short = np.zeros(n, dtype=bool)
extreme_long = np.zeros(n, dtype=bool)
extreme_short = np.zeros(n, dtype=bool)
outside_long = np.zeros(n, dtype=bool)
outside_short = np.zeros(n, dtype=bool)
doji_long = np.zeros(n, dtype=bool)
doji_short = np.zeros(n, dtype=bool)

for index in range(n):
    open_value = open_values[index]
    close_value = close_values[index]
    high_value = high_values[index]
    low_value = low_values[index]
    full_range = candle_range[index]
    bullish = close_value > open_value
    bearish = close_value < open_value
    doji = close_value == open_value

    wick_long[index] = bool(
        (bullish and open_value - low_value >= (close_value - open_value) * wick_multiplier and high_value - close_value <= full_range * body_percentage)
        or (bearish and close_value - low_value >= (open_value - close_value) * wick_multiplier and high_value - close_value <= full_range * body_percentage)
        or (doji and close_value != high_value and full_range >= (high_value - close_value) * wick_multiplier and high_value - close_value <= full_range * body_percentage)
        or (open_value == high_value and close_value == high_value and np.isfinite(average_range_50[index]) and full_range >= average_range_50[index])
    )
    wick_short[index] = bool(
        (bearish and high_value - open_value >= (open_value - close_value) * wick_multiplier and close_value - low_value <= full_range * body_percentage)
        or (bullish and high_value - close_value >= (close_value - open_value) * wick_multiplier and close_value - low_value <= full_range * body_percentage)
        or (doji and close_value != low_value and full_range >= (close_value - low_value) * wick_multiplier and close_value - low_value <= full_range * body_percentage)
        or (open_value == low_value and close_value == low_value and np.isfinite(average_range_50[index]) and full_range >= average_range_50[index])
    )

    if index >= 1 and np.isfinite(average_body[index]) and np.isfinite(average_extreme_range[index]):
        previous_body_down = open_values[index - 1] - close_values[index - 1]
        previous_body_up = close_values[index - 1] - open_values[index - 1]
        previous_range = candle_range[index - 1]
        extreme_long[index] = bool(
            previous_body_down >= extreme_body_size * previous_range
            and previous_range > average_extreme_range[index] * extreme_multiplier
            and previous_body_down > average_body[index]
            and bullish
        )
        extreme_short[index] = bool(
            previous_body_up >= extreme_body_size * previous_range
            and previous_range > average_extreme_range[index] * extreme_multiplier
            and previous_body_up > average_body[index]
            and bearish
        )

    if index >= 1 and np.isfinite(average_outside_range[index]):
        outside_long[index] = bool(
            low_value < low_values[index - 1]
            and close_value > high_values[index - 1]
            and full_range >= average_outside_range[index] * outside_multiplier
        )
        outside_short[index] = bool(
            high_value > high_values[index - 1]
            and close_value < low_values[index - 1]
            and full_range >= average_outside_range[index] * outside_multiplier
        )

    if index >= 1:
        previous_range = candle_range[index - 1]
        previous_body = body_size[index - 1]
        first_short = (
            previous_body <= previous_range * doji_percentage
            and close_value < low_values[index - 1]
            and np.isfinite(sma_10[index])
            and low_values[index - 1] > sma_10[index]
            and bearish
        )
        first_long = (
            previous_body <= previous_range * doji_percentage
            and close_value > high_values[index - 1]
            and np.isfinite(sma_10[index])
            and high_values[index - 1] < sma_10[index]
            and bullish
        )
        second_short = False
        second_long = False
        if index >= 2:
            second_short = bool(
                close_value < low_values[index - 2]
                and close_values[index - 1] >= low_values[index - 2]
                and previous_body <= previous_body * doji_percentage
                and bearish
                and np.isfinite(sma_10[index])
                and low_values[index - 2] > sma_10[index]
            )
            second_long = bool(
                close_value > high_values[index - 2]
                and close_values[index - 1] <= high_values[index - 2]
                and previous_body <= previous_body * doji_percentage
                and bullish
                and np.isfinite(sma_10[index])
                and high_values[index - 2] < sma_10[index]
            )
        doji_short[index] = bool(first_short or second_short)
        doji_long[index] = bool(first_long or second_long)

long_signal = low_pivot & (
    (enable_wick & wick_long)
    | (enable_extreme & extreme_long)
    | (enable_outside & outside_long)
    | (enable_doji & doji_long)
)

# The source applies its bullish wick branch to both aggregate sides.
short_signal = high_pivot & (
    (enable_wick & wick_long)
    | (enable_extreme & extreme_short)
    | (enable_outside & outside_short)
    | (enable_doji & doji_short)
)

long_marks = [float(low_values[index]) if long_signal[index] else None for index in range(n)]
short_marks = [float(high_values[index]) if short_signal[index] else None for index in range(n)]

plots = [{
    "name": "Candle Alignment Anchor",
    "title": False,
    "data": [float(value) for value in close_values],
    "color": "rgba(0,0,0,0)",
    "type": "line",
    "lineWidth": 1,
    "overlay": True,
}]
if show_sma:
    plots.append({"name": "SMA", "data": finite(display_sma), "color": sma_color, "type": "line", "lineWidth": 2, "overlay": True})

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [
        {"type": "buy", "text": "L", "name": "Long Reversal", "data": long_marks, "color": long_color, "renderMode": "events"},
        {"type": "sell", "text": "S", "name": "Short Reversal", "data": short_marks, "color": short_color, "renderMode": "events"},
    ],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "highPivot": [bool(value) for value in high_pivot],
        "lowPivot": [bool(value) for value in low_pivot],
        "rangeSma50": finite(average_range_50),
        "averageBody": finite(average_body),
        "averageExtremeRange": finite(average_extreme_range),
        "averageOutsideRange": finite(average_outside_range),
        "sma10": finite(sma_10),
        "displaySma": finite(display_sma),
        "wickLong": [bool(value) for value in wick_long],
        "wickShort": [bool(value) for value in wick_short],
        "extremeLong": [bool(value) for value in extreme_long],
        "extremeShort": [bool(value) for value in extreme_short],
        "outsideLong": [bool(value) for value in outside_long],
        "outsideShort": [bool(value) for value in outside_short],
        "dojiLong": [bool(value) for value in doji_long],
        "dojiShort": [bool(value) for value in doji_short],
        "longSignal": [bool(value) for value in long_signal],
        "shortSignal": [bool(value) for value in short_signal],
    },
}
