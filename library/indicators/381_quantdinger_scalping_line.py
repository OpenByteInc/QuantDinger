# ============================================================
# QuantDinger Scalping Line
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Scalping Line"
my_indicator_description = "Measures a short signal average against a dead-zone filtered double-smoothed baseline and marks zero-line reversals."

# @param source str close Source values=close,open,high,low,hl2,hlc3,ohlc4
# @param percent float 1.0 Baseline dead-zone percent range=0:100:0.1
# @param main_period int 100 Main smoothing period range=1:1000:1
# @param signal_period int 7 Signal smoothing period range=1:1000:1
# @param positive_color str #008000 Positive fill color
# @param negative_color str #FF0000 Negative fill color
# @param line_color str #800000 Scalping line color

source_name = str(params.get("source", "close")).lower()
percent = min(100.0, max(0.0, float(params.get("percent", 1.0))))
main_period = min(1000, max(1, int(params.get("main_period", 100))))
signal_period = min(1000, max(1, int(params.get("signal_period", 7))))
positive_color = str(params.get("positive_color", "#008000"))
negative_color = str(params.get("negative_color", "#FF0000"))
line_color = str(params.get("line_color", "#800000"))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    values = np.asarray(values, dtype=float)
    valid = np.isfinite(values).astype(int)
    clean = np.where(np.isfinite(values), values, 0.0)
    sums = np.cumsum(clean)
    counts = np.cumsum(valid)
    for index in range(length - 1, len(values)):
        total = sums[index] - (sums[index - length] if index >= length else 0.0)
        count = counts[index] - (counts[index - length] if index >= length else 0)
        if count == length:
            result[index] = total / length
    return result


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
    "close": close, "open": opened, "high": high, "low": low,
    "hl2": (high + low) / 2.0,
    "hlc3": (high + low + close) / 3.0,
    "ohlc4": (opened + high + low + close) / 4.0,
}
source_name = source_name if source_name in sources else "close"
source = sources[source_name]
first_length = int(np.ceil(main_period / 2.0))
second_length = int(np.floor(main_period / 2.0)) + 1
first_average = _sma(source, first_length)
baseline = _sma(first_average, second_length)
upper_dead_zone = close + baseline * percent / 100.0
lower_dead_zone = close - baseline * percent / 100.0
filtered_baseline = np.where(
    np.isfinite(baseline),
    np.where((baseline > upper_dead_zone) | (baseline < lower_dead_zone), baseline, close),
    np.nan,
)
signal_average = _sma(close, signal_period)
scalping_line = signal_average - filtered_baseline

buy = np.zeros(n, dtype=bool)
sell = np.zeros(n, dtype=bool)
for index in range(1, n):
    if np.all(np.isfinite([scalping_line[index - 1], scalping_line[index]])):
        buy[index] = scalping_line[index] > 0.0 and scalping_line[index - 1] <= 0.0
        sell[index] = scalping_line[index] < 0.0 and scalping_line[index - 1] >= 0.0

state_fill = [
    {"upper": float(max(value, 0.0)), "lower": float(min(value, 0.0)), "color": positive_color if value >= 0.0 else negative_color, "opacity": 0.20}
    if np.isfinite(value) else None
    for value in scalping_line
]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "Scalping Line", "title": True, "data": _finite(scalping_line), "color": line_color, "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Zero", "title": True, "data": [0.0] * n, "color": "#808080", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Direction Fill", "title": False, "data": state_fill, "color": positive_color, "type": "cloud", "overlay": False},
    ],
    "signals": [
        {"type": "buy", "name": "Positive Cross", "text": "BUY", "data": [{"active": True, "price": float(scalping_line[index]), "text": "BUY", "color": positive_color} if buy[index] else None for index in range(n)], "color": positive_color, "renderMode": "events", "overlay": False},
        {"type": "sell", "name": "Negative Cross", "text": "SELL", "data": [{"active": True, "price": float(scalping_line[index]), "text": "SELL", "color": negative_color} if sell[index] else None for index in range(n)], "color": negative_color, "renderMode": "events", "overlay": False},
    ],
    "layers": [], "tables": [], "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "source": _finite(source), "firstLength": int(first_length), "secondLength": int(second_length),
        "firstAverage": _finite(first_average), "baseline": _finite(baseline),
        "upperDeadZone": _finite(upper_dead_zone), "lowerDeadZone": _finite(lower_dead_zone),
        "filteredBaseline": _finite(filtered_baseline), "signalAverage": _finite(signal_average),
        "scalpingLine": _finite(scalping_line), "buy": buy.tolist(), "sell": sell.tolist(),
    },
}
