"""Durable, strategy-scoped cancellation requests and exchange dispatch."""
import json
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)


def persist_cancellations(context, service):
    references = set(context._cancelled_order_ids)
    if not references:
        return
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("""
            UPDATE pending_orders po
            SET payload_json = (COALESCE(NULLIF(po.payload_json, ''), '{}')::jsonb
                    || '{"strategy_cancel_requested": true}'::jsonb)::text,
                status = CASE WHEN po.status = 'pending' AND COALESCE(po.attempts, 0) = 0
                              AND COALESCE(po.exchange_order_id, '') = ''
                              THEN 'cancelled' ELSE po.status END,
                updated_at = NOW()
            FROM strategy_order_intents soi
            WHERE po.order_intent_id = soi.id
              AND po.strategy_id = soi.strategy_id AND po.strategy_run_id = soi.strategy_run_id
              AND soi.strategy_id = %s AND soi.strategy_run_id = %s
              AND soi.client_order_id = ANY(%s)
              AND po.status IN ('pending', 'processing', 'sent', 'syncing')
            RETURNING soi.id, soi.client_order_id, po.status
        """, (service.strategy_id, service.strategy_run_id, sorted(references)))
        rows = cur.fetchall() or []
        for row in rows:
            cur.execute("""
                UPDATE strategy_order_intents SET status = %s, updated_at = NOW()
                WHERE id = %s AND status NOT IN ('filled', 'cancelled', 'rejected', 'failed', 'expired')
            """, ('cancelled' if row['status'] == 'cancelled' else 'cancel_pending', row['id']))
        db.commit()
        cur.close()
    acknowledged = {str(row['client_order_id']) for row in rows}
    terminal = {'filled', 'cancelled', 'canceled', 'rejected', 'failed', 'expired'}
    acknowledged.update(ref for ref in references if context.get_order_status(ref)['status'] in terminal)
    context._cancelled_order_ids.difference_update(acknowledged)


def intercept_cancelled_dispatch(order_row):
    if not order_row.get('order_intent_id'):
        return False
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute('SELECT * FROM pending_orders WHERE id = %s FOR UPDATE', (order_row['id'],))
        row = cur.fetchone()
        if not row:
            return True
        payload = row.get('payload_json') or '{}'
        payload = json.loads(payload) if isinstance(payload, str) else payload
        if not payload.get('strategy_cancel_requested'):
            return False
        if row['status'] == 'processing':
            # The fetched row predates the claim; zero attempts proves no earlier dispatch.
            never_sent = int(order_row.get('attempts') or 0) == 0 and not row.get('exchange_order_id')
            status = 'cancelled' if never_sent else ('sent' if row.get('exchange_order_id') else 'pending')
            error = 'strategyV2.cancellationNeedsReconciliation' if status == 'pending' else ''
            cur.execute('UPDATE pending_orders SET status=%s, last_error=%s, updated_at=NOW() WHERE id=%s',
                        (status, error, row['id']))
            cur.execute('''UPDATE strategy_order_intents SET status=%s, updated_at=NOW()
                           WHERE id=%s AND status NOT IN ('filled','cancelled','rejected','failed','expired')''',
                        ('cancelled' if never_sent else 'cancel_pending', row['order_intent_id']))
        db.commit()
        cur.close()
    return True


def dispatch_requested_cancel(client, row, payload, exchange_config):
    if not payload.get('strategy_cancel_requested'):
        return
    try:
        exchange = str(exchange_config.get('exchange_id') or row.get('exchange_id') or '').lower()
        if exchange in {'alpaca', 'ibkr', 'tiger', 'webull'}:
            client.cancel_order(str(row['exchange_order_id']))
        else:
            from app.services.pending_orders.live_order_phases import cancel_live_limit_order
            cancel_live_limit_order(
                client=client, symbol=str(row.get('symbol') or payload.get('symbol') or ''),
                order_id=str(row['exchange_order_id']), client_order_id='',
                market_type=str(row.get('market_type') or payload.get('market_type') or 'spot'),
                exchange_config=exchange_config,
            )
    except Exception as exc:
        # Even a rejected cancellation may have raced a fill; always reconcile.
        logger.warning('Strategy cancellation unconfirmed pending_id=%s: %s', row.get('id'), exc)
