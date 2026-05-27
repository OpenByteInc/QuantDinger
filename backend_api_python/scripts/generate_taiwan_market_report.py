"""Manual Taiwan market report generator.

2026/05/27 Steve Peng：新增台股報告手動產生腳本。
修改原因：讓使用者在沒有前端或排程器時，也能用 mock provider 產生台股資訊報告。
修改前代碼：沒有台股報告 CLI。
修改後功能：輸出開盤前/收盤後報告或候選股回測 JSON；不含下單、券商連線或交易執行。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.taiwan_market import TaiwanMarketService, create_taiwan_market_provider  # noqa: E402


def _parse_date(value: str | None):
    """功能：解析 CLI 日期參數；未提供時由 service 使用 Asia/Taipei 今日。"""
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _write_json(payload: dict, output: str | None) -> None:
    """功能：將報告輸出到 stdout 或指定檔案。"""
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if output:
        target = Path(output).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + os.linesep, encoding="utf-8")
        print(str(target))
        return
    print(text)


def main() -> int:
    """功能：CLI 入口，支援報告與回測手動產生。

    使用說明：
      python scripts/generate_taiwan_market_report.py --session pre_market
      python scripts/generate_taiwan_market_report.py --session post_market --output report.json
      python scripts/generate_taiwan_market_report.py --backtest --days 60
    """
    parser = argparse.ArgumentParser(description="Generate Taiwan market read-only reports.")
    parser.add_argument("--provider", default="mock", help="Data provider name. Default: mock")
    parser.add_argument("--session", default="pre_market", choices=["pre_market", "post_market"], help="Report session.")
    parser.add_argument("--date", default="", help="Report date in YYYY-MM-DD.")
    parser.add_argument("--top", type=int, default=20, help="Number of candidates.")
    parser.add_argument("--include-etf", action="store_true", help="Include ETFs in the universe.")
    parser.add_argument("--backtest", action="store_true", help="Generate backtest summary instead of market report.")
    parser.add_argument("--days", type=int, default=60, help="Backtest sample days.")
    parser.add_argument("--output", default="", help="Optional output JSON path.")
    args = parser.parse_args()

    service = TaiwanMarketService(provider=create_taiwan_market_provider(args.provider))
    if args.backtest:
        payload = service.backtest_top_candidates(days=args.days, top_n=args.top, include_etf=args.include_etf)
    else:
        payload = service.generate_report(
            args.session,
            as_of=_parse_date(args.date),
            top_n=args.top,
            include_etf=args.include_etf,
        )
    _write_json(payload, args.output or None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
