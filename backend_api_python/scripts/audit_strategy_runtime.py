"""Compare isolated runtime revisions without a database or exchange connection."""

import argparse
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
import re
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]


def contained_path(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError(f"Path must stay within {resolved_root}")
    return resolved_candidate


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def scenarios():
    sys.path.insert(0, str(ROOT))
    from app.services.strategy_runtime.executors import build_executor_strategy_payload, executor_templates

    sql = (ROOT / "migrations/strategy_v2_templates.sql").read_text(encoding="utf-8")
    pattern = re.compile(r"\('(?P<key>strategy_v2_[^']+)', '(?:script|portfolio_strategy)', .*?\$(?P<tag>[a-z]+)\$(?P<code>.*?)\$(?P=tag)\$", re.S)
    result = [{"name": m["key"], "code": m["code"]} for m in pattern.finditer(sql)]
    result.extend([
        {**item, "name": item["name"] + "_triggered", "wide_body": True}
        for item in list(result) if "bullish_three_lines" in item["name"]
    ])
    for item in executor_templates()["items"]:
        kind = item["executor_type"]
        for side in (["long"] if kind == "dca" else ["long", "short"]):
            payload = build_executor_strategy_payload({
                **item["defaults"], "executor_type": kind, "execution_mode": "signal",
                "symbol": "BTC/USDT", "side": side,
            }, user_id=7)
            result.append({"name": f"robot_{kind}_{side}", "code": payload["code"]})
    return result


def frame_for(rank, frequency, periods):
    import numpy as np
    import pandas as pd
    from app.services.strategy_v2.frequencies import frequency_seconds

    x = np.arange(periods)
    price = 100 + rank * 5 + x * .02 + np.sin(x / (7 + rank)) * 7 + np.sin(x / 31) * 5
    price += np.where((x // 40) % 2, 8, -8)
    opening = price * np.where(x % 23 == 0, .96, .999)
    return pd.DataFrame({
        "open": opening, "high": price * 1.015, "low": np.minimum(opening, price) * .985,
        "close": price, "volume": 1000000., "market_cap": (rank + 1) * 1e9,
        "return_on_equity": .12 + rank * .01, "revenue_growth": .08 + rank * .008,
        "debt_to_equity": .4 + rank * .05, "industry": "tech" if rank % 2 else "finance",
    }, index=pd.date_range("2025-01-01", periods=periods, freq=pd.Timedelta(seconds=frequency_seconds(frequency))))


def worker(args):
    if args.audit_root is None or args.input is None:
        raise ValueError("worker mode requires --audit-root and --input")
    audit_root = args.audit_root.resolve()
    input_path = contained_path(audit_root, args.input)
    output_path = contained_path(audit_root, args.output)
    sys.path.insert(0, str(args.app_root))
    from app.services.strategy_v2 import StrategyV2BacktestRunner, StrategyV2LiveSession, compile_strategy_v2
    from app.services.strategy_v2.frequencies import frequency_seconds
    import pandas as pd

    results = []
    with output_path.open("w", encoding="utf-8") as output_stream:
        for scenario in json.loads(input_path.read_text(encoding="utf-8")):
            code = scenario["code"]
            manifest = compile_strategy_v2(code).manifest
            frames = {
                item.key: frame_for(rank, manifest.driving_frequency, args.bars)
                for rank, item in enumerate(manifest.universe.instruments)
            }
            if scenario.get("wide_body"):
                for frame in frames.values():
                    frame["open"] = frame["close"] * .8
                    frame["low"] = frame["open"] * .985
            durations, hashes = [], []
            for _ in range(args.repeats):
                start = time.perf_counter()
                result = StrategyV2BacktestRunner(
                    code=code, frames=frames, initial_capital=100000,
                    commission=.0005, slippage=.0005,
                ).run()
                durations.append(time.perf_counter() - start)
                hashes.append(digest(result))
            assert len(set(hashes)) == 1, scenario["name"]
            if scenario.get("wide_body"):
                assert result["totalExecutions"] > 0, scenario["name"]

            def session(current_frames):
                return StrategyV2LiveSession(
                    code=code, frames=current_frames, initial_capital=100000,
                    params={"persist_runtime_state": True}, schedule_timezone="UTC",
                )

            live_records = []
            live_seconds = 0
            active = None
            for offset in range(args.bars - 12, args.bars):
                current = {key: frame.iloc[:offset + 1] for key, frame in frames.items()}
                if active is None:
                    active = session(current)
                elif offset == args.bars - 6:
                    saved = active.session_snapshot()
                    active = session(current)
                    active.restore_session_snapshot(saved)
                # Fixed external snapshots exercise both flat and held legs without sending orders.
                positions = {
                    key: {"amount": (2 if offset % 3 else 0) * (-1 if manifest.direction_mode == "short_only" else 1),
                          "avg_cost": float(frame.close.iloc[-1]), "last_price": float(frame.close.iloc[-1]),
                          "side": "short" if manifest.direction_mode == "short_only" else "long"}
                    for key, frame in current.items()
                }
                active.synchronize_positions(positions, available_cash=90000, total_value=100000)
                when = next(iter(current.values())).index[-1]
                clock = when + pd.Timedelta(seconds=frequency_seconds(manifest.driving_frequency))
                start = time.perf_counter()
                orders, logs, timestamp = active.process(current, schedule_time=clock)
                live_seconds += time.perf_counter() - start
                repeated, repeated_logs, _ = active.process(current, schedule_time=clock)
                live_records.append({
                    "orders": [asdict(item) for item in orders], "logs": logs,
                    "time": timestamp, "repeatedOrders": [asdict(item) for item in repeated],
                    "repeatedLogs": repeated_logs, "snapshot": active.session_snapshot(),
                })
            entry = dict(
                name=scenario["name"], strategy_type=manifest.strategy_type, symbols=len(frames),
                seconds=durations, median_seconds=statistics.median(durations),
                result_sha256=hashes[0], executions=result["totalExecutions"],
                audit=result["audit"]["passed"], live_sha256=digest(live_records),
                live_seconds=live_seconds, live_duplicate_orders=sum(len(r["repeatedOrders"]) for r in live_records),
            )
            results.append(entry)
            output_stream.seek(0)
            output_stream.truncate()
            json.dump(results, output_stream, indent=2)
            output_stream.flush()
            print(json.dumps(entry), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--bars", type=int, default=320)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--docker-image")
    parser.add_argument("--app-root", type=Path, default=ROOT)
    parser.add_argument("--audit-root", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.worker:
        return worker(args)
    revision = subprocess.check_output(["git", "rev-parse", args.revision], cwd=ROOT).decode().strip()
    args.output.mkdir(parents=True, exist_ok=True)
    args.output = args.output.resolve()
    inputs = args.output / "scenarios.json"
    inputs.write_text(json.dumps(scenarios()), encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="qd-runtime-audit-") as temporary:
        archive = subprocess.check_output(["git", "archive", revision, "backend_api_python/app"], cwd=ROOT.parent)
        with tarfile.open(fileobj=io.BytesIO(archive)) as files:
            files.extractall(temporary, filter="data")
        baseline_root = Path(temporary) / "backend_api_python"
        for name, app_root in [("baseline", baseline_root), ("current", ROOT)]:
            if args.docker_image:
                command = [
                    "docker", "run", "--rm", "--network", "none",
                    "-e", "PYTHONDONTWRITEBYTECODE=1",
                    "--mount", f"type=bind,src={ROOT},dst=/work,readonly",
                    "--mount", f"type=bind,src={app_root},dst=/source,readonly",
                    "--mount", f"type=bind,src={args.output},dst=/audit",
                    "--workdir", "/work", "--entrypoint", "python", args.docker_image,
                    "/work/scripts/audit_strategy_runtime.py", "--worker", "--app-root", "/source",
                    "--audit-root", "/audit",
                    "--input", "/audit/scenarios.json", "--output", f"/audit/{name}.json",
                ]
            else:
                command = [
                    sys.executable, str(Path(__file__).resolve()), "--worker", "--app-root", str(app_root),
                    "--audit-root", str(args.output),
                    "--input", str(inputs), "--output", str(args.output / f"{name}.json"),
                ]
            subprocess.run(command + ["--bars", str(args.bars), "--repeats", str(args.repeats)], check=True, cwd=ROOT)
    baseline = json.loads((args.output / "baseline.json").read_text())
    current = json.loads((args.output / "current.json").read_text())
    comparisons = [dict(
        name=old["name"], result_equal=old["result_sha256"] == new["result_sha256"],
        live_equal=old["live_sha256"] == new["live_sha256"],
        speedup=old["median_seconds"] / new["median_seconds"],
        baseline_seconds=old["median_seconds"], current_seconds=new["median_seconds"],
        executions=new["executions"], audit=new["audit"],
    ) for old, new in zip(baseline, current)]
    report = {"baseline_revision": revision, "docker_image": args.docker_image, "bars": args.bars,
              "repeats": args.repeats, "comparisons": comparisons}
    (args.output / "comparison.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert all(r["result_equal"] and r["live_equal"] and r["audit"] for r in comparisons), report
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
