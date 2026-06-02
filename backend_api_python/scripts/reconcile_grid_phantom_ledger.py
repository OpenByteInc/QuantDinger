"""
清理网格策略「phantom 账本」：交易所已 flat，但本地仍有初始底仓成交 / 双向持仓。

典型场景：
- 中性网格在 Bitget 单向模式下初始多+空被 net 成 0，本地仍记 long+short
- 前端隐藏 neutral 的 initialPositionPct，但 DB 残留 15% 导致误记初始底仓

用法（在 backend_api_python 目录下）：
  # 只诊断，不改数据
  python scripts/reconcile_grid_phantom_ledger.py --strategy-id 123

  # 确认后执行清理
  python scripts/reconcile_grid_phantom_ledger.py --strategy-id 123 --apply

  # 交易所仍有仓也强制清本地账本（慎用）
  python scripts/reconcile_grid_phantom_ledger.py --strategy-id 123 --apply --force

建议：先停止策略并确认 Bitget 挂单已撤，再运行本脚本。
"""

from __future__ import annotations

import argparse
import json
import sys


def _print_report(report: dict) -> None:
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile grid phantom ledger vs exchange")
    parser.add_argument("--strategy-id", type=int, required=True, help="qd_strategies_trading.id")
    parser.add_argument("--apply", action="store_true", help="Actually delete phantom ledger rows")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Clear ledger even when exchange still shows positions",
    )
    parser.add_argument(
        "--no-reset-initial-flag",
        action="store_true",
        help="Do not reset grid_resting.initial_market_done",
    )
    args = parser.parse_args()

    from app.services.grid.ledger_reconcile import clear_phantom_grid_ledger

    report = clear_phantom_grid_ledger(
        int(args.strategy_id),
        apply=bool(args.apply),
        force=bool(args.force),
        reset_initial_flag=not bool(args.no_reset_initial_flag),
    )
    _print_report(report)

    if not report.get("cleared") and report.get("would_delete_trade_ids"):
        print("\n[dry-run] Re-run with --apply to execute.", file=sys.stderr)
    if report.get("cleared"):
        print("\n[done] Phantom grid ledger cleared.", file=sys.stderr)


if __name__ == "__main__":
    main()
