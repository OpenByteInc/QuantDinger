"""Run only against an explicitly supplied, disposable PostgreSQL database."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.services.grid.resting_orders_repo import GridRestingOrder, GridRestingOrderRepository
from app.utils import db_postgres as pg


@pytest.fixture
def repository(monkeypatch):
    dsn = os.environ.get("QD_GRID_SUBMISSION_TEST_DSN")
    if not dsn:
        pytest.skip("QD_GRID_SUBMISSION_TEST_DSN must point to a disposable database")
    import psycopg2
    from psycopg2 import sql
    from psycopg2.pool import ThreadedConnectionPool

    schema = "grid_submit_test_" + uuid4().hex
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    pool = ThreadedConnectionPool(1, 5, dsn, options=f"-c search_path={schema}")
    monkeypatch.setattr(pg, "_get_connection_pool", lambda: pool)
    monkeypatch.setattr(pg, "_acquire_conn_with_wait", lambda p: p.getconn())

    def query(statement, parameters=()):
        with pg.get_pg_connection() as db:
            cursor = db.cursor()
            cursor.execute(statement, parameters)
            rows = cursor.fetchall() if cursor._cursor.description else []
            db.commit()
            cursor.close()
            return rows

    repo = GridRestingOrderRepository()
    try:
        yield repo, query
    finally:
        pool.closeall()
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


def intent(cell=1):
    return GridRestingOrder(strategy_id=9, symbol="BTC/USDT", cell_index=cell,
        purpose="long_entry", side="buy", pos_side="long", price=100, quantity=0.01,
        client_order_id=uuid4().hex)


def test_concurrent_reservations_allow_only_one_submit(repository):
    repo, query = repository
    barrier = Barrier(2)
    def reserve(cell):
        expected = repo.list_open(9)
        barrier.wait(timeout=10)
        return repo.reserve_pending(intent(cell), expected)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, [1, 2]))
    assert sum(result is not None for result in results) == 1
    assert len(repo.list_open(9)) == 1
    assert repo.list_open(9)[0].status == "pending"


def test_pending_intent_survives_new_repository_and_acknowledges(repository):
    repo, query = repository
    identifier = repo.reserve_pending(intent(), [])
    restarted = GridRestingOrderRepository()
    assert restarted.reserve_pending(intent(2), restarted.list_open(9)) is None
    assert restarted.acknowledge_submission(identifier, "venue-123")
    row = restarted.list_open(9)[0]
    assert row.status == "open" and row.exchange_order_id == "venue-123"


@pytest.mark.parametrize("status", ["partial", "filled", "cancelled"])
def test_ack_cannot_regress_reconciled_fill(repository, status):
    repo, query = repository
    identifier = repo.reserve_pending(intent(), [])
    repo.update_status(identifier, status=status, filled_quantity=0.005, processed_fill_qty=0.005)
    assert not repo.acknowledge_submission(identifier, "venue-123")
    row = query("SELECT * FROM qd_grid_resting_orders WHERE id = %s", (identifier,))[0]
    assert row["status"] == status
    assert float(row["processed_fill_qty"]) == 0.005


def test_recovered_open_snapshot_releases_pending_state_and_saves_id(repository, monkeypatch):
    from app.services.execution_streams import processor as module
    from app.services.live_trading.fill_accounting import lock_strategy_fills

    repo, query = repository
    identifier = repo.reserve_pending(intent(), [])
    monkeypatch.setattr(module, "posted_totals", lambda *a: dict(quantity=0, average=0, fees={}, quote=0))
    processor = module.ExecutionEventProcessor(MagicMock())
    processor._fees = lambda *a, **k: ({}, 0)
    processor._incremental_fees = lambda *a: ({}, 0)
    runner = SimpleNamespace(exchange_config={}, engine=MagicMock())
    event = dict(order_status="open", exchange_order_id="recovered-1", cumulative_quantity=0,
                 is_cumulative=True, _runner=runner, _client=object(), symbol="BTC/USDT")
    with pg.get_pg_transaction():
        lock_strategy_fills(9)
        processor._project_grid(event, {"owner_id": identifier, "strategy_id": 9})
    row = repo.list_open(9)[0]
    assert row.status == "open" and row.exchange_order_id == "recovered-1"
    runner.engine.on_order_filled.assert_not_called()


def test_storage_failure_does_not_look_like_empty_orders(repository):
    repo, query = repository
    query("DROP TABLE qd_grid_resting_orders")
    with pytest.raises(Exception):
        repo.list_open(9)
    assert repo.has_open_for_cell(9, 1, "long_entry") is True
    assert repo.reserve_pending(intent(), []) is None


def test_ack_rollback_preserves_intent_for_reconciliation(repository):
    repo, query = repository
    identifier = repo.reserve_pending(intent(), [])
    with pytest.raises(RuntimeError, match="cell write failed"):
        with pg.get_pg_transaction():
            assert repo.acknowledge_submission(identifier, "venue-123")
            raise RuntimeError("cell write failed")
    assert repo.list_open(9)[0].status == "pending"
    assert repo.reserve_pending(intent(2), repo.list_open(9)) is None
