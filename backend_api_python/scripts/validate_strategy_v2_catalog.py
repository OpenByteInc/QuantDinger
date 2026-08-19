"""Create and validate the complete Strategy API V2 default catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from app.services.script_source import get_script_source_service
from app.services.strategy_runtime.executors import build_executor_strategy_payload, executor_templates
from app.services.strategy_v2 import (
    StrategyV2BacktestService,
    StrategyV2DeploymentService,
    StrategyV2LiveSession,
    canonical_source_metadata,
    compile_strategy_v2,
)
from app.utils.db import get_db_connection


SUITE_KEY = "strategy_v2_catalog_acceptance"
START_DATE = datetime(2025, 7, 1)
END_DATE = datetime(2026, 6, 29, 23, 59)
UNIVERSE_MEMBERS = (
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "GOOGL",
    "AVGO",
    "COST",
    "JPM",
    "LLY",
    "XOM",
    "UNH",
)


class AcceptanceUniverseService:
    def list_universes(self, _user_id: int) -> list[dict[str, Any]]:
        return [{"id": 91001, "code": "sp500", "source_ref": "SP500"}]

    def candidate_members(self, _user_id: int, _universe_id: int, **_kwargs: Any) -> list[dict[str, Any]]:
        return self._members()

    def resolve_members(self, _user_id: int, _universe_id: int, **_kwargs: Any) -> list[dict[str, Any]]:
        return self._members()

    @staticmethod
    def _members() -> list[dict[str, Any]]:
        return [
            {
                "market": "USStock",
                "symbol": symbol,
                "exchange_id": "",
                "market_type": "spot",
                "instrument_id": "",
            }
            for symbol in UNIVERSE_MEMBERS
        ]


def controlled_fixture_frame(
    market: str,
    symbol: str,
    frequency: str,
    _start_date: datetime,
    end_date: datetime,
    **_kwargs: Any,
) -> pd.DataFrame:
    normalized = str(frequency or "1d").strip().lower()
    if normalized.endswith("m"):
        periods, pandas_frequency = 900, f"{max(1, int(normalized[:-1] or 1))}min"
    elif normalized.endswith("h"):
        periods, pandas_frequency = 900, f"{max(1, int(normalized[:-1] or 1))}h"
    else:
        periods, pandas_frequency = 620, "B"
    index = pd.date_range(end=pd.Timestamp(end_date), periods=periods, freq=pandas_frequency)
    seed = int(hashlib.sha256(f"{market}:{symbol}".encode("utf-8")).hexdigest()[:8], 16)
    offset = (seed % 31) / 31.0
    x = np.arange(periods, dtype=float)
    base = 100_000.0 if str(market) == "Crypto" else 75.0 + float(seed % 180)
    regime = 1.0 + 0.00035 * x + 0.045 * np.sin(x / 19.0 + offset) + 0.018 * np.sin(x / 4.0)
    close = base * regime
    open_values = np.roll(close, 1)
    open_values[0] = close[0] * 0.998
    if str(market) == "CNStock" and periods > 420:
        for event_index in (periods - 220, periods - 90):
            anchor = float(close[event_index - 1])
            open_values[event_index] = anchor * 0.965
            close[event_index] = anchor * 1.045
            close[event_index + 1:event_index + 8] = np.linspace(anchor * 1.03, anchor * 0.96, 7)
    high = np.maximum(open_values, close) * (1.004 + 0.001 * np.sin(x / 7.0) ** 2)
    low = np.minimum(open_values, close) * (0.996 - 0.001 * np.cos(x / 9.0) ** 2)
    volume = 1_000_000.0 + (seed % 500_000) + 180_000.0 * (1.0 + np.sin(x / 11.0))
    factor_offset = float(seed % 17) / 100.0
    return pd.DataFrame(
        {
            "open": open_values,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "market_cap": (50.0 + factor_offset) * 1_000_000_000,
            "pe_ratio": 14.0 + factor_offset * 10,
            "pb_ratio": 2.0 + factor_offset,
            "return_on_equity": 0.14 + factor_offset,
            "revenue_growth": 0.08 + factor_offset / 2,
            "debt_to_equity": 0.35 + factor_offset,
            "free_cash_flow": (3.0 + factor_offset) * 1_000_000_000,
        },
        index=index,
    )


def identity_fundamental_enricher(
    frames: dict[str, pd.DataFrame],
    _members: list[dict[str, Any]],
) -> dict[str, pd.DataFrame]:
    return frames


def default_params(schema: Any) -> dict[str, Any]:
    raw = schema if isinstance(schema, dict) else {}
    return {
        str(item["name"]): item.get("default")
        for item in raw.get("params") or []
        if isinstance(item, dict) and item.get("name")
    }


def catalog_entries() -> list[dict[str, Any]]:
    source_service = get_script_source_service()
    templates = [
        item
        for item in source_service.list_templates()
        if str(item.get("template_key") or "").startswith("strategy_v2_")
    ]
    if len(templates) != 12:
        raise RuntimeError(f"Expected 12 Strategy API V2 templates, found {len(templates)}")
    entries = [
        {
            "catalog_key": item["template_key"],
            "title": item.get("title") or item["template_key"],
            "description": item.get("description") or "",
            "asset_type": item.get("asset_type") or "script",
            "code": item.get("code") or "",
            "param_schema": item.get("param_schema") or {},
            "params": default_params(item.get("param_schema")),
            "catalog_group": "portfolio" if item.get("asset_type") == "portfolio_strategy" else "cta",
        }
        for item in templates
    ]
    for template in executor_templates().get("items") or []:
        kind = str(template["executor_type"])
        generated = build_executor_strategy_payload(
            {
                **(template.get("defaults") or {}),
                "executor_type": kind,
                "strategy_name": f"{kind.replace('_', ' ').title()} Robot",
                "symbol": "BTC/USDT",
                "execution_mode": "signal",
                "initial_capital": 10_000,
                "leverage": 1,
                "dynamic_anchor": True,
                "notification_config": {"channels": ["browser"], "targets": {}},
            },
            user_id=1,
        )
        entries.append(
            {
                "catalog_key": generated["template_key"],
                "title": generated["strategy_name"],
                "description": generated["description"],
                "asset_type": generated["asset_type"],
                "code": generated["code"],
                "param_schema": {},
                "params": {},
                "catalog_group": "robot",
            }
        )
    if len(entries) != 16:
        raise RuntimeError(f"Expected 16 catalog entries, found {len(entries)}")
    return entries


def upsert_source(entry: dict[str, Any], *, user_id: int) -> int:
    service = get_script_source_service()
    existing = next(
        (
            item
            for item in service.list_sources(user_id)
            if (item.get("metadata") or {}).get("acceptanceSuite") == SUITE_KEY
            and (item.get("metadata") or {}).get("catalogKey") == entry["catalog_key"]
        ),
        None,
    )
    metadata, _manifest = canonical_source_metadata(
        entry["code"],
        {
            "acceptanceSuite": SUITE_KEY,
            "catalogKey": entry["catalog_key"],
            "catalogGroup": entry["catalog_group"],
        },
    )
    payload = {
        "user_id": user_id,
        "name": entry["title"],
        "description": entry["description"],
        "code": entry["code"],
        "asset_type": entry["asset_type"],
        "template_key": entry["catalog_key"],
        "param_schema": entry["param_schema"],
        "status": "draft",
        "visibility": "private",
        "metadata": metadata,
    }
    if existing:
        changed = any(
            existing.get(field) != payload[field]
            for field in ("name", "description", "code", "asset_type", "template_key", "param_schema", "metadata")
        )
        if changed:
            service.update_source(int(existing["id"]), user_id, payload)
        return int(existing["id"])
    return int(service.create_source(payload))


def find_deployment(*, user_id: int, source_id: int) -> int | None:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT id
            FROM qd_strategies_trading
            WHERE user_id = ? AND trading_config::jsonb ->> 'script_source_id' = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (int(user_id), str(source_id)),
        )
        row = cur.fetchone() or {}
        cur.close()
    return int(row.get("id") or 0) or None


def upsert_deployment(entry: dict[str, Any], *, user_id: int, source_id: int) -> int:
    existing_id = find_deployment(user_id=user_id, source_id=source_id)
    return StrategyV2DeploymentService().save(
        user_id=user_id,
        strategy_id=existing_id,
        payload={
            "sourceId": source_id,
            "name": entry["title"],
            "initialCapital": 10_000,
            "executionMode": "signal",
            "notificationChannels": ["browser"],
            "notificationTargets": {},
            "leverageEnabled": False,
            "leverage": 1,
            "params": entry["params"],
        },
    )


def validate_entry(
    entry: dict[str, Any],
    *,
    user_id: int,
    source_id: int,
    strategy_id: int,
    service: StrategyV2BacktestService,
) -> dict[str, Any]:
    program = compile_strategy_v2(entry["code"])
    _, result = service.run(
        user_id=user_id,
        code=entry["code"],
        start_date=START_DATE,
        end_date=END_DATE,
        initial_capital=10_000,
        params=entry["params"],
        persist=False,
        strategy_id=strategy_id,
        source_id=source_id,
        strategy_name=entry["title"],
    )
    candidates, _ = service.resolve_candidates(
        user_id=user_id,
        manifest=program.manifest,
        start_date=START_DATE,
        end_date=END_DATE,
    )
    frequency_frames, skipped = service.fetch_frequency_frames(
        candidates,
        program.manifest.frequencies,
        {frequency: START_DATE for frequency in program.manifest.frequencies},
        END_DATE,
    )
    frames = frequency_frames[program.manifest.driving_frequency]
    if program.manifest.fundamental_dependencies:
        frames = identity_fundamental_enricher(frames, candidates)
        frequency_frames[program.manifest.driving_frequency] = frames
    universe_keys = list(frames)
    session = StrategyV2LiveSession(
        code=entry["code"],
        frames=frames,
        frequency_frames=frequency_frames,
        initial_capital=10_000,
        params=entry["params"],
        universe_resolver=lambda _reference, _timestamp: universe_keys,
    )
    intents, logs, timestamp = session.process(
        frames,
        frequency_frames=frequency_frames,
    )
    return {
        "catalogKey": entry["catalog_key"],
        "catalogGroup": entry["catalog_group"],
        "title": entry["title"],
        "sourceId": source_id,
        "strategyId": strategy_id,
        "codeValidation": {
            "status": "passed",
            "apiVersion": program.manifest.api_version,
            "strategyType": program.manifest.strategy_type,
            "frequency": program.manifest.driving_frequency,
            "frequencies": list(program.manifest.frequencies),
        },
        "semanticSimulation": {
            "status": "passed" if result.get("totalExecutions", 0) > 0 and (result.get("audit") or {}).get("passed") else "failed",
            "dataKind": "controlled_fixture",
            "totalExecutions": result.get("totalExecutions"),
            "closedTrades": result.get("totalTrades"),
            "equityPoints": len(result.get("equityCurve") or []),
            "ledgerReconciled": bool((result.get("audit") or {}).get("passed")),
            "persistedAsMarketResult": False,
        },
        "notifyOnly": {
            "status": "passed",
            "executionMode": "signal",
            "notificationChannels": ["browser"],
            "exchangeOrdersAllowed": False,
            "replayTimestamp": timestamp.isoformat(),
            "intents": len(intents),
            "logs": len(logs),
            "symbols": len(frames),
            "symbolsSkipped": len(skipped),
        },
    }


def run(*, user_id: int, output: Path) -> dict[str, Any]:
    entries = catalog_entries()
    service = StrategyV2BacktestService(
        universe_service=AcceptanceUniverseService(),
        frame_fetcher=controlled_fixture_frame,
        fundamental_enricher=identity_fundamental_enricher,
        data_kind="fixture",
        data_source="strategy_specific_controlled_fixture",
    )
    results = []
    for entry in entries:
        source_id = upsert_source(entry, user_id=user_id)
        strategy_id = upsert_deployment(entry, user_id=user_id, source_id=source_id)
        results.append(
            validate_entry(
                entry,
                user_id=user_id,
                source_id=source_id,
                strategy_id=strategy_id,
                service=service,
            )
        )
    summary = {
        "total": len(results),
        "cta": sum(item["catalogGroup"] == "cta" for item in results),
        "portfolio": sum(item["catalogGroup"] == "portfolio" for item in results),
        "robot": sum(item["catalogGroup"] == "robot" for item in results),
        "codeValidationPassed": sum(item["codeValidation"]["status"] == "passed" for item in results),
        "semanticSimulationPassed": sum(item["semanticSimulation"]["status"] == "passed" for item in results),
        "notifyOnlyPassed": sum(item["notifyOnly"]["status"] == "passed" for item in results),
        "exchangeOrdersCreated": 0,
    }
    report = {
        "suite": SUITE_KEY,
        "reportType": "semantic_fixture_validation",
        "marketPerformanceClaims": False,
        "generatedAt": datetime.now().astimezone().isoformat(),
        "userId": user_id,
        "dateRange": {"start": START_DATE.date().isoformat(), "end": END_DATE.date().isoformat()},
        "summary": summary,
        "items": results,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", type=int, default=1)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "reports" / "strategy_v2_catalog_semantic_validation.json",
    )
    args = parser.parse_args()
    report = run(user_id=max(1, args.user_id), output=args.output)
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
