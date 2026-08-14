"""One private execution stream per credential/venue endpoint."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from app.services.execution_streams.adapters import ADAPTERS
from app.services.execution_streams.events import ExecutionEvent
from app.services.execution_streams.processor import ExecutionEventProcessor
from app.services.execution_streams.repository import ExecutionEventRepository
from app.services.live_trading.capabilities import supported_crypto_exchange_ids
from app.utils.credential_crypto import decrypt_credential_blob
from app.utils.db import get_db_connection
from app.utils.logger import get_logger
from app.utils.thread_capacity import format_thread_capacity

logger = get_logger(__name__)


@dataclass(frozen=True)
class StreamSpec:
    key: str
    credential_id: int
    user_id: int
    exchange_id: str
    market_type: str
    config_json: str
    symbols: Tuple[str, ...]

    def config(self) -> Dict[str, Any]:
        value = json.loads(self.config_json or "{}")
        return value if isinstance(value, dict) else {}


class ExecutionStreamSupervisor:
    def __init__(self) -> None:
        self.repository = ExecutionEventRepository()
        self.processor = ExecutionEventProcessor(self.repository)
        self._adapters: Dict[str, Any] = {}
        self._specs: Dict[str, StreamSpec] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._processor_thread: Optional[threading.Thread] = None
        self._last_catchup: Dict[str, float] = {}
        self._last_replace: Dict[str, float] = {}
        self._orphans: Dict[str, Any] = {}
        self._refresh_sec = max(10.0, float(os.getenv("EXECUTION_STREAM_REFRESH_SEC", "30")))
        self._max_adapters = max(
            1,
            int(os.getenv("EXECUTION_STREAM_MAX_ADAPTERS", "64")),
        )
        self._enabled = str(os.getenv("ENABLE_PRIVATE_EXECUTION_STREAMS", "true")).lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def start(self) -> bool:
        if not self._enabled:
            logger.info("Private execution streams disabled")
            return False
        with self._lock:
            if self._thread and self._thread.is_alive():
                return True
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="ExecutionStreamSupervisor", daemon=True)
            self._processor_thread = threading.Thread(
                target=self._process_loop,
                name="ExecutionEventProcessor",
                daemon=True,
            )
            try:
                self._thread.start()
                self._processor_thread.start()
            except RuntimeError as exc:
                self._stop.set()
                for thread in (self._thread, self._processor_thread):
                    if thread and thread.is_alive():
                        thread.join(timeout=1.0)
                logger.error(
                    "Private execution stream supervisor failed to start: %s; %s",
                    exc,
                    format_thread_capacity(),
                )
                return False
        logger.info("Private execution stream supervisor started")
        return True

    def stop(self, timeout: float = 8.0) -> None:
        self._stop.set()
        with self._lock:
            adapters = list(self._adapters.items())
        for key, adapter in adapters:
            try:
                stopped = adapter.stop(timeout=2.0)
            except Exception:
                stopped = False
            if stopped:
                with self._lock:
                    self._adapters.pop(key, None)
                    self._specs.pop(key, None)
            else:
                logger.warning(
                    "Execution stream remained alive during supervisor stop stream=%s",
                    key,
                )
        for thread in (self._thread, self._processor_thread):
            if thread and thread.is_alive():
                thread.join(timeout=timeout)

    def is_healthy(self, *, exchange_id: str, credential_id: int, market_type: str) -> bool:
        exchange = str(exchange_id or "").lower()
        market = str(market_type or "").lower()
        keys = [
            f"{exchange}:{int(credential_id or 0)}:{market}",
            f"{exchange}:{int(credential_id or 0)}:all",
            f"{exchange}:{int(credential_id or 0)}:usstock",
            f"{exchange}:{int(credential_id or 0)}:stock",
        ]
        with self._lock:
            return any(bool(self._adapters.get(key) and self._adapters[key].connected) for key in keys)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._reconcile()
            except Exception:
                logger.error("Execution stream reconciliation failed", exc_info=True)
            self._stop.wait(self._refresh_sec)

    def _process_loop(self) -> None:
        while not self._stop.is_set():
            try:
                count = self.processor.process_pending(limit=200)
            except Exception:
                logger.error("Execution event processor failed", exc_info=True)
                count = 0
            self._stop.wait(0.05 if count else 0.25)

    def _reconcile(self) -> None:
        desired = {spec.key: spec for spec in self._discover_specs()}
        with self._lock:
            current_keys = set(self._adapters)
        for key in current_keys - set(desired):
            with self._lock:
                adapter = self._adapters.get(key)
            if adapter:
                if not adapter.stop():
                    logger.warning(
                        "Execution stream did not stop; dropping handle without starting a replacement stream=%s",
                        key,
                    )
                    with self._lock:
                        self._adapters.pop(key, None)
                        self._specs.pop(key, None)
                        self._orphans[key] = adapter
                    self.repository.update_health(
                        stream_key=key,
                        credential_id=int(getattr(adapter, "credential_id", 0)),
                        exchange_id=str(getattr(adapter, "exchange_id", key.split(":", 1)[0])),
                        market_type=str(getattr(adapter, "market_type", "")),
                        state="orphaned",
                        error=self._health_detail(adapter, "stop timeout"),
                        rest_fallback=True,
                    )
                    continue
                with self._lock:
                    self._adapters.pop(key, None)
                    self._specs.pop(key, None)
                self.repository.update_health(
                    stream_key=key,
                    credential_id=int(getattr(adapter, "credential_id", 0)),
                    exchange_id=str(getattr(adapter, "exchange_id", key.split(":", 1)[0])),
                    market_type=str(getattr(adapter, "market_type", "")),
                    state="stopped",
                )
        for key, spec in desired.items():
            with self._lock:
                existing_spec = self._specs.get(key)
                existing = self._adapters.get(key)
                orphan = self._orphans.get(key)
            if orphan is not None:
                alive = bool(getattr(orphan, "is_alive", lambda: False)())
                if alive or bool(getattr(orphan, "orphaned", False) and alive):
                    logger.warning(
                        "Execution stream orphaned thread still alive; not starting a second stream=%s",
                        key,
                    )
                    self.repository.update_health(
                        stream_key=key,
                        credential_id=spec.credential_id,
                        exchange_id=spec.exchange_id,
                        market_type=spec.market_type,
                        state="orphaned",
                        error=self._health_detail(orphan, "orphaned thread alive"),
                        rest_fallback=True,
                    )
                    self._run_rest_catchup_limited(spec)
                    continue
                with self._lock:
                    self._orphans.pop(key, None)
            if existing and existing_spec == spec:
                if existing.connected:
                    continue
                self._run_rest_catchup_limited(spec)
            if existing:
                if spec.exchange_id == "futu" and key in self._last_replace:
                    last = float(self._last_replace.get(key, 0.0) or 0.0)
                    if time.monotonic() - last < self._refresh_sec:
                        self._run_rest_catchup_limited(spec)
                        continue
                if not existing.stop():
                    logger.warning(
                        "Execution stream replacement dropped old handle without starting a second stream=%s",
                        key,
                    )
                    with self._lock:
                        self._adapters.pop(key, None)
                        self._specs.pop(key, None)
                        self._orphans[key] = existing
                    self.repository.update_health(
                        stream_key=key,
                        credential_id=spec.credential_id,
                        exchange_id=spec.exchange_id,
                        market_type=spec.market_type,
                        state="orphaned",
                        error=self._health_detail(existing, "stop timeout"),
                        rest_fallback=True,
                    )
                    self._run_rest_catchup_limited(spec)
                    continue
                with self._lock:
                    self._adapters.pop(key, None)
                    self._specs.pop(key, None)
                self._last_replace[key] = time.monotonic()
            adapter_cls = ADAPTERS.get(spec.exchange_id)
            if adapter_cls is None:
                continue
            adapter = adapter_cls(
                credential_id=spec.credential_id,
                user_id=spec.user_id,
                exchange_id=spec.exchange_id,
                market_type=spec.market_type,
                config=spec.config(),
                symbols=spec.symbols,
                on_event=self._on_event,
                on_state=lambda state, error, reconnect, _spec=spec: self._on_state(
                    _spec, state, error, reconnect
                ),
            )
            with self._lock:
                self._adapters[key] = adapter
                self._specs[key] = spec
            try:
                adapter.start()
            except RuntimeError as exc:
                with self._lock:
                    self._adapters.pop(key, None)
                    self._specs.pop(key, None)
                self.repository.update_health(
                    stream_key=key,
                    credential_id=spec.credential_id,
                    exchange_id=spec.exchange_id,
                    market_type=spec.market_type,
                    state="error",
                    error=f"{exc}; {format_thread_capacity()}",
                    rest_fallback=True,
                )
                logger.error(
                    "Execution stream thread failed to start stream=%s: %s; %s",
                    key,
                    exc,
                    format_thread_capacity(),
                )
                self._run_rest_catchup_limited(spec, force=True)

    def _on_event(self, event: ExecutionEvent) -> None:
        event_id = self.repository.ingest(event)
        if event_id:
            self.repository.update_health(
                stream_key=self._stream_key_for_event(event),
                credential_id=event.credential_id,
                exchange_id=event.exchange_id,
                market_type=event.market_type,
                state="connected",
                event=True,
            )

    @staticmethod
    def _stream_key_for_event(event: ExecutionEvent) -> str:
        if event.exchange_id in {"okx", "bybit"}:
            market = "all"
        elif event.exchange_id in {"alpaca", "ibkr"}:
            market = "usstock"
        elif event.exchange_id == "futu":
            market = "stock"
        else:
            market = event.market_type
        return f"{event.exchange_id}:{event.credential_id}:{market}"

    @staticmethod
    def _health_detail(adapter: Any, error: str = "") -> str:
        pool_snap: Dict[str, Any] = {}
        try:
            from app.services.futu_trading.session_pool import get_futu_session_pool

            pool_snap = get_futu_session_pool().snapshot()
        except Exception:
            pool_snap = {}
        payload = {
            "error": str(error or ""),
            "connected": bool(getattr(adapter, "connected", False)),
            "orphaned": bool(getattr(adapter, "orphaned", False)),
            "opend": f"{(getattr(adapter, 'config', {}) or {}).get('futu_host') or (getattr(adapter, 'config', {}) or {}).get('host') or ''}:{(getattr(adapter, 'config', {}) or {}).get('futu_port') or (getattr(adapter, 'config', {}) or {}).get('port') or ''}",
            "pool": pool_snap,
        }
        return json.dumps(payload, ensure_ascii=False)[:2000]

    def _on_state(self, spec: StreamSpec, state: str, error: str, reconnect: bool) -> None:
        detail = error
        if spec.exchange_id == "futu" and state in {"error", "orphaned", "disconnected"}:
            adapter = self._adapters.get(spec.key) or self._orphans.get(spec.key)
            detail = self._health_detail(adapter, error)
        self.repository.update_health(
            stream_key=spec.key,
            credential_id=spec.credential_id,
            exchange_id=spec.exchange_id,
            market_type=spec.market_type,
            state=state,
            error=detail,
            reconnect=reconnect,
            rest_fallback=state != "connected",
        )
        if state == "connected":
            self._run_rest_catchup_limited(spec, force=True)
        elif state in {"error", "disconnected", "orphaned"}:
            self._run_rest_catchup_limited(spec)

    def _run_rest_catchup_limited(self, spec: StreamSpec, *, force: bool = False) -> None:
        now = time.monotonic()
        last = float(self._last_catchup.get(spec.key, 0.0) or 0.0)
        if not force and now - last < self._refresh_sec:
            return
        self._last_catchup[spec.key] = now
        self._run_rest_catchup(spec)

    @staticmethod
    def _run_rest_catchup(spec: StreamSpec) -> None:
        """Reconcile the disconnect window before relying on new WS events."""
        try:
            from app.startup import get_pending_order_worker

            worker = get_pending_order_worker()
            worker.request_exchange_catchup(
                exchange_id=spec.exchange_id,
                credential_id=spec.credential_id,
                market_type=spec.market_type,
            )
        except Exception:
            logger.debug("REST catch-up scheduling failed for %s", spec.key, exc_info=True)

    def _discover_specs(self) -> List[StreamSpec]:
        symbols = self._symbols_by_credential()
        credentials = self._credential_rows(symbols)
        specs: List[StreamSpec] = []
        crypto = supported_crypto_exchange_ids()
        for row in credentials:
            exchange = str(row.get("exchange_id") or "").strip().lower()
            if exchange not in crypto | {"alpaca", "ibkr", "futu"}:
                continue
            try:
                plain = decrypt_credential_blob(row.get("encrypted_config"))
                config = json.loads(plain or "{}")
            except Exception as exc:
                logger.warning("Execution stream credential decrypt failed id=%s: %s", row.get("id"), exc)
                continue
            if not isinstance(config, dict):
                continue
            config.setdefault("exchange_id", exchange)
            if exchange == "ibkr":
                config["ibkr_client_id"] = int(
                    config.get("ibkr_stream_client_id")
                    or os.getenv("IBKR_STREAM_CLIENT_ID", "8")
                )
            credential_id = int(row.get("id") or 0)
            user_id = int(row.get("user_id") or 1)
            scope = str(config.get("market_scope") or config.get("marketScope") or "both").lower()
            if exchange in {"alpaca", "ibkr"}:
                markets = ["usstock"]
            elif exchange == "futu":
                markets = ["stock"]
            elif exchange in {"okx", "bybit"}:
                markets = ["all"]
            elif scope == "spot":
                markets = ["spot"]
            elif scope in {"swap", "future", "futures", "perp"}:
                markets = ["swap"]
            else:
                markets = ["spot", "swap"]
            safe_config = json.dumps(config, sort_keys=True, separators=(",", ":"))
            credential_symbols = tuple(sorted(symbols.get(credential_id, set())))
            for market in markets:
                key = f"{exchange}:{credential_id}:{market}"
                specs.append(
                    StreamSpec(
                        key=key,
                        credential_id=credential_id,
                        user_id=user_id,
                        exchange_id=exchange,
                        market_type=market,
                        config_json=safe_config,
                        symbols=credential_symbols,
                    )
                )
        if len(specs) > self._max_adapters:
            logger.error(
                "Private execution stream adapter cap reached desired=%s cap=%s; "
                "overflow accounts will use REST fallback",
                len(specs),
                self._max_adapters,
            )
            specs = specs[:self._max_adapters]
        return specs

    @staticmethod
    def _credential_rows(credential_ids: Iterable[int]) -> List[Dict[str, Any]]:
        active_ids = sorted({int(value) for value in credential_ids if int(value) > 0})
        if not active_ids:
            return []
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT id, user_id, exchange_id, encrypted_config
                FROM qd_exchange_credentials
                WHERE id = ANY(%s)
                  AND LOWER(exchange_id) = ANY(%s)
                ORDER BY id
                """,
                (
                    active_ids,
                    sorted(supported_crypto_exchange_ids() | {"alpaca", "ibkr", "futu"}),
                ),
            )
            rows = [dict(row) for row in (cur.fetchall() or [])]
            cur.close()
        return rows

    @staticmethod
    def _symbols_by_credential() -> Dict[int, Set[str]]:
        out: Dict[int, Set[str]] = {}
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute(
                """
                SELECT COALESCE(NULLIF(exchange_config->>'credential_id', ''),
                                NULLIF(exchange_config->>'credentials_id', ''), '0')::INTEGER AS credential_id,
                       symbol
                FROM qd_strategies_trading
                WHERE COALESCE(symbol, '') <> ''
                  AND LOWER(COALESCE(status, '')) = 'running'
                  AND LOWER(COALESCE(execution_mode, 'signal')) = 'live'
                UNION
                SELECT credential_id, symbol
                FROM pending_orders
                WHERE credential_id > 0 AND COALESCE(symbol, '') <> ''
                  AND status IN ('pending','processing','sent','syncing')
                """
            )
            for row in cur.fetchall() or []:
                credential_id = int(row.get("credential_id") or 0)
                if credential_id > 0:
                    out.setdefault(credential_id, set()).add(str(row.get("symbol") or ""))
            cur.close()
        return out


_supervisor: Optional[ExecutionStreamSupervisor] = None
_supervisor_lock = threading.Lock()


def get_execution_stream_supervisor() -> ExecutionStreamSupervisor:
    global _supervisor
    with _supervisor_lock:
        if _supervisor is None:
            _supervisor = ExecutionStreamSupervisor()
        return _supervisor
