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
