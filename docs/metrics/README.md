# Repository traffic metrics

Public, cumulative traffic for this GitHub repository (official GitHub Traffic API).

| File | Purpose |
|------|---------|
| [`traffic.json`](traffic.json) | Machine-readable totals + per-day history (used by README badges) |
| [`traffic-history.md`](traffic-history.md) | Human-readable daily table |

## How it works

1. [`.github/workflows/repo-traffic-badges.yml`](../../.github/workflows/repo-traffic-badges.yml) runs daily (and on manual dispatch).
2. [`scripts/update_repo_traffic.py`](../../scripts/update_repo_traffic.py) fetches the last **14 days** from GitHub.
3. Daily rows merge into `view_days` / `clone_days` — **older days already stored are kept**, so totals accumulate.

## README badges

- **Repo Views** → `views_total` (repository page views)
- **Git Clones** → `clones_total` (`git clone` events)

## Optional baseline (pre-tracking history)

If you tracked views elsewhere before this workflow existed, set once in `traffic.json`:

```json
{
  "baseline_views": 120000,
  "baseline_clones": 8500
}
```

The next workflow run adds API-measured days on top (does not overwrite baselines).

## Run manually

GitHub → **Actions** → **Update repo traffic badges** → **Run workflow**

Or locally (needs a token with `repo` read access to the repository):

```bash
export GITHUB_REPOSITORY=brokermr810/QuantDinger
export GITHUB_TOKEN=ghp_...
python scripts/update_repo_traffic.py
```
