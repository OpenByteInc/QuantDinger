# ============================================================
# QuantDinger Cumulative Delta Volume
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Cumulative Delta Volume"
my_indicator_description = (
    "Estimates candle volume delta from body and wick geometry, accumulates it, "
    "and displays standard or Heikin Ashi CDV candles with optional averages."
)

indicator_timeframes = ["1H", "4H", "1D", "1W"]

# @param style str Candle Display style values=Candle,Line
# @param heikin_ashi bool true Use Heikin Ashi CDV candles
# @param show_sma1 bool false Show SMA 1
# @param sma1_length int 50 SMA 1 length range=1:500:1
# @param sma1_color str #00E676 SMA 1 color
# @param show_sma2 bool false Show SMA 2
# @param sma2_length int 200 SMA 2 length range=1:1000:1
# @param sma2_color str #EF5350 SMA 2 color
# @param show_ema1 bool false Show EMA 1
# @param ema1_length int 50 EMA 1 length range=1:500:1
# @param ema1_color str #00E676 EMA 1 color
# @param show_ema2 bool false Show EMA 2
# @param ema2_length int 200 EMA 2 length range=1:1000:1
# @param ema2_color str #EF5350 EMA 2 color
# @param up_color str #00E676 Up body color
# @param down_color str #EF5350 Down body color
# @param up_border_color str #74E05E Up border color
# @param down_border_color str #FFAD7D Down border color
# @param up_wick_color str #B5B5B8 Up wick color
# @param down_wick_color str #B5B5B8 Down wick color

style = str(params.get("style", "Candle")).title()
heikin_ashi = bool(params.get("heikin_ashi", True))
show_sma1 = bool(params.get("show_sma1", False))
sma1_length = min(500, max(1, int(params.get("sma1_length", 50))))
sma1_color = str(params.get("sma1_color", "#00E676"))
show_sma2 = bool(params.get("show_sma2", False))
sma2_length = min(1000, max(1, int(params.get("sma2_length", 200))))
sma2_color = str(params.get("sma2_color", "#EF5350"))
show_ema1 = bool(params.get("show_ema1", False))
ema1_length = min(500, max(1, int(params.get("ema1_length", 50))))
ema1_color = str(params.get("ema1_color", "#00E676"))
show_ema2 = bool(params.get("show_ema2", False))
ema2_length = min(1000, max(1, int(params.get("ema2_length", 200))))
ema2_color = str(params.get("ema2_color", "#EF5350"))
up_color = str(params.get("up_color", "#00E676"))
down_color = str(params.get("down_color", "#EF5350"))
up_border_color = str(params.get("up_border_color", "#74E05E"))
down_border_color = str(params.get("down_border_color", "#FFAD7D"))
up_wick_color = str(params.get("up_wick_color", "#B5B5B8"))
down_wick_color = str(params.get("down_wick_color", "#B5B5B8"))

frame = df.copy().reset_index(drop=True)
n = len(frame)


def numeric(column):
    return pd.to_numeric(frame[column], errors="coerce").ffill().bfill().fillna(0.0).to_numpy(dtype=float)


open_values = numeric("open")
high_values = numeric("high")
low_values = numeric("low")
close_values = numeric("close")
volume_values = numeric("volume")

top_wick = high_values - np.maximum(open_values, close_values)
bottom_wick = np.minimum(open_values, close_values) - low_values
body = np.abs(close_values - open_values)
denominator = top_wick + bottom_wick + body
rate = np.full(n, 0.5, dtype=float)
valid = denominator != 0.0
rate[valid] = 0.5 * (top_wick[valid] + bottom_wick[valid] + 2.0 * body[valid]) / denominator[valid]
rate[~np.isfinite(rate) | (rate == 0.0)] = 0.5

delta_up = volume_values * rate
delta_down = volume_values * rate
delta = np.where(close_values >= open_values, delta_up, -delta_down)
cumulative_delta = np.cumsum(delta)

candle_open = np.full(n, np.nan, dtype=float)
candle_high = np.full(n, np.nan, dtype=float)
candle_low = np.full(n, np.nan, dtype=float)
candle_close = cumulative_delta.copy()
if n > 1:
    candle_open[1:] = cumulative_delta[:-1]
    candle_high[1:] = np.maximum(cumulative_delta[1:], cumulative_delta[:-1])
    candle_low[1:] = np.minimum(cumulative_delta[1:], cumulative_delta[:-1])

ha_close = (candle_open + candle_high + candle_low + candle_close) * 0.25
ha_open = np.full(n, np.nan, dtype=float)
ha_high = np.full(n, np.nan, dtype=float)
ha_low = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index == 0 or not np.isfinite(ha_open[index - 1]):
        if np.isfinite(candle_open[index]) and np.isfinite(candle_close[index]):
            ha_open[index] = (candle_open[index] + candle_close[index]) * 0.5
    else:
        ha_open[index] = (ha_open[index - 1] + ha_close[index - 1]) * 0.5
    if np.isfinite(ha_open[index]) and np.isfinite(ha_close[index]):
        ha_high[index] = max(candle_high[index], ha_open[index], ha_close[index])
        ha_low[index] = min(candle_low[index], ha_open[index], ha_close[index])

display_open = ha_open if heikin_ashi else candle_open
display_high = ha_high if heikin_ashi else candle_high
display_low = ha_low if heikin_ashi else candle_low
display_close = ha_close if heikin_ashi else candle_close


def sma(values, length):
    return pd.Series(values, dtype=float).rolling(length, min_periods=length).mean().to_numpy(dtype=float)


def ema(values, length):
    values = np.asarray(values, dtype=float)
    result = np.full(len(values), np.nan, dtype=float)
    finite_indexes = np.flatnonzero(np.isfinite(values))
    if len(finite_indexes) < length:
        return result
    seed_indexes = finite_indexes[:length]
    seed_at = int(seed_indexes[-1])
    previous = float(np.mean(values[seed_indexes]))
    result[seed_at] = previous
    alpha = 2.0 / (length + 1.0)
    for index in range(seed_at + 1, len(values)):
        if np.isfinite(values[index]):
            previous = alpha * float(values[index]) + (1.0 - alpha) * previous
        result[index] = previous
    return result


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


plots = []
if style == "Line":
    plots.append({"name": "CDV Line", "data": finite(cumulative_delta), "color": "#2962FF", "type": "line", "lineWidth": 2, "overlay": False})
else:
    candles = []
    for index in range(n):
        if not all(np.isfinite(value) for value in (display_open[index], display_high[index], display_low[index], display_close[index])):
            candles.append(None)
            continue
        rising_candle = display_open[index] <= display_close[index]
        candles.append({
            "open": float(display_open[index]),
            "high": float(display_high[index]),
            "low": float(display_low[index]),
            "close": float(display_close[index]),
            "color": up_color if rising_candle else down_color,
            "fillColor": up_color if rising_candle else down_color,
            "borderColor": up_border_color if rising_candle else down_border_color,
            "wickColor": up_wick_color if rising_candle else down_wick_color,
        })
    plots.append({"name": "CDV Candles", "data": candles, "type": "candlestick", "overlay": False})
    if show_sma1:
        plots.append({"name": "SMA 1", "data": finite(sma(display_close, sma1_length)), "color": sma1_color, "type": "line", "lineWidth": 1, "overlay": False})
    if show_sma2:
        plots.append({"name": "SMA 2", "data": finite(sma(display_close, sma2_length)), "color": sma2_color, "type": "line", "lineWidth": 1, "overlay": False})
    if show_ema1:
        plots.append({"name": "EMA 1", "data": finite(ema(display_close, ema1_length)), "color": ema1_color, "type": "line", "lineWidth": 1, "overlay": False})
    if show_ema2:
        plots.append({"name": "EMA 2", "data": finite(ema(display_close, ema2_length)), "color": ema2_color, "type": "line", "lineWidth": 1, "overlay": False})

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "topWick": finite(top_wick),
        "bottomWick": finite(bottom_wick),
        "body": finite(body),
        "rate": finite(rate),
        "deltaUp": finite(delta_up),
        "deltaDown": finite(delta_down),
        "delta": finite(delta),
        "cumulativeDelta": finite(cumulative_delta),
        "candleOpen": finite(candle_open),
        "candleHigh": finite(candle_high),
        "candleLow": finite(candle_low),
        "candleClose": finite(candle_close),
        "haOpen": finite(ha_open),
        "haHigh": finite(ha_high),
        "haLow": finite(ha_low),
        "haClose": finite(ha_close),
        "displayOpen": finite(display_open),
        "displayHigh": finite(display_high),
        "displayLow": finite(display_low),
        "displayClose": finite(display_close),
    },
}
