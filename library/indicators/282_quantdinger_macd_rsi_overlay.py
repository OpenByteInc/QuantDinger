# ============================================================
# QuantDinger MACD RSI Overlay
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger MACD RSI Overlay"
my_indicator_description = "Projects adaptive MACD- and RSI-style price lines onto the chart with cross alerts and a compact trend table."

# @param display_mode str MACD Display values=MACD,RSI 14,Both
# @param smoothing_length int 1 Line smoothing range=1:200:1
# @param signal_length int 14 Signal length range=5:200:1
# @param macd_color color #00FFFF MACD line color
# @param rsi_color color #1360EE RSI line color
# @param signal_color color #F8DE7E Signal line color
# @param overbought int 70 RSI overbought level range=0:100:1
# @param oversold int 30 RSI oversold level range=0:100:1
# @param overbought_color color #00FF00 Overbought line color
# @param oversold_color color #FF0000 Oversold line color
# @param trend_factor int 30 Underlying trend length range=1:300:1
# @param scaling_factor float 2.5 Price projection scale range=0:10:0.1
# @param show_trend_table bool true Show trend table
# @param table_size str Normal Table text size values=Auto,Tiny,Small,Normal,Large,Huge
# @param table_position str Top-Right Table position values=Top-Right,Top-Center,Top-Left,Bottom-Right,Bottom-Center,Bottom-Left,Middle-Right,Middle-Left

display_mode = str(params.get("display_mode", "MACD"))
smoothing_length = min(200, max(1, int(params.get("smoothing_length", 1))))
signal_length = min(200, max(5, int(params.get("signal_length", 14))))
macd_color = str(params.get("macd_color", "#00FFFF"))
rsi_color = str(params.get("rsi_color", "#1360EE"))
signal_color = str(params.get("signal_color", "#F8DE7E"))
overbought = min(100, max(0, int(params.get("overbought", 70))))
oversold = min(100, max(0, int(params.get("oversold", 30))))
overbought_color = str(params.get("overbought_color", "#00FF00"))
oversold_color = str(params.get("oversold_color", "#FF0000"))
trend_factor = min(300, max(1, int(params.get("trend_factor", 30))))
scaling_factor = min(10.0, max(0.0, float(params.get("scaling_factor", 2.5))))
show_trend_table = bool(params.get("show_trend_table", True))
table_size = str(params.get("table_size", "Normal"))
table_position = str(params.get("table_position", "Top-Right")).lower()

df = df.copy()
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, period):
    return pd.Series(values).rolling(period, min_periods=period).mean().to_numpy(dtype=float)


def _wma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    weights = np.arange(1, period + 1, dtype=float)
    divisor = float(np.sum(weights))
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        if np.all(np.isfinite(window)):
            result[index] = float(np.dot(window, weights) / divisor)
    return result


def _rma(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    finite_buffer = []
    previous = np.nan
    for index, value in enumerate(values):
        if not np.isfinite(value):
            continue
        if not np.isfinite(previous):
            finite_buffer.append(float(value))
            if len(finite_buffer) < period:
                continue
            previous = float(np.mean(finite_buffer[-period:]))
        else:
            previous = (previous * (period - 1.0) + float(value)) / period
        result[index] = previous
    return result


def _rsi(values, period):
    result = np.full(len(values), np.nan, dtype=float)
    if len(values) <= period:
        return result
    changes = np.diff(values)
    gains = np.maximum(changes, 0.0)
    losses = np.maximum(-changes, 0.0)
    average_gain = float(np.mean(gains[:period])); average_loss = float(np.mean(losses[:period]))
    for index in range(period, len(values)):
        if index > period:
            average_gain = (average_gain * (period - 1.0) + gains[index - 1]) / period
            average_loss = (average_loss * (period - 1.0) + losses[index - 1]) / period
        result[index] = 50.0 if average_gain == 0.0 and average_loss == 0.0 else 100.0 if average_loss == 0.0 else 0.0 if average_gain == 0.0 else 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return result


def _crosses(first, second):
    over = np.zeros(len(first), dtype=bool); under = np.zeros(len(first), dtype=bool)
    for index in range(1, len(first)):
        if np.all(np.isfinite([first[index], second[index], first[index - 1], second[index - 1]])):
            over[index] = first[index] > second[index] and first[index - 1] <= second[index - 1]
            under[index] = first[index] < second[index] and first[index - 1] >= second[index - 1]
    return over, under


price_average = _sma(close, 2)
trend_average = _sma(close, trend_factor)
error = np.abs(price_average - trend_average)
error_sum = pd.Series(error).rolling(10, min_periods=10).sum().to_numpy(dtype=float)
adaptive_average = np.full(n, np.nan, dtype=float)
for index in range(n):
    if not np.all(np.isfinite([price_average[index], trend_average[index], error_sum[index]])):
        continue
    if error_sum[index] == 0.0:
        adaptive_average[index] = close[index - 1] if index > 0 else np.nan
    else:
        weight = error[index] / error_sum[index]
        adaptive_average[index] = price_average[index] * weight + trend_average[index] * (1.0 - weight)

projected_average = np.full(n, np.nan, dtype=float)
for index in range(1, n):
    if np.isfinite(adaptive_average[index - 1]):
        projected_average[index] = scaling_factor * close[index] + (1.0 - scaling_factor) * adaptive_average[index - 1]

macd_line = _wma(_rma(projected_average, 5), smoothing_length)
rsi_line = _wma(_rma(projected_average, 1), smoothing_length)
signal_line = _sma(projected_average, signal_length)
price_rsi = _rsi(close, 14)
rsi_colors = [overbought_color if np.isfinite(value) and value > overbought else oversold_color if np.isfinite(value) and value < oversold else rsi_color for value in price_rsi]

macd_signal_over, macd_signal_under = _crosses(macd_line, signal_line)
rsi_signal_over, rsi_signal_under = _crosses(rsi_line, signal_line)
price_macd_over, price_macd_under = _crosses(close, macd_line)
price_rsi_over, price_rsi_under = _crosses(close, rsi_line)
rsi_macd_over, rsi_macd_under = _crosses(rsi_line, macd_line)

macd_visible = display_mode in ("MACD", "Both")
rsi_visible = display_mode in ("RSI 14", "Both")
plots = [
    {"name": "MACD Line", "data": _finite(macd_line) if macd_visible else [None] * n, "color": macd_color, "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "RSI Line", "data": _finite(rsi_line) if rsi_visible else [None] * n, "color": rsi_colors, "type": "line", "lineWidth": 1, "overlay": True},
    {"name": "Signal Line", "data": _finite(signal_line), "color": signal_color, "type": "line", "lineWidth": 1, "overlay": True},
]

alert_definitions = [
    ("MACD Crosses Over Signal Line", "buy", macd_signal_over, macd_line), ("MACD Crosses Under Signal Line", "sell", macd_signal_under, macd_line),
    ("RSI Crosses Over Signal Line", "buy", rsi_signal_over, rsi_line), ("RSI Crosses Under Signal Line", "sell", rsi_signal_under, rsi_line),
    ("Close Crosses Over MACD", "buy", price_macd_over, close), ("Close Crosses Under MACD", "sell", price_macd_under, close),
    ("Close Crosses Over RSI", "buy", price_rsi_over, close), ("Close Crosses Under RSI", "sell", price_rsi_under, close),
    ("RSI Crosses Over MACD", "buy", rsi_macd_over, rsi_line), ("RSI Crosses Under MACD", "sell", rsi_macd_under, rsi_line),
]
signals = [{
    "type": signal_type, "name": name, "text": "", "data": [float(values[index]) if events[index] and np.isfinite(values[index]) else None for index in range(n)],
    "color": "rgba(0,0,0,0)", "renderMode": "hidden",
} for name, signal_type, events, values in alert_definitions]

latest_macd_bullish = bool(n and np.isfinite(macd_line[-1]) and macd_line[-1] > close[-1])
latest_rsi_bullish = bool(n and np.isfinite(rsi_line[-1]) and close[-1] < rsi_line[-1])
if display_mode == "MACD":
    trend_text = "MACD: " + ("Bullish" if latest_macd_bullish else "Bearish")
elif display_mode == "RSI 14":
    trend_text = "RSI: " + ("Bullish" if latest_rsi_bullish else "Bearish")
else:
    trend_text = "MACD: " + ("Bullish" if latest_macd_bullish else "Bearish") + "\nRSI: " + ("Bullish" if latest_rsi_bullish else "Bearish")
mixed = "Bullish" in trend_text and "Bearish" in trend_text
trend_color = "#FFA500" if mixed else "#00FF00" if "Bullish" in trend_text else "#FF0000"
tables = []
if show_trend_table:
    tables.append({"title": "Trend", "position": table_position, "size": table_size.lower(), "rows": [[trend_text, ""]], "backgroundColor": trend_color, "opacity": 0.70, "compact": True})

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": signals, "layers": [], "tables": tables,
    "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "priceAverage": _finite(price_average), "trendAverage": _finite(trend_average), "error": _finite(error), "errorSum": _finite(error_sum),
        "adaptiveAverage": _finite(adaptive_average), "projectedAverage": _finite(projected_average), "macd": _finite(macd_line), "rsi": _finite(rsi_line),
        "signal": _finite(signal_line), "priceRsi": _finite(price_rsi), "rsiColors": rsi_colors,
        "macdSignalOver": macd_signal_over.tolist(), "macdSignalUnder": macd_signal_under.tolist(),
        "rsiSignalOver": rsi_signal_over.tolist(), "rsiSignalUnder": rsi_signal_under.tolist(),
        "priceMacdOver": price_macd_over.tolist(), "priceMacdUnder": price_macd_under.tolist(),
        "priceRsiOver": price_rsi_over.tolist(), "priceRsiUnder": price_rsi_under.tolist(),
        "rsiMacdOver": rsi_macd_over.tolist(), "rsiMacdUnder": rsi_macd_under.tolist(), "trendText": trend_text, "trendColor": trend_color,
    },
}
