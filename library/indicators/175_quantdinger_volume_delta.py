# ============================================================
# QuantDinger Volume Delta
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger Volume Delta"
my_indicator_description = (
    "Estimates buy and sell participation from each candle, draws volume-share candles and a fixed "
    "volume dashboard, and confirms fast/slow EMA turns with recent volume."
)

# @param volume_candles bool true Show volume-colored price candles
# @param buy_color str #12CEF8 Buy volume color
# @param sell_color str #FE3F00 Sell volume color
# @param table_position str top-right Table position values=top-left,top-right,bottom-left,bottom-right
# @param show_table bool true Show volume dashboard
# @param show_volume_labels bool false Show current volume signal labels
# @param show_ema bool true Show EMA trend overlay
# @param use_volume_confirmation bool true Require volume confirmation
# @param fast_length int 12 Fast EMA length range=1:500:1
# @param slow_length int 26 Slow EMA length range=2:500:1
# @param confirmation_length int 6 Volume confirmation length range=1:100:1

volume_candles = bool(params.get("volume_candles", True))
buy_color = str(params.get("buy_color", "#12CEF8"))
sell_color = str(params.get("sell_color", "#FE3F00"))
table_position = str(params.get("table_position", "top-right"))
if table_position not in ("top-left", "top-right", "bottom-left", "bottom-right"):
    table_position = "top-right"
show_table = bool(params.get("show_table", True))
show_volume_labels = bool(params.get("show_volume_labels", False))
show_ema = bool(params.get("show_ema", True))
use_volume_confirmation = bool(params.get("use_volume_confirmation", True))
fast_length = min(500, max(1, int(params.get("fast_length", 12))))
slow_length = min(500, max(2, int(params.get("slow_length", 26))))
confirmation_length = min(100, max(1, int(params.get("confirmation_length", 6))))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)

def ema(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    alpha = 2.0 / (length + 1.0)
    previous = np.nan
    for index, value in enumerate(values):
        if np.isfinite(value):
            previous = float(value) if not np.isfinite(previous) else alpha * float(value) + (1.0 - alpha) * previous
            result[index] = previous
    return result

def sma(values, length):
    result = np.full(len(values), np.nan, dtype=float)
    for index in range(length - 1, len(values)):
        result[index] = float(np.mean(values[index - length + 1:index + 1]))
    return result

def rgba(color, opacity):
    value = str(color).strip()
    if value.startswith("#") and len(value) in (4, 7):
        if len(value) == 4:
            red, green, blue = (int(value[position] * 2, 16) for position in range(1, 4))
        else:
            red = int(value[1:3], 16)
            green = int(value[3:5], 16)
            blue = int(value[5:7], 16)
        return f"rgba({red},{green},{blue},{min(1.0, max(0.0, float(opacity))):.3f})"
    return value

price_range = high - low
buy_volume = np.zeros(n, dtype=float)
valid_range = np.abs(price_range) > 1e-12
buy_volume[valid_range] = volume[valid_range] * (close[valid_range] - low[valid_range]) / price_range[valid_range]
buy_volume[~valid_range] = np.nan
buy_volume[valid_range] = np.minimum(volume[valid_range], np.maximum(0.0, buy_volume[valid_range]))
sell_volume = volume - buy_volume
buy_percent = np.full(n, np.nan, dtype=float)
valid_volume = volume > 1e-12
buy_percent[valid_volume & np.isfinite(buy_volume)] = buy_volume[valid_volume & np.isfinite(buy_volume)] / volume[valid_volume & np.isfinite(buy_volume)] * 100.0
sell_percent = 100.0 - buy_percent
buy_dominant = buy_volume > sell_volume
higher_volume = np.where(buy_dominant, buy_volume, sell_volume)
lower_volume = np.where(buy_dominant, sell_volume, buy_volume)
dominant_colors = [buy_color if buy_dominant[index] else sell_color for index in range(n)]
minor_colors = [sell_color if buy_dominant[index] else buy_color for index in range(n)]

fast_ema = ema(close, fast_length)
slow_ema = ema(close, slow_length)
volume_ma = sma(volume, confirmation_length)
volume_confirmed = np.zeros(n, dtype=bool)
for index in range(confirmation_length - 1, n):
    start = index - confirmation_length + 1
    above_sum = 0.0
    below_sum = 0.0
    for cursor in range(start, index + 1):
        if not np.isfinite(volume_ma[cursor]):
            continue
        if volume[cursor] > volume_ma[cursor]:
            above_sum += volume[cursor]
        elif volume[cursor] < volume_ma[cursor]:
            below_sum += volume[cursor]
    volume_confirmed[index] = above_sum >= below_sum

trend_up = fast_ema > slow_ema
crossover = np.zeros(n, dtype=bool)
crossunder = np.zeros(n, dtype=bool)
for index in range(1, n):
    if not (
        np.isfinite(fast_ema[index]) and np.isfinite(slow_ema[index])
        and np.isfinite(fast_ema[index - 1]) and np.isfinite(slow_ema[index - 1])
    ):
        continue
    crossover[index] = fast_ema[index] > slow_ema[index] and fast_ema[index - 1] <= slow_ema[index - 1]
    crossunder[index] = fast_ema[index] < slow_ema[index] and fast_ema[index - 1] >= slow_ema[index - 1]

buy_signal = crossover & (volume_confirmed | (not use_volume_confirmation)) & show_ema
sell_signal = crossunder & (volume_confirmed | (not use_volume_confirmation)) & show_ema

total_points = [
    {"value": float(volume[index]), "color": rgba(dominant_colors[index], 0.12)}
    for index in range(n)
]
total_line_points = [
    {"value": float(volume[index]), "color": dominant_colors[index]}
    for index in range(n)
]
higher_points = [
    {"value": float(higher_volume[index]), "color": dominant_colors[index]} if np.isfinite(higher_volume[index]) else None
    for index in range(n)
]
lower_points = [
    {"value": float(lower_volume[index]), "color": minor_colors[index]} if np.isfinite(lower_volume[index]) else None
    for index in range(n)
]

plots = [
    {"name": "Total Volume Background", "title": False, "data": total_points, "color": rgba(buy_color, 0.12), "type": "bar", "overlay": False},
    {"name": "Total Volume", "data": total_line_points, "color": buy_color, "type": "line", "lineWidth": 2, "overlay": False},
    {"name": "Dominant Volume", "data": higher_points, "color": buy_color, "type": "bar", "overlay": False},
    {"name": "Minor Volume", "data": lower_points, "color": sell_color, "type": "bar", "overlay": False},
]

if volume_candles:
    outer_candles = []
    inner_candles = []
    for index in range(n):
        dominant_color = dominant_colors[index]
        body_base = min(open_price[index], close[index]) if buy_dominant[index] else max(open_price[index], close[index])
        dominant_percent = buy_percent[index] if buy_dominant[index] else sell_percent[index]
        body_value = (
            body_base + abs(open_price[index] - close[index]) * dominant_percent / 100.0
            if buy_dominant[index]
            else body_base - abs(open_price[index] - close[index]) * dominant_percent / 100.0
        )
        outer_candles.append({
            "open": float(open_price[index]),
            "high": float(high[index]),
            "low": float(low[index]),
            "close": float(close[index]),
            "color": dominant_color,
            "fillColor": rgba(dominant_color, 0.20),
            "borderColor": dominant_color,
            "wickColor": dominant_color,
        })
        inner_candles.append({
            "open": float(body_base),
            "high": float(max(body_base, body_value)),
            "low": float(min(body_base, body_value)),
            "close": float(body_value),
            "color": dominant_color,
            "fillColor": dominant_color,
            "borderColor": dominant_color,
            "wickColor": dominant_color,
        } if np.isfinite(body_value) else None)
    plots.append({"name": "Volume Share Candles", "data": outer_candles, "type": "candlestick", "overlay": True})
    plots.append({"name": "Dominant Candle Body", "data": inner_candles, "type": "candlestick", "overlay": True})

if show_ema:
    fast_points = [
        {"value": float(fast_ema[index]), "color": buy_color if trend_up[index] else sell_color}
        if np.isfinite(fast_ema[index]) else None
        for index in range(n)
    ]
    slow_points = [
        {"value": float(slow_ema[index]), "color": buy_color if trend_up[index] else sell_color}
        if np.isfinite(slow_ema[index]) else None
        for index in range(n)
    ]
    ema_cloud = [
        {
            "upper": float(max(fast_ema[index], slow_ema[index])),
            "lower": float(min(fast_ema[index], slow_ema[index])),
            "color": rgba(buy_color if trend_up[index] else sell_color, 0.18),
        }
        if np.isfinite(fast_ema[index]) and np.isfinite(slow_ema[index]) else None
        for index in range(n)
    ]
    plots.append({"name": "Fast EMA", "data": fast_points, "color": buy_color, "type": "line", "lineWidth": 2, "overlay": True})
    plots.append({"name": "Slow EMA", "data": slow_points, "color": sell_color, "type": "line", "lineWidth": 2, "overlay": True})
    plots.append({"name": "EMA Trend Cloud", "data": ema_cloud, "color": rgba(buy_color, 0.18), "type": "cloud", "overlay": True})

def signal_points(mask, values, text, color):
    return [
        {"value": float(values[index]), "text": text, "color": color} if mask[index] else None
        for index in range(n)
    ]

signals = [
    {"type": "buy", "name": "Volume Confirmed EMA Cross Up", "text": "EMA UP", "color": buy_color, "data": signal_points(buy_signal, low, "EMA UP", buy_color), "renderMode": "events"},
    {"type": "sell", "name": "Volume Confirmed EMA Cross Down", "text": "EMA DN", "color": sell_color, "data": signal_points(sell_signal, high, "EMA DN", sell_color), "renderMode": "events"},
]
if show_volume_labels:
    signals.append({
        "type": "buy",
        "name": "Buy Dominant Volume",
        "text": "BUY VOL",
        "color": buy_color,
        "data": signal_points(buy_dominant, low, "BUY VOL", buy_color),
        "renderMode": "events",
    })
    signals.append({
        "type": "sell",
        "name": "Sell Dominant Volume",
        "text": "SELL VOL",
        "color": sell_color,
        "data": signal_points(~buy_dominant, high, "SELL VOL", sell_color),
        "renderMode": "events",
    })

tables = []
if show_table and n:
    latest_buy_percent = float(buy_percent[-1])
    filled_buy_cells = int(np.ceil(20.0 * latest_buy_percent / 100.0)) if np.isfinite(latest_buy_percent) else 0
    grid_cells = []
    for cell_index in range(20):
        grid_cells.append({
            "text": "",
            "backgroundColor": buy_color if cell_index < filled_buy_cells else sell_color,
        })
    price_direction = 0.0 if n < 2 else float(np.sign(close[-1] - close[-2]))
    net_display = price_direction * volume[-1]
    rows = [
        [
            {"text": "Current Volume", "color": "#FFFFFF", "backgroundColor": "#0B3040"},
            {"text": "", "backgroundColor": "#0B3040"},
        ],
        [
            {"text": "Buy", "color": "#000000", "backgroundColor": "#92D050"},
            {"text": "Sell", "color": "#000000", "backgroundColor": "#FF0000"},
        ],
        [
            {"text": f"{buy_volume[-1]:,.0f}", "color": "#FFFFFF", "backgroundColor": "#074F69"},
            {"text": f"{sell_volume[-1]:,.0f}", "color": "#FFFFFF", "backgroundColor": "#074F69"},
        ],
        [
            {"text": f"{buy_percent[-1]:.1f}%", "color": "#FFFFFF", "backgroundColor": "#074F69"},
            {"text": f"{sell_percent[-1]:.1f}%", "color": "#FFFFFF", "backgroundColor": "#074F69"},
        ],
        [
            {"text": f"Net: {net_display:,.0f}", "color": "#FFFFFF", "backgroundColor": "#074F69"},
            {"text": "", "backgroundColor": "#074F69"},
        ],
        grid_cells,
    ]
    tables.append({"title": "Volume Candles", "position": table_position, "rows": rows, "compact": True})

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": signals,
    "layers": [],
    "tables": tables,
    "bar_colors": [None] * n,
    "background_colors": [None] * n,
    "calculatedVars": {
        "buyVolume": [float(value) if np.isfinite(value) else None for value in buy_volume],
        "sellVolume": [float(value) if np.isfinite(value) else None for value in sell_volume],
        "buyPercent": [float(value) if np.isfinite(value) else None for value in buy_percent],
        "sellPercent": [float(value) if np.isfinite(value) else None for value in sell_percent],
        "higherVolume": [float(value) if np.isfinite(value) else None for value in higher_volume],
        "lowerVolume": [float(value) if np.isfinite(value) else None for value in lower_volume],
        "fastEMA": [float(value) if np.isfinite(value) else None for value in fast_ema],
        "slowEMA": [float(value) if np.isfinite(value) else None for value in slow_ema],
        "volumeMA": [float(value) if np.isfinite(value) else None for value in volume_ma],
        "volumeConfirmed": volume_confirmed.tolist(), "trendUp": trend_up.tolist(),
        "crossUp": crossover.tolist(), "crossDown": crossunder.tolist(),
        "buySignal": buy_signal.tolist(), "sellSignal": sell_signal.tolist(),
    },
}
