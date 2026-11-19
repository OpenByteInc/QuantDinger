"""Process boundary for untrusted Strategy API V2 runtime callbacks."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
from typing import Any, Mapping

import pandas as pd

from app.utils.safe_exec import (
    _redact_sensitive_text,
    _sandbox_transport_decode,
    _sandbox_transport_encode,
)

from .contract import CompiledStrategyV2, inspect_strategy_v2
from .protection import ProtectionSpec
from .runtime import OrderIntent


_MAX_REQUEST_BYTES = 512 * 1024 * 1024
_MAX_RESPONSE_BYTES = 256 * 1024 * 1024
_DEFAULT_CALL_TIMEOUT = 20.0
_DEFAULT_LIVE_REQUEST_TIMEOUT = 120.0
_DEFAULT_BACKTEST_TIMEOUT = 900.0
_ORDER_KINDS = frozenset({
    "quantity",
    "value",
    "target_quantity",
    "target_value",
    "target_percent",
})


class StrategySandboxError(RuntimeError):
    pass


class StrategySandboxTerminatedError(StrategySandboxError):
    """The session cannot safely service another request."""


def live_sandbox_timeout_seconds() -> float:
    """Budget a complete live request, including frame decoding and all callbacks."""
    configured = os.getenv("STRATEGY_RUNTIME_SANDBOX_TIMEOUT_SECONDS", "").strip()
    try:
        value = float(configured or _DEFAULT_LIVE_REQUEST_TIMEOUT)
    except (TypeError, ValueError):
        value = _DEFAULT_LIVE_REQUEST_TIMEOUT
    if not math.isfinite(value):
        value = _DEFAULT_LIVE_REQUEST_TIMEOUT
    return min(300.0, max(2.0, value))


def _clean_worker_environment() -> dict[str, str]:
    return {
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "PYTHONNOUSERSITE": "1",
        "QD_STRATEGY_RUNTIME_WORKER": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }


class _JsonSandboxProcess:
    def __init__(self, initial_request: Mapping[str, Any], *, timeout: float) -> None:
        self._lock = threading.Lock()
        self._responses: queue.Queue[Any] = queue.Queue(maxsize=1)
        self._stderr = bytearray()
        self._closed = False
        self._terminal_error: str | None = None
        self._temporary = tempfile.TemporaryDirectory(prefix="quantdinger-strategy-runtime-")
        worker = Path(__file__).parents[2] / "utils" / "strategy_runtime_worker.py"
        self._process = subprocess.Popen(
            [sys.executable, "-I", str(worker)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self._temporary.name,
            env=_clean_worker_environment(),
            close_fds=True,
            start_new_session=True,
        )
        self._reader = threading.Thread(
            target=self._read_responses,
            name="StrategySandboxResponseReader",
            daemon=True,
        )
        self._stderr_reader = threading.Thread(
            target=self._read_stderr,
            name="StrategySandboxStderrReader",
            daemon=True,
        )
        self._reader.start()
        self._stderr_reader.start()
        try:
            self.request(initial_request, timeout=timeout)
        except Exception:
            self._closed = True
            self._terminate()
            try:
                self._temporary.cleanup()
            except OSError:
                pass
            raise

    def _read_responses(self) -> None:
        stdout = self._process.stdout
        if stdout is None:
            self._responses.put(StrategySandboxError("strategy sandbox stdout unavailable"))
            return
        try:
            while True:
                line = stdout.readline(_MAX_RESPONSE_BYTES + 1)
                if not line:
                    self._responses.put(
                        StrategySandboxError("strategy sandbox terminated unexpectedly")
                    )
                    return
                if len(line) > _MAX_RESPONSE_BYTES:
                    self._responses.put(
                        StrategySandboxError("strategy sandbox response exceeded its limit")
                    )
                    return
                try:
                    response = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    self._responses.put(
                        StrategySandboxError(f"strategy sandbox protocol error: {exc}")
                    )
                    return
                self._responses.put(response)
        except Exception as exc:
            self._responses.put(StrategySandboxError(f"strategy sandbox read failed: {exc}"))

    def _read_stderr(self) -> None:
        stderr = self._process.stderr
        if stderr is None:
            return
        try:
            while len(self._stderr) < 64 * 1024:
                chunk = stderr.read(min(4096, 64 * 1024 - len(self._stderr)))
                if not chunk:
                    return
                self._stderr.extend(chunk)
        except Exception:
            return

    def request(self, request: Mapping[str, Any], *, timeout: float = _DEFAULT_CALL_TIMEOUT) -> Any:
        with self._lock:
            if self._terminal_error is not None:
                raise StrategySandboxTerminatedError(self._terminal_error)
            if self._closed or self._process.poll() is not None:
                raise self._abort(self._failure_detail(
                    f"strategy sandbox is not running (exit_code={self._process.poll()})"
                ))
            operation = str(request.get("op") or "unknown")[:80]
            try:
                encoded = _sandbox_transport_encode(dict(request))
                payload = json.dumps(
                    encoded,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8") + b"\n"
            except (TypeError, ValueError) as exc:
                raise StrategySandboxError(f"strategy sandbox input is invalid: {exc}") from exc
            if len(payload) > _MAX_REQUEST_BYTES:
                raise StrategySandboxError("strategy sandbox request exceeded its limit")
            stdin = self._process.stdin
            if stdin is None:
                raise self._abort("strategy sandbox stdin unavailable")
            try:
                stdin.write(payload)
                stdin.flush()
                response = self._responses.get(timeout=max(0.1, float(timeout)))
            except queue.Empty as exc:
                raise self._abort(
                    f"strategy sandbox timed out after {float(timeout):g} seconds "
                    f"(operation={operation})"
                ) from exc
            except (BrokenPipeError, OSError) as exc:
                raise self._abort(self._failure_detail(str(exc))) from exc
            if isinstance(response, Exception):
                raise self._abort(self._failure_detail(str(response)))
            if not isinstance(response, dict):
                raise self._abort("strategy sandbox returned a non-object response")
            if not response.get("success"):
                error = _redact_sensitive_text(response.get("error") or "strategy sandbox failed")
                # The worker exits after a failed operation; never retry its partial state.
                raise self._abort(str(error)[:4000])
            try:
                return _sandbox_transport_decode(response.get("result"))
            except (TypeError, ValueError, ArithmeticError) as exc:
                raise self._abort(f"strategy sandbox result is invalid (operation={operation})") from exc

    def _abort(self, message: str) -> StrategySandboxTerminatedError:
        if self._terminal_error is None:
            self._terminal_error = message
        self._terminate()
        return StrategySandboxTerminatedError(self._terminal_error)

    def _failure_detail(self, message: str) -> str:
        detail = _redact_sensitive_text(
            bytes(self._stderr).decode("utf-8", errors="replace")
        )
        return f"{message}; {detail[:500]}" if detail else message

    def _terminate(self) -> None:
        if self._process.poll() is None:
            self._process.kill()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._process.poll() is None and self._process.stdin is not None:
                try:
                    self._process.stdin.write(b'{"op":"close"}\n')
                    self._process.stdin.flush()
                    self._process.wait(timeout=2)
                except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                    self._terminate()
            self._temporary.cleanup()


class _IsolatedContextProxy:
    def __init__(self, owner: "IsolatedStrategyV2LiveSession") -> None:
        self._owner = owner
        self._cancelled_order_ids: set[str] = set()

    def _update_metadata(self, result: Mapping[str, Any] | None) -> None:
        if isinstance(result, Mapping):
            self._cancelled_order_ids = {
                str(item) for item in result.get("cancelled_order_ids", ())
            }

    def order_references(self) -> list[str]:
        result = self._owner._call_context("order_references")
        return [str(item) for item in (result or ())]

    def update_order_statuses(self, values: Mapping[str, Mapping[str, Any]]) -> None:
        self._owner._call_context("update_order_statuses", values=dict(values or {}))

    def order_status_snapshot(self) -> dict[str, dict[str, Any]]:
        result = self._owner._call_context("order_status_snapshot")
        return {
            str(key): dict(value)
            for key, value in dict(result or {}).items()
            if isinstance(value, Mapping)
        }

    def get_order_status(self, reference: object) -> dict[str, Any]:
        result = self._owner._call_context(
            "get_order_status",
            reference=str(reference or ""),
        )
        return dict(result or {})


class IsolatedStrategyV2LiveSession:
    """Live-session API backed by a credential-free, kernel-confined process."""

    def __init__(
        self,
        *,
        code: str,
        frames: Mapping[str, pd.DataFrame],
        frequency_frames: Mapping[str, Mapping[str, pd.DataFrame]] | None = None,
        initial_capital: float,
        params: Mapping[str, Any] | None = None,
        deployment_risk: Mapping[str, Any] | None = None,
        universe_members: list[str] | None = None,
        schedule_timezone: str = "UTC",
        program: CompiledStrategyV2 | None = None,
        timeout: float | None = None,
        max_memory_mb: int = 2048,
    ) -> None:
        self.program = program or inspect_strategy_v2(code)
        self._timeout = live_sandbox_timeout_seconds() if timeout is None else max(1.0, float(timeout))
        self.context = _IsolatedContextProxy(self)
        self._process = _JsonSandboxProcess({
            "op": "init_live",
            "code": str(code or ""),
            "frames": dict(frames),
            "frequency_frames": dict(frequency_frames or {}),
            "initial_capital": float(initial_capital),
            "params": dict(params or {}),
            "deployment_risk": dict(deployment_risk or {}),
            "universe_members": list(universe_members or ()),
            "schedule_timezone": str(schedule_timezone or "UTC"),
            "max_memory_mb": int(max_memory_mb),
            "discovery_snapshot": dict(self.program.discovery_snapshot or {}),
        }, timeout=max(20.0, self._timeout))

    def _request(self, op: str, **values: Any) -> Any:
        values["cancelled_order_ids"] = sorted(self.context._cancelled_order_ids)
        result = self._process.request({"op": op, **values}, timeout=self._timeout)
        if isinstance(result, Mapping) and "value" in result:
            self.context._update_metadata(result)
            return result.get("value")
        return result

    def _call_context(self, action: str, **values: Any) -> Any:
        return self._request("context", action=action, **values)

    def process(
        self,
        frames: Mapping[str, pd.DataFrame],
        *,
        frequency_frames: Mapping[str, Mapping[str, pd.DataFrame]] | None = None,
        schedule_time: Any = None,
        universe_members: list[str] | None = None,
    ) -> tuple[list[OrderIntent], list[str], pd.Timestamp]:
        value = self._request(
            "process",
            frames=dict(frames),
            frequency_frames=dict(frequency_frames or {}),
            schedule_time=schedule_time,
            universe_members=list(universe_members or ()),
        )
        return (
            _decode_order_intents(value.get("orders")),
            _decode_logs(value.get("logs")),
            pd.Timestamp(value.get("timestamp")),
        )

    def synchronize_positions(
        self,
        positions: Mapping[str, Mapping[str, Any]],
        *,
        available_cash: float | None = None,
        total_value: float | None = None,
    ) -> None:
        self._request(
            "synchronize_positions",
            positions=dict(positions or {}),
            available_cash=available_cash,
            total_value=total_value,
        )

    def evaluate_protections(
        self,
        prices: Mapping[str, float],
        *,
        timestamp: object = None,
    ) -> list[OrderIntent]:
        return _decode_order_intents(self._request(
            "evaluate_protections",
            prices=dict(prices or {}),
            timestamp=timestamp,
        ))

    def evaluate_equity_risk(
        self,
        *,
        timestamp: object = None,
    ) -> tuple[list[OrderIntent], list[str], str]:
        value = self._request("evaluate_equity_risk", timestamp=timestamp)
        return (
            _decode_order_intents(value.get("orders")),
            _decode_logs(value.get("logs")),
            str(value.get("reason") or ""),
        )

    def evaluate_price_tick(
        self,
        prices: Mapping[str, float],
        *,
        timestamp: object = None,
    ) -> tuple[list[OrderIntent], list[str]]:
        value = self._request(
            "evaluate_price_tick",
            prices=dict(prices or {}),
            timestamp=timestamp,
        )
        return _decode_order_intents(value.get("orders")), _decode_logs(value.get("logs"))

    def release_equity_risk_exit(self) -> None:
        self._request("release_equity_risk_exit")

    def pending_protection_exit_symbols(self) -> set[str]:
        return {str(item) for item in self._request("pending_protection_exit_symbols") or ()}

    def release_protection_exit(
        self,
        symbol: object,
        *,
        position_side: object = "",
    ) -> None:
        self._request(
            "release_protection_exit",
            symbol=str(symbol or ""),
            position_side=str(position_side or ""),
        )

    def session_snapshot(self) -> dict[str, Any]:
        return dict(self._request("session_snapshot") or {})

    def restore_session_snapshot(self, values: Mapping[str, Any] | None) -> None:
        self._request("restore_session_snapshot", snapshot=dict(values or {}))

    def restore_protection_snapshot(self, values: Mapping[str, Any] | None) -> None:
        self._request("restore_protection_snapshot", snapshot=dict(values or {}))

    def close(self) -> None:
        self._process.close()


def run_strategy_backtest_isolated(
    *,
    code: str,
    frames: Mapping[str, pd.DataFrame],
    frequency_frames: Mapping[str, Mapping[str, pd.DataFrame]] | None,
    initial_capital: float,
    params: Mapping[str, Any] | None,
    leverage_enabled: bool,
    leverage: float,
    commission: float,
    slippage: float,
    universe_members_by_date: Mapping[str, list[str]] | None = None,
    default_universe_members: list[str] | None = None,
    instrument_rules: Mapping[str, Any] | None = None,
    custom_factors: Mapping[str, Any] | None = None,
    start_date: Any = None,
    end_date: Any = None,
    timeout: float = _DEFAULT_BACKTEST_TIMEOUT,
    max_memory_mb: int = 4096,
) -> dict[str, Any]:
    program = inspect_strategy_v2(code)
    process = _JsonSandboxProcess({
        "op": "run_backtest",
        "code": str(code or ""),
        "frames": dict(frames),
        "frequency_frames": dict(frequency_frames or {}),
        "initial_capital": float(initial_capital),
        "params": dict(params or {}),
        "leverage_enabled": bool(leverage_enabled),
        "leverage": float(leverage),
        "commission": float(commission),
        "slippage": float(slippage),
        "universe_members_by_date": dict(universe_members_by_date or {}),
        "default_universe_members": list(default_universe_members or ()),
        "instrument_rules": dict(instrument_rules or {}),
        "custom_factors": dict(custom_factors or {}),
        "start_date": start_date,
        "end_date": end_date,
        "max_memory_mb": int(max_memory_mb),
        "discovery_snapshot": dict(program.discovery_snapshot or {}),
    }, timeout=max(20.0, float(timeout)))
    try:
        result = process.request({"op": "backtest_result"}, timeout=float(timeout))
        if not isinstance(result, Mapping):
            raise StrategySandboxError("strategy sandbox returned an invalid backtest")
        return dict(result)
    finally:
        process.close()


def _decode_logs(values: Any) -> list[str]:
    return [str(item)[:4000] for item in list(values or ())[:2000]]


def _decode_order_intents(values: Any) -> list[OrderIntent]:
    rows = list(values or ())
    if len(rows) > 10_000:
        raise StrategySandboxError("strategy sandbox returned too many order intents")
    orders: list[OrderIntent] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise StrategySandboxError("strategy sandbox returned an invalid order intent")
        kind = str(raw.get("kind") or "")
        value = float(raw.get("value") or 0.0)
        if kind not in _ORDER_KINDS or not math.isfinite(value):
            raise StrategySandboxError("strategy sandbox returned an unsafe order intent")
        signal_time = raw.get("signal_time")
        orders.append(OrderIntent(
            symbol=str(raw.get("symbol") or "")[:300],
            kind=kind,
            value=value,
            reason=str(raw.get("reason") or "strategy")[:500],
            protection=ProtectionSpec.from_value(raw.get("protection")),
            signal_time=pd.Timestamp(signal_time) if signal_time else None,
            attempts=max(0, int(raw.get("attempts") or 0)),
            pending_direction=int(raw.get("pending_direction") or 0),
            order_type=str(raw.get("order_type") or "market")[:40],
            limit_price=float(raw.get("limit_price") or 0.0),
            execution_algo=str(raw.get("execution_algo") or "market")[:40],
            maker_wait_sec=float(raw.get("maker_wait_sec") or 0.0),
            maker_offset_bps=float(raw.get("maker_offset_bps") or 0.0),
            position_side=str(raw.get("position_side") or "")[:16],
            client_order_id=str(raw.get("client_order_id") or "")[:100],
        ))
    return orders
