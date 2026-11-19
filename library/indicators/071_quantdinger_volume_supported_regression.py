# ============================================================
# QuantDinger Volume-Supported Regression Trend
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Volume-Supported Regression Trend"
my_indicator_description = (
    "Short- and long-term price regression slopes colored by the matching "
    "buy- and sell-volume regression energy."
)

# @param short_length int 20 Short regression period range=5:200:1
# @param long_length int 50 Long regression period range=5:300:1

short_length = max(5, int(params.get("short_length", 20)))
long_length = max(5, int(params.get("long_length", 50)))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
n = len(df)

top_wick = high - np.maximum(open_price, close)
bottom_wick = np.minimum(open_price, close) - low
body = np.abs(close - open_price)
denominator = top_wick + bottom_wick + body
bull_rate = np.divide(0.5 * (top_wick + bottom_wick + np.where(open_price <= close, 2.0 * body, 0.0)), denominator, out=np.full(n, 0.5, dtype=float), where=denominator != 0.0)
bear_rate = np.divide(0.5 * (top_wick + bottom_wick + np.where(open_price > close, 2.0 * body, 0.0)), denominator, out=np.full(n, 0.5, dtype=float), where=denominator != 0.0)
buy_volume = volume * bull_rate
sell_volume = volume * bear_rate


def regression_slope(values, period):
    result = np.full(n, np.nan, dtype=float)
    x_sum = float(period * (period - 1)) / 2.0
    x2_sum = float(period * (period - 1) * (2 * period - 1)) / 6.0
    denominator_value = period * x2_sum - x_sum * x_sum
    for index in range(period - 1, n):
        y_sum = 0.0
        xy_sum = 0.0
        valid = True
        for position in range(period):
            value = values[index - period + 1 + position]
            if not np.isfinite(value):
                valid = False
                break
            y_sum += value
            xy_sum += float(position) * value
        if valid:
            result[index] = (period * xy_sum - x_sum * y_sum) / denominator_value if denominator_value != 0.0 else 0.0
    return result


short_price_slope = regression_slope(close, short_length)
long_price_slope = regression_slope(close, long_length)
short_buy_slope = regression_slope(buy_volume, short_length)
short_sell_slope = regression_slope(sell_volume, short_length)
long_buy_slope = regression_slope(buy_volume, long_length)
long_sell_slope = regression_slope(sell_volume, long_length)


def color_series(price_slope, buy_slope, sell_slope, green_colors, red_colors):
    data = []
    for index in range(n):
        if not np.isfinite(price_slope[index]) or not np.isfinite(buy_slope[index]) or not np.isfinite(sell_slope[index]):
            data.append(None)
        elif price_slope[index] > 0.0:
            color = green_colors[0] if buy_slope[index] > 0.0 and buy_slope[index] > sell_slope[index] else green_colors[1] if buy_slope[index] > 0.0 else green_colors[2]
            data.append({"value": float(price_slope[index]), "color": color})
        elif price_slope[index] < 0.0:
            color = red_colors[0] if sell_slope[index] > 0.0 and buy_slope[index] < sell_slope[index] else red_colors[1] if sell_slope[index] > 0.0 else red_colors[2]
            data.append({"value": float(price_slope[index]), "color": color})
        else:
            data.append({"value": 0.0, "color": "#808080"})
    return data


short_data = color_series(short_price_slope * short_length, short_buy_slope, short_sell_slope, ["#00FF00", "#00BC00", "#00FF006F"], ["#FF0000", "#BF0000", "#FF00006F"])
long_data = color_series(long_price_slope * long_length, long_buy_slope, long_sell_slope, ["#008EFF", "#006EC5", "#024478"], ["#FD9701", "#CE7A00", "#663D00"])

output = {
    "name": my_indicator_name,
    "plots": [
        {"name": "Short Trend", "data": short_data, "color": "#00FF00", "type": "histogram", "baseValue": 0.0, "lineWidth": 2, "overlay": False},
        {"name": "Long Trend", "data": long_data, "color": "#008EFF", "type": "line", "lineWidth": 3, "overlay": False},
        {"name": "Zero", "data": [0.0 for _ in range(n)], "color": "#808080", "type": "line", "lineWidth": 1, "overlay": False},
    ],
    "signals": [],
    "layers": [],
}
