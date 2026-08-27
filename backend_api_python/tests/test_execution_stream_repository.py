from unittest.mock import MagicMock

import app.services.execution_streams.repository as repository_module
from app.services.execution_streams.repository import ExecutionEventRepository


def test_legacy_binding_uses_event_market_for_recursive_lookup(monkeypatch):
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        "id": 22,
        "credential_id": 7,
        "exchange_id": "futu",
        "market_type": "spot",
        "owner_type": "pending_order",
        "owner_id": 22,
        "user_id": 1,
        "strategy_id": 9,
        "symbol": "00700.HK",
        "signal_type": "close_long",
        "client_order_id": "qd_9_22",
        "exchange_order_id": "OID-22",
    }
    connection = MagicMock()
    connection.cursor.return_value = cursor
    context = MagicMock()
    context.__enter__.return_value = connection
    context.__exit__.return_value = False
    monkeypatch.setattr(repository_module, "get_db_connection", lambda: context)

    repository = ExecutionEventRepository()
    repository.register_binding = MagicMock()
    repository.resolve_binding = MagicMock(return_value={"id": 91})

    result = repository._discover_legacy_binding(
        {
            "credential_id": 7,
            "exchange_id": "futu",
            "market_type": "hkstock",
            "exchange_order_id": "OID-22",
            "client_order_id": "qd_9_22",
        }
    )

    assert result == {"id": 91}
    assert repository.register_binding.call_args.kwargs["market_type"] == "hkstock"
