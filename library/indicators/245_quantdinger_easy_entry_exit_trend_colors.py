# ============================================================
# QuantDinger Easy Entry / Exit Trend Colors
# Independent Python implementation for the QuantDinger runtime.
# ============================================================

my_indicator_name = "QuantDinger Easy Entry / Exit Trend Colors"
my_indicator_description = (
    "Combines MACD volatility bands with short/long SMA regime crosses, "
    "pane events, and extreme MACD candle coloring."
)

# @param band_length int 10 Band period range=1:500:1
# @param deviation float 1.0 Band deviation range=0.0001:10.0:0.1
# @param fast_ema int 12 Fast EMA length range=1:500:1
# @param slow_ema int 26 Slow EMA length range=1:500:1
# @param signal_length int 9 Reserved signal length range=1:500:1
# @param alternate_scale int 10000 Reserved alternate-market scale range=1:1000000:1
# @param vertical_space int 30 Reserved vertical space percent range=10:100:10
# @param short_sma int 50 Short-term SMA length range=1:1000:1
# @param long_sma int 200 Long-term SMA length range=1:2000:1
# @param bullish_color str #00E676 Bullish color
# @param bearish_color str #F23645 Bearish color
# @param show_regime_events bool true Show SMA regime events

band_length = min(500, max(1, int(params.get("band_length", 10))))
deviation = min(10.0, max(0.0001, float(params.get("deviation", 1.0))))
fast_ema_length = min(500, max(1, int(params.get("fast_ema", 12))))
slow_ema_length = min(500, max(1, int(params.get("slow_ema", 26))))
signal_length = min(500, max(1, int(params.get("signal_length", 9))))
alternate_scale = min(1000000, max(1, int(params.get("alternate_scale", 10000))))
vertical_space = min(100, max(10, int(params.get("vertical_space", 30))))
short_sma_length = min(1000, max(1, int(params.get("short_sma", 50))))
long_sma_length = min(2000, max(1, int(params.get("long_sma", 200))))
bullish_color = str(params.get("bullish_color", "#00E676"))
bearish_color = str(params.get("bearish_color", "#F23645"))
show_regime_events = bool(params.get("show_regime_events", True))

df = df.copy()
close_values = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
n = len(df)


def finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def ema(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if not len(source):
        return result
    alpha = 2.0 / (float(length) + 1.0)
    state = float(source[0])
    result[0] = state
    for index in range(1, len(source)):
        state = alpha * float(source[index]) + (1.0 - alpha) * state
        result[index] = state
    return result


def sma(values, length):
    return pd.Series(values, dtype=float).rolling(length, min_periods=length).mean().to_numpy(dtype=float)


fast_ema = ema(close_values, fast_ema_length)
slow_ema = ema(close_values, slow_ema_length)
macd = fast_ema - slow_ema
short_average = sma(close_values, short_sma_length)
long_average = sma(close_values, long_sma_length)

golden_cross = np.zeros(n, dtype=bool)
death_cross = np.zeros(n, dtype=bool)
for index in range(1, n):
    if not all(np.isfinite(value) for value in (short_average[index], long_average[index], short_average[index - 1], long_average[index - 1])):
        continue
    crossed = (
        (long_average[index - 1] <= short_average[index - 1] and long_average[index] > short_average[index])
        or (long_average[index - 1] >= short_average[index - 1] and long_average[index] < short_average[index])
    )
    golden_cross[index] = crossed and short_average[index - 1] <= long_average[index - 1]
    death_cross[index] = crossed and long_average[index - 1] <= short_average[index - 1]

macd_average = sma(macd, band_length)
macd_deviation = pd.Series(macd, dtype=float).rolling(band_length, min_periods=band_length).std(ddof=0).to_numpy(dtype=float)
upper_band = macd_average + macd_deviation * deviation
lower_band = macd_average - macd_deviation * deviation

bear_band_cross = np.zeros(n, dtype=bool)
bull_band_cross = np.zeros(n, dtype=bool)
for index in range(1, n):
    if not all(np.isfinite(value) for value in (macd[index], macd[index - 1], upper_band[index], upper_band[index - 1])):
        continue
    crossed = (
        (macd[index - 1] <= upper_band[index - 1] and macd[index] > upper_band[index])
        or (macd[index - 1] >= upper_band[index - 1] and macd[index] < upper_band[index])
    )
    bull_band_cross[index] = crossed and macd[index] > upper_band[index]
    bear_band_cross[index] = crossed and macd[index] < upper_band[index]

green_triangle = np.isfinite(upper_band) & (macd >= upper_band)
red_triangle = np.isfinite(upper_band) & (macd <= upper_band)
above_extreme = np.isfinite(upper_band) & (macd > upper_band)
below_extreme = np.isfinite(lower_band) & (macd < lower_band)
bar_colors = ["#FFFF00" if above_extreme[index] else "#00FFFF" if below_extreme[index] else None for index in range(n)]

band_cloud = [
    {"upper": float(upper_band[index]), "lower": float(lower_band[index]), "color": "rgba(41,98,255,0.25)"}
    if np.isfinite(upper_band[index]) and np.isfinite(lower_band[index]) else None for index in range(n)
]
green_points = [
    {"value": float(macd[index]), "color": bullish_color, "size": 2}
    if green_triangle[index] else None for index in range(n)
]
red_points = [
    {"value": float(macd[index]), "color": bearish_color, "size": 2}
    if red_triangle[index] else None for index in range(n)
]
bull_cross_points = [
    {"value": float((macd[index] + upper_band[index]) * 0.5), "color": bullish_color, "size": 5}
    if bull_band_cross[index] else None for index in range(n)
]
bear_cross_points = [
    {"value": float((macd[index] + upper_band[index]) * 0.5), "color": bearish_color, "size": 5}
    if bear_band_cross[index] else None for index in range(n)
]

finite_band_values = np.concatenate((upper_band[np.isfinite(upper_band)], lower_band[np.isfinite(lower_band)]))
pane_top = float(np.max(finite_band_values)) if len(finite_band_values) else 1.0
pane_bottom = float(np.min(finite_band_values)) if len(finite_band_values) else -1.0
golden_points = [
    {"value": pane_bottom, "color": bullish_color, "size": 6}
    if show_regime_events and golden_cross[index] else None for index in range(n)
]
death_points = [
    {"value": pane_top, "color": "#880E4F", "size": 6}
    if show_regime_events and death_cross[index] else None for index in range(n)
]

plots = [
    {"name": "Upper Band", "data": finite(upper_band), "color": "#9CA3AF", "type": "line", "lineWidth": 2, "overlay": False},
    {"name": "Lower Band", "data": finite(lower_band), "color": "#9CA3AF", "type": "line", "lineWidth": 2, "overlay": False},
    {"name": "Band Fill", "title": False, "data": band_cloud, "type": "cloud", "overlay": False},
    {"name": "Zero Line", "title": False, "data": [0.0 for _ in range(n)], "color": "#F59E0B", "type": "line", "lineWidth": 1, "overlay": False},
    {"name": "Bull MACD Cross", "title": False, "data": bull_cross_points, "color": bullish_color, "type": "scatter", "lineWidth": 5, "overlay": False},
    {"name": "Bear MACD Cross", "title": False, "data": bear_cross_points, "color": bearish_color, "type": "scatter", "lineWidth": 5, "overlay": False},
    {"name": "Green MACD Triangles", "title": False, "data": green_points, "color": bullish_color, "type": "scatter", "lineWidth": 2, "overlay": False},
    {"name": "Red MACD Triangles", "title": False, "data": red_points, "color": bearish_color, "type": "scatter", "lineWidth": 2, "overlay": False},
    {"name": "Golden Cross", "title": False, "data": golden_points, "color": bullish_color, "type": "scatter", "lineWidth": 6, "overlay": False},
    {"name": "Death Cross", "title": False, "data": death_points, "color": "#880E4F", "type": "scatter", "lineWidth": 6, "overlay": False},
]

signals = [
    {"type": "buy", "name": "Golden Cross", "text": "GOLDEN", "color": bullish_color, "renderMode": "hidden", "data": [float(close_values[index]) if golden_cross[index] else None for index in range(n)]},
    {"type": "sell", "name": "Death Cross", "text": "DEATH", "color": "#880E4F", "renderMode": "hidden", "data": [float(close_values[index]) if death_cross[index] else None for index in range(n)]},
    {"type": "buy", "name": "Bull MACD", "text": "BULL", "color": bullish_color, "renderMode": "hidden", "data": [float(close_values[index]) if bull_band_cross[index] else None for index in range(n)]},
    {"type": "sell", "name": "Bear MACD", "text": "BEAR", "color": bearish_color, "renderMode": "hidden", "data": [float(close_values[index]) if bear_band_cross[index] else None for index in range(n)]},
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": [],
    "tables": [],
    "bar_colors": bar_colors,
    "background_colors": [None for _ in range(n)],
    "calculatedVars": {
        "fastEma": finite(fast_ema), "slowEma": finite(slow_ema), "macd": finite(macd),
        "shortSma": finite(short_average), "longSma": finite(long_average),
        "goldenCross": golden_cross.tolist(), "deathCross": death_cross.tolist(),
        "macdAverage": finite(macd_average), "macdDeviation": finite(macd_deviation),
        "upperBand": finite(upper_band), "lowerBand": finite(lower_band),
        "bullBandCross": bull_band_cross.tolist(), "bearBandCross": bear_band_cross.tolist(),
        "greenTriangle": green_triangle.tolist(), "redTriangle": red_triangle.tolist(),
        "aboveExtreme": above_extreme.tolist(), "belowExtreme": below_extreme.tolist(),
        "reservedSignalLength": int(signal_length), "reservedAlternateScale": int(alternate_scale),
        "reservedVerticalSpace": int(vertical_space),
    },
}
