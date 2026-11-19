# ============================================================
# QuantDinger Directional Kernel Filter
# Independent Python implementation for the QuantDinger runtime.
# ============================================================

my_indicator_name = "QuantDinger Directional Kernel Filter"
my_indicator_description = (
    "Applies fast and slow Gaussian kernels whose observations are reweighted by "
    "ATR-normalized agreement with each kernel's previously estimated direction."
)

# @param source str close Source values=open,high,low,close,hl2,hlc3,ohlc4
# @param fast_length int 18 Fast kernel length range=3:500:1
# @param slow_length int 35 Slow kernel length range=5:500:1
# @param kernel_width float 0.35 Kernel width range=0.05:1.0:0.05
# @param direction_strength float 3.5 Directional weight range=0.0:5.0:0.1
# @param normalization_length int 14 ATR normalization length range=2:500:1
# @param long_color str #00FF00 Long color
# @param short_color str #FF0000 Short color
# @param line_width int 3 Line width range=1:6:1
# @param show_ribbon bool true Show trend ribbon
# @param show_base bool false Show base Gaussian kernel
# @param paint_candles bool false Paint trend candles
# @param background_color bool false Show trend background

source_name = str(params.get("source", "close")).lower()
fast_length = min(500, max(3, int(params.get("fast_length", 18))))
slow_length = min(500, max(5, int(params.get("slow_length", 35))))
kernel_width = min(1.0, max(0.05, float(params.get("kernel_width", 0.35))))
direction_strength = min(5.0, max(0.0, float(params.get("direction_strength", 3.5))))
normalization_length = min(500, max(2, int(params.get("normalization_length", 14))))
long_color = str(params.get("long_color", "#00FF00"))
short_color = str(params.get("short_color", "#FF0000"))
line_width = min(6, max(1, int(params.get("line_width", 3))))
show_ribbon = bool(params.get("show_ribbon", True))
show_base = bool(params.get("show_base", False))
paint_candles = bool(params.get("paint_candles", False))
background_color = bool(params.get("background_color", False))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)

sources = {
    "open": open_price,
    "high": high,
    "low": low,
    "close": close,
    "hl2": (high + low) / 2.0,
    "hlc3": (high + low + close) / 3.0,
    "ohlc4": (open_price + high + low + close) / 4.0,
}
source = np.asarray(sources.get(source_name, close), dtype=float)


def wilder_atr(high_values, low_values, close_values, length):
    count = len(close_values)
    true_range = np.full(count, np.nan, dtype=float)
    if count:
        true_range[0] = high_values[0] - low_values[0]
    for index in range(1, count):
        true_range[index] = max(
            high_values[index] - low_values[index],
            abs(high_values[index] - close_values[index - 1]),
            abs(low_values[index] - close_values[index - 1]),
        )
    result = np.full(count, np.nan, dtype=float)
    if count >= length:
        result[length - 1] = float(np.mean(true_range[:length]))
        for index in range(length, count):
            result[index] = (result[index - 1] * (length - 1) + true_range[index]) / length
    return result, true_range


def directional_kernel(values, length, width, strength, normalizer):
    count = len(values)
    base = np.full(count, np.nan, dtype=float)
    filtered = np.full(count, np.nan, dtype=float)
    prior_normalized = np.zeros(count, dtype=float)
    bandwidth = max(1.0, float(length) * float(width))
    gaussian = np.exp(-0.5 * (np.arange(length, dtype=float) / bandwidth) ** 2)

    for index in range(count):
        available = min(length, index + 1)
        window = values[index - np.arange(available)]
        weights = gaussian[:available]
        valid = np.isfinite(window)
        if not np.any(valid):
            continue
        base[index] = float(np.dot(window[valid], weights[valid]) / np.sum(weights[valid]))

        prior_direction = 0.0
        if index >= 2 and np.isfinite(base[index - 1]) and np.isfinite(base[index - 2]):
            prior_direction = float(base[index - 1] - base[index - 2])
        norm_value = normalizer[index - 1] if index >= 1 and np.isfinite(normalizer[index - 1]) else normalizer[index]
        denominator = max(float(norm_value), 1e-12) if np.isfinite(norm_value) else 1e-12
        current_normalizer = max(float(normalizer[index]), 1e-12) if np.isfinite(normalizer[index]) else denominator
        prior_normalized[index] = min(1.0, max(-1.0, prior_direction / denominator))

        if index < length - 1:
            filtered[index] = base[index]
            continue

        directional_weights = np.zeros(length, dtype=float)
        for lag in range(length):
            current = values[index - lag]
            if not np.isfinite(current):
                continue
            if lag < length - 1:
                previous = values[index - lag - 1]
                if not np.isfinite(previous):
                    continue
                local_move = float(current - previous)
            else:
                local_move = 0.0
            local_norm_value = normalizer[index - lag]
            local_denominator = max(float(local_norm_value), 1e-12) if np.isfinite(local_norm_value) else current_normalizer
            local_normalized = min(1.0, max(-1.0, local_move / local_denominator))
            alignment = prior_normalized[index] * local_normalized
            exponent = min(3.0, max(-3.0, strength * alignment))
            directional_weights[lag] = gaussian[lag] * np.exp(exponent)
        total_weight = float(np.sum(directional_weights))
        if total_weight > 0.0:
            filtered[index] = float(np.dot(window, directional_weights) / total_weight)
        else:
            filtered[index] = base[index]
    return filtered, base, prior_normalized


atr, true_range = wilder_atr(high, low, close, normalization_length)
fast_kernel, fast_base, fast_direction = directional_kernel(
    source, fast_length, kernel_width, direction_strength, atr
)
slow_kernel, slow_base, slow_direction = directional_kernel(
    source, slow_length, kernel_width, direction_strength, atr
)

trend_state = np.zeros(n, dtype=int)
state = 0
for index in range(n):
    if np.isfinite(fast_kernel[index]) and np.isfinite(slow_kernel[index]):
        if fast_kernel[index] > slow_kernel[index]:
            state = 1
        elif fast_kernel[index] < slow_kernel[index]:
            state = -1
    trend_state[index] = state

trend_colors = [long_color if value == 1 else short_color for value in trend_state]


def with_alpha(color, alpha):
    value = str(color).strip()
    if value.startswith("#") and len(value) in (4, 7):
        if len(value) == 4:
            red, green, blue = (int(value[position] * 2, 16) for position in range(1, 4))
        else:
            red, green, blue = int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
        return f"rgba({red},{green},{blue},{min(1.0, max(0.0, float(alpha))):.3f})"
    return color


def points(values, colors=None, alpha=None):
    result = []
    for index, value in enumerate(values):
        if not np.isfinite(value):
            result.append(None)
            continue
        point = {"value": float(value)}
        if colors is not None:
            point["color"] = with_alpha(colors[index], alpha) if alpha is not None else colors[index]
        result.append(point)
    return result


plots = [
    {
        "name": "Fast Directional Kernel",
        "data": points(fast_kernel, trend_colors),
        "color": long_color,
        "type": "line",
        "lineWidth": line_width,
        "overlay": True,
    }
]

if show_ribbon:
    plots.append({
        "name": "Slow Directional Kernel",
        "data": points(slow_kernel, trend_colors, 0.40),
        "color": with_alpha(long_color, 0.40),
        "type": "line",
        "lineWidth": 2,
        "overlay": True,
    })
    ribbon = []
    for index in range(n):
        if np.isfinite(fast_kernel[index]) and np.isfinite(slow_kernel[index]):
            ribbon.append({
                "upper": float(max(fast_kernel[index], slow_kernel[index])),
                "lower": float(min(fast_kernel[index], slow_kernel[index])),
                "color": with_alpha(trend_colors[index], 0.18),
            })
        else:
            ribbon.append(None)
    plots.append({"name": "Directional Kernel Ribbon", "title": False, "data": ribbon, "type": "cloud", "overlay": True})

if show_base:
    plots.append({
        "name": "Base Gaussian Kernel",
        "data": points(fast_base),
        "color": "rgba(148,163,184,0.40)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    })

if paint_candles:
    candles = [
        {
            "open": float(open_price[index]),
            "high": float(high[index]),
            "low": float(low[index]),
            "close": float(close[index]),
            "color": trend_colors[index],
            "fillColor": trend_colors[index],
            "borderColor": trend_colors[index],
            "wickColor": trend_colors[index],
        }
        for index in range(n)
    ]
    plots.append({"name": "Trend Candles", "title": False, "data": candles, "type": "candlestick", "overlay": True})

bull_flip = np.r_[False, (trend_state[1:] == 1) & (trend_state[:-1] == -1)] if n else np.array([], dtype=bool)
bear_flip = np.r_[False, (trend_state[1:] == -1) & (trend_state[:-1] == 1)] if n else np.array([], dtype=bool)
backgrounds = [with_alpha(trend_colors[index], 0.10) if background_color else None for index in range(n)]

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "layers": [],
    "tables": [],
    "bar_colors": trend_colors if paint_candles else [None for _ in range(n)],
    "background_colors": backgrounds,
    "calculatedVars": {
        "atr": [float(value) if np.isfinite(value) else None for value in atr],
        "trueRange": [float(value) if np.isfinite(value) else None for value in true_range],
        "fastKernel": [float(value) if np.isfinite(value) else None for value in fast_kernel],
        "slowKernel": [float(value) if np.isfinite(value) else None for value in slow_kernel],
        "fastBase": [float(value) if np.isfinite(value) else None for value in fast_base],
        "slowBase": [float(value) if np.isfinite(value) else None for value in slow_base],
        "fastDirection": [float(value) for value in fast_direction],
        "slowDirection": [float(value) for value in slow_direction],
        "directionalAdjustment": [
            float(fast_kernel[index] - fast_base[index])
            if np.isfinite(fast_kernel[index]) and np.isfinite(fast_base[index]) else None
            for index in range(n)
        ],
        "kernelSpread": [
            float(fast_kernel[index] - slow_kernel[index])
            if np.isfinite(fast_kernel[index]) and np.isfinite(slow_kernel[index]) else None
            for index in range(n)
        ],
        "trendState": [int(value) for value in trend_state],
        "bullFlip": [bool(value) for value in bull_flip],
        "bearFlip": [bool(value) for value in bear_flip],
    },
}
