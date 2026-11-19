# ============================================================
# QuantDinger Volume Clusters
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Volume Clusters"
my_indicator_description = "Builds fixed fifteen-row volume-density profiles for consecutive bar blocks, alternating colors and marking each block's volume point of control."

# @param block_size int 15 Bars per profile block range=10:166:1
# @param visual_row_width float 10.0 Visual row width range=10:200:1
# @param show_vpoc bool true Show volume point of control
# @param filter_size int 200 Maximum-density EMA filter length range=1:2000:1
# @param use_custom_volume_source bool false Use df custom_volume column when available

block_size = min(166, max(10, int(params.get("block_size", 15))))
visual_row_width = max(10.0, float(params.get("visual_row_width", 10.0)))
show_vpoc = bool(params.get("show_vpoc", True))
filter_size = max(1, int(params.get("filter_size", 200)))
use_custom_volume_source = bool(params.get("use_custom_volume_source", False))
row_count = 15


def _finite(values):
    return [float(value) if np.isfinite(value) else None for value in values]


df = df.copy().reset_index(drop=True)
for column in ("time", "open", "high", "low", "close", "volume"):
    if column not in df.columns:
        df[column] = 0.0
    df[column] = pd.to_numeric(df[column], errors="coerce")
if "custom_volume" in df.columns:
    df["custom_volume"] = pd.to_numeric(df["custom_volume"], errors="coerce")
df = df.dropna(subset=["time", "open", "high", "low", "close", "volume"]).reset_index(drop=True)
high = df["high"].to_numpy(dtype=float)
low = df["low"].to_numpy(dtype=float)
close = df["close"].to_numpy(dtype=float)
volume = df["volume"].to_numpy(dtype=float)
custom_volume_available = "custom_volume" in df.columns and bool(np.any(np.isfinite(df["custom_volume"].to_numpy(dtype=float))))
if use_custom_volume_source and custom_volume_available:
    candidate_volume = df["custom_volume"].to_numpy(dtype=float)
    profile_volume = np.where(np.isfinite(candidate_volume), candidate_volume, volume)
else:
    profile_volume = volume.copy()
n = len(df)

blocks = []
maximum_density_series = np.full(n, np.nan, dtype=float)
current_maximum_density = np.nan
for start in range(0, n, block_size):
    end = min(n - 1, start + block_size - 1)
    if end < start:
        continue
    block_high = float(np.max(high[start:end + 1]))
    block_low = float(np.min(low[start:end + 1]))
    block_height = block_high - block_low
    row_height = block_height / row_count
    row_values = np.zeros(row_count, dtype=float)
    row_prices = np.full(row_count, np.nan, dtype=float)
    if row_height > 0.0:
        row_lows = block_low + np.arange(row_count, dtype=float) * row_height
        row_highs = row_lows + row_height
        row_prices = (row_lows + row_highs) / 2.0
        for bar_index in range(start, end + 1):
            affected = [
                row_index for row_index in range(row_count)
                if high[bar_index] > row_lows[row_index] and low[bar_index] < row_highs[row_index]
            ]
            if affected:
                contribution = profile_volume[bar_index] / len(affected) / row_height
                for row_index in affected:
                    row_values[row_index] += contribution
    maximum_density = float(np.max(row_values)) if len(row_values) else 0.0
    vpoc_index = None
    running_maximum = 0.0
    for row_index, row_value in enumerate(row_values):
        if running_maximum < row_value:
            running_maximum = float(row_value)
            vpoc_index = int(row_index)
    vpoc_price = float(row_prices[vpoc_index]) if vpoc_index is not None and np.isfinite(row_prices[vpoc_index]) else None
    current_maximum_density = maximum_density
    maximum_density_series[start:end + 1] = current_maximum_density
    blocks.append({
        "blockIndex": int(start // block_size), "startIndex": int(start), "endIndex": int(end),
        "high": block_high, "low": block_low, "rowHeight": float(row_height),
        "rowPrices": _finite(row_prices), "rowValues": [float(value) for value in row_values],
        "maximumDensity": maximum_density, "vpocIndex": vpoc_index, "vpocPrice": vpoc_price,
    })

maximum_density_average = np.full(n, np.nan, dtype=float)
alpha = 2.0 / (filter_size + 1.0)
previous_average = np.nan
for index, value in enumerate(maximum_density_series):
    if np.isfinite(value):
        previous_average = float(value) if not np.isfinite(previous_average) else alpha * value + (1.0 - alpha) * previous_average
        maximum_density_average[index] = previous_average

for block in blocks:
    baseline = maximum_density_average[block["endIndex"]]
    widths = []
    for value in block["rowValues"]:
        width = int(np.floor(visual_row_width * value / baseline)) if np.isfinite(baseline) and baseline > 0.0 else 0
        widths.append(width)
    block["normalization"] = float(baseline) if np.isfinite(baseline) else None
    block["rowWidths"] = widths

history_rows = np.full((row_count, n), np.nan, dtype=float)
history_colors = [["rgba(0,0,0,0)" for _ in range(n)] for _ in range(row_count)]
for block in blocks[:-3]:
    color = "#800080" if (block["blockIndex"] + 3) % 2 == 0 else "#008080"
    for row_index in range(row_count):
        price = block["rowPrices"][row_index]
        if price is None:
            continue
        width = min(28, max(0, block["rowWidths"][row_index]))
        end_index = min(n - 1, block["startIndex"] + width)
        history_rows[row_index, block["startIndex"]:end_index + 1] = price
        for position in range(block["startIndex"], end_index + 1):
            history_colors[row_index][position] = color

layers = []
for block in blocks[-3:]:
    color = "#800080" if (block["blockIndex"] + 3) % 2 == 0 else "#008080"
    layers.extend([
        {"type": "line", "role": "Profile Block High", "startIndex": block["startIndex"], "endIndex": block["endIndex"], "y1": block["high"], "y2": block["high"], "color": "#0000FF", "lineWidth": 1},
        {"type": "line", "role": "Profile Block Low", "startIndex": block["startIndex"], "endIndex": block["endIndex"], "y1": block["low"], "y2": block["low"], "color": "#0000FF", "lineWidth": 1},
    ])
    for row_index in range(row_count):
        price = block["rowPrices"][row_index]
        if price is None:
            continue
        width = max(0, block["rowWidths"][row_index])
        layers.append({
            "type": "line", "role": f"Volume Row {row_index + 1}",
            "startIndex": block["startIndex"], "endIndex": min(n - 1, block["startIndex"] + width),
            "y1": price, "y2": price, "color": color, "lineWidth": 2,
            "density": block["rowValues"][row_index], "widthBars": width,
        })
    if show_vpoc and block["vpocPrice"] is not None:
        layers.append({
            "type": "label", "role": "Volume Point of Control", "index": block["endIndex"],
            "price": block["vpocPrice"], "text": "◆", "color": "#FF9800", "textColor": "#FF9800", "size": "tiny",
        })

vpoc_plot = np.full(n, np.nan, dtype=float)
if show_vpoc:
    for block in blocks:
        if block["vpocPrice"] is not None:
            vpoc_plot[block["endIndex"]] = block["vpocPrice"]
boundary_high = np.full(n, np.nan, dtype=float)
boundary_low = np.full(n, np.nan, dtype=float)
for block in blocks:
    boundary_high[block["startIndex"]:block["endIndex"] + 1] = block["high"]
    boundary_low[block["startIndex"]:block["endIndex"] + 1] = block["low"]

tables = []
if use_custom_volume_source and not custom_volume_available:
    tables = [{
        "title": "Volume source", "position": "top-right", "compact": True,
        "rows": [[{"text": "Custom volume unavailable"}], [{"text": "Using chart volume"}]],
    }]

output = {
    "schema_version": 2, "name": my_indicator_name,
    "plots": [
        {"name": "Block High", "data": _finite(boundary_high), "color": "rgba(0,0,255,0.20)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "Block Low", "data": _finite(boundary_low), "color": "rgba(0,0,255,0.20)", "type": "line", "lineWidth": 1, "overlay": True},
        {"name": "VPoC", "data": _finite(vpoc_plot), "color": "#FF9800", "type": "scatter", "size": 3, "overlay": True},
    ] + [
        {"name": f"History Profile Row {row_index + 1}", "title": False, "data": _finite(history_rows[row_index]), "color": history_colors[row_index], "type": "line", "lineWidth": 2, "overlay": True}
        for row_index in range(row_count)
    ] + [
        {"name": "Price Guide", "title": False, "data": _finite(close), "color": "rgba(0,0,0,0.01)", "type": "line", "lineWidth": 1, "overlay": True},
    ],
    "signals": [], "layers": layers, "tables": tables,
    "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "blocks": blocks, "maximumDensity": _finite(maximum_density_series),
        "maximumDensityAverage": _finite(maximum_density_average), "vpoc": _finite(vpoc_plot),
        "customVolumeRequested": use_custom_volume_source, "customVolumeAvailable": custom_volume_available,
        "customVolumeFallback": bool(use_custom_volume_source and not custom_volume_available),
    },
}
