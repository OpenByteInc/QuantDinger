# ============================================================
# QuantDinger Liquidity Spectrum
# Independent Python implementation for the QuantDinger indicator runtime.
# Port contract: chart-only indicator; signals do not place orders.
# ============================================================

my_indicator_name = "QuantDinger Liquidity Spectrum"
my_indicator_description = (
    "Maps recent volume into a 100-bin horizontal profile, projects high-volume "
    "liquidity levels, and sizes price bubbles by normalized bar volume."
)

# @param lookback int 200 Lookback range=20:5000:10
# @param show_volume_bubbles bool true Display volume bubbles
# @param show_volume_profile bool true Display volume profile
# @param show_liquidity_levels bool true Display liquidity levels
# @param bullish_color color #22B16C Bullish color
# @param bearish_color color #DD7E2A Bearish color

lookback = min(5000, max(1, int(params.get("lookback", 200))))
show_volume_bubbles = bool(params.get("show_volume_bubbles", True))
show_volume_profile = bool(params.get("show_volume_profile", True))
show_liquidity_levels = bool(params.get("show_liquidity_levels", True))
bullish_color = str(params.get("bullish_color", "#22B16C"))
bearish_color = str(params.get("bearish_color", "#DD7E2A"))

df = df.copy()
open_price = df["open"].astype(float).to_numpy(dtype=float)
high = df["high"].astype(float).to_numpy(dtype=float)
low = df["low"].astype(float).to_numpy(dtype=float)
close = df["close"].astype(float).to_numpy(dtype=float)
volume = df["volume"].astype(float).to_numpy(dtype=float)
time_values = df["time"].astype(float).to_numpy(dtype=float)
n = len(df)
if n and np.nanmax(np.abs(time_values)) < 100000000000.0:
    time_values = time_values * 1000.0


def color_with_alpha(color, alpha):
    value = str(color).strip()
    if value.startswith("#") and len(value) == 7:
        red = int(value[1:3], 16); green = int(value[3:5], 16); blue = int(value[5:7], 16)
        return f"rgba({red},{green},{blue},{max(0.0, min(1.0, float(alpha))):.3f})"
    return value

volume_deviation = (
    pd.Series(volume, dtype=float)
    .rolling(200, min_periods=200)
    .std(ddof=0)
    .to_numpy(dtype=float)
)
normalized_volume = np.full(n, np.nan, dtype=float)
valid_deviation = np.isfinite(volume_deviation) & (volume_deviation > 0.0)
normalized_volume[valid_deviation] = volume[valid_deviation] / volume_deviation[valid_deviation]

first_visible = max(0, n - lookback)
bubble_data = [None for _ in range(n)]
layers = []
positive_intervals = np.diff(time_values)
positive_intervals = positive_intervals[np.isfinite(positive_intervals) & (positive_intervals > 0.0)]
interval = float(np.median(positive_intervals)) if positive_intervals.size else 60000.0

for index in range(first_visible, n):
    normalized = normalized_volume[index]
    if not show_volume_bubbles or not np.isfinite(normalized):
        continue
    if normalized < 1.0:
        radius = 2.0
    elif normalized < 2.0:
        radius = 4.0
    elif normalized < 3.0:
        radius = 6.0
    elif normalized < 4.0:
        radius = 8.0
    else:
        radius = 11.0
    bullish = close[index] > open_price[index]
    base_color = bullish_color if bullish else bearish_color
    alpha = 0.50 + 0.50 * min(max(float(normalized), 0.0), 5.0) / 5.0
    color = color_with_alpha(base_color, alpha)
    price = float((high[index] + low[index] + close[index]) / 3.0)
    bubble_data[index] = {"value": price, "color": color, "size": radius}
    if normalized >= 4.0:
        layers.append(
            {
                "type": "label",
                "index": int(index),
                "price": price,
                "text": f"{volume[index]:,.0f}",
                "color": "rgba(0, 0, 0, 0.0)",
                "textColor": "#D9D9D9",
                "side": "center",
            }
        )

effective_lookback = min(lookback, n)
profile_start = n - 1 - lookback - (50 if show_volume_profile else 0)
window_high = np.nan
window_low = np.nan
price_step = np.nan
bins = np.asarray([], dtype=float)
values = np.asarray([], dtype=int)
if effective_lookback > 0:
    window_start = n - effective_lookback
    window_high = float(np.max(high[window_start:n]))
    window_low = float(np.min(low[window_start:n]))
    price_step = (window_high - window_low) / 100.0
    if price_step > 0.0:
        bins = np.zeros(100, dtype=float)
        for bin_index in range(100):
            lower = window_low + price_step * bin_index
            upper = lower + price_step
            inside = (
                (close[window_start:n] >= lower - price_step)
                & (close[window_start:n] <= upper + price_step)
            )
            bins[bin_index] = float(np.sum(volume[window_start:n][inside]))

        maximum_bin = float(np.max(bins))
        values = np.zeros(100, dtype=int)
        if maximum_bin > 0.0:
            values = np.asarray(bins / maximum_bin * 50.0, dtype=int)

        if profile_start >= 0:
            for bin_index in range(100):
                lower = window_low + price_step * bin_index
                upper = lower + price_step
                midpoint = (lower + upper) / 2.0
                value = int(values[bin_index])
                supportive = close[-1] > midpoint
                base_color = bullish_color if supportive else bearish_color

                if show_volume_profile:
                    alpha = 0.10 + 0.90 * value / 50.0
                    layers.append(
                        {
                            "type": "zone",
                            "start": float(time_values[profile_start]),
                            "end": float(time_values[min(n - 1, profile_start + value)]),
                            "top": float(upper),
                            "bottom": float(lower),
                            "fillColor": color_with_alpha(base_color, alpha),
                            "borderColor": "rgba(0, 0, 0, 0.0)",
                            "borderSize": 0,
                        }
                    )

                if show_liquidity_levels and value > 25:
                    alpha = 0.10 + 0.90 * (value - 25) / 25.0
                    line_width = 1 if value <= 35 else 2
                    layers.append(
                        {
                            "type": "line",
                            "start": float(time_values[profile_start]),
                            "end": float(time_values[-1] + interval * 5.0),
                            "y1": float(midpoint),
                            "y2": float(midpoint),
                            "color": color_with_alpha(base_color, alpha),
                            "lineWidth": int(line_width),
                            "dashed": False,
                        }
                    )

            future_time = float(time_values[-1] + interval * 5.0)
            layers.append(
                {
                    "type": "zone",
                    "start": float(time_values[profile_start]),
                    "end": future_time,
                    "top": window_high,
                    "bottom": window_low,
                    "fillColor": "rgba(0, 0, 0, 0.0)",
                    "borderColor": "rgba(220, 220, 220, 0.50)",
                    "borderSize": 1,
                }
            )

        high_index = int(window_start + np.argmax(high[window_start:n]))
        low_index = int(window_start + np.argmin(low[window_start:n]))
        layers.append(
            {
                "type": "label",
                "index": high_index,
                "price": window_high,
                "text": f"{window_high:g}",
                "color": bearish_color,
                "textColor": "#FFFFFF",
                "side": "above",
            }
        )
        layers.append(
            {
                "type": "label",
                "index": low_index,
                "price": window_low,
                "text": f"{window_low:g}",
                "color": bullish_color,
                "textColor": "#FFFFFF",
                "side": "below",
            }
        )

plots = [
    {"name": "Volume Bubbles", "data": bubble_data, "color": bullish_color, "type": "circle", "size": 2, "overlay": True},
]

plots = [plot for plot in plots if any(item is not None for item in plot["data"])]
plots.append(
    {
        "name": "Price Reference",
        "title": False,
        "data": [float(value) for value in close],
        "color": "rgba(255, 255, 255, 0.01)",
        "type": "line",
        "lineWidth": 1,
        "overlay": True,
    }
)

output = {
    "schema_version": 2, "name": my_indicator_name, "plots": plots, "signals": [], "layers": layers, "tables": [],
    "bar_colors": [None] * n, "background_colors": [None] * n,
    "calculatedVars": {
        "volumeDeviation": [float(value) if np.isfinite(value) else None for value in volume_deviation],
        "normalizedVolume": [float(value) if np.isfinite(value) else None for value in normalized_volume],
        "firstVisible": int(first_visible), "effectiveLookback": int(effective_lookback), "profileStart": int(profile_start),
        "windowHigh": float(window_high) if np.isfinite(window_high) else None, "windowLow": float(window_low) if np.isfinite(window_low) else None,
        "priceStep": float(price_step) if np.isfinite(price_step) else None,
        "bins": bins.tolist() if effective_lookback > 0 and price_step > 0.0 else [],
        "binValues": values.tolist() if effective_lookback > 0 and price_step > 0.0 else [],
        "barInterval": interval,
    },
}
