# ============================================================
# QuantDinger ATR Bands
# Independent Python implementation for the QuantDinger indicator runtime.
# ============================================================

my_indicator_name = "QuantDinger ATR Bands"
my_indicator_description = (
    "Plots close-anchored ATR stop bands, optional reward-scaled take-profit bands, "
    "and an optional stop/target value table."
)

# @param atr_period int 3 ATR period range=1:500:1
# @param atr_multiplier float 2.5 ATR band scale factor range=0.01:20.0:0.1
# @param show_take_profit_bands bool false Show take-profit bands
# @param take_profit_scale float 1.5 Take-profit reward/risk scale range=1.0:20.0:0.1
# @param show_table bool false Show stop and target table
# @param allow_table_repainting bool false Use current candle values in table
# @param show_take_profit_in_table bool false Include take-profit values in table
# @param align_table_vertically bool true Use vertical table layout
# @param table_position str Bottom_Right Table position values=Top_Right,Top_Left,Top_Center,Middle_Right,Middle_Left,Middle_Center,Bottom_Right,Bottom_Left,Bottom_Center
# @param table_color str #00AFC8 Table accent color
# @param table_header_color str #FFFF00 Table header color
# @param table_text_color str #FFFFFF Table value color
# @param table_long_background str rgba(0,255,0,0.10) Long row background
# @param table_short_background str rgba(255,0,0,0.20) Short row background

atr_period = min(500, max(1, int(params.get("atr_period", 3))))
atr_multiplier = min(20.0, max(0.01, float(params.get("atr_multiplier", 2.5))))
show_take_profit_bands = bool(params.get("show_take_profit_bands", False))
take_profit_scale = min(20.0, max(1.0, float(params.get("take_profit_scale", 1.5))))
show_table = bool(params.get("show_table", False))
allow_table_repainting = bool(params.get("allow_table_repainting", False))
show_take_profit_in_table = bool(params.get("show_take_profit_in_table", False))
align_table_vertically = bool(params.get("align_table_vertically", True))
table_position = str(params.get("table_position", "Bottom_Right"))
table_color = str(params.get("table_color", "#00AFC8"))
table_header_color = str(params.get("table_header_color", "#FFFF00"))
table_text_color = str(params.get("table_text_color", "#FFFFFF"))
table_long_background = str(params.get("table_long_background", "rgba(0,255,0,0.10)"))
table_short_background = str(params.get("table_short_background", "rgba(255,0,0,0.20)"))

df = df.copy()
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
n = len(df)


def wilder_average(values, length):
    source = np.asarray(values, dtype=float)
    result = np.full(len(source), np.nan, dtype=float)
    if len(source) < length:
        return result
    seed = source[:length]
    if np.all(np.isfinite(seed)):
        result[length - 1] = float(np.mean(seed))
    for index in range(length, len(source)):
        if np.isfinite(result[index - 1]) and np.isfinite(source[index]):
            result[index] = (result[index - 1] * (length - 1) + source[index]) / length
    return result


def finite_line(values, visible=True):
    if not visible:
        return [None for _ in range(len(values))]
    return [float(value) if np.isfinite(value) else None for value in values]


def format_price(value):
    if not np.isfinite(value):
        return "—"
    magnitude = abs(float(value))
    decimals = 2 if magnitude >= 1000.0 else 4 if magnitude >= 1.0 else 8
    return f"{float(value):.{decimals}f}"


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

atr = wilder_average(true_range, atr_period)
scaled_atr = atr * atr_multiplier
upper_band = close + scaled_atr
lower_band = close - scaled_atr
long_target = close + (close - lower_band) * take_profit_scale
short_target = close - (upper_band - close) * take_profit_scale

plots = [
    {
        "name": "Upper ATR Band",
        "data": finite_line(upper_band),
        "color": "rgba(0,255,0,0.50)",
        "type": "line",
        "lineWidth": 2,
        "overlay": True,
    },
    {
        "name": "Lower ATR Band",
        "data": finite_line(lower_band),
        "color": "rgba(255,0,0,0.50)",
        "type": "line",
        "lineWidth": 2,
        "overlay": True,
    },
    {
        "name": "Upper Take-Profit Band",
        "data": finite_line(long_target, show_take_profit_bands),
        "color": "rgba(255,255,255,0.20)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    },
    {
        "name": "Lower Take-Profit Band",
        "data": finite_line(short_target, show_take_profit_bands),
        "color": "rgba(255,255,0,0.20)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    },
]

position_map = {
    "Top_Right": "top-right",
    "Top_Left": "top-left",
    "Top_Center": "top-right",
    "Middle_Right": "top-right",
    "Middle_Left": "top-left",
    "Middle_Center": "top-right",
    "Bottom_Right": "bottom-right",
    "Bottom_Left": "bottom-left",
    "Bottom_Center": "bottom-right",
}

tables = []
if show_table and n:
    table_index = n - 1 if allow_table_repainting or n == 1 else n - 2
    if np.isfinite(scaled_atr[table_index]):
        long_rows = [
            [
                {"text": "Long ATR Stop", "color": table_header_color, "backgroundColor": table_long_background},
                {"text": format_price(lower_band[table_index]), "color": table_text_color, "backgroundColor": table_long_background},
            ],
            [
                {"text": "Long ATR Stop Dist", "color": table_header_color, "backgroundColor": table_long_background},
                {"text": format_price(close[table_index] - lower_band[table_index]), "color": table_text_color, "backgroundColor": table_long_background},
            ],
        ]
        short_rows = [
            [
                {"text": "Short ATR Stop", "color": table_header_color, "backgroundColor": table_short_background},
                {"text": format_price(upper_band[table_index]), "color": table_text_color, "backgroundColor": table_short_background},
            ],
            [
                {"text": "Short ATR Stop Dist", "color": table_header_color, "backgroundColor": table_short_background},
                {"text": format_price(upper_band[table_index] - close[table_index]), "color": table_text_color, "backgroundColor": table_short_background},
            ],
        ]
        if show_take_profit_in_table:
            long_rows.append([
                {"text": "Long ATR TP", "color": table_header_color, "backgroundColor": table_long_background},
                {"text": format_price(long_target[table_index]), "color": table_text_color, "backgroundColor": table_long_background},
            ])
            short_rows.append([
                {"text": "Short ATR TP", "color": table_header_color, "backgroundColor": table_short_background},
                {"text": format_price(short_target[table_index]), "color": table_text_color, "backgroundColor": table_short_background},
            ])
            if take_profit_scale != 1.0:
                target_distance = format_price(scaled_atr[table_index] * take_profit_scale)
                long_rows.append([
                    {"text": "Long ATR TP Dist", "color": table_header_color, "backgroundColor": table_long_background},
                    {"text": target_distance, "color": table_text_color, "backgroundColor": table_long_background},
                ])
                short_rows.append([
                    {"text": "Short ATR TP Dist", "color": table_header_color, "backgroundColor": table_short_background},
                    {"text": target_distance, "color": table_text_color, "backgroundColor": table_short_background},
                ])

        rows = long_rows + short_rows
        if not align_table_vertically:
            labels = [row[0] for row in rows]
            values = [row[1] for row in rows]
            rows = [labels, values]
        tables.append({
            "title": "ATR STOPS & TARGETS",
            "position": position_map.get(table_position, "bottom-right"),
            "compact": True,
            "borderColor": table_color,
            "accentColor": table_color,
            "rows": rows,
        })

output = {
    "schema_version": 2,
    "name": my_indicator_name,
    "plots": plots,
    "signals": [],
    "tables": tables,
}
