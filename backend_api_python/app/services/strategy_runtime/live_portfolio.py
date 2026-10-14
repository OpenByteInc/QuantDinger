"""Portfolio data scope and cash accounting for the live executor."""
from collections import defaultdict

from app.services.strategy_v2.instruments import parse_instrument
from app.utils.db import get_db_connection


def refresh_members(service, candidates, manifest, user_id, strategy_id, now, exchange_id=''):
    if manifest.universe.kind == 'static':
        return
    fresh, _ = service.resolve_candidates(user_id=user_id, manifest=manifest, start_date=now, end_date=now)
    # Preserve symbols owned by the strategy, including after a process restart.
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("""
            SELECT symbol FROM qd_strategy_positions WHERE strategy_id = %s AND size > 0
            UNION SELECT symbol FROM qd_strategy_virtual_positions WHERE strategy_id = %s AND size > 0
            UNION SELECT symbol FROM pending_orders WHERE strategy_id = %s
              AND status IN ('pending', 'processing', 'sent', 'syncing')
        """, (strategy_id, strategy_id, strategy_id))
        active = {str(row['symbol']) for row in cur.fetchall() or []}
        cur.close()
    merged = {item['key']: item for item in fresh}
    for item in candidates:
        if str(item.get('symbol')) in active:
            merged[item['key']] = item
    if len(manifest.markets) == 1:
        for symbol in active:
            spec = parse_instrument(f'{manifest.markets[0]}:{symbol}')
            merged.setdefault(spec.key, dict(market=spec.market, symbol=spec.symbol,
                market_type=spec.market_type, exchange_id=spec.exchange_id, key=spec.key))
    candidates[:] = sorted(merged.values(), key=lambda item: item['key'])
    if exchange_id:
        for item in candidates:
            if item.get('market') == 'Crypto':
                spec = parse_instrument(f"Crypto:{item['symbol']}@{exchange_id}:{item.get('market_type') or 'spot'}")
                item.update(exchange_id=exchange_id, key=spec.key)


def positions_by_symbol(executor, strategy_id, candidates, strategy):
    from app.services.strategy_live_guard import resolve_strategy_direction_mode
    direction_mode = resolve_strategy_direction_mode(strategy or {})
    strategy_config = strategy or {}
    trading_config = strategy_config.get('trading_config') or {}
    if not isinstance(trading_config, dict):
        trading_config = {}
    default_market_type = str(
        trading_config.get('market_type')
        or strategy_config.get('market_type')
        or 'spot'
    ).strip().lower()
    grouped = defaultdict(list)
    for row in executor._get_current_positions(strategy_id, None):
        grouped[str(row.get('symbol') or '').split(':')[0]].append(row)
    output = {}
    for member in candidates:
        rows = grouped.get(str(member.get('symbol') or '').split(':')[0], [])
        market_type = str(member.get('market_type') or default_market_type).strip().lower()
        if market_type in {'future', 'futures', 'perp', 'perpetual'}:
            market_type = 'swap'
        explicit_legs = market_type == 'swap' and direction_mode in {
            'long_only', 'short_only', 'both', 'neutral',
        }
        owned_sides = (
            {'long'} if direction_mode == 'long_only'
            else {'short'} if direction_mode == 'short_only'
            else {'long', 'short'}
        )
        selected_rows = (
            [
                row for row in rows
                if ('short' if row.get('side') == 'short' else 'long') in owned_sides
            ]
            if explicit_legs else rows[:1]
        )
        for row in selected_rows:
            side = 'short' if row.get('side') == 'short' else 'long'
            key = member['key'] + (f'::{side}' if explicit_legs else '')
            output[key] = dict(amount=row.get('size') or 0, side=side,
                position_side=side if explicit_legs else '', avg_cost=row.get('entry_price') or 0,
                last_price=row.get('current_price') or 0)
    return output


def available_strategy_cash(equity, positions, candidates, leverage, prices=None):
    markets = {item['key']: item.get('market_type') or 'spot' for item in candidates}
    occupied = 0.0
    for key, row in positions.items():
        symbol = key.rsplit('::', 1)[0]
        quantity = abs(float(row.get('amount') or 0))
        price = float(prices.get(symbol, 0) if prices is not None else row.get('last_price') or row.get('avg_cost') or 0)
        if quantity and price <= 0:
            return 0.0
        occupied += quantity * price / (max(1, leverage) if markets.get(symbol) == 'swap' else 1)
    return max(0.0, equity - occupied)


def pricing_members(candidates, positions):
    if len(candidates) < 30:
        return candidates
    held = {key.rsplit('::', 1)[0] for key in positions}
    return [item for item in candidates if item['key'] in held] or candidates[:1]
