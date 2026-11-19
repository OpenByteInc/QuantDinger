# ============================================================
# QuantDinger Volume Suite
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Volume Suite"
my_indicator_description = (
    "Switches among relative volume, cumulative relative volume, total volume, intrabar buy/sell "
    "volume, volume delta, and cumulative volume delta with threshold and imbalance coloring."
)

indicator_lower_timeframes = ["1m", "3m", "5m", "15m", "30m", "1H"]

# @param display str Relative_Volume Display family values=Relative_Volume,CRVOL,Volume,Buy_Sell_Volume,Volume_Delta,CVD
# @param up_color str #D1D4DC Primary up color
# @param down_color str #9598A1 Primary down color
# @param threshold_type str RELATIVE Threshold method values=SMA,RELATIVE,Z_SCORE
# @param threshold1_enabled bool true Enable first threshold
# @param threshold1 float 1.5 First threshold range=0.1:20.0:0.1
# @param threshold2_enabled bool true Enable second threshold
# @param threshold2 float 2.5 Second threshold range=0.1:20.0:0.1
# @param threshold3_enabled bool true Enable third threshold
# @param threshold3 float 3.5 Third threshold range=0.1:20.0:0.1
# @param up_threshold1 str #C8E6C9 First up threshold color
# @param down_threshold1 str #FAA1A4 First down threshold color
# @param up_threshold2 str #A5D6A7 Second up threshold color
# @param down_threshold2 str #F77C80 Second down threshold color
# @param up_threshold3 str #66BB6A Third up threshold color
# @param down_threshold3 str #F7525F Third down threshold color
# @param show_imbalance1 bool false Mark high-volume small-range imbalance
# @param imbalance1_color str #FF9800 First imbalance color
# @param volume_imbalance1 float 1.0 First volume z threshold range=-10.0:10.0:0.1
# @param price_imbalance1 float 0.0 First price z threshold range=-10.0:10.0:0.1
# @param show_imbalance2 bool false Mark low-volume large-range imbalance
# @param imbalance2_color str #2196F3 Second imbalance color
# @param volume_imbalance2 float 0.0 Second volume z threshold range=-10.0:10.0:0.1
# @param price_imbalance2 float 1.0 Second price z threshold range=-10.0:10.0:0.1
# @param color_host_candles bool false Color host candles on thresholds
# @param gradient bool false Use gradient colors
# @param gradient_low str #FFE0B2 Gradient low color
# @param gradient_high str #F7525F Gradient high color
# @param lower_timeframe str 1m Lower timeframe values=1m,3m,5m,15m,30m,1H
# @param z_length int 50 Z-score length range=2:500:1
# @param sma_length int 300 SMA threshold length range=2:1000:1
# @param relative_length int 20 Relative length range=1:500:1
# @param relative_style str Line Relative volume style values=Columns,Histogram,Line
# @param cumulative_relative_style str Candles CRVOL style values=Candles,Line
# @param buy_sell_style str Columns Buy/sell style values=Columns,Histogram,Line
# @param delta_style str Columns Delta style values=Columns,Histogram,Line
# @param cumulative_delta_style str Candles CVD style values=Candles,Line
# @param show_ma1 bool false Show price average one
# @param ma1_type str SMA Average one type values=SMA,EMA,VWMA
# @param ma1_length int 50 Average one length range=1:500:1
# @param ma1_color str #808080 Average one color
# @param show_ma2 bool false Show price average two
# @param ma2_type str EMA Average two type values=SMA,EMA,VWMA
# @param ma2_length int 50 Average two length range=1:500:1
# @param ma2_color str #808080 Average two color
# @param show_ma3 bool false Show price average three
# @param ma3_type str VWMA Average three type values=SMA,EMA,VWMA
# @param ma3_length int 50 Average three length range=1:500:1
# @param ma3_color str #808080 Average three color
# @param show_ma4 bool false Show price average four
# @param ma4_type str SMA Average four type values=SMA,EMA,VWMA
# @param ma4_length int 50 Average four length range=1:500:1
# @param ma4_color str #808080 Average four color
# @param show_vwap bool false Show anchored VWAP
# @param show_vwap_bands bool false Show VWAP deviation bands
# @param vwap_anchor str 1D VWAP anchor values=1H,4H,12H,1D,1W,1M
# @param vwap_stdev float 1.0 VWAP deviation multiplier range=0.1:10.0:0.1
# @param vwap_color str #808080 VWAP color

display = str(params.get("display", "Relative_Volume"))
valid_displays = ("Relative_Volume", "CRVOL", "Volume", "Buy_Sell_Volume", "Volume_Delta", "CVD")
if display not in valid_displays:
    display = "Relative_Volume"
up_color = str(params.get("up_color", "#D1D4DC"))
down_color = str(params.get("down_color", "#9598A1"))
threshold_type = str(params.get("threshold_type", "RELATIVE"))
threshold1_enabled = bool(params.get("threshold1_enabled", True))
threshold1 = min(20.0, max(0.1, float(params.get("threshold1", 1.5))))
threshold2_enabled = bool(params.get("threshold2_enabled", True))
threshold2 = min(20.0, max(0.1, float(params.get("threshold2", 2.5))))
threshold3_enabled = bool(params.get("threshold3_enabled", True))
threshold3 = min(20.0, max(0.1, float(params.get("threshold3", 3.5))))
up_threshold1 = str(params.get("up_threshold1", "#C8E6C9"))
down_threshold1 = str(params.get("down_threshold1", "#FAA1A4"))
up_threshold2 = str(params.get("up_threshold2", "#A5D6A7"))
down_threshold2 = str(params.get("down_threshold2", "#F77C80"))
up_threshold3 = str(params.get("up_threshold3", "#66BB6A"))
down_threshold3 = str(params.get("down_threshold3", "#F7525F"))
show_imbalance1 = bool(params.get("show_imbalance1", False))
imbalance1_color = str(params.get("imbalance1_color", "#FF9800"))
volume_imbalance1 = float(params.get("volume_imbalance1", 1.0))
price_imbalance1 = float(params.get("price_imbalance1", 0.0))
show_imbalance2 = bool(params.get("show_imbalance2", False))
imbalance2_color = str(params.get("imbalance2_color", "#2196F3"))
volume_imbalance2 = float(params.get("volume_imbalance2", 0.0))
price_imbalance2 = float(params.get("price_imbalance2", 1.0))
color_host_candles = bool(params.get("color_host_candles", False))
gradient = bool(params.get("gradient", False))
gradient_low = str(params.get("gradient_low", "#FFE0B2"))
gradient_high = str(params.get("gradient_high", "#F7525F"))
lower_timeframe = str(params.get("lower_timeframe", "1m"))
if lower_timeframe not in ("1m", "3m", "5m", "15m", "30m", "1H"):
    lower_timeframe = "1m"
z_length = min(500, max(2, int(params.get("z_length", 50))))
sma_length = min(1000, max(2, int(params.get("sma_length", 300))))
relative_length = min(500, max(1, int(params.get("relative_length", 20))))
relative_style = str(params.get("relative_style", "Line"))
cumulative_relative_style = str(params.get("cumulative_relative_style", "Candles"))
buy_sell_style = str(params.get("buy_sell_style", "Columns"))
delta_style = str(params.get("delta_style", "Columns"))
cumulative_delta_style = str(params.get("cumulative_delta_style", "Candles"))
show_ma1 = bool(params.get("show_ma1", False))
ma1_type = str(params.get("ma1_type", "SMA"))
ma1_length = min(500, max(1, int(params.get("ma1_length", 50))))
ma1_color = str(params.get("ma1_color", "#808080"))
show_ma2 = bool(params.get("show_ma2", False))
ma2_type = str(params.get("ma2_type", "EMA"))
ma2_length = min(500, max(1, int(params.get("ma2_length", 50))))
ma2_color = str(params.get("ma2_color", "#808080"))
show_ma3 = bool(params.get("show_ma3", False))
ma3_type = str(params.get("ma3_type", "VWMA"))
ma3_length = min(500, max(1, int(params.get("ma3_length", 50))))
ma3_color = str(params.get("ma3_color", "#808080"))
show_ma4 = bool(params.get("show_ma4", False))
ma4_type = str(params.get("ma4_type", "SMA"))
ma4_length = min(500, max(1, int(params.get("ma4_length", 50))))
ma4_color = str(params.get("ma4_color", "#808080"))
show_vwap = bool(params.get("show_vwap", False))
show_vwap_bands = bool(params.get("show_vwap_bands", False))
vwap_anchor = str(params.get("vwap_anchor", "1D"))
if vwap_anchor not in ("1H", "4H", "12H", "1D", "1W", "1M"):
    vwap_anchor = "1D"
vwap_stdev = min(10.0, max(0.1, float(params.get("vwap_stdev", 1.0))))
vwap_color = str(params.get("vwap_color", "#808080"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
raw_time = df["time"].astype(float).to_numpy(dtype=float) if "time" in df.columns else np.arange(len(df), dtype=float)
time_seconds = raw_time / 1000.0 if len(raw_time) and np.nanmax(np.abs(raw_time)) > 10_000_000_000 else raw_time
n = len(df)

def sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.mean(window))
    return result

def ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result

def vwma(values, weights, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        start = index - length + 1
        denominator = float(np.sum(weights[start:index + 1]))
        if denominator > 1e-12:
            result[index] = float(np.sum(values[start:index + 1] * weights[start:index + 1]) / denominator)
    return result

def relative(values, length):
    average = sma(values, length)
    result = np.full(len(values), np.nan, dtype=float)
    valid = np.isfinite(average) & (np.abs(average) > 1e-12)
    result[valid] = values[valid] / average[valid]
    return result

def zscore(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if not np.all(np.isfinite(window)):
            continue
        deviation = float(np.std(window, ddof=0))
        if deviation > 1e-12:
            result[index] = (values[index] - float(np.mean(window))) / deviation
    return result

def hex_rgb(color):
    value = str(color).strip()
    if value.startswith("#") and len(value) in (4, 7):
        if len(value) == 4:
            return tuple(int(value[position] * 2, 16) for position in range(1, 4))
        return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
    return 128, 128, 128

def gradient_color(value, minimum, maximum, low_color, high_color):
    if not (np.isfinite(value) and np.isfinite(minimum) and np.isfinite(maximum)) or maximum <= minimum:
        return low_color
    weight = min(1.0, max(0.0, (value - minimum) / (maximum - minimum)))
    low_rgb = hex_rgb(low_color)
    high_rgb = hex_rgb(high_color)
    channels = [int(round(low_rgb[index] + (high_rgb[index] - low_rgb[index]) * weight)) for index in range(3)]
    return "#" + "".join(f"{channel:02X}" for channel in channels)

chart_seconds_map = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1H": 3600, "2H": 7200, "4H": 14400, "6H": 21600,
    "8H": 28800, "12H": 43200, "1D": 86400, "1W": 604800,
}
lower_seconds = chart_seconds_map[lower_timeframe]
runtime_context = globals().get("ctx")
chart_timeframe = str(getattr(runtime_context, "timeframe", "") or "")
can_request_lower = (
    callable(globals().get("request_lower_tf"))
    and chart_timeframe in chart_seconds_map
    and lower_seconds < chart_seconds_map[chart_timeframe]
)
if can_request_lower:
    intrabars = request_lower_tf(lower_timeframe, fields=["open", "close", "volume"])
    intrabar_open = intrabars.get("open", [[] for _ in range(n)])
    intrabar_close = intrabars.get("close", [[] for _ in range(n)])
    intrabar_volume = intrabars.get("volume", [[] for _ in range(n)])
else:
    intrabar_open = [[] for _ in range(n)]
    intrabar_close = [[] for _ in range(n)]
    intrabar_volume = [[] for _ in range(n)]

buy_volume = np.zeros(n, dtype=float)
sell_volume = np.zeros(n, dtype=float)
intrabar_counts = np.zeros(n, dtype=int)
for index in range(n):
    count = min(len(intrabar_open[index]), len(intrabar_close[index]), len(intrabar_volume[index]))
    intrabar_counts[index] = count
    if count:
        for cursor in range(count):
            current_volume = float(intrabar_volume[index][cursor])
            if float(intrabar_close[index][cursor]) > float(intrabar_open[index][cursor]):
                buy_volume[index] += current_volume
            elif float(intrabar_close[index][cursor]) < float(intrabar_open[index][cursor]):
                sell_volume[index] += current_volume
    else:
        bar_range = max(float(high[index] - low[index]), 1e-12)
        buy_share = min(1.0, max(0.0, float(close[index] - low[index]) / bar_range))
        buy_volume[index] = max(0.0, float(volume[index])) * buy_share
        sell_volume[index] = max(0.0, float(volume[index])) - buy_volume[index]

delta_volume = buy_volume - sell_volume
cumulative_delta = np.cumsum(delta_volume)
relative_volume = relative(volume, relative_length)
cumulative_relative = np.zeros(n, dtype=float)
for index in range(n):
    contribution = relative_volume[index] if np.isfinite(relative_volume[index]) else 0.0
    cumulative_relative[index] = (cumulative_relative[index - 1] if index else 0.0) + (contribution if close[index] > open_price[index] else -contribution)

positive_delta = np.where(delta_volume > 0, delta_volume, 0.0)
negative_delta = np.where(delta_volume < 0, delta_volume, 0.0)
relative_positive_delta = relative(positive_delta, relative_length)
relative_negative_delta = relative(negative_delta, relative_length)
volume_relative = relative(volume, relative_length)
buy_relative = relative(buy_volume, relative_length)
sell_relative = relative(sell_volume, relative_length)

volume_z = zscore(volume, z_length)
relative_z = zscore(relative_volume, z_length)
positive_delta_z = zscore(relative_positive_delta, z_length)
negative_delta_z = zscore(relative_negative_delta, z_length)
buy_z = zscore(buy_volume, z_length)
sell_z = zscore(sell_volume, z_length)
price_range_z = zscore(high - low, z_length)

volume_sma = sma(volume, sma_length)
relative_sma = sma(relative_volume, sma_length)
positive_sma = sma(positive_delta, sma_length)
negative_sma = sma(negative_delta, sma_length)
buy_sma = sma(buy_volume, sma_length)
sell_sma = sma(sell_volume, sma_length)

volume_colors = [up_color if close[index] > open_price[index] else down_color for index in range(n)]
relative_colors = list(volume_colors)
delta_colors = [up_color if delta_volume[index] > 0 else down_color for index in range(n)]
buy_colors = [up_color for _ in range(n)]
sell_colors = [down_color for _ in range(n)]
host_colors = [None for _ in range(n)]

thresholds = [
    (threshold1_enabled, threshold1, up_threshold1, down_threshold1, 2.0),
    (threshold2_enabled, threshold2, up_threshold2, down_threshold2, 3.0),
    (threshold3_enabled, threshold3, up_threshold3, down_threshold3, 7.0),
]

for index in range(n):
    for enabled, threshold, up_threshold_color, down_threshold_color, delta_scale in thresholds:
        if not enabled:
            continue
        candle_up = close[index] > open_price[index]
        selected_up = up_threshold_color if candle_up else down_threshold_color
        if threshold_type == "SMA":
            if display == "Volume" and np.isfinite(volume_sma[index]) and volume[index] >= volume_sma[index] * (threshold + 1.0):
                volume_colors[index] = selected_up
                host_colors[index] = selected_up
            elif display in ("Relative_Volume", "CRVOL") and np.isfinite(relative_sma[index]) and relative_volume[index] >= relative_sma[index] * (threshold + 1.0):
                relative_colors[index] = selected_up
                host_colors[index] = selected_up
            elif display in ("Volume_Delta", "CVD"):
                if delta_volume[index] > 0 and np.isfinite(positive_sma[index]) and delta_volume[index] > positive_sma[index] * (threshold + 1.0) * delta_scale:
                    delta_colors[index] = up_threshold_color
                    host_colors[index] = up_threshold_color
                if delta_volume[index] < 0 and np.isfinite(negative_sma[index]) and delta_volume[index] < negative_sma[index] * (threshold + 1.0) * delta_scale:
                    delta_colors[index] = down_threshold_color
                    host_colors[index] = down_threshold_color
            elif display == "Buy_Sell_Volume":
                if np.isfinite(buy_sma[index]) and buy_volume[index] > buy_sma[index] * (threshold + 1.0):
                    buy_colors[index] = up_threshold_color
                    host_colors[index] = up_threshold_color
                if np.isfinite(sell_sma[index]) and sell_volume[index] > sell_sma[index] * (threshold + 1.0):
                    sell_colors[index] = down_threshold_color
                    host_colors[index] = down_threshold_color
        elif threshold_type == "RELATIVE":
            if display == "Volume" and np.isfinite(volume_relative[index]) and volume_relative[index] > threshold:
                volume_colors[index] = selected_up
                host_colors[index] = selected_up
            elif display in ("Relative_Volume", "CRVOL") and np.isfinite(relative_volume[index]) and relative_volume[index] >= threshold:
                relative_colors[index] = selected_up
                host_colors[index] = selected_up
            elif display in ("Volume_Delta", "CVD"):
                if delta_volume[index] > 0 and np.isfinite(relative_positive_delta[index]) and relative_positive_delta[index] > threshold * 1.5:
                    delta_colors[index] = up_threshold_color
                    host_colors[index] = up_threshold_color
                if delta_volume[index] < 0 and np.isfinite(relative_negative_delta[index]) and relative_negative_delta[index] > threshold * 1.5:
                    delta_colors[index] = down_threshold_color
                    host_colors[index] = down_threshold_color
            elif display == "Buy_Sell_Volume":
                if np.isfinite(buy_relative[index]) and buy_relative[index] > threshold:
                    buy_colors[index] = up_threshold_color
                    host_colors[index] = up_threshold_color
                if np.isfinite(sell_relative[index]) and sell_relative[index] >= threshold:
                    sell_colors[index] = down_threshold_color
                    host_colors[index] = down_threshold_color
        else:
            if display == "Volume" and np.isfinite(volume_z[index]) and volume_z[index] >= threshold:
                volume_colors[index] = selected_up
                host_colors[index] = selected_up
            elif display in ("Relative_Volume", "CRVOL") and np.isfinite(relative_z[index]) and relative_z[index] >= threshold:
                relative_colors[index] = selected_up
                host_colors[index] = selected_up
            elif display in ("Volume_Delta", "CVD"):
                if delta_volume[index] > 0 and np.isfinite(positive_delta_z[index]) and positive_delta_z[index] >= threshold:
                    delta_colors[index] = up_threshold_color
                    host_colors[index] = up_threshold_color
                if delta_volume[index] < 0 and np.isfinite(negative_delta_z[index]) and negative_delta_z[index] >= threshold:
                    delta_colors[index] = down_threshold_color
                    host_colors[index] = down_threshold_color
            elif display == "Buy_Sell_Volume":
                if np.isfinite(buy_z[index]) and buy_z[index] >= threshold:
                    buy_colors[index] = up_threshold_color
                    host_colors[index] = up_threshold_color
                if np.isfinite(sell_z[index]) and sell_z[index] >= threshold:
                    sell_colors[index] = down_threshold_color
                    host_colors[index] = down_threshold_color

source_for_imbalance = volume if display == "Volume" else relative_volume if display in ("Relative_Volume", "CRVOL") else np.abs(delta_volume)
source_for_imbalance_z = zscore(source_for_imbalance, z_length)
for index in range(n):
    imbalance_color = None
    if show_imbalance1 and np.isfinite(source_for_imbalance_z[index]) and np.isfinite(price_range_z[index]):
        if source_for_imbalance_z[index] > volume_imbalance1 and price_range_z[index] < price_imbalance1:
            imbalance_color = imbalance1_color
    if show_imbalance2 and np.isfinite(source_for_imbalance_z[index]) and np.isfinite(price_range_z[index]):
        if source_for_imbalance_z[index] < volume_imbalance2 and price_range_z[index] > price_imbalance2:
            imbalance_color = imbalance2_color
    if imbalance_color is not None:
        volume_colors[index] = imbalance_color
        relative_colors[index] = imbalance_color
        delta_colors[index] = imbalance_color
        if buy_volume[index] > sell_volume[index]:
            buy_colors[index] = imbalance_color
        elif sell_volume[index] > buy_volume[index]:
            sell_colors[index] = imbalance_color
        host_colors[index] = imbalance_color

if gradient:
    gradient_length = 50
    for index in range(n):
        start = max(0, index - gradient_length + 1)
        volume_colors[index] = gradient_color(volume[index], 0.0, float(np.max(volume[start:index + 1])), gradient_low, gradient_high)
        if np.isfinite(relative_volume[index]):
            finite_relative = relative_volume[start:index + 1][np.isfinite(relative_volume[start:index + 1])]
            maximum_relative = float(np.max(finite_relative)) if len(finite_relative) else relative_volume[index]
            relative_colors[index] = gradient_color(relative_volume[index], 0.0, maximum_relative, gradient_low, gradient_high)
        delta_window = delta_volume[start:index + 1]
        delta_colors[index] = gradient_color(delta_volume[index], float(np.min(delta_window)), float(np.max(delta_window)), gradient_low, gradient_high)
        buy_colors[index] = gradient_color(buy_volume[index], 0.0, float(np.max(buy_volume[start:index + 1])), gradient_low, gradient_high)
        sell_colors[index] = gradient_color(sell_volume[index], 0.0, float(np.max(sell_volume[start:index + 1])), gradient_low, gradient_high)

def point_series(values, colors):
    return [
        {"value": float(values[index]), "color": colors[index]} if np.isfinite(values[index]) else None
        for index in range(n)
    ]

def plot_type(style):
    return "line" if style == "Line" else "histogram" if style == "Histogram" else "column"

plots = []
if display == "Relative_Volume":
    plots.append({"name": "RVOL", "data": point_series(relative_volume, relative_colors), "color": up_color, "type": plot_type(relative_style), "overlay": False})
elif display == "Volume":
    plots.append({"name": "Volume", "data": point_series(volume, volume_colors), "color": up_color, "type": "bar", "overlay": False})
elif display == "Buy_Sell_Volume":
    plots.append({"name": "Buy Volume", "data": point_series(buy_volume, buy_colors), "color": up_color, "type": plot_type(buy_sell_style), "overlay": False})
    plots.append({"name": "Sell Volume", "data": point_series(-sell_volume, sell_colors), "color": down_color, "type": plot_type(buy_sell_style), "overlay": False})
elif display == "Volume_Delta":
    plots.append({"name": "Volume Delta", "data": point_series(delta_volume, delta_colors), "color": up_color, "type": plot_type(delta_style), "overlay": False})
elif display == "CRVOL":
    if cumulative_relative_style == "Line":
        plots.append({"name": "Cumulative Relative Volume", "data": point_series(cumulative_relative, relative_colors), "color": up_color, "type": "line", "overlay": False})
    else:
        candles = []
        for index in range(n):
            opening = cumulative_relative[index - 1] if index else 0.0
            closing = cumulative_relative[index]
            candles.append({
                "open": float(opening), "high": float(max(opening, closing)),
                "low": float(min(opening, closing)), "close": float(closing),
                "color": relative_colors[index], "fillColor": relative_colors[index],
                "borderColor": relative_colors[index], "wickColor": relative_colors[index],
            })
        plots.append({"name": "Cumulative Relative Volume", "data": candles, "type": "candlestick", "overlay": False})
else:
    if cumulative_delta_style == "Line":
        plots.append({"name": "Cumulative Volume Delta", "data": point_series(cumulative_delta, delta_colors), "color": up_color, "type": "line", "overlay": False})
    else:
        candles = []
        for index in range(n):
            opening = cumulative_delta[index - 1] if index else 0.0
            closing = cumulative_delta[index]
            candles.append({
                "open": float(opening), "high": float(max(opening, closing)),
                "low": float(min(opening, closing)), "close": float(closing),
                "color": delta_colors[index], "fillColor": delta_colors[index],
                "borderColor": delta_colors[index], "wickColor": delta_colors[index],
            })
        plots.append({"name": "Cumulative Volume Delta", "data": candles, "type": "candlestick", "overlay": False})

def moving_average(kind, length):
    if kind == "EMA":
        return ema(close, length)
    if kind == "VWMA":
        return vwma(close, volume, length)
    return sma(close, length)

price_average_audit = {}
average_settings = [
    (1, show_ma1, ma1_type, ma1_length, ma1_color),
    (2, show_ma2, ma2_type, ma2_length, ma2_color),
    (3, show_ma3, ma3_type, ma3_length, ma3_color),
    (4, show_ma4, ma4_type, ma4_length, ma4_color),
]
for slot, enabled, average_type, average_length, average_color in average_settings:
    values = moving_average(average_type, average_length)
    price_average_audit[f"priceAverage{slot}"] = [float(value) if np.isfinite(value) else None for value in values]
    if enabled:
        plots.append({"name": f"Price Average {slot}", "data": price_average_audit[f"priceAverage{slot}"], "color": average_color, "type": "line", "overlay": True})

vwap_values = np.full(n, np.nan, dtype=float)
vwap_upper = np.full(n, np.nan, dtype=float)
vwap_lower = np.full(n, np.nan, dtype=float)
vwap_reset = np.zeros(n, dtype=bool)
if show_vwap:
    typical = (high + low + close) / 3.0
    prior_group = None
    sum_weight = 0.0
    sum_price_weight = 0.0
    sum_square_weight = 0.0
    for index in range(n):
        day = int(time_seconds[index] // 86400)
        if vwap_anchor in ("1H", "4H", "12H"):
            anchor_seconds = {"1H": 3600, "4H": 14400, "12H": 43200}[vwap_anchor]
            group = int(time_seconds[index] // anchor_seconds)
        elif vwap_anchor == "1W":
            group = (day + 3) // 7
        elif vwap_anchor == "1M":
            shifted = day + 719468
            era = shifted // 146097 if shifted >= 0 else (shifted - 146096) // 146097
            day_of_era = shifted - era * 146097
            year_of_era = (day_of_era - day_of_era // 1460 + day_of_era // 36524 - day_of_era // 146096) // 365
            day_of_year = day_of_era - (365 * year_of_era + year_of_era // 4 - year_of_era // 100)
            month_phase = (5 * day_of_year + 2) // 153
            month = month_phase + 3 if month_phase < 10 else month_phase - 9
            year = year_of_era + era * 400 + (1 if month <= 2 else 0)
            group = year * 12 + month
        else:
            group = day
        if group != prior_group:
            vwap_reset[index] = True
            sum_weight = 0.0
            sum_price_weight = 0.0
            sum_square_weight = 0.0
            prior_group = group
        weight = max(0.0, volume[index])
        sum_weight += weight
        sum_price_weight += typical[index] * weight
        sum_square_weight += typical[index] * typical[index] * weight
        if sum_weight > 1e-12:
            vwap_values[index] = sum_price_weight / sum_weight
            variance = max(0.0, sum_square_weight / sum_weight - vwap_values[index] * vwap_values[index])
            deviation = np.sqrt(variance) * vwap_stdev
            vwap_upper[index] = vwap_values[index] + deviation
            vwap_lower[index] = vwap_values[index] - deviation
    plots.append({"name": "Anchored VWAP", "data": [float(vwap_values[index]) if np.isfinite(vwap_values[index]) and not vwap_reset[index] else None for index in range(n)], "color": vwap_color, "type": "line", "overlay": True})
    if show_vwap_bands:
        plots.append({"name": "VWAP Upper SD", "data": [float(vwap_upper[index]) if np.isfinite(vwap_upper[index]) and not vwap_reset[index] else None for index in range(n)], "color": vwap_color, "type": "line", "overlay": True})
        plots.append({"name": "VWAP Lower SD", "data": [float(vwap_lower[index]) if np.isfinite(vwap_lower[index]) and not vwap_reset[index] else None for index in range(n)], "color": vwap_color, "type": "line", "overlay": True})

coverage = int(np.sum(intrabar_counts > 0))
average_intrabars = float(np.mean(intrabar_counts[intrabar_counts > 0])) if coverage else 0.0
quality = "Intrabar" if coverage == n else "Hybrid estimate" if coverage else "Bar estimate"
tables = [{
    "title": "Volume Suite",
    "position": "bottom-right",
    "rows": [
        ["Display", display.replace("_", " ")],
        ["Lower TF", lower_timeframe],
        ["Covered bars", str(coverage) + " / " + str(n)],
        ["Avg intrabars", f"{average_intrabars:.1f}"],
        ["Data quality", quality],
    ],
    "compact": True,
}]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": [],
    "tables": tables,
    "bar_colors": host_colors if color_host_candles else [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "buyVolume": [float(value) for value in buy_volume],
        "sellVolume": [float(value) for value in sell_volume],
        "deltaVolume": [float(value) for value in delta_volume],
        "cumulativeDelta": [float(value) for value in cumulative_delta],
        "relativeVolume": [float(value) if np.isfinite(value) else None for value in relative_volume],
        "cumulativeRelative": [float(value) for value in cumulative_relative],
        "intrabarCounts": [int(value) for value in intrabar_counts],
        "coverageBars": int(coverage), "averageIntrabars": float(average_intrabars),
        "canRequestLower": bool(can_request_lower), "lowerTimeframe": lower_timeframe,
        "volumeZ": [float(value) if np.isfinite(value) else None for value in volume_z],
        "relativeZ": [float(value) if np.isfinite(value) else None for value in relative_z],
        "priceRangeZ": [float(value) if np.isfinite(value) else None for value in price_range_z],
        "volumeColors": volume_colors, "relativeColors": relative_colors, "deltaColors": delta_colors,
        "buyColors": buy_colors, "sellColors": sell_colors, "hostColors": host_colors,
        "vwap": [float(value) if np.isfinite(value) else None for value in vwap_values],
        "vwapUpper": [float(value) if np.isfinite(value) else None for value in vwap_upper],
        "vwapLower": [float(value) if np.isfinite(value) else None for value in vwap_lower],
        "vwapReset": vwap_reset.tolist(),
        "vwapAnchor": vwap_anchor,
        **price_average_audit,
    },
}
