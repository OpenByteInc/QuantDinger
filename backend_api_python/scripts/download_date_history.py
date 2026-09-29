#!/usr/bin/env python3
"""Download CN stock and CN futures history into the DATE archive.

Uses the same DataSourceFactory path as the running backend. Local CSV
read is disabled for this process so each run refreshes from remote and
merges into DATE/.

Examples (from backend_api_python, or inside the backend container):

    python scripts/download_date_history.py
    python scripts/download_date_history.py --market stocks --limit 1500
    python scripts/download_date_history.py --market futures --symbols RB0,SC0,IF0
    python scripts/download_date_history.py --universe DATE/universe.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("SECRET_KEY", "download-date-history")
os.environ.setdefault("ADMIN_USER", "download")
os.environ.setdefault("ADMIN_PASSWORD", "download-pass")
# Always hit remote providers; still write CSV via QUANTDINGER_DATE_WRITE.
os.environ["QUANTDINGER_DATE_READ"] = "false"
os.environ.setdefault("QUANTDINGER_DATE_WRITE", "true")

from app.data_sources.factory import DataSourceFactory  # noqa: E402
from app.data_sources.local_archive import date_root, kline_path, read_klines  # noqa: E402

DEFAULT_STOCKS = [
    "000001",
    "000333",
    "000725",
    "000858",
    "002415",
    "002594",
    "300750",
    "600036",
    "600276",
    "600519",
    "600900",
    "601012",
    "601166",
    "601318",
    "601899",
    "000001.SH",
    "000300.SH",
    "000016.SH",
    "399001.SZ",
    "399006.SZ",
]

DEFAULT_FUTURES = [
    "RB0",
    "HC0",
    "I0",
    "CU0",
    "AU0",
    "AG0",
    "SC0",
    "M0",
    "C0",
    "CF0",
    "TA0",
    "MA0",
    "IF0",
    "IH0",
    "IC0",
    "IM0",
]


def _parse_symbols(raw: str) -> List[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def _load_universe(path: str) -> Dict[str, List[str]]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    stocks = data.get("CNStock") or data.get("stocks") or []
    futures = data.get("Futures") or data.get("futures") or []
    return {"CNStock": list(stocks), "Futures": list(futures)}


def _download_one(market: str, symbol: str, timeframe: str, limit: int) -> Dict[str, Any]:
    rows = DataSourceFactory.get_kline(market, symbol, timeframe, limit)
    path = kline_path(market, symbol, timeframe)
    stored = read_klines(market, symbol, timeframe) if path else []
    return {
        "market": market,
        "symbol": symbol,
        "fetched": len(rows),
        "stored": len(stored),
        "path": str(path) if path else "",
        "ok": bool(rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Download stock/futures history into DATE/")
    parser.add_argument("--market", choices=("all", "stocks", "futures"), default="all")
    parser.add_argument("--timeframe", default="1D")
    parser.add_argument("--limit", type=int, default=1500)
    parser.add_argument("--symbols", default="", help="Comma-separated symbols; overrides defaults")
    parser.add_argument("--universe", default="", help="JSON file with CNStock / Futures lists")
    parser.add_argument("--sleep", type=float, default=0.35, help="Pause between symbols (seconds)")
    args = parser.parse_args()

    stocks = list(DEFAULT_STOCKS)
    futures = list(DEFAULT_FUTURES)
    if args.universe:
        loaded = _load_universe(args.universe)
        stocks = loaded["CNStock"] or stocks
        futures = loaded["Futures"] or futures
    if args.symbols:
        picked = _parse_symbols(args.symbols)
        if args.market == "futures":
            futures = picked
        elif args.market == "stocks":
            stocks = picked
        else:
            stocks = picked
            futures = picked

    jobs: List[tuple[str, str]] = []
    if args.market in ("all", "stocks"):
        jobs.extend(("CNStock", s) for s in stocks)
    if args.market in ("all", "futures"):
        jobs.extend(("Futures", s) for s in futures)

    root = date_root()
    root.mkdir(parents=True, exist_ok=True)
    print(f"DATE root: {root}")
    print(f"jobs={len(jobs)} timeframe={args.timeframe} limit={args.limit}")

    failed = 0
    for i, (market, symbol) in enumerate(jobs, start=1):
        try:
            result = _download_one(market, symbol, args.timeframe, args.limit)
        except Exception as exc:
            failed += 1
            print(f"[{i}/{len(jobs)}] {market} {symbol} ERROR {exc}")
            continue
        status = "ok" if result["ok"] else "empty"
        if not result["ok"]:
            failed += 1
        print(
            f"[{i}/{len(jobs)}] {status:5} {market:8} {symbol:12} "
            f"fetched={result['fetched']:<5} stored={result['stored']:<5} {result['path']}"
        )
        if args.sleep and i < len(jobs):
            time.sleep(args.sleep)

    print(f"done failed={failed}/{len(jobs)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
