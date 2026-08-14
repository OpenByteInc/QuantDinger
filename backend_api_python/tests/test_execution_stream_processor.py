from types import SimpleNamespace

from app.services.execution_streams.processor import ExecutionEventProcessor


def test_futu_cumulative_event_excludes_fill_already_in_trade_ledger():
    delta, target = ExecutionEventProcessor._fill_progress(
        previous=0,
        durable_recorded=10,
        event_qty=0,
        cumulative=10,
        is_cumulative=True,
    )

    assert delta == 0
    assert target == 10


def test_futu_incremental_event_excludes_ledger_ahead_quantity():
    delta, target = ExecutionEventProcessor._fill_progress(
        previous=2,
        durable_recorded=5,
        event_qty=4,
        cumulative=0,
        is_cumulative=False,
    )

    assert delta == 1
    assert target == 6


def test_futu_quote_currency_fees_skip_client(monkeypatch):
    created = []
    processor = ExecutionEventProcessor()
    processor.repository = SimpleNamespace(
        fee_components=lambda _event_id: [{"currency": "HKD", "amount": 1.25, "quote_amount": None}],
    )
    monkeypatch.setattr(
        "app.services.execution_streams.processor.create_client",
        lambda *_args, **_kwargs: created.append(1) or object(),
    )
    event = {"id": 11, "exchange_id": "futu"}
    assert processor._fee_client_required(event, {"exchange_id": "futu"}) is False
    with processor._fee_client(event, {"exchange_id": "futu"}, "spot") as client:
        fees, quote = processor._fees(event, client=client, symbol="00700.HK", price=456.6)
    assert client is None
    assert created == []
    assert fees == {"HKD": 1.25}
    assert quote == 1.25


def test_processor_releases_fee_client_after_conversion(monkeypatch):
    class Client:
        disconnected = False

        def disconnect(self):
            self.disconnected = True

    client = Client()
    processor = ExecutionEventProcessor()
    processor.repository = SimpleNamespace(
        fee_components=lambda _event_id: [{"currency": "BTC", "amount": 0.01}],
    )
    monkeypatch.setattr(
        "app.services.execution_streams.processor.create_client",
        lambda *_args, **_kwargs: client,
    )
    event = {"id": 12, "exchange_id": "binance"}
    with processor._fee_client(event, {"exchange_id": "binance"}, "swap") as got:
        assert got is client
        assert client.disconnected is False
    assert client.disconnected is True

