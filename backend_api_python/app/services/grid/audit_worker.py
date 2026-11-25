"""Bounded order verification on the existing trading command worker."""

from app.services.grid.audit_state import audit_identity, stamp_audit
from app.services.grid.order_audit import audit_grid_orders
from app.services.grid.poller import get_grid_fill_poller
from app.services.grid.resting_orders_repo import GridRestingOrderRepository
from app.services.strategy import StrategyService
from app.services.exchange_execution import coalesce_exchange_config_from_payload, resolve_exchange_config


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

    audit = audit_grid_orders(strategy, eligible, user_id=user_id, allow_request=allow_request)
    for row in eligible:
        entry = audit['orders'].get(row.id)
        if entry:
            repo.save_audit(row, entry)
    # Per-order evidence is overwritten in place, not copied into every command history row.
    return {key: value for key, value in audit.items() if key != 'orders'}
