"""Shared atomic REST budgets; venue policy is kept in request guards."""

from __future__ import annotations

import math
import os
import threading
import time


class ExchangeRateLimitError(RuntimeError):
    def __init__(self, retry_after: float, reason: str = "cooldown", *, exchange="exchange"):
        self.retry_after = max(0.0, retry_after)
        super().__init__(f"{exchange} rate limit: {reason}; retry after {math.ceil(self.retry_after)}s")


_RESERVE = """
local ban = redis.call('PTTL', KEYS[1])
if ban > 0 then return {ban, 1} end
if ARGV[1] == 'write' then return {0, 0} end
local pressure = redis.call('PTTL', KEYS[2])
if pressure > 0 then return {pressure, 1} end
local count = tonumber(redis.call('GET', KEYS[3]) or '0')
if count >= tonumber(ARGV[2]) then return {redis.call('PTTL', KEYS[3]), 0} end
redis.call('INCR', KEYS[3])
if count == 0 then redis.call('PEXPIRE', KEYS[3], 1000) end
return {0, 0}
"""
_EXTEND = """
local remaining = redis.call('PTTL', KEYS[1])
if remaining < tonumber(ARGV[1]) then
  redis.call('SET', KEYS[1], '1', 'PX', ARGV[1])
end
return 1
"""


class SharedRequestBudget:
    def __init__(self, *, exchange="exchange", env_prefix="EXCHANGE"):
        self.exchange = exchange
        self.env_prefix = env_prefix
        self._lock = threading.Lock()
        self._deadlines = {}
        self._windows = {}
        self._redis = None
        self._redis_url = None
        self._unavailable_until = 0.0

    def _connection(self):
        if self._redis_url is None:
            with self._lock:
                if self._redis_url is None:
                    from app.config.redis_urls import celery_broker_url

                    configured = any(os.getenv(key, "").strip() for key in (
                        "CELERY_BROKER_URL", "CELERY_REDIS_HOST", "REDIS_HOST",
                    ))
                    redis_url = os.getenv(f"{self.env_prefix}_REST_REDIS_URL", "").strip() or (
                        celery_broker_url() if configured else ""
                    )
                    if redis_url:
                        import redis
                        from redis.backoff import NoBackoff
                        from redis.retry import Retry

                        self._redis = redis.Redis.from_url(
                            redis_url, socket_connect_timeout=0.5, socket_timeout=0.5,
                            retry=Retry(NoBackoff(), 0),
                        )
                    self._redis_url = redis_url
        return self._redis

    def _eval(self, script, keys, args):
        if time.monotonic() < self._unavailable_until:
            raise self._limit(5, "shared coordinator unavailable")
        try:
            connection = self._connection()
            if connection is None:
                return None
            return connection.eval(script, len(keys), *keys, *args)
        except ExchangeRateLimitError:
            raise
        except Exception as exc:
            self._unavailable_until = time.monotonic() + 5
            # Never replace a configured shared guard with unlimited local traffic.
            raise self._limit(5, "shared coordinator unavailable") from exc

    def _limit(self, seconds, reason="cooldown"):
        return ExchangeRateLimitError(seconds, reason, exchange=self.exchange)

    def _key(self, scope, suffix):
        namespace = os.getenv(f"{self.env_prefix}_REST_NAMESPACE", f"quantdinger:{self.env_prefix.lower()}:rest:v1").strip()
        return f"{namespace}:{{{scope}}}:{suffix}"

    def _reserve(self, scope, lane, limit):
        now = time.monotonic()
        with self._lock:
            remaining = self._deadlines.get((scope, "ban"), 0) - now
            if lane != "write":
                remaining = max(remaining, self._deadlines.get((scope, "pressure"), 0) - now)
            if remaining > 0:
                return remaining, True
        result = self._eval(_RESERVE, [self._key(scope, name) for name in (
            "ban", "pressure", lane,
        )], [lane, limit])
        if result is not None:
            return max(0, int(result[0])) / 1000.0, bool(result[1])
        if lane == "write":
            return 0.0, False
        with self._lock:
            start, count = self._windows.get((scope, lane), (now, 0))
            if now - start >= 1:
                start, count = now, 0
            if count >= limit:
                return max(0, start + 1 - now), False
            self._windows[(scope, lane)] = (start, count + 1)
        return 0.0, False

    def _extend(self, scope, kind, seconds):
        seconds = max(0.001, seconds)
        with self._lock:
            now = time.monotonic()
            if len(self._deadlines) >= 256:
                self._deadlines = {key: until for key, until in self._deadlines.items() if until > now}
            key = (scope, kind)
            self._deadlines[key] = max(self._deadlines.get(key, 0), now + seconds)
        self._eval(_EXTEND, [self._key(scope, kind)], [math.ceil(seconds * 1000)])
