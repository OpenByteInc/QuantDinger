"""Process-wide FutuOpenD session pool.

OpenD allows 128 TCP connections. Each FutuClient historically opened a
quote context and a trade context. Recreating those on every worker tick
or execution-event projection exhausts the budget.

This pool keeps at most one live session per
``(host, port, env, market, firm, acc_id, mode)`` key, with reference
counts. ``release()`` decrements the count but does not close; ``drain()``
closes idle and in-use sessions at process shutdown.
"""

from __future__ import annotations

import atexit
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from app.utils.logger import get_logger

logger = get_logger(__name__)

SessionKey = Tuple[str, int, str, str, str, int, str]
_BACKOFF_CAP_SEC = 30.0


@dataclass
class _Entry:
    client: Any
    refs: int = 0
    mode: str = "both"
    fail_until: float = 0.0
    fail_streak: int = 0
    last_error: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


class FutuSessionPool:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: Dict[SessionKey, _Entry] = {}

    @staticmethod
    def key_from_config(exchange_config: Dict[str, Any], *, mode: str) -> SessionKey:
        from app.services.futu_trading.config import config_from_exchange_config

        cfg = config_from_exchange_config(exchange_config if isinstance(exchange_config, dict) else {})
        normalized = str(mode or "both").strip().lower()
        if normalized not in {"quote", "trade", "both"}:
            normalized = "both"
        return (
            str(cfg.host or "127.0.0.1"),
            int(cfg.port or 11111),
            str(cfg.trade_env or "demo"),
            str(cfg.trade_market or "HK"),
            str(cfg.security_firm or "FUTUSECURITIES"),
            int(cfg.acc_id or 0),
            normalized,
        )

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "sessions": len(self._entries),
                "refs": sum(entry.refs for entry in self._entries.values()),
                "keys": [
                    {
                        "host": key[0],
                        "port": key[1],
                        "env": key[2],
                        "market": key[3],
                        "mode": key[6],
                        "refs": entry.refs,
                        "connected": bool(getattr(entry.client, "connected", False)),
                    }
                    for key, entry in self._entries.items()
                ],
            }

    def acquire(
        self,
        exchange_config: Dict[str, Any],
        *,
        mode: str = "both",
        factory: Any = None,
    ) -> Any:
        """Return a connected client for ``mode`` (quote | trade | both)."""
        requested = str(mode or "both").strip().lower()
        if requested not in {"quote", "trade", "both"}:
            requested = "both"
        key = self.key_from_config(exchange_config, mode=requested)
        with self._lock:
            if requested == "trade":
                both_key = self.key_from_config(exchange_config, mode="both")
                both = self._entries.get(both_key)
                if both and bool(getattr(both.client, "connected", False)):
                    both.refs += 1
                    return both.client
            entry = self._entries.get(key)
            now = time.monotonic()
            if entry and entry.fail_until > now:
                raise RuntimeError(
                    f"FutuOpenD connect backoff until {entry.fail_until - now:.1f}s: {entry.last_error}"
                )
            if entry and bool(getattr(entry.client, "connected", False)):
                entry.refs += 1
                return entry.client
            if entry and entry.client is not None:
                self._force_close(entry.client)
                self._entries.pop(key, None)

            try:
                client = factory() if factory is not None else self._create_client(exchange_config, requested)
            except Exception as exc:
                self.note_connect_failure(exchange_config, mode=requested, error=str(exc))
                raise
            mark_pooled(client, key)
            entry = _Entry(client=client, refs=1, mode=requested, fail_streak=0, fail_until=0.0)
            self._entries[key] = entry
            return client

    def note_connect_failure(self, exchange_config: Dict[str, Any], *, mode: str, error: str) -> None:
        key = self.key_from_config(exchange_config, mode=mode)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                entry = _Entry(client=None, refs=0, mode=mode)  # type: ignore[arg-type]
                self._entries[key] = entry
            entry.fail_streak = int(entry.fail_streak or 0) + 1
            delay = min(_BACKOFF_CAP_SEC, float(2 ** min(entry.fail_streak, 4)))
            entry.last_error = str(error or "")[:500]
            entry.fail_until = time.monotonic() + delay

    def release(self, client: Any) -> None:
        if client is None:
            return
        key = getattr(client, "_futu_pool_key", None)
        with self._lock:
            if key is None:
                return
            entry = self._entries.get(key)
            if entry is None or entry.client is not client:
                # Client may have been borrowed via the both-session alias.
                for stored_key, stored in list(self._entries.items()):
                    if stored.client is client:
                        stored.refs = max(0, stored.refs - 1)
                        return
                return
            entry.refs = max(0, entry.refs - 1)

    def drain(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        for entry in entries:
            self._force_close(entry.client)

    def _create_client(self, exchange_config: Dict[str, Any], mode: str) -> Any:
        from app.services.futu_trading.client import FutuClient
        from app.services.futu_trading.config import config_from_exchange_config
        from app.services.futu_trading.quote_client import FutuQuoteClient

        cfg = config_from_exchange_config(exchange_config if isinstance(exchange_config, dict) else {})
        if mode == "quote":
            client = FutuQuoteClient(cfg)
            if not client.connect():
                raise RuntimeError(f"Failed to connect Futu quote session {cfg.host}:{cfg.port}")
            return client
        client = FutuClient(cfg)
        if not client.connect(need_quote=(mode != "trade")):
            raise RuntimeError(f"Failed to connect FutuOpenD at {cfg.host}:{cfg.port}")
        return client

    @staticmethod
    def _force_close(client: Any) -> None:
        if client is None:
            return
        try:
            closer = getattr(client, "disconnect", None)
            if callable(closer):
                closer(force=True)
        except TypeError:
            try:
                client.disconnect()
            except Exception:
                pass
        except Exception:
            pass


_pool: Optional[FutuSessionPool] = None
_pool_lock = threading.Lock()


def get_futu_session_pool() -> FutuSessionPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = FutuSessionPool()
            atexit.register(_pool.drain)
        return _pool


def mark_pooled(client: Any, key: SessionKey) -> None:
    try:
        client._futu_pool_key = key
        client._futu_pooled = True
    except Exception:
        pass


def reset_futu_session_pool_for_tests() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.drain()
        _pool = FutuSessionPool()
