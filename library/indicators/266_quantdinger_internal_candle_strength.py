# ============================================================
# QuantDinger Internal Candle Strength
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Internal Candle Strength"
my_indicator_description = (
    "Splits each host candle into price rows and colors each row by the signed "
    "body strength of lower-timeframe candles that crossed it."
)

indicator_lower_timeframes = ["1m", "3m", "5m", "15m", "30m", "1H"]
indicator_lower_timeframe_param = "lower_timeframe"

# @param lower_timeframe str 1m Intrabar timeframe values=1m,3m,5m,15m,30m,1H
# @param row_size float 20.0 Manual row size range=0.000001:1000000.0:0.1
# @param automatic_size bool true Use ten percent of ATR 200
# @param show_table bool true Show row-size table

lower_timeframe = str(params.get("lower_timeframe", "1m"))
if lower_timeframe not in indicator_lower_timeframes:
    lower_timeframe = "1m"
row_size = min(1000000.0, max(0.000001, float(params.get("row_size", 20.0))))
automatic_size = bool(params.get("automatic_size", True))
show_table = bool(params.get("show_table", True))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
if "time" not in df.columns:
    df["time"] = np.arange(len(df), dtype=float) * 3600.0
times = df["time"].to_numpy(dtype=float)
n = len(df)
if n and np.nanmax(np.abs(times)) < 100000000000.0:
    times = times * 1000.0
positive_intervals = np.diff(times)
positive_intervals = positive_intervals[np.isfinite(positive_intervals) & (positive_intervals > 0.0)]
bar_interval = float(np.median(positive_intervals)) if len(positive_intervals) else 3600000.0

true_range = np.full(n, np.nan, dtype=float)
for index in range(n):
    if index == 0:
        true_range[index] = high[index] - low[index]
    else:
        true_range[index] = max(
            high[index] - low[index],
            abs(high[index] - close[index - 1]),
            abs(low[index] - close[index - 1]),
        )

atr_200 = np.full(n, np.nan, dtype=float)
if n >= 200:
    atr_200[199] = float(np.mean(true_range[:200]))
    for index in range(200, n):
        atr_200[index] = (atr_200[index - 1] * 199.0 + true_range[index]) / 200.0
optimal_size = atr_200 * 0.1
parsed_size = optimal_size.copy() if automatic_size else np.full(n, row_size, dtype=float)

try:
    intrabars = request_lower_tf(lower_timeframe, fields=["open", "high", "low", "close"])
except Exception:
    intrabars = {}
intrabar_open = intrabars.get("open", [[] for _ in range(n)]) if isinstance(intrabars, dict) else [[] for _ in range(n)]
intrabar_high = intrabars.get("high", [[] for _ in range(n)]) if isinstance(intrabars, dict) else [[] for _ in range(n)]
intrabar_low = intrabars.get("low", [[] for _ in range(n)]) if isinstance(intrabars, dict) else [[] for _ in range(n)]
intrabar_close = intrabars.get("close", [[] for _ in range(n)]) if isinstance(intrabars, dict) else [[] for _ in range(n)]


def _gradient(strength):
    ratio = min(1.0, max(0.0, float(strength) / 100.0))
    red = int(round(255 + (0 - 255) * ratio))
    green = int(round(82 + (137 - 82) * ratio))
    blue = int(round(82 + (123 - 82) * ratio))
    return f"#{red:02X}{green:02X}{blue:02X}"


row_counts = np.zeros(n, dtype=int)
intrabar_counts = np.zeros(n, dtype=int)
row_strengths = [[] for _ in range(n)]
row_records = []

for index in range(n):
    size = parsed_size[index]
    if not np.isfinite(size) or size <= 0.0 or not np.isfinite(high[index]) or not np.isfinite(low[index]):
        continue
    candle_range = high[index] - low[index]
    rows = max(int(np.floor(candle_range / size + 0.5)), 1)
    row_counts[index] = rows
    opens = intrabar_open[index] if index < len(intrabar_open) else []
    highs = intrabar_high[index] if index < len(intrabar_high) else []
    lows = intrabar_low[index] if index < len(intrabar_low) else []
    closes = intrabar_close[index] if index < len(intrabar_close) else []
    count = min(len(opens), len(highs), len(lows), len(closes))
    intrabar_counts[index] = count
    step = candle_range / rows if rows else 0.0
    row_bottom = float(low[index])
    for row in range(rows):
        row_top = float(low[index] + step * (row + 1))
        numerator = 0.0
        denominator = 0.0
        for cursor in range(count):
            intrabar_high_value = float(highs[cursor])
            intrabar_low_value = float(lows[cursor])
            if intrabar_high_value > row_bottom and intrabar_low_value < row_top:
                difference = float(closes[cursor]) - float(opens[cursor])
                numerator += difference
                denominator += abs(difference)
        if denominator <= 0.0:
            row_bottom = row_top
            continue
        strength = 50.0 * (numerator / denominator) + 50.0
        row_strengths[index].append(float(strength))
        row_records.append({
            "index": index, "row": row, "top": row_top,
            "bottom": row_bottom, "strength": float(strength),
            "color": _gradient(strength),
        })
        row_bottom = row_top

sampled_indexes = sorted({int(record["index"]) for record in row_records})
display_indexes = set(sampled_indexes[-24:])
row_records = [record for record in row_records if int(record["index"]) in display_indexes][-200:]
label_keys = {(int(record["index"]), int(record["row"])) for record in row_records[-12:]}
layers = []
for record in row_records:
    index = record["index"]
    start_time = float(times[index])
    end_time = float(times[index] + bar_interval)
    middle_time = start_time + bar_interval * 0.5
    middle_price = (record["top"] + record["bottom"]) * 0.5
    layers.extend([
        {
            "type": "zone", "role": "Internal Strength Row",
            "start": start_time, "end": end_time,
            "top": record["top"], "bottom": record["bottom"],
            "fillColor": "rgba(93,96,107,0.20)",
            "borderColor": "rgba(0,0,0,0)", "borderSize": 0,
        },
        {
            "type": "line", "role": "Internal Strength Boundary",
            "start": start_time, "end": end_time,
            "y1": record["bottom"], "y2": record["bottom"],
            "color": record["color"], "lineWidth": 1,
        },
    ])
    if (int(index), int(record["row"])) in label_keys:
        layers.append({
            "type": "label", "role": "Internal Strength Value",
            "timestamp": middle_time, "price": middle_price,
            "text": f"{record['strength'] / 100.0:.0%}", "anchor": "center",
            "color": "rgba(0,0,0,0)", "borderColor": "rgba(0,0,0,0)",
            "textColor": record["color"], "fontSize": 8,
        })

if row_records:
    last_record = row_records[-1]
    layers.append({
        "type": "line", "role": "Internal Strength Top Boundary",
        "start": float(times[last_record["index"]]),
        "end": float(times[last_record["index"]] + bar_interval),
        "y1": last_record["top"], "y2": last_record["top"],
        "color": last_record["color"], "lineWidth": 1,
    })

candles = []
for index in range(n):
    wick_color = "#00897B" if close[index] > open_price[index] else "#FF5252"
    candles.append({
        "open": float(open_price[index]),
        "high": float(max(close[index], open_price[index])),
        "low": float(min(close[index], open_price[index])),
        "close": float(open_price[index]),
        "color": "rgba(0,0,0,0)", "fillColor": "rgba(0,0,0,0)",
        "borderColor": "rgba(0,0,0,0)", "wickColor": wick_color,
    })

tables = []
if show_table:
    current_size = parsed_size[-1] if n and np.isfinite(parsed_size[-1]) else np.nan
    current_optimal = optimal_size[-1] if n and np.isfinite(optimal_size[-1]) else np.nan
    tables.append({
        "title": "Internal Candle Strength",
        "position": "bottom-right",
        "rows": [
            ["Current Row Size", f"{current_size:.6g}" if np.isfinite(current_size) else "Warming up"],
            ["Optimal Row Size", f"{current_optimal:.6g}" if np.isfinite(current_optimal) else "Warming up"],
            ["Lower TF Coverage", f"{int(np.sum(intrabar_counts > 0))} / {n}"],
        ],
        "compact": True,
    })


def _finite_or_none(values):
    return [float(value) if np.isfinite(value) else None for value in values]


output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": [{"name": "Internal Direction Wick", "data": candles, "type": "candlestick", "overlay": True}],
    "signals": [],
    "layers": layers,
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "trueRange": _finite_or_none(true_range), "atr200": _finite_or_none(atr_200),
        "optimalSize": _finite_or_none(optimal_size), "parsedSize": _finite_or_none(parsed_size),
        "rowCounts": row_counts.tolist(), "intrabarCounts": intrabar_counts.tolist(),
        "rowStrengths": row_strengths, "retainedRows": row_records,
        "barInterval": bar_interval,
    },
}
