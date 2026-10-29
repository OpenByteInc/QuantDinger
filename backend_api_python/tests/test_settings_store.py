import os
from contextlib import contextmanager

import pytest

from app.services.settings import runtime, store


class FakeDatabase:
    """Just enough of qd_runtime_settings for the store's three statements."""

    def __init__(self):
        self.rows = {}
        self.sequence = 0
        self.reads = 0

    @contextmanager
    def connection(self):
        yield self

    def cursor(self):
        return self

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        if sql.startswith("SELECT nextval"):
            self.sequence += 1
            self._result = [{"version": self.sequence}]
        elif sql.startswith("INSERT INTO qd_runtime_settings"):
            key, value, secret, version, _operator = params
            self.rows[key] = {"setting_key": key, "value": value, "is_secret": secret, "version": version}
        elif sql.startswith("SELECT setting_key"):
            self.reads += 1
            self._result = list(self.rows.values())
        else:
            raise AssertionError(sql)

    def fetchone(self):
        return self._result[0]

    def fetchall(self):
        return self._result

    def commit(self):
        pass

    def close(self):
        pass


class FakeRedisCache:
    def __init__(self):
        self.entries = {}
        self.is_redis = True

    def get(self, key):
        return self.entries.get(key)

    def set(self, key, value, ttl=300):
        self.entries[key] = value


@pytest.fixture
def db(monkeypatch):
    database = FakeDatabase()
    monkeypatch.setattr(store, "get_db_connection", database.connection)
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "test-credential-key")
    return database


@pytest.fixture
def cache(monkeypatch):
    fake = FakeRedisCache()
    monkeypatch.setattr(store, "CacheManager", lambda: fake)
    return fake


def test_save_encrypts_secrets_and_round_trips(db, cache):
    store.save_values({"OPENAI_API_KEY": "sk-live", "LLM_PROVIDER": "openai"}, secret_keys={"OPENAI_API_KEY"})

    assert db.rows["OPENAI_API_KEY"]["value"] != "sk-live"
    assert db.rows["LLM_PROVIDER"]["value"] == "openai"
    # The shared snapshot never holds plaintext secrets either.
    assert cache.entries[store.CACHE_KEY]["rows"]["OPENAI_API_KEY"]["value"] != "sk-live"
    assert store.load_values() == {"OPENAI_API_KEY": "sk-live", "LLM_PROVIDER": "openai"}


def test_each_save_bumps_the_shared_version(db, cache):
    store.save_values({"LLM_PROVIDER": "openai"})
    first = store.load_snapshot()["version"]
    store.save_values({"LLM_PROVIDER": "deepseek"})

    assert store.load_snapshot()["version"] > first
    assert store.load_values() == {"LLM_PROVIDER": "deepseek"}


def test_evicted_cache_is_rebuilt_from_database(db, cache):
    store.save_values({"LLM_PROVIDER": "openai"})
    cache.entries.clear()

    assert store.load_values() == {"LLM_PROVIDER": "openai"}
    assert store.CACHE_KEY in cache.entries
    reads = db.reads
    store.load_values()
    assert db.reads == reads  # served from the rebuilt cache


def test_memory_cache_is_not_trusted_across_processes(db, cache):
    cache.is_redis = False
    store.save_values({"LLM_PROVIDER": "openai"})
    reads = db.reads

    store.load_values()
    store.load_values()

    assert db.reads == reads + 2
    assert cache.entries == {}


def test_read_settings_overlays_saved_values_on_env_file(db, cache, monkeypatch):
    from app.services.settings import env_file

    monkeypatch.setattr(env_file, "read_env_file", lambda: {"LLM_PROVIDER": "openrouter", "TZ": "UTC"})
    store.save_values({"LLM_PROVIDER": "openai"})

    assert store.read_settings() == {"LLM_PROVIDER": "openai", "TZ": "UTC"}


def test_sync_applies_new_version_and_keeps_process_owned_keys(db, cache, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("SECRET_KEY", "process-secret")
    calls = []
    monkeypatch.setattr(runtime, "refresh_runtime_services", lambda: calls.append("refresh"))

    store.save_values({"LLM_PROVIDER": "openai", "SECRET_KEY": "saved-secret"})
    version = runtime.sync_runtime_settings(0)

    assert os.environ["LLM_PROVIDER"] == "openai"
    assert os.environ["SECRET_KEY"] == "process-secret"
    assert calls == ["refresh"]
    # Nothing new saved: no reload.
    assert runtime.sync_runtime_settings(version) == version
    assert calls == ["refresh"]
