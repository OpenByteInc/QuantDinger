"""Two-phase isolated proof for ChatGPT account -> native Strategy V2.

Generate runs on the Windows host with the local Codex login. Backtest runs in
an ephemeral ``--network none`` QuantDinger image. The JSON handoff is hashed
and contains no credential material.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import re
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]
TEMPLATE_SQL = ROOT / "migrations" / "strategy_v2_templates.sql"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def load_transport():
    path = ROOT / "app" / "services" / "chatgpt_account.py"
    spec = importlib.util.spec_from_file_location("qd_pipeline_chatgpt", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def template_code() -> str:
    sql = TEMPLATE_SQL.read_text(encoding="utf-8")
    match = re.search(
        r"\('strategy_v2_double_ma'.*?\$(?P<tag>[a-z]+)\$(?P<code>.*?)\$(?P=tag)\$",
        sql,
        re.DOTALL,
    )
    if not match:
        raise RuntimeError("strategy_v2_double_ma template not found")
    return match.group("code").strip()


def strip_fence(value: str) -> str:
    match = re.search(r"```(?:python)?\s*(.*?)```", value, re.DOTALL | re.IGNORECASE)
    return (match.group(1) if match else value).strip()


def generate(path: Path, model: str) -> None:
    original = template_code()
    spec_text = (
        "Development-only synthetic SMA revision: preserve every Strategy API V2 contract and "
        "change only the fast_period default from 10 to 12. Return the complete source only."
    )
    transport = load_transport()
    client = transport.CodexAppServerClient(timeout_seconds=180)
    discussion = client.complete(
        [{"role": "user", "content": "In one short sentence, confirm you can revise a supplied Strategy API V2 source without using tools."}],
        model=model,
    )
    revised = strip_fence(client.complete(
        [
            {"role": "system", "content": "Return Python source only. Do not call tools and do not invent a different API."},
            {"role": "user", "content": f"# Change specification\n{spec_text}\n\n# Existing verified source\n{original}"},
        ],
        model=model,
    ))
    if not revised or revised == original:
        raise RuntimeError("model did not produce a revised candidate")
    payload = {
        "task_id": "AQ-QD-AUTH-001",
        "model": model,
        "discussion": discussion,
        "spec": spec_text,
        "spec_hash": sha256_text(spec_text),
        "base_code_hash": sha256_text(original),
        "candidate_code": revised,
        "candidate_code_hash": sha256_text(revised),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("model", "spec_hash", "base_code_hash", "candidate_code_hash")}))


def backtest(path: Path, result_path: Path) -> None:
    import pandas as pd
    from app.services.strategy_v2 import StrategyV2BacktestRunner, compile_strategy_v2

    payload = json.loads(path.read_text(encoding="utf-8"))
    code = str(payload["candidate_code"])
    if sha256_text(code) != payload["candidate_code_hash"]:
        raise RuntimeError("candidate hash mismatch")
    program = compile_strategy_v2(code)
    index = pd.date_range("2025-01-01", periods=420, freq="4h")
    closes = [100 + (i * 0.025) + 8.0 * math.sin(i / 9.0) for i in range(len(index))]
    frame = pd.DataFrame({
        "open": [x * 0.998 for x in closes],
        "high": [x * 1.006 for x in closes],
        "low": [x * 0.994 for x in closes],
        "close": closes,
        "volume": [1_000_000.0] * len(index),
    }, index=index)
    frames = {instrument.key: frame.copy() for instrument in program.manifest.universe.instruments}
    result = StrategyV2BacktestRunner(
        code=code,
        frames=frames,
        initial_capital=100_000,
        commission=0.001,
        slippage=0.0005,
    ).run()
    run_id = f"AQ-QD-AUTH-001-{payload['candidate_code_hash'][:12]}"
    evidence = {
        "run_id": run_id,
        "engine": result.get("engine"),
        "manifest": result.get("manifest"),
        "trade_count": len(result.get("trades") or []),
        "equity_points": len(result.get("equityCurve") or []),
        "commission": 0.001,
        "slippage": 0.0005,
        "network": "none",
        "persisted_to_production": False,
        "candidate_code_hash": payload["candidate_code_hash"],
    }
    result_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("generate", "backtest"))
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--model", default="gpt-6-luna")
    args = parser.parse_args()
    if args.phase == "generate":
        generate(args.artifact, args.model)
    else:
        if not args.result:
            parser.error("--result is required for backtest")
        backtest(args.artifact, args.result)


if __name__ == "__main__":
    main()
