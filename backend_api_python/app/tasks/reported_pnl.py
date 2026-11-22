"""Finite venue-report backfills; HTTP history reads never wait for an exchange."""
from collections import defaultdict

from app.celery_app import celery_app
from app.services.execution_streams.pnl_reconciliation import _claim, enrich_reported_order_pnl, order_key
from app.services.strategy import get_strategy_service
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)


def schedule_reported_pnl(rows, *, strategy_id: int, user_id: int) -> None:
    groups = defaultdict(list)
    for row in rows:
        if (row.get('exchange_pnl') or {}).get('status') == 'pending':
            groups[order_key(row)].append(row)
    scheduled = 0
    for key, members in groups.items():
        if scheduled >= 2:
            break
        if key[1] == 'ibkr':
            continue
        expected = sum(float(row.get('amount') or 0) for row in members)
        if expected <= 0 or not _claim(key, expected):
            continue
        scheduled += 1
        try:
            with celery_app.connection_for_write(connect_timeout=0.5, transport_options={
                'socket_connect_timeout': 0.5, 'socket_timeout': 0.5, 'max_retries': 0,
            }) as connection:
                reconcile_reported_pnl.apply_async(
                    args=[int(strategy_id), int(user_id), list(key)], connection=connection,
                    queue='maintenance', retry=False, expires=120,
                )
        except Exception:
            logger.warning('Unable to queue order P&L backfill for strategy=%s', strategy_id, exc_info=True)
            break


@celery_app.task(name='quantdinger.tasks.reconcile_reported_pnl', ignore_result=True,
                 soft_time_limit=90, time_limit=120)
def reconcile_reported_pnl(strategy_id: int, user_id: int, key: list) -> None:
    strategy = get_strategy_service().get_strategy(strategy_id, user_id=user_id)
    if not strategy:
        return
    key = tuple(key)
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute('''
            SELECT t.id, t.credential_id, c.exchange_id, t.market_type, t.symbol,
                   t.exchange_order_id, t.amount, t.type, t.created_at
            FROM qd_strategy_trades t
            JOIN qd_exchange_credentials c ON c.id = t.credential_id AND c.user_id = t.user_id
            WHERE t.strategy_id = %s AND t.user_id = %s
              AND t.credential_id = %s AND LOWER(c.exchange_id) = %s
              AND LOWER(t.market_type) = %s AND t.symbol = %s AND t.exchange_order_id = %s
            ORDER BY t.id DESC
        ''', (strategy_id, user_id, *key))
        rows = [dict(row) for row in cur.fetchall() or []]
        cur.close()
    enrich_reported_order_pnl(rows, user_id=user_id, trading_config=strategy.get('trading_config') or {},
                             claimed_keys={key})
