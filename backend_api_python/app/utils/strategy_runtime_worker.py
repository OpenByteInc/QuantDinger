"""Credential-free worker for Strategy API V2 backtest and live callbacks."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import types
from typing import Any, Mapping


_MAX_REQUEST_BYTES = 512 * 1024 * 1024


class _NullWriter:
    def write(self, value: str) -> int:
        return len(value)

    def flush(self) -> None:
        return None


def _install_app_namespace(app_dir: Path) -> None:
    app_package = types.ModuleType("app")
    app_package.__path__ = [str(app_dir)]
    app_package.__package__ = "app"
    sys.modules["app"] = app_package
    sys.path.insert(0, str(app_dir.parent))


def _set_resource_limits(max_memory_mb: object = 2048) -> None:
    if not sys.platform.startswith("linux"):
        return
    import resource

    memory_mb = min(8192, max(512, int(max_memory_mb or 2048)))
    memory = memory_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64 * 1024 * 1024, 64 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))


def _read_request(stream, decode, pd, np) -> dict[str, Any] | None:
    line = stream.readline(_MAX_REQUEST_BYTES + 1)
    if not line:
        return None
    if len(line) > _MAX_REQUEST_BYTES:
        raise ValueError("strategy sandbox request exceeded its limit")
    value = json.loads(line.decode("utf-8"))
    decoded = decode(value, pd, np)
    if not isinstance(decoded, dict):
        raise TypeError("strategy sandbox request must be an object")
    return decoded


def _send(protocol, payload: Mapping[str, Any], encode, pd, np) -> None:
    document = encode(dict(payload), pd, np)
    protocol.write(json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n")
    protocol.flush()


def _order_metadata(order) -> dict[str, Any]:
    return {
        "symbol": str(order.symbol or ""),
        "kind": str(order.kind or ""),
        "value": float(order.value or 0.0),
        "reason": str(order.reason or ""),
        "protection": order.protection.metadata() if order.protection is not None else None,
        "signal_time": order.signal_time,
        "attempts": int(order.attempts or 0),
        "pending_direction": int(order.pending_direction or 0),
        "order_type": str(order.order_type or "market"),
        "limit_price": float(order.limit_price or 0.0),
        "execution_algo": str(order.execution_algo or "market"),
        "maker_wait_sec": float(order.maker_wait_sec or 0.0),
        "maker_offset_bps": float(order.maker_offset_bps or 0.0),
        "position_side": str(order.position_side or ""),
        "client_order_id": str(order.client_order_id or ""),
    }


class _UniverseResolver:
    def __init__(
        self,
        default_members: list[str] | None = None,
        members_by_date: Mapping[str, list[str]] | None = None,
    ) -> None:
        self.current_members = [str(item) for item in (default_members or ())]
        self.members_by_date = {
            str(key): [str(item) for item in value]
            for key, value in dict(members_by_date or {}).items()
        }

    def set_current(self, values: list[str] | None) -> None:
        if values is not None:
            self.current_members = [str(item) for item in values]

    def __call__(self, _reference: str, timestamp) -> list[str]:
        key = timestamp.date().isoformat()
        return list(self.members_by_date.get(key, self.current_members))


def _context_result(session, value: Any) -> dict[str, Any]:
    return {
        "value": value,
        "cancelled_order_ids": sorted(session.context._cancelled_order_ids),
    }


def _handle_live_request(session, resolver: _UniverseResolver, request: dict[str, Any]) -> Any:
    session.context._cancelled_order_ids = {
        str(item) for item in request.get("cancelled_order_ids", ())
    }
    op = str(request.get("op") or "")
    if op == "context":
        action = str(request.get("action") or "")
        if action == "order_references":
            value = sorted(str(item) for item in session.context.order_references())
        elif action == "update_order_statuses":
            session.context.update_order_statuses(request.get("values") or {})
            value = None
        elif action == "order_status_snapshot":
            value = session.context.order_status_snapshot()
        elif action == "get_order_status":
            value = session.context.get_order_status(request.get("reference"))
        else:
            raise ValueError("strategy sandbox context operation is not allowed")
        return _context_result(session, value)
    if op == "process":
        resolver.set_current(request.get("universe_members"))
        orders, logs, timestamp = session.process(
            request.get("frames") or {},
            frequency_frames=request.get("frequency_frames") or None,
            schedule_time=request.get("schedule_time"),
        )
        value = {
            "orders": [_order_metadata(item) for item in orders],
            "logs": list(logs),
            "timestamp": timestamp,
        }
    elif op == "synchronize_positions":
        session.synchronize_positions(
            request.get("positions") or {},
            available_cash=request.get("available_cash"),
            total_value=request.get("total_value"),
        )
        value = None
    elif op == "evaluate_protections":
        value = [
            _order_metadata(item)
            for item in session.evaluate_protections(
                request.get("prices") or {},
                timestamp=request.get("timestamp"),
            )
        ]
    elif op == "evaluate_equity_risk":
        orders, logs, reason = session.evaluate_equity_risk(
            timestamp=request.get("timestamp")
        )
        value = {
            "orders": [_order_metadata(item) for item in orders],
            "logs": list(logs),
            "reason": reason,
        }
    elif op == "evaluate_price_tick":
        orders, logs = session.evaluate_price_tick(
            request.get("prices") or {},
            timestamp=request.get("timestamp"),
        )
        value = {
            "orders": [_order_metadata(item) for item in orders],
            "logs": list(logs),
        }
    elif op == "release_equity_risk_exit":
        session.release_equity_risk_exit()
        value = None
    elif op == "pending_protection_exit_symbols":
        value = sorted(session.pending_protection_exit_symbols())
    elif op == "has_due_schedule":
        value = session.has_due_schedule(request.get("schedule_time"))
    elif op == "release_protection_exit":
        session.release_protection_exit(
            request.get("symbol"),
            position_side=request.get("position_side"),
        )
        value = None
    elif op == "session_snapshot":
        value = session.session_snapshot()
    elif op == "restore_session_snapshot":
        session.restore_session_snapshot(request.get("snapshot") or {})
        value = None
    elif op == "restore_protection_snapshot":
        session.restore_protection_snapshot(request.get("snapshot") or {})
        value = None
    else:
        raise ValueError("strategy sandbox operation is not allowed")
    return _context_result(session, value)


def main() -> int:
    os.environ.clear()
    os.environ.update({
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONNOUSERSITE": "1",
        "QD_STRATEGY_RUNTIME_WORKER": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    })
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    request_stream = sys.stdin.buffer
    sys.stdout = _NullWriter()
    sys.stderr = _NullWriter()
    app_dir = Path(__file__).resolve().parents[1]
    _install_app_namespace(app_dir)

    import numpy as np
    import pandas as pd
    from app.services.instrument_rules import InstrumentRulesSnapshot
    from app.services.strategy_v2.runtime import (
        StrategyV2BacktestRunner,
        StrategyV2LiveSession,
    )
    from app.utils.safe_exec_worker import (
        _apply_os_isolation,
        _decode,
        _deny_network,
        _encode,
        _preload_safe_modules,
    )
    from app.utils import safe_exec as safe_exec_module

    try:
        initial = _read_request(request_stream, _decode, pd, np)
        if initial is None:
            return 1
        code = str(initial.get("code") or "")
        discovery_snapshot = initial.get("discovery_snapshot") or {}
        _preload_safe_modules(safe_exec_module)
        _deny_network()
        _set_resource_limits(initial.get("max_memory_mb"))
        _apply_os_isolation(Path(__file__).resolve().parent)

        mode = str(initial.get("op") or "")
        session = None
        backtest_result = None
        if mode == "init_live":
            resolver = _UniverseResolver(initial.get("universe_members"))
            session = StrategyV2LiveSession(
                code=code,
                frames=initial.get("frames") or {},
                frequency_frames=initial.get("frequency_frames") or None,
                initial_capital=float(initial.get("initial_capital") or 0.0),
                params=initial.get("params") or {},
                deployment_risk=initial.get("deployment_risk") or None,
                universe_resolver=resolver,
                schedule_timezone=str(initial.get("schedule_timezone") or "UTC"),
                discovery_snapshot=discovery_snapshot,
            )
        elif mode == "run_backtest":
            resolver = _UniverseResolver(
                initial.get("default_universe_members"),
                initial.get("universe_members_by_date"),
            )
            rules_metadata = initial.get("instrument_rules") or {}
            rules = (
                InstrumentRulesSnapshot.from_metadata(rules_metadata)
                if rules_metadata
                else None
            )
            custom_factors = {
                str(key).lower(): value
                for key, value in dict(initial.get("custom_factors") or {}).items()
            }
            runner = StrategyV2BacktestRunner(
                code=code,
                frames=initial.get("frames") or {},
                frequency_frames=initial.get("frequency_frames") or None,
                initial_capital=float(initial.get("initial_capital") or 0.0),
                params=initial.get("params") or {},
                leverage_enabled=bool(initial.get("leverage_enabled")),
                leverage=float(initial.get("leverage") or 1.0),
                commission=float(initial.get("commission") or 0.0),
                slippage=float(initial.get("slippage") or 0.0),
                universe_resolver=resolver,
                instrument_rules=rules,
                custom_factor_resolver=lambda factor_id: custom_factors[str(factor_id).lower()],
                discovery_snapshot=discovery_snapshot,
            )
            backtest_result = runner.run(
                start_date=initial.get("start_date"),
                end_date=initial.get("end_date"),
            )
        else:
            raise ValueError("strategy sandbox initialization mode is not allowed")
        _send(protocol, {"success": True, "error": None, "result": None}, _encode, pd, np)

        while True:
            request = _read_request(request_stream, _decode, pd, np)
            if request is None or request.get("op") == "close":
                return 0
            try:
                if request.get("op") == "backtest_result" and backtest_result is not None:
                    result = backtest_result
                    backtest_result = None
                elif session is not None:
                    result = _handle_live_request(session, resolver, request)
                else:
                    raise ValueError("strategy sandbox operation is not available")
                _send(
                    protocol,
                    {"success": True, "error": None, "result": result},
                    _encode,
                    pd,
                    np,
                )
            except Exception as exc:
                _send(
                    protocol,
                    {
                        "success": False,
                        "error": str(exc or type(exc).__name__)[:4000],
                        "result": None,
                    },
                    _encode,
                    pd,
                    np,
                )
                return 1
    except Exception as exc:
        try:
            _send(
                protocol,
                {
                    "success": False,
                    "error": str(exc or type(exc).__name__)[:4000],
                    "result": None,
                },
                _encode if "_encode" in locals() else lambda value, *_: value,
                pd if "pd" in locals() else None,
                np if "np" in locals() else None,
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
