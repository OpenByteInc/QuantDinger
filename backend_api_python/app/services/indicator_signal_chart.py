"""Chart image for indicator signal alerts: recent candles, the signal marker and the entry candles."""

from __future__ import annotations

from io import BytesIO
from typing import Callable, List, Optional, Tuple

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1200, 675
HISTORY = 36  # candles drawn before the signal candle
STEP_WIDTH = 2.4  # entry candles are drawn this many candles wide, so they stand out
BG, GRID, TEXT, MUTED = "#0f1115", "#1f2430", "#e5e7eb", "#9ca3af"
UP, DOWN = "#22c55e", "#ef4444"
LEFT, RIGHT, TOP, BOTTOM = 24, 112, 96, 44  # plot margins; prices on the right


def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def _drawable(text: str) -> str:
    """``text`` when Pillow's bundled font can draw it (Latin scripts only), else ''."""
    return text if all(ord(ch) < 0x250 for ch in text) else ""


def _rgba(color: str, alpha: int) -> Tuple[int, int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16), alpha


def render_signal_chart(
    candles: pd.DataFrame,
    signal_time: int,
    side: str,
    steps: List[str],
    *,
    title: str,
    subtitle: str,
    badge: str,
    marker: str,
    time_text: Callable[[int], str],
    price_text: Callable[[float], str],
    result: Optional[Tuple[int, bool, str]] = None,
) -> bytes:
    """PNG of the candles up to the signal candle, then one band per entry candle (``steps``
    labels them: the entry, then each gale). Candles after the signal candle are drawn when
    ``candles`` has them. ``result`` is ``(deciding step, won, text)`` once the trade is decided.
    ``candles`` has epoch-second ``time`` plus open/high/low/close. Text the font cannot draw is left out.
    """
    times = [int(t) for t in candles["time"]]
    pos = times.index(int(signal_time))
    start = max(0, pos - HISTORY)
    rows = candles.iloc[start: pos + 1 + len(steps)]
    spacing = times[pos] - times[pos - 1] if pos else 60
    sig = pos - start  # slot of the signal candle
    color = UP if side == "buy" else DOWN
    marker, subtitle = _drawable(marker), _drawable(subtitle)

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img, "RGBA")  # RGBA fills blend onto an RGB image
    x0, x1, y0, y1 = LEFT, WIDTH - RIGHT, TOP, HEIGHT - BOTTOM
    unit = (x1 - x0) / (sig + 1 + STEP_WIDTH * len(steps))
    low, high = float(rows["low"].min()), float(rows["high"].max())
    pad = (high - low or abs(high) * 0.001 or 1.0) * 0.2  # room for the marker and its label

    def y(price: float) -> float:
        return y1 - (price - low + pad) / (high - low + 2 * pad) * (y1 - y0)

    def left(i: int) -> float:  # left edge of slot i (0 = first drawn candle)
        return x0 + i * unit if i <= sig + 1 else x0 + (sig + 1) * unit + (i - sig - 1) * STEP_WIDTH * unit

    def x(i: int) -> float:
        return left(i) + (unit if i <= sig else STEP_WIDTH * unit) / 2

    for k in range(5):  # grid and price scale
        price = low - pad + (high - low + 2 * pad) * (k + 0.5) / 5
        draw.line([(x0, y(price)), (x1, y(price))], fill=GRID)
        draw.text((x1 + 10, y(price)), price_text(price), font=_font(15), fill=MUTED, anchor="lm")

    for j, label in enumerate(steps):  # entry candles; once decided, the lost ones red and the decider in its colour
        i = sig + 1 + j
        if result is not None and j <= result[0]:
            fill = _rgba(UP if result[1] and j == result[0] else DOWN, 70)
        else:
            fill = _rgba(color, 46 if j == 0 else 24)
        draw.rectangle([left(i), y0, left(i) + STEP_WIDTH * unit, y1], fill=fill)
        draw.text((x(i), y0 + 8), label, font=_font(13), fill=TEXT, anchor="mt")
        draw.text((x(i), y1 + 12), time_text(times[pos] + spacing * (j + 1)), font=_font(14), fill=color, anchor="mt")

    for i in range(0, sig - 1, max(1, (sig + 1) // 7)):  # time scale, clear of the signal and entry times
        draw.text((x(i), y1 + 12), time_text(times[start] + spacing * i), font=_font(14), fill=MUTED, anchor="mt")
    draw.text((x(sig), y1 + 12), time_text(times[pos]), font=_font(14), fill=TEXT, anchor="mt")

    for i, row in enumerate(rows.itertuples()):  # candles
        body = UP if row.close >= row.open else DOWN
        draw.line([(x(i), y(row.high)), (x(i), y(row.low))], fill=body, width=2)
        top, bottom = sorted((y(row.open), y(row.close)))
        draw.rectangle([x(i) - unit * 0.32, top, x(i) + unit * 0.32, max(bottom, top + 1)], fill=body)

    # Signal marker: a triangle beyond the signal candle's wick, pointing at it, with the marker name.
    cx = x(sig)
    if side == "buy":
        tip = y(float(rows["low"].iloc[sig])) + 8
        draw.polygon([(cx, tip), (cx - 10, tip + 18), (cx + 10, tip + 18)], fill=color)
        draw.text((cx + 10, tip + 24), marker, font=_font(14), fill=color, anchor="rt")  # clear of the entry bands
    else:
        tip = y(float(rows["high"].iloc[sig])) - 8
        draw.polygon([(cx, tip), (cx - 10, tip - 18), (cx + 10, tip - 18)], fill=color)
        draw.text((cx + 10, tip - 24), marker, font=_font(14), fill=color, anchor="rb")

    # Entry price: dashed from the signal candle across the entry candles, tagged on the price scale.
    entry = float(candles["close"].iloc[pos])
    ey, end = y(entry), left(sig + 1 + len(steps))
    for dash in range(int(cx), int(end), 12):
        draw.line([(dash, ey), (min(dash + 6, end), ey)], fill=color, width=2)
    tag = price_text(entry)
    draw.rectangle([x1 + 4, ey - 12, x1 + 14 + draw.textlength(tag, font=_font(15)), ey + 12], fill=color)
    draw.text((x1 + 9, ey), tag, font=_font(15), fill="#ffffff", anchor="lm")

    draw.text((LEFT, 22), title, font=_font(30), fill=TEXT)
    draw.text((LEFT, 62), subtitle, font=_font(16), fill=MUTED)
    right = WIDTH - LEFT
    tags = [(badge, color)] + ([(result[2], UP if result[1] else DOWN)] if result else [])
    for text, fill in reversed(tags):  # side badge, then the result once known, top right
        width = draw.textlength(text, font=_font(26)) + 32
        draw.rounded_rectangle([right - width, 22, right, 66], radius=8, fill=fill)
        draw.text((right - width / 2, 44), text, font=_font(26), fill="#ffffff", anchor="mm")
        right -= width + 12

    out = BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
