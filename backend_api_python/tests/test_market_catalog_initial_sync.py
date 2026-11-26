"""Initial market catalog synchronization tests."""

from __future__ import annotations

import pytest


class _CatalogCursor:
    def __init__(self, row):
        self.row = row

    def execute(self, _query):
        assert "status IN ('success', 'partial')" in _query
        return None

    def fetchone(self):
        return self.row

    def close(self):
        return None


class _CatalogConnection:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def cursor(self):
        return _CatalogCursor(self.row)


def test_initial_sync_starts_when_catalog_is_not_initialized(monkeypatch):
    from app.services import market_catalog_sync

    monkeypatch.setenv("MARKET_CATALOG_AUTO_SYNC", "true")
    monkeypatch.delenv("PYTHON_API_DEBUG", raising=False)
    monkeypatch.setattr(market_catalog_sync, "_market_catalog_is_initialized", lambda: False)
    monkeypatch.setattr(
        market_catalog_sync,
        "start_market_catalog_sync",
        lambda trigger: {"started": True, "run_id": 41, "trigger": trigger},
    )

    result = market_catalog_sync.start_market_catalog_sync_on_boot()

    assert result == {"started": True, "run_id": 41, "trigger": "startup"}


def test_initial_sync_skips_an_initialized_catalog(monkeypatch):
    from app.services import market_catalog_sync

    monkeypatch.setenv("MARKET_CATALOG_AUTO_SYNC", "true")
    monkeypatch.delenv("PYTHON_API_DEBUG", raising=False)
    monkeypatch.setattr(market_catalog_sync, "_market_catalog_is_initialized", lambda: True)
    monkeypatch.setattr(
        market_catalog_sync,
        "start_market_catalog_sync",
        lambda trigger: (_ for _ in ()).throw(AssertionError(f"unexpected sync: {trigger}")),
    )

    result = market_catalog_sync.start_market_catalog_sync_on_boot()

    assert result == {"started": False, "reason": "already_initialized"}


def test_initial_sync_respects_disabled_setting(monkeypatch):
    from app.services import market_catalog_sync

    monkeypatch.setenv("MARKET_CATALOG_AUTO_SYNC", "false")
    monkeypatch.setattr(
        market_catalog_sync,
        "_market_catalog_is_initialized",
        lambda: (_ for _ in ()).throw(AssertionError("database should not be queried")),
    )

    result = market_catalog_sync.start_market_catalog_sync_on_boot()

    assert result == {"started": False, "reason": "disabled"}


def test_catalog_schema_version_is_recorded_for_upgrade_detection(monkeypatch):
    from app.services import market_catalog_sync
    from app.services import symbol_master_sync
    monkeypatch.setattr(symbol_master_sync, "sync_symbol_master", lambda markets: {market: {"ok": True, "rows": 1, "upserted": 1} for market in markets})

    completed = {}
    monkeypatch.setattr(
        market_catalog_sync,
        "fetch_crypto_symbols_with_diagnostics",
        lambda: ([], []),
    )
    monkeypatch.setattr(
        market_catalog_sync,
        "reclassify_stored_equity_products",
        lambda: 0,
    )
    monkeypatch.setattr(
        market_catalog_sync,
        "_finish_run",
        lambda run_id, status, result: completed.update(
            run_id=run_id, status=status, result=result,
        ),
    )

    market_catalog_sync._run_sync(17)

    assert completed["status"] == "success"
    assert {item['market_type'] for item in completed['result']['contexts']} == {'HKStock', 'Futures'}
    assert completed["result"]["catalog_schema_version"] == (
        market_catalog_sync.MARKET_CATALOG_SCHEMA_VERSION
    )


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"active_crypto": 10, "latest_success_result": {"rows": 10}}, False),
        ({
            "active_crypto": 10,
            "latest_success_result": {"catalog_schema_version": 2},
        }, False),
        ({
            "active_crypto": 0,
            "latest_success_result": {"catalog_schema_version": 2},
        }, False),
        ({
            "active_crypto": 10,
            "latest_success_result": {"catalog_schema_version": 4},
        }, True),
    ],
)
def test_initialized_catalog_requires_current_schema(monkeypatch, row, expected):
    from app.services import market_catalog_sync

    monkeypatch.setattr(
        market_catalog_sync,
        "get_db_connection",
        lambda: _CatalogConnection(row),
    )

    assert market_catalog_sync._market_catalog_is_initialized() is expected
