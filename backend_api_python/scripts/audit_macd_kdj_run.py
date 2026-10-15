"""Independently audit MACD/KDJ signals for a persisted market-data run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from app.services.factors import compute_talib_indicator
from app.services.script_source import get_script_source_service
from app.services.strategy_v2.market_data import load_strategy_frame
from app.services.strategy_v2.service import _frame_provenance
from app.services.strategy_v2.snapshot import MarketDataSnapshotStore
from app.services.strategy_v2.storage import StrategyBacktestRepository


def audit_run(*, user_id: int, run_id: int) -> dict:
    run = StrategyBacktestRepository().get_run(user_id=user_id, run_id=run_id)
    if not run:
        raise RuntimeError(f"Run {run_id} was not found")
    source = get_script_source_service().get_source(int(run["source_id"]), user_id)
    if not source or source.get("template_key") != "strategy_v2_macd_kdj":
        raise RuntimeError("The selected run is not the default MACD/KDJ strategy")
    result = run.get("result") or {}
    provenance = ((result.get("dataProvenance") or {}).get("symbols") or [None])[0]
    if not provenance:
        raise RuntimeError("The run does not contain market-data provenance")

    if provenance.get("snapshotId"):
        frame = MarketDataSnapshotStore().load(str(provenance["snapshotId"]))
        replay_source = "persisted_content_addressed_snapshot"
    else:
        frame = load_strategy_frame(
            "Crypto",
            "BTC/USDT",
            str(run.get("timeframe") or "4h"),
            pd.Timestamp(provenance["firstBar"]).to_pydatetime(),
            pd.Timestamp(provenance["lastBar"]).to_pydatetime(),
            market_type="spot",
        )
        replay_source = "market_data_refetch"
    params = run.get("params") or {}
    macd = compute_talib_indicator(
        "MACD",
        frame,
        {
            "fastperiod": int(params.get("fast_period", 12)),
            "slowperiod": int(params.get("slow_period", 26)),
            "signalperiod": int(params.get("signal_period", 9)),
        },
    )
    kdj = compute_talib_indicator(
        "KDJ",
        frame,
        {
            "fastk_period": int(params.get("kdj_period", 9)),
            "slowk_period": int(params.get("kdj_smooth_k", 3)),
            "slowd_period": int(params.get("kdj_smooth_d", 3)),
        },
    )
    overbought = float(params.get("overbought", 85))
    replay_provenance = _frame_provenance(str(provenance["instrument"]), frame)
    replay_hash_matches = bool(provenance.get("contentHash")) and replay_provenance["contentHash"] == provenance.get("contentHash")
    simulation_times = [pd.Timestamp(item["time"]) for item in result.get("equityCurve") or []]
    executions = result.get("executions") or result.get("rawTrades") or []
    executions_by_fill: dict[pd.Timestamp, list[dict]] = {}
    for execution in executions:
        executions_by_fill.setdefault(pd.Timestamp(execution["time"]), []).append(execution)

    expected = []
    is_long = False
    for index, timestamp in enumerate(simulation_times):
        for execution in executions_by_fill.get(timestamp, []):
            execution_type = str(execution.get("type") or "")
            if execution_type in {"open_long", "add_long", "reverse_to_long"}:
                is_long = True
            elif execution_type in {"close_long", "reverse_to_short"}:
                is_long = False
        if timestamp not in frame.index or timestamp not in macd.index or timestamp not in kdj.index:
            continue
        location = frame.index.get_loc(timestamp)
        if not isinstance(location, int) or location < 1:
            continue
        values = (
            float(macd["macdhist"].iloc[location - 1]),
            float(macd["macdhist"].iloc[location]),
            float(kdj["k"].iloc[location - 1]),
            float(kdj["d"].iloc[location - 1]),
            float(kdj["k"].iloc[location]),
            float(kdj["d"].iloc[location]),
        )
        if not all(value == value for value in values):
            continue
        previous_histogram, histogram, previous_k, previous_d, k_value, d_value = values
        enter = (
            histogram > 0
            and (previous_histogram <= 0 < histogram or previous_k <= previous_d and k_value > d_value)
            and k_value < overbought
        )
        exit_signal = histogram <= 0 or previous_k >= previous_d and k_value < d_value
        if index >= len(simulation_times) - 1:
            continue
        if enter and not is_long:
            expected.append({"signalTime": str(timestamp), "fillTime": str(simulation_times[index + 1]), "reason": "macd_kdj_entry"})
        elif exit_signal and is_long:
            expected.append({"signalTime": str(timestamp), "fillTime": str(simulation_times[index + 1]), "reason": "macd_kdj_exit"})

    actual = [
        {
            "signalTime": str(pd.Timestamp(item["signal_time"])),
            "fillTime": str(pd.Timestamp(item["time"])),
            "reason": str(item.get("reason") or ""),
        }
        for item in executions
    ]
    expected_keys = {(item["signalTime"], item["fillTime"], item["reason"]) for item in expected}
    actual_keys = {(item["signalTime"], item["fillTime"], item["reason"]) for item in actual}
    missing = sorted(expected_keys - actual_keys)
    unexpected = sorted(actual_keys - expected_keys)
    return {
        "runId": run_id,
        "strategy": source["name"],
        "dataKind": (result.get("dataProvenance") or {}).get("kind"),
        "marketData": provenance,
        "replayContentHash": replay_provenance["contentHash"],
        "replayHashMatches": replay_hash_matches,
        "replaySource": replay_source,
        "indicatorImplementation": "TA-Lib direct recomputation",
        "expectedSignals": len(expected),
        "actualExecutions": len(actual),
        "missingExecutions": [list(item) for item in missing],
        "unexpectedExecutions": [list(item) for item in unexpected],
        "signalAuditPassed": not missing and not unexpected,
        "exactInputReplayPassed": replay_hash_matches,
        "ledgerAuditPassed": bool((result.get("audit") or {}).get("passed")),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit_run(user_id=args.user_id, run_id=args.run_id)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
