# ============================================================
# QuantDinger Rank Correlation Trio
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Rank Correlation Trio"
my_indicator_description = (
    "Three Spearman rank-correlation momentum curves on short, medium, and "
    "long lookbacks with configurable extreme levels."
)

# @param short_length int 9 Short interval range=2:100:1
# @param medium_length int 36 Medium interval range=2:150:1
# @param long_length int 52 Long interval range=2:200:1
# @param upper_level float 80.0 Upper level range=0.0:100.0:5.0
# @param lower_level float -80.0 Lower level range=-100.0:0.0:5.0

short_length = max(2, int(params.get("short_length", 9)))
medium_length = max(2, int(params.get("medium_length", 36)))
long_length = max(2, int(params.get("long_length", 52)))
upper_level = float(params.get("upper_level", 80.0))
lower_level = float(params.get("lower_level", -80.0))

df = df.copy()
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def rank_correlation(values, period):
    result = np.full(n, np.nan, dtype=float)
    denominator = float(period * (period * period - 1))
    for index in range(period - 1, n):
        start = index - period + 1
        squared_distance = 0.0
        for position in range(period):
            value = values[start + position]
            less_count = 0
            equal_count = 0
            for comparison in range(period):
                other = values[start + comparison]
                if other < value:
                    less_count += 1
                elif other == value:
                    equal_count += 1
            price_rank = less_count + (equal_count + 1.0) / 2.0
            time_rank = float(position + 1)
            difference = time_rank - price_rank
            squared_distance += difference * difference
        result[index] = (1.0 - 6.0 * squared_distance / denominator) * 100.0
    return result


short_rci = rank_correlation(close, short_length)
medium_rci = rank_correlation(close, medium_length)
long_rci = rank_correlation(close, long_length)

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "RCI Short", "data": [float(value) if np.isfinite(value) else None for value in short_rci], "color": "#EF4444", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "RCI Medium", "data": [float(value) if np.isfinite(value) else None for value in medium_rci], "color": "#2962FF", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "RCI Long", "data": [float(value) if np.isfinite(value) else None for value in long_rci], "color": "#16A34A", "type": "line", "lineWidth": 2, "overlay": False},
        {"name": "Upper Level", "data": [float(upper_level) for _ in range(n)], "color": "#808080", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": False},
        {"name": "Lower Level", "data": [float(lower_level) for _ in range(n)], "color": "#808080", "type": "line", "lineWidth": 1, "lineStyle": "dashed", "overlay": False},
    ],
    "signals": [],
    "layers": [],
}
