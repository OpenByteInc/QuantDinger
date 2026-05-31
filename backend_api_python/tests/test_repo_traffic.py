"""Tests for cumulative GitHub traffic metrics."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.update_repo_traffic import _merge_days, _render_history_md


def test_merge_days_keeps_higher_count_and_accumulates():
    existing = {"2026-05-01": 100, "2026-05-02": 50}
    rows = [
        {"timestamp": "2026-05-02T00:00:00Z", "count": 55},
        {"timestamp": "2026-05-03T00:00:00Z", "count": 80},
    ]
    merged = _merge_days(existing, rows)
    assert merged["2026-05-01"] == 100
    assert merged["2026-05-02"] == 55
    assert merged["2026-05-03"] == 80
    assert sum(merged.values()) == 235


def test_render_history_includes_totals_and_baseline_note():
    md = _render_history_md(
        owner="brokermr810",
        repo="QuantDinger",
        views_total=1200,
        clones_total=340,
        view_days={"2026-05-30": 100, "2026-05-31": 120},
        clone_days={"2026-05-31": 12},
        baseline_views=1000,
        baseline_clones=300,
        updated_at="2026-05-31T12:00:00Z",
    )
    assert "**1,200**" in md
    assert "**340**" in md
    assert "baseline" in md
    assert "2026-05-31" in md
