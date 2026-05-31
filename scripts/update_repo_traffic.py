#!/usr/bin/env python3
"""Accumulate GitHub repo view/clone counts into docs/metrics/traffic.json.

GitHub Traffic API only returns the last 14 days per request. This script
merges daily rows into ``view_days`` / ``clone_days`` so totals grow over time
instead of resetting every fortnight.

Run in GitHub Actions (``repo-traffic-badges.yml``) or locally:

    set GITHUB_REPOSITORY=owner/repo
    set GITHUB_TOKEN=ghp_...
    python scripts/update_repo_traffic.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
METRICS_PATH = REPO_ROOT / "docs" / "metrics" / "traffic.json"
HISTORY_MD_PATH = REPO_ROOT / "docs" / "metrics" / "traffic-history.md"
API = "https://api.github.com/repos/{owner}/{repo}/traffic"


def _api_get(path: str, token: str, owner: str, repo: str) -> dict:
    req = urllib.request.Request(
        f"{API.format(owner=owner, repo=repo)}/{path}?per=day",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _merge_days(existing: dict[str, int], rows: list[dict], count_key: str = "count") -> dict[str, int]:
    out = dict(existing or {})
    for row in rows or []:
        ts = (row.get("timestamp") or "")[:10]
        if not ts:
            continue
        # Keep the higher count if GitHub revises a day within the 14-day window.
        out[ts] = max(int(out.get(ts) or 0), int(row.get(count_key) or 0))
    return out


def _render_history_md(
    *,
    owner: str,
    repo: str,
    views_total: int,
    clones_total: int,
    view_days: dict[str, int],
    clone_days: dict[str, int],
    baseline_views: int,
    baseline_clones: int,
    updated_at: str,
) -> str:
    all_dates = sorted(set(view_days) | set(clone_days), reverse=True)
    lines = [
        "# Repository traffic history",
        "",
        "Cumulative **GitHub official** traffic for "
        f"[{owner}/{repo}](https://github.com/{owner}/{repo}). "
        "Updated daily by "
        "[`.github/workflows/repo-traffic-badges.yml`](../../.github/workflows/repo-traffic-badges.yml).",
        "",
        "GitHub only exposes ~14 days of detail per API call; this file **merges daily rows** "
        "into the repo so totals keep growing.",
        "",
        "| Metric | Total |",
        "|--------|------:|",
        f"| **Page views** | **{views_total:,}** |",
        f"| **Git clones** | **{clones_total:,}** |",
        "",
    ]
    if baseline_views or baseline_clones:
        lines.extend([
            f"_Includes manual baseline: {baseline_views:,} views, {baseline_clones:,} clones "
            "(set in `traffic.json` before automated tracking)._",
            "",
        ])
    if updated_at:
        lines.append(f"Last updated: `{updated_at}` (UTC)")
        lines.append("")
    if not all_dates:
        lines.append("_No daily rows yet — run the traffic workflow once on GitHub Actions._")
        lines.append("")
        return "\n".join(lines) + "\n"

    lines.extend([
        "## Daily breakdown (newest first)",
        "",
        "| Date | Views | Clones |",
        "|------|------:|-------:|",
    ])
    for day in all_dates:
        lines.append(
            f"| {day} | {int(view_days.get(day) or 0):,} | {int(clone_days.get(day) or 0):,} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("GITHUB_TOKEN not set", file=sys.stderr)
        return 1

    repo_full = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo_full or "/" not in repo_full:
        print("GITHUB_REPOSITORY not set (expected owner/repo)", file=sys.stderr)
        return 1
    owner, repo = repo_full.split("/", 1)

    if METRICS_PATH.is_file():
        data = json.loads(METRICS_PATH.read_text(encoding="utf-8"))
    else:
        data = {}

    clone_days: dict[str, int] = dict(data.get("clone_days") or {})
    view_days: dict[str, int] = dict(data.get("view_days") or {})
    baseline_views = int(data.get("baseline_views") or 0)
    baseline_clones = int(data.get("baseline_clones") or 0)

    try:
        clones = _api_get("clones", token, owner, repo)
        clone_days = _merge_days(clone_days, clones.get("clones") or [])
    except urllib.error.HTTPError as exc:
        print(f"clones API failed: {exc}", file=sys.stderr)

    try:
        views = _api_get("views", token, owner, repo)
        view_days = _merge_days(view_days, views.get("views") or [])
    except urllib.error.HTTPError as exc:
        print(f"views API failed: {exc}", file=sys.stderr)

    views_total = baseline_views + sum(view_days.values())
    clones_total = baseline_clones + sum(clone_days.values())
    updated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    all_days = sorted(set(view_days) | set(clone_days))
    tracking_since = all_days[0] if all_days else data.get("tracking_since")

    out = {
        "repo": repo_full,
        "views_total": views_total,
        "clones_total": clones_total,
        "baseline_views": baseline_views,
        "baseline_clones": baseline_clones,
        "tracking_since": tracking_since,
        "updated_at": updated_at,
        "view_days": view_days,
        "clone_days": clone_days,
    }
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")

    HISTORY_MD_PATH.write_text(
        _render_history_md(
            owner=owner,
            repo=repo,
            views_total=views_total,
            clones_total=clones_total,
            view_days=view_days,
            clone_days=clone_days,
            baseline_views=baseline_views,
            baseline_clones=baseline_clones,
            updated_at=updated_at,
        ),
        encoding="utf-8",
    )
    print(
        f"Wrote {METRICS_PATH} and {HISTORY_MD_PATH}: "
        f"views={views_total} clones={clones_total} days={len(all_days)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
