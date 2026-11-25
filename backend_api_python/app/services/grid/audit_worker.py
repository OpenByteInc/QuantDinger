"""Bounded order verification on the existing trading command worker."""

import json
from types import SimpleNamespace

from app.services.grid.audit_state import audit_identity, stamp_audit
from app.services.grid.engine import GridEngine
from app.services.grid.order_audit import audit_grid_orders
from app.services.grid.poller import get_grid_fill_poller
from app.services.grid.resting_orders_repo import GridRestingOrderRepository
from app.services.strategy import StrategyService, _strip_legacy_risk_pct_basis
from app.services.strategy_command_repository import StrategyCommandRepository
from app.services.exchange_execution import coalesce_exchange_config_from_payload, resolve_exchange_config
from app.utils.db import get_db_connection


def binding_matches(binding: dict, row, strategy: dict, config: dict, market: str) -> bool:
    return (
        int(binding.get('strategy_id') or 0) == int(strategy['id'])
        and int(binding.get('user_id') or 0) == int(strategy['user_id'])
        and int(binding.get('credential_id') or 0) == int(config.get('credential_id') or config.get('credentials_id') or 0)
        and str(binding.get('exchange_id') or '').lower() == str(config.get('exchange_id') or '').lower()
        and str(binding.get('market_type') or '').lower() == market
        and str(binding.get('exchange_order_id') or '') == str(row.exchange_order_id or '')
        and str(binding.get('symbol') or '') == row.symbol
    )


def _stopped_context_unchanged(strategy: dict, command_valid) -> bool:
    if not command_valid():
        return False
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute('SELECT * FROM qd_strategies_trading WHERE id = %s FOR UPDATE', (int(strategy['id']),))
        row = cur.fetchone()
        cur.close()
    if not row or row.get('status') != 'stopped':
        return False
    current = dict(row)
    for key in ('exchange_config', 'trading_config'):
        if isinstance(current.get(key), str):
            current[key] = json.loads(current[key])
    return (
        int(current.get('desired_runtime_epoch') or 0) == int(strategy.get('desired_runtime_epoch') or 0)
        and audit_identity(current) == audit_identity(strategy)
        and _strip_legacy_risk_pct_basis(current.get('trading_config') or {}) == (strategy.get('trading_config') or {})
        and not StrategyCommandRepository().has_active_strategy_lease(int(strategy['id']))
    )


def _reconciled(row, entry) -> bool:
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute('SELECT status, filled_quantity, processed_fill_qty FROM qd_grid_resting_orders WHERE id = %s', (row.id,))
        saved = cur.fetchone() or {}
        cur.close()
    return (saved.get('status') == entry['status']
            and float(saved.get('filled_quantity') or 0) + 1e-12 >= float(entry.get('filled') or 0)
            and float(saved.get('processed_fill_qty') or 0) + 1e-12 >= float(saved.get('filled_quantity') or 0))


def run_grid_order_audit(strategy_id: int, *, user_id: int, command_valid) -> dict:
    strategy = StrategyService().get_strategy(strategy_id, user_id=user_id)
    if not strategy or strategy.get('execution_mode') != 'live':
        return {'status': 'skipped'}
    repo = GridRestingOrderRepository()
    rows = repo.list_for_audit(strategy_id)
    poller = get_grid_fill_poller()
    tc = strategy.get('trading_config') or {}
    market = str(tc.get('market_type') or strategy.get('market_type') or 'swap').lower()
    if not rows:
        return {'completed': True, 'active': 0, 'terminal': 0, 'unknown': 0}
    config = resolve_exchange_config(coalesce_exchange_config_from_payload(strategy), user_id=user_id)
    # Existing bindings prevent a later credential edit from reassigning an old order.
    eligible = []
    bindings = repo.execution_bindings([row.id for row in rows])
    for row in rows:
        binding = bindings.get(row.id) or {}
        if binding and not binding_matches(binding, row, strategy, config, market):
            repo.save_audit(row, stamp_audit({'status': 'unverified'}, row, audit_identity(strategy)))
        else:
            eligible.append(row)

    def allow_request(bound):
        key = f'cred:{bound.get("credential_id") or bound.get("credentials_id")}'
        return command_valid() and poller._allow_credential_request(key)

    def reconcile(client, row, bound, mt, entry):
        # Running grids retain their normal actor/poller owner. Recovery never starts a runner.
        if strategy.get('status') != 'stopped':
            return False
        binding = repo.execution_binding(row.id)
        if not binding_matches(binding, row, strategy, bound, mt):
            return False
        def deny_submission(*args, **kwargs):
            raise RuntimeError('Reconciliation must not submit exchange orders')
        engine = GridEngine(strategy_id, row.symbol, {**tc, 'market_type': mt}, bound,
            user_id=user_id, strategy_run_id=int((row.extra or {}).get('strategy_run_id') or 0),
            create_client_fn=deny_submission, enqueue_market=deny_submission)
        runner = SimpleNamespace(engine=engine, exchange_config=bound, user_id=user_id, strategy_id=strategy_id)
        result = poller._poll_order(runner, client, row, mt, accounting_only=True,
            before_project=lambda: _stopped_context_unchanged(strategy, command_valid))
        return result != 'unknown' and _reconciled(row, entry)

    audit = audit_grid_orders(strategy, eligible, user_id=user_id,
                              reconcile_order=reconcile, allow_request=allow_request)
    for row in eligible:
        entry = audit['orders'].get(row.id)
        if entry:
            repo.save_audit(row, entry)
    # Per-order evidence is overwritten in place, not copied into every command history row.
    return {key: value for key, value in audit.items() if key != 'orders'}
