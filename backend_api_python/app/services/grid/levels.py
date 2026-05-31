"""Grid level / cell generation (shared with legacy script)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class GridCellSpec:
    index: int
    lower_price: float
    upper_price: float


def generate_levels(lower: float, upper: float, grid_count: int, mode: str) -> List[float]:
    n = max(2, int(grid_count or 2))
    lo, hi = float(lower), float(upper)
    if hi <= lo:
        return []
    if str(mode or "").lower() == "geometric" and lo > 0 and hi > lo:
        ratio = (hi / lo) ** (1.0 / (n - 1))
        return [lo * (ratio ** i) for i in range(n)]
    step = (hi - lo) / float(n - 1)
    return [lo + step * i for i in range(n)]


def generate_cells(levels: List[float]) -> List[GridCellSpec]:
    if not levels or len(levels) < 2:
        return []
    out: List[GridCellSpec] = []
    for i in range(len(levels) - 1):
        lo, hi = float(levels[i]), float(levels[i + 1])
        if hi > lo:
            out.append(GridCellSpec(index=i, lower_price=lo, upper_price=hi))
    return out
