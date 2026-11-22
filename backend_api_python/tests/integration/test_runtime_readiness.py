"""Generation/lease acknowledgements against an isolated PostgreSQL schema."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.services.strategy_command_repository import StrategyCommandRepository
from app.services.strategy_event_subscriptions import StrategyEventSubscriptionRepository
from app.services.strategy_runtime.identity import ensure_strategy_run
from app.services.trading_executor import TradingExecutor
from app.utils import db_postgres as pg


@pytest.fixture
def runtime_db(monkeypatch):
    dsn = os.environ.get("QD_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("QD_TEST_POSTGRES_DSN is required")
    import psycopg2
    from psycopg2 import sql
    from psycopg2.pool import ThreadedConnectionPool

    schema = "qd_runtime_test_" + uuid4().hex
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    tables = (
        "qd_strategies_trading", "qd_strategy_commands", "qd_strategy_runtime_leases",
        "qd_strategy_event_subscriptions", "strategy_runs", "strategy_runtime_state",
        "strategy_runtime_events",
        "pending_orders", "strategy_order_fills", "qd_strategy_virtual_trades",
    )
    with admin.cursor() as cur:
        cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        for table in tables:
            cur.execute(sql.SQL("CREATE TABLE {}.{} (LIKE public.{} INCLUDING DEFAULTS INCLUDING INDEXES)").format(
                sql.Identifier(schema), sql.Identifier(table), sql.Identifier(table),
            ))
            if table not in {"qd_strategy_runtime_leases", "qd_strategy_event_subscriptions"}:
                cur.execute(sql.SQL("CREATE SEQUENCE {}.{}").format(sql.Identifier(schema), sql.Identifier(table + "_ids")))
                cur.execute(sql.SQL("ALTER TABLE {}.{} ALTER COLUMN id SET DEFAULT nextval(%s)").format(
                    sql.Identifier(schema), sql.Identifier(table),
                ), (schema + "." + table + "_ids",))
    pool = ThreadedConnectionPool(1, 5, dsn, options=f"-c search_path={schema}")
    monkeypatch.setattr(pg, "_get_connection_pool", lambda: pool)
    monkeypatch.setattr(pg, "_acquire_conn_with_wait", lambda p: p.getconn())

    def query(statement, params=()):
        with pg.get_pg_connection() as db:
            cur = db.cursor()
            cur.execute(statement, params)
            result = cur.fetchall() if cur._cursor.description else []
            db.commit()
            cur.close()
            return result

    query("""INSERT INTO qd_strategies_trading
        (id, user_id, strategy_name, status, desired_runtime_epoch)
        VALUES (1324, 1, 'daily test', 'running', 6)""")
    query("""INSERT INTO strategy_runs (id, strategy_id, runtime_epoch)
        VALUES (4360, 1324, 1)""")
    query("SELECT setval(%s, 4360)", (schema + ".strategy_runs_ids",))
    query("""INSERT INTO qd_strategy_event_subscriptions
        (strategy_id, event_type, partition_key, timeframe, strategy_shard)
        VALUES (1324, 'market.bar.closed.v1', 'daily-test', '1d', 44)""")
    try:
        yield query, StrategyCommandRepository(), StrategyEventSubscriptionRepository()
    finally:
        pool.closeall()
        with admin.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        admin.close()


def test_history_health_reads_only_latest_indexed_rows(runtime_db, monkeypatch):
    from app.services.strategy_runtime import health
    query, _, _ = runtime_db
    query("INSERT INTO strategy_runs (id,strategy_id,runtime_epoch) VALUES (4361,1324,6)")
    query("""INSERT INTO strategy_runtime_state (strategy_run_id,strategy_id,state_key,state_json)
        VALUES (4360,1324,'health','{"last_heartbeat_at":999,"last_error":"old"}'),
               (4361,1324,'health','{"last_heartbeat_at":1000}'),
               (4361,1324,'params','{"last_heartbeat_at":2000}')""")
    query("""INSERT INTO strategy_runtime_events (strategy_run_id,strategy_id,event_type,message,created_at)
        SELECT 4361,1324,'test',n::text,TIMESTAMP '2026-11-22' FROM generate_series(1,10000) n""")
    query("ANALYZE strategy_runtime_events")
    statements = []

    def read(sql, params):
        statements.append((sql, params))
        return query(sql, params)

    monkeypatch.setattr(health, '_query', read)
    snapshots = {1324: health._empty_snapshot()}
    health._load_latest_runs(snapshots, '%s', [1324])
    health._load_health_state(snapshots, '%s', [1324])
    health._load_latest_events(snapshots, '%s', [1324])
    assert snapshots[1324]['run_id'] == 4361
    assert snapshots[1324]['last_heartbeat_at'] == 1000
    assert snapshots[1324]['last_error'] == ''
    assert snapshots[1324]['last_event_message'] == '10000'
    sql, params = statements[-1]
    plan = query('EXPLAIN (ANALYZE, FORMAT JSON) ' + sql, params)[0]['QUERY PLAN'][0]['Plan']

    def nodes(plan):
        yield plan
        for child in plan.get('Plans', []):
            yield from nodes(child)

    scans = [node for node in nodes(plan) if node.get('Relation Name') == 'strategy_runtime_events']
    assert len(scans) == 1
    assert scans[0]['Node Type'] in {'Index Scan', 'Index Only Scan'}
    assert scans[0]['Actual Rows'] == 1


def test_health_pending_orders_and_latest_fills_keep_run_and_mode_isolation(runtime_db):
    from app.services.strategy_runtime import health
    query, _, _ = runtime_db
    query("UPDATE qd_strategies_trading SET execution_mode='live' WHERE id=1324")
    query("INSERT INTO strategy_runs (id,strategy_id,runtime_epoch) VALUES (4361,1324,6)")
    query("""INSERT INTO pending_orders (strategy_id,strategy_run_id,user_id,symbol,signal_type,idempotency_key,status)
        VALUES (1324,4360,1,'BTC/USDT','open_long','old','failed'),
               (1324,4361,1,'BTC/USDT','open_long','new','pending')""")
    query("""INSERT INTO strategy_order_fills (strategy_id,filled_at)
        VALUES (1324,'2026-11-21'),(1324,'2026-11-22')""")
    query("""INSERT INTO qd_strategy_virtual_trades
        (user_id,strategy_id,virtual_order_id,pending_order_id,symbol,type,side,created_at)
        VALUES (1,1324,1,1,'BTC/USDT','open_long','long','2026-11-23')""")
    snapshots = {1324: health._empty_snapshot()}
    health._load_latest_runs(snapshots, '%s', [1324])
    health._load_pending_orders(snapshots, '%s', [1324])
    health._load_latest_fills(snapshots, '%s', [1324])
    assert snapshots[1324]['pending_orders'] == 1
    assert snapshots[1324]['historical_failed_orders'] == 0
    assert snapshots[1324]['last_fill_at'].isoformat().startswith('2026-11-22')
    query("UPDATE qd_strategies_trading SET execution_mode='signal' WHERE id=1324")
    health._load_latest_fills(snapshots, '%s', [1324])
    assert snapshots[1324]['last_fill_at'].isoformat().startswith('2026-11-23')


def test_old_healthy_daily_run_does_not_satisfy_new_start(runtime_db):
    query, repository, subscriptions = runtime_db
    token = repository.acquire_strategy_lease(strategy_id=1324, owner_id="evaluator", lease_seconds=30)
    assert subscriptions.unready_strategy_ids() == [1324]
    assert not repository.acknowledge_runtime_ready(strategy_id=1324, run_id=4360, epoch=1, token=token)
    assert not repository.runtime_request(1324)["ready"]
    run = ensure_strategy_run(strategy_id=1324, runtime_epoch=6)
    assert run.runtime_epoch == 6 and run.strategy_run_id > 4360
    assert repository.acknowledge_runtime_ready(strategy_id=1324, run_id=run.strategy_run_id, epoch=6, token=token)
    assert repository.runtime_request(1324)["ready"]
    assert subscriptions.unready_strategy_ids() == []
    assert query("SELECT runtime_status FROM strategy_runs WHERE id = 4360")[0]["runtime_status"] == "stopped"


@pytest.mark.parametrize("new_owner", ["evaluator", "replacement"])
def test_lease_takeover_requires_fresh_readiness_even_for_same_owner(runtime_db, new_owner):
    query, repository, subscriptions = runtime_db
    run = ensure_strategy_run(strategy_id=1324, runtime_epoch=6)
    token = repository.acquire_strategy_lease(strategy_id=1324, owner_id="evaluator", lease_seconds=30)
    assert repository.acknowledge_runtime_ready(strategy_id=1324, run_id=run.strategy_run_id, epoch=6, token=token)
    query("UPDATE qd_strategy_runtime_leases SET lease_expires_at = NOW() - INTERVAL '1 second'")
    newer = repository.acquire_strategy_lease(strategy_id=1324, owner_id=new_owner, lease_seconds=30)
    assert newer > token
    assert subscriptions.unready_strategy_ids() == [1324]
    assert not repository.acknowledge_runtime_ready(strategy_id=1324, run_id=run.strategy_run_id, epoch=6, token=token)
    assert repository.acknowledge_runtime_ready(strategy_id=1324, run_id=run.strategy_run_id, epoch=6, token=newer)


def test_start_polling_is_durable_without_consuming_failure_attempts(runtime_db):
    query, repository, _ = runtime_db
    command = repository.enqueue(strategy_id=1324, command_type="start")
    assert command.payload["requested_runtime_epoch"] == 6
    claimed = repository.claim_next(owner_id="controller", lease_seconds=30, max_attempts=3)
    assert claimed.id == command.id and claimed.attempts == 1
    repository.defer_start(claimed.id, owner_id="controller", result={"status": "starting", "runtime_epoch": 6})
    query("""UPDATE qd_strategy_commands SET available_at = NOW(),
        claimed_at = NOW() - INTERVAL '200 seconds' WHERE id = %s""", (command.id,))
    retried = repository.claim_next(owner_id="other-controller", lease_seconds=30, max_attempts=3)
    assert retried.id == command.id and retried.attempts == 1
    assert retried.elapsed_seconds >= 200
    assert retried.result["status"] == "starting"


def test_remote_restart_is_claimable_and_advances_epoch_only_once(runtime_db):
    _, repository, _ = runtime_db
    repository.acquire_strategy_lease(strategy_id=1324, owner_id="remote-evaluator", lease_seconds=30)
    command = repository.enqueue(strategy_id=1324, command_type="restart", user_id=1)
    claimed = repository.claim_next(owner_id="controller", lease_seconds=30, max_attempts=3)
    assert claimed.id == command.id
    assert repository.begin_restart(claimed) == 7
    assert repository.begin_restart(claimed) == 7
    assert repository.runtime_request(1324)["desired_runtime_epoch"] == 7
    assert not repository.has_active_strategy_lease(1324)
    assert repository.get(command.id).payload["runtime_epoch"] == 7


def test_pending_stop_blocks_preparation_and_late_failure_cannot_stop_new_epoch(runtime_db):
    query, repository, subscriptions = runtime_db
    assert subscriptions.unready_strategy_ids() == [1324]
    repository.enqueue(strategy_id=1324, command_type="stop")
    assert subscriptions.unready_strategy_ids() == []
    TradingExecutor._mark_stopped(1324, expected_epoch=1)
    assert repository.runtime_request(1324)["status"] == "running"
    TradingExecutor._mark_stopped(1324, expected_epoch=6)
    assert repository.runtime_request(1324)["status"] == "stopped"
    assert not repository.acknowledge_runtime_ready(strategy_id=1324, run_id=4360, epoch=6, token=1)


def test_commands_for_one_strategy_cannot_run_on_two_controllers(runtime_db):
    _, repository, _ = runtime_db
    start = repository.enqueue(strategy_id=1324, command_type="start")
    repository.enqueue(strategy_id=1324, command_type="stop")
    assert repository.claim_next(owner_id="a", lease_seconds=30, max_attempts=3).id == start.id
    assert repository.claim_next(owner_id="b", lease_seconds=30, max_attempts=3) is None


def test_concurrent_claimers_never_execute_two_commands_for_one_strategy(runtime_db):
    query, repository, _ = runtime_db
    repository.enqueue(strategy_id=1324, command_type="start")
    repository.enqueue(strategy_id=1324, command_type="stop")
    with ThreadPoolExecutor(max_workers=2) as pool:
        for _ in range(20):
            query("UPDATE qd_strategy_commands SET status = 'pending', attempts = 0, lease_expires_at = NULL")
            barrier = threading.Barrier(2)

            def claim(owner):
                barrier.wait(timeout=5)
                return repository.claim_next(owner_id=owner, lease_seconds=30, max_attempts=3)

            results = list(pool.map(claim, ("a", "b")))
            assert sum(result is not None for result in results) == 1
