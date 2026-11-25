"""Persisted grid verification results shared by HTTP and trading workers."""

import hashlib
import json
import time
from datetime import datetime, timezone

from app.services.exchange_execution import coalesce_exchange_config_from_payload
from app.services.strategy_command_repository import StrategyCommandRepository

AUDIT_INTERVAL_SECONDS = 30
UNKNOWN_RECHECK_SECONDS = 300


def audit_identity(strategy: dict) -> str:
    tc = strategy.get('trading_config') or {}
    context = [strategy.get('id'), strategy.get('user_id'),
               tc.get('market_type') or strategy.get('market_type') or 'swap',
               coalesce_exchange_config_from_payload(strategy)]
    return hashlib.sha256(json.dumps(context, sort_keys=True, default=str).encode()).hexdigest()


def cached_order_audit(row, identity: str) -> dict:
    saved = (row.extra or {}).get('exchange_audit') or {}
    if (saved.get('identity') != identity
            or saved.get('exchange_order_id') != str(row.exchange_order_id or '')
            or saved.get('symbol') != row.symbol
            or float(row.filled_quantity or 0) > float(saved.get('filled') or 0) + 1e-12):
        return {}
    result = dict(saved)
    if row.status == result.get('status') and float(row.processed_fill_qty or 0) + 1e-12 >= float(result.get('filled') or 0):
        result['reconciliation_pending'] = False
    return result


def audit_is_fresh(entry: dict, *, now: float | None = None) -> bool:
    if not entry:
        return False
    retry_cancel_update = (entry.get('status') == 'cancelled' and not entry.get('filled')
                           and entry.get('reconciliation_pending'))
    if entry.get('status') in {'filled', 'cancelled'} and not retry_cancel_update:
        return True
    try:
        age = (time.time() if now is None else now) - datetime.fromisoformat(entry['checked_at']).timestamp()
    except (KeyError, ValueError, TypeError):
        return False
    ttl = UNKNOWN_RECHECK_SECONDS if entry.get('status') == 'not_open' else AUDIT_INTERVAL_SECONDS
    return 0 <= age < ttl


def read_grid_order_audit(strategy: dict, rows: list, *, user_id: int, sync: bool) -> dict:
    """HTTP only reads evidence and queues a deduplicated worker command."""
    identity = audit_identity(strategy)
    orders = {}
    needs_refresh = False
    for row in rows:
        entry = cached_order_audit(row, identity)
        fresh = audit_is_fresh(entry)
        needs_refresh = needs_refresh or not fresh
        if entry:
            orders[row.id] = entry
    active = sum(entry.get('status') in {'open', 'partial'} for entry in orders.values())
    terminal = sum(entry.get('status') in {'filled', 'cancelled'} for entry in orders.values())
    unknown = len(rows) - active - terminal
    reconciliation_pending = sum(bool(entry.get('reconciliation_pending')) for entry in orders.values())
    pending = False
    error = 'grid_exchange_orders_unverified' if unknown else ''
    if reconciliation_pending:
        error = 'grid_exchange_reconciliation_pending'
    if sync and rows and needs_refresh:
        epoch = int(strategy.get('desired_runtime_epoch') or 0)
        bucket = int(time.time() // AUDIT_INTERVAL_SECONDS)
        command = StrategyCommandRepository().enqueue(
            strategy_id=int(strategy['id']), user_id=user_id, command_type='reconcile',
            payload={'grid_order_audit': True},
            idempotency_key=f'grid-audit:{strategy["id"]}:{epoch}:{bucket}',
        )
        pending = command.status in {'pending', 'processing'}
        if command.status == 'failed':
            error = 'grid_exchange_snapshot_failed'
        elif (command.result or {}).get('error'):
            error = command.result['error']
    times = [entry['checked_at'] for entry in orders.values() if entry.get('checked_at')]
    return dict(orders=orders, active=active, terminal=terminal, unknown=unknown,
                completed=not needs_refresh, pending=pending, error=error,
                reconciliation_pending=reconciliation_pending,
                checked_at=min(times) if times else None)


def stamp_audit(entry: dict, row, identity: str) -> dict:
    return {**entry, 'identity': identity, 'exchange_order_id': str(row.exchange_order_id or ''),
            'symbol': row.symbol, 'checked_at': datetime.now(timezone.utc).isoformat()}
