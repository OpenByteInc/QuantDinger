"""Thread-affine IBKR session ownership.

``ib_insync`` binds a connection to the asyncio event loop of the thread that
created it.  The HTTP API serves requests from a pool of gthread workers, so a
client connected on request thread A cannot be driven from request thread B:
the call runs against a different (idle) loop and blocks until it times out.

:class:`IBKRSession` therefore pins one :class:`~app.services.ibkr_trading.client.IBKRClient`
to a dedicated single-thread executor whose thread owns a permanent event loop.
Every call is marshalled onto that thread, which also serialises access so two
concurrent requests cannot interleave on the same socket.

A process-wide registry keyed by ``(host, port, client_id)`` guarantees the
process never competes with itself for a client id -- TWS/IB Gateway allows one
session per client id and answers a second one with
``Error 326: client id is already in use``.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional, Tuple

from app.services.ibkr_trading.client import IBKRClient, IBKRConfig
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Attributes that only read local state and are safe to serve inline.
_DIRECT_ATTRS = frozenset({"config", "connected", "get_connection_status"})

# Upper bound for a marshalled IBKR call.  IBKRConfig.timeout (20s) covers the
# connect handshake; the rest of the calls are request/response round trips.
_CALL_TIMEOUT_SEC = 60.0

_registry: Dict[Tuple[str, int, int], "IBKRSession"] = {}
_registry_lock = threading.RLock()


def _session_key(config: IBKRConfig) -> Tuple[str, int, int]:
    return (str(config.host).strip(), int(config.port), int(config.client_id))


class IBKRSession:
    """An :class:`IBKRClient` pinned to its own thread and event loop.

    Attribute access is proxied to the wrapped client: plain state reads are
    served inline, every method call is submitted to the owning thread.
    """

    def __init__(self, config: IBKRConfig):
        self._config = config
        self._key = _session_key(config)
        self._client = IBKRClient(config)
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=f"ibkr-{self._key[0]}-{self._key[1]}-{self._key[2]}",
        )
        self._executor.submit(_install_event_loop).result(timeout=10)

    # -- lifecycle -------------------------------------------------------

    def connect(self) -> bool:
        return self._submit(self._client.connect)

    def disconnect(self) -> None:
        try:
            self._submit(self._client.disconnect)
        finally:
            self._executor.shutdown(wait=False)
            _forget(self._key, self)

    @property
    def connected(self) -> bool:
        return self._client.connected

    # -- proxy -----------------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        # Only reached for attributes not defined on IBKRSession itself.
        # Private names are never proxied, so a half-built instance raises
        # AttributeError instead of recursing on ``self._client``.
        if name.startswith("_"):
            raise AttributeError(name)
        attr = getattr(self._client, name)
        if name in _DIRECT_ATTRS or not callable(attr):
            return attr

        def _marshalled(*args: Any, **kwargs: Any) -> Any:
            return self._submit(lambda: attr(*args, **kwargs))

        _marshalled.__name__ = name
        return _marshalled

    def _submit(self, fn: Callable[[], Any]) -> Any:
        return self._executor.submit(fn).result(timeout=_CALL_TIMEOUT_SEC)


def _install_event_loop() -> None:
    """Give the session thread a permanent event loop for ib_insync."""
    asyncio.set_event_loop(asyncio.new_event_loop())


def _forget(key: Tuple[str, int, int], session: "IBKRSession") -> None:
    with _registry_lock:
        if _registry.get(key) is session:
            _registry.pop(key, None)


def get_or_create_session(config: IBKRConfig) -> IBKRSession:
    """Return the process-wide session for ``config``, connecting if needed.

    Re-using the live session for a ``(host, port, client_id)`` triple is what
    keeps the process from answering its own connection with Error 326.  A dead
    session is replaced.
    """
    key = _session_key(config)
    with _registry_lock:
        existing = _registry.get(key)
        if existing is not None:
            if existing.connected:
                return existing
            try:
                existing.disconnect()
            except Exception as exc:  # pragma: no cover - defensive
                logger.debug("Stale IBKR session disconnect raised: %s", exc)
            _registry.pop(key, None)

        session = IBKRSession(config)
        _registry[key] = session

    if not session.connect():
        _forget(key, session)
        session.disconnect()
        raise ConnectionError(
            "Failed to connect to IBKR TWS/Gateway at "
            f"{config.host}:{config.port} (clientId={config.client_id})."
        )
    return session


def find_session(config: IBKRConfig) -> Optional[IBKRSession]:
    """Return the live session for ``config`` without connecting."""
    with _registry_lock:
        session = _registry.get(_session_key(config))
    if session is not None and session.connected:
        return session
    return None


__all__ = ["IBKRSession", "get_or_create_session", "find_session"]
