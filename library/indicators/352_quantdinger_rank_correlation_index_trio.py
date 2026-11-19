# ============================================================
# QuantDinger Rank Correlation Index Trio
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Rank Correlation Index Trio"
my_indicator_description = "Plots short, medium, and long Spearman rank-correlation oscillators with configurable threshold lines."

# @param short_interval int 9 Short interval range=2:10000:1
# @param middle_interval int 36 Middle interval range=2:10000:1
# @param long_interval int 52 Long interval range=2:10000:1
# @param source str Close Price source values=Close,Open,High,Low,HL2,HLC3,OHLC4
# @param upper_line float 80.0 Upper threshold range=-100:100:1
# @param lower_line float -80.0 Lower threshold range=-100:100:1

short_interval = min(10000, max(2, int(params.get("short_interval", 9))))
middle_interval = min(10000, max(2, int(params.get("middle_interval", 36))))
long_interval = min(10000, max(2, int(params.get("long_interval", 52))))
source_name = str(params.get("source", "Close"))
upper_line = min(100.0, max(-100.0, float(params.get("upper_line", 80.0))))
lower_line = min(100.0, max(-100.0, float(params.get("lower_line", -80.0))))


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


def _source(name, opened, high, low, close):
    key = name.strip().upper()
    if key == "OPEN":
        return opened.copy()
    if key == "HIGH":
        return high.copy()
    if key == "LOW":
        return low.copy()
    if key == "HL2":
        return (high + low) / 2.0
    if key == "HLC3":
        return (high + low + close) / 3.0
    if key == "OHLC4":
        return (opened + high + low + close) / 4.0
    return close.copy()


def _average_ranks(values):
    order = sorted(range(len(values)), key=lambda index: (float(values[index]), index))
    ranks = np.empty(len(values), dtype=float)
    start = 0
    while start < len(order):
        end = start + 1
        while end < len(order) and values[order[end]] == values[order[start]]:
            end += 1
        average_rank = (start + 1 + end) / 2.0
        for position in range(start, end):
            ranks[order[position]] = average_rank
        start = end
    return ranks


def _rank_correlation(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    time_ranks = np.arange(1, length + 1, dtype=float)
    denominator = length * (length * length - 1.0)
    for index in range(length - 1, len(values)):
        window = values[index - length + 1:index + 1]
        if np.all(np.isfinite(window)):
            price_ranks = _average_ranks(window)
            result[index] = 100.0 * (1.0 - 6.0 * float(np.sum((time_ranks - price_ranks) ** 2)) / denominator)
    return result


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
source = _source(source_name, opened, high, low, close)
n = len(df)

short_rci = _rank_correlation(source, short_interval)
middle_rci = _rank_correlation(source, middle_interval)
long_rci = _rank_correlation(source, long_interval)

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [
        {"name": "RCI Short", "data": _finite(short_rci), "color": "#F23645", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "RCI Middle", "data": _finite(middle_rci), "color": "#2962FF", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "RCI Long", "data": _finite(long_rci), "color": "#089981", "type": "line", "lineWidth": 1, "overlay": False},
        {"name": "Upper Line", "data": [upper_line] * n, "color": "rgba(120,123,134,0.70)", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": False},
        {"name": "Lower Line", "data": [lower_line] * n, "color": "rgba(120,123,134,0.70)", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": False},
    ],
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "source": _finite(source),
        "shortRci": _finite(short_rci),
        "middleRci": _finite(middle_rci),
        "longRci": _finite(long_rci),
    },
}
