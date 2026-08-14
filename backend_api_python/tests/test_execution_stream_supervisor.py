from __future__ import annotations

import json

from app.services.execution_streams import supervisor as supervisor_module
from app.services.execution_streams.supervisor import ExecutionStreamSupervisor, StreamSpec


def test_stream_discovery_only_loads_credentials_used_by_active_work(monkeypatch):
    service = ExecutionStreamSupervisor()
    service._max_adapters = 64
    requested_ids: list[int] = []

    monkeypatch.setattr(service, "_symbols_by_credential", lambda: {7: {"BTC/USDT"}})

    def credential_rows(ids):
        requested_ids.extend(ids)
        return [{
            "id": 7,
            "user_id": 3,
            "exchange_id": "binance",
            "encrypted_config": "encrypted",
        }]

    monkeypatch.setattr(service, "_credential_rows", credential_rows)
    monkeypatch.setattr(
        supervisor_module,
        "decrypt_credential_blob",
        lambda _value: json.dumps({"market_scope": "spot"}),
    )

    specs = service._discover_specs()

    assert requested_ids == [7]
    assert [spec.key for spec in specs] == ["binance:7:spot"]
    assert specs[0].symbols == ("BTC/USDT",)


def test_stream_discovery_applies_adapter_cap_and_uses_rest_for_overflow(monkeypatch):
    service = ExecutionStreamSupervisor()
    service._max_adapters = 1
    monkeypatch.setattr(
        service,
        "_symbols_by_credential",
        lambda: {7: {"BTC/USDT"}, 8: {"ETH/USDT"}},
    )
    monkeypatch.setattr(
        service,
        "_credential_rows",
        lambda _ids: [
            {
                "id": 7,
                "user_id": 3,
                "exchange_id": "binance",
                "encrypted_config": "first",
            },
            {
                "id": 8,
                "user_id": 3,
                "exchange_id": "gate",
                "encrypted_config": "second",
            },
        ],
    )
    monkeypatch.setattr(
        supervisor_module,
        "decrypt_credential_blob",
        lambda _value: json.dumps({"market_scope": "spot"}),
    )

    specs = service._discover_specs()

    assert len(specs) == 1
    assert specs[0].key == "binance:7:spot"


def test_stream_discovery_registers_futu_with_canonical_stock_key(monkeypatch):
    service = ExecutionStreamSupervisor()
    monkeypatch.setattr(service, "_symbols_by_credential", lambda: {9: {"AAPL"}})
    monkeypatch.setattr(
        service,
        "_credential_rows",
        lambda _ids: [{
            "id": 9,
            "user_id": 3,
            "exchange_id": "futu",
            "encrypted_config": "encrypted",
        }],
    )
    monkeypatch.setattr(
        supervisor_module,
        "decrypt_credential_blob",
        lambda _value: json.dumps({"trade_market": "US"}),
    )

    specs = service._discover_specs()

    assert [spec.key for spec in specs] == ["futu:9:stock"]
    assert specs[0].market_type == "stock"
    assert service._stream_key_for_event(
        type("Event", (), {
            "exchange_id": "futu",
            "credential_id": 9,
            "market_type": "USStock",
        })()
    ) == "futu:9:stock"


def test_reconcile_replaces_disconnected_adapter_with_unchanged_spec(monkeypatch):
    service = ExecutionStreamSupervisor()
    spec = StreamSpec(
        key="futu:9:stock",
        credential_id=9,
        user_id=3,
        exchange_id="futu",
        market_type="stock",
        config_json="{}",
        symbols=("AAPL",),
    )

    class OldAdapter:
        connected = False

        def stop(self):
            return True

    created = []

    class NewAdapter:
        connected = False

        def __init__(self, **_kwargs):
            created.append(self)

        def start(self):
            self.connected = True

    service._adapters[spec.key] = OldAdapter()
    service._specs[spec.key] = spec
    monkeypatch.setattr(service, "_discover_specs", lambda: [spec])
    monkeypatch.setattr(service, "_run_rest_catchup_limited", lambda *_args, **_kwargs: None)
    monkeypatch.setitem(supervisor_module.ADAPTERS, "futu", NewAdapter)

    service._reconcile()

    assert created
    assert service._adapters[spec.key] is created[0]
    assert created[0].connected


def test_reconcile_does_not_start_second_futu_stream_when_stop_times_out(monkeypatch):
    service = ExecutionStreamSupervisor()
    spec = StreamSpec(
        key="futu:9:stock",
        credential_id=9,
        user_id=3,
        exchange_id="futu",
        market_type="stock",
        config_json="{}",
        symbols=("AAPL",),
    )

    class OldAdapter:
        connected = False
        orphaned = False
        credential_id = 9

        def stop(self):
            self.orphaned = True
            return False

        def is_alive(self):
            return True

    created = []

    class NewAdapter:
        connected = False

        def __init__(self, **_kwargs):
            created.append(self)

        def start(self):
            self.connected = True

    service._adapters[spec.key] = OldAdapter()
    service._specs[spec.key] = spec
    monkeypatch.setattr(service, "_discover_specs", lambda: [spec])
    monkeypatch.setattr(service, "_run_rest_catchup_limited", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "_health_detail", lambda *_args, **_kwargs: "orphaned")
    monkeypatch.setattr(service.repository, "update_health", lambda **_kwargs: None)
    monkeypatch.setitem(supervisor_module.ADAPTERS, "futu", NewAdapter)

    service._reconcile()

    assert created == []
    assert spec.key not in service._adapters
    assert spec.key in service._orphans


def test_active_stream_query_excludes_stopped_and_signal_strategies(monkeypatch):
    executed: list[str] = []

    class Cursor:
        def execute(self, sql, _params=None):
            executed.append(sql)

        def fetchall(self):
            return []

        def close(self):
            return None

    class Connection:
        def cursor(self):
            return Cursor()

    class Context:
        def __enter__(self):
            return Connection()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(supervisor_module, "get_db_connection", lambda: Context())

    assert ExecutionStreamSupervisor._symbols_by_credential() == {}
    normalized = " ".join(executed[0].lower().split())
    assert "status, '')) = 'running'" in normalized
    assert "execution_mode, 'signal')) = 'live'" in normalized
