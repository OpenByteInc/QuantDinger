from unittest.mock import MagicMock

import pytest

from app.services.grid import order_audit, audit_state
from app.services.grid.resting_orders_repo import GridRestingOrder


@pytest.fixture
def audit_setup(monkeypatch):
    monkeypatch.setattr('app.services.grid.resting_orders_repo._ensure_table', lambda: None)
    strategy = {"id": 3, "market_type": "spot", "exchange_config": {"credential_id": 9}, "trading_config": {}}
    config = {"credential_id": 9, "exchange_id": "bybit", "testnet": True}
    resolve = MagicMock(return_value=config)
    monkeypatch.setattr(order_audit, "resolve_exchange_config", resolve)
    monkeypatch.setattr(order_audit, "bind_instrument_product_contract", lambda cfg, *a, **kw: cfg)
    client = MagicMock()
    monkeypatch.setattr(order_audit, "create_client", lambda cfg, **kw: client)
    # An API worker has no in-memory runner and must not start one for a read.
    monkeypatch.setattr("app.services.grid.runner.get_runner", MagicMock(side_effect=AssertionError("runner lookup")))
    return strategy, resolve, client


def test_api_worker_audit_matches_only_owned_order_ids_and_symbols(monkeypatch, audit_setup):
    strategy, resolve, _ = audit_setup
    rows = [GridRestingOrder(id=n, strategy_id=3, symbol="ETH/USDT", exchange_order_id=str(n)) for n in (1, 2, 3)]
    remote = [
        {"exchange_order_id": "1", "symbol": "ETH/USDT", "status": "New", "price": 2600.01, "amount": .004, "filled": 0},
        {"exchange_order_id": "2", "symbol": "BTC/USDT", "status": "New"},
        {"exchange_order_id": "99", "symbol": "ETH/USDT", "status": "New"},
    ]
    monkeypatch.setattr(order_audit, "fetch_exchange_open_orders", lambda *a, **kw: remote)
    result = order_audit.audit_grid_orders(strategy, rows, user_id=7)
    resolve.assert_called_once_with({"credential_id": 9}, user_id=7)
    assert result["completed"]
    assert result["active"] == 1
    assert result["unknown"] == 2
    assert result["orders"][1]["price"] == 2600.01
    assert result["orders"][2]["status"] == "not_open"
    assert result["orders"][3]["status"] == "not_open"
    assert result["checked_at"].endswith("+00:00")
    assert all(row.status == "pending" for row in rows)


def test_failed_exchange_read_is_not_zero_confirmed_success(monkeypatch, audit_setup):
    strategy, _, _ = audit_setup
    monkeypatch.setattr(order_audit, "fetch_exchange_open_orders", MagicMock(side_effect=TimeoutError()))
    result = order_audit.audit_grid_orders(strategy, [GridRestingOrder(id=1, symbol="ETH/USDT", exchange_order_id="1")], user_id=7)
    assert result["active"] == result["terminal"] == 0
    assert result["unknown"] == 1
    assert result["orders"][1]["status"] == "unverified"
    assert result["error"] == "grid_exchange_orders_unverified"


@pytest.mark.parametrize("status", ["NEW", "live", "open", "PartiallyFilled", "partially_filled"])
def test_native_open_statuses_are_confirmed(monkeypatch, audit_setup, status):
    strategy, _, _ = audit_setup
    monkeypatch.setattr(order_audit, "fetch_exchange_open_orders", lambda *a, **kw: [{"exchange_order_id": "1", "symbol": "ETH/USDT", "status": status}])
    result = order_audit.audit_grid_orders(strategy, [GridRestingOrder(id=1, symbol="ETH/USDT", exchange_order_id="1")], user_id=7)
    assert result["active"] == 1
    assert not result["error"]


@pytest.mark.parametrize("sync", [True, False])
def test_grid_route_does_not_count_local_ids_as_exchange_confirmation(monkeypatch, sync):
    import inspect
    from flask import Flask, g
    from app.routes import strategy_grid_routes as route

    service = MagicMock()
    service.get_strategy.return_value = {"id": 3, "trading_config": {}, "exchange_config": {"credential_id": 9}}
    monkeypatch.setattr(route, "get_strategy_service", lambda: service)
    monkeypatch.setattr("app.services.strategy_runtime.bot_type.resolve_bot_type", lambda *a, **kw: "grid")
    repo = MagicMock()
    repo.list_for_strategy.return_value = [GridRestingOrder(id=1, strategy_id=3, symbol="ETH/USDT", exchange_order_id="123", status="open")]
    monkeypatch.setattr("app.services.grid.resting_orders_repo.GridRestingOrderRepository", lambda: repo)
    audit = MagicMock(return_value={"active": 1, "completed": True, "checked_at": "2026-09-19T05:00:00+00:00", "orders": {1: {"status": "open", "price": 2600.01}}})
    monkeypatch.setattr(audit_state, "read_grid_order_audit", audit)
    app = Flask(__name__)
    with app.test_request_context(f"/?id=3&sync={int(sync)}"):
        g.user_id = 7
        result = inspect.unwrap(route.get_grid_resting_orders)().get_json()["data"]
    service.get_strategy.assert_called_once_with(3, user_id=7)
    assert result["summary"]["verified_exchange_orders"] == 1
    assert result["summary"]["unverified_orders"] == 0
    assert result["orders"][0]["exchange_status"] == "open"
    audit.assert_called_once_with(service.get_strategy.return_value, repo.list_for_strategy.return_value, user_id=7, sync=sync)


def test_grid_route_returns_virtual_resting_orders_without_exchange_audit(monkeypatch):
    import inspect
    from flask import Flask, g
    from app.routes import strategy_grid_routes as route

    service = MagicMock()
    service.get_strategy.return_value = {
        "id": 3,
        "execution_mode": "signal",
        "trading_config": {},
        "exchange_config": {},
    }
    monkeypatch.setattr(route, "get_strategy_service", lambda: service)
    monkeypatch.setattr("app.services.strategy_runtime.bot_type.resolve_bot_type", lambda *a, **kw: "grid")
    monkeypatch.setattr(
        "app.services.virtual_trading.list_virtual_limit_orders",
        lambda *a, **kw: [{
            "id": 8,
            "strategy_id": 3,
            "symbol": "BTC/USDT",
            "side": "long",
            "action": "open_long",
            "reason": "long_entry",
            "status": "open",
            "limit_price": 95,
            "requested_qty": 2,
            "fill_qty": 0,
            "fill_price": 0,
            "client_order_id": "grid-4-long-entry-1",
        }],
    )
    audit = MagicMock(side_effect=AssertionError("signal mode must not audit an exchange"))
    monkeypatch.setattr(audit_state, "read_grid_order_audit", audit)
    app = Flask(__name__)
    with app.test_request_context("/?id=3&sync=1"):
        g.user_id = 7
        result = inspect.unwrap(route.get_grid_resting_orders)().get_json()["data"]

    assert result["summary"]["virtual_mode"] is True
    assert result["summary"]["unverified_orders"] == 0
    assert result["orders"][0]["cell_index"] == 4
    assert result["orders"][0]["exchange_status"] == "open"
    assert result["orders"][0]["exchange_price"] == pytest.approx(95)
    assert result["orders"][0]["exchange_order_id"] == "virtual:8"
    audit.assert_not_called()


def _binance_order(status, executed="0", avg="0"):
    # Shape of a Binance USDT-M GET /fapi/v1/order response.
    return {"orderId": 8389766332, "symbol": "ETHUSDT", "status": status, "executedQty": executed,
            "avgPrice": avg, "origQty": "0.004", "price": "2600.01"}


@pytest.fixture
def ledger(monkeypatch):
    repo = MagicMock()
    repo.mark_cancelled_if_unfilled.return_value = True
    monkeypatch.setattr(order_audit, "GridRestingOrderRepository", lambda: repo, raising=False)
    return repo


@pytest.fixture
def binance(monkeypatch, audit_setup):
    from app.services.live_trading.binance import BinanceFuturesClient

    strategy, _, _ = audit_setup
    strategy["market_type"] = "swap"
    client = MagicMock(spec=BinanceFuturesClient)
    monkeypatch.setattr(order_audit, "create_client", lambda cfg, **kw: client)
    return strategy, client


def test_order_missing_from_open_orders_applies_exchange_cancelled_status(monkeypatch, binance, ledger):
    """#404: a grid order cancelled on the exchange must not stay 'not in open orders' forever."""
    strategy, client = binance
    monkeypatch.setattr(order_audit, "fetch_exchange_open_orders", lambda *a, **kw: [])
    client.get_order.return_value = _binance_order("CANCELED")
    row = GridRestingOrder(id=5, strategy_id=3, symbol="ETH/USDT", exchange_order_id="8389766332",
                           client_order_id="grid-3-short_exit-1", status="open")

    result = order_audit.audit_grid_orders(strategy, [row], user_id=7)

    client.get_order.assert_called_once_with(symbol="ETH/USDT", order_id="8389766332",
                                             client_order_id="grid-3-short_exit-1")
    ledger.mark_cancelled_if_unfilled.assert_called_once_with(5, "8389766332")
    assert result["orders"][5]["status"] == "cancelled"
    assert result["orders"][5]["local_status"] == "cancelled"
    assert row.status == "cancelled"
    assert result["terminal"] == 1
    assert result["unknown"] == 0
    assert result["error"] == ""


@pytest.mark.parametrize("raw_status", ["EXPIRED", "CANCELED"])
def test_terminal_order_with_fills_is_left_for_the_fill_poller(monkeypatch, binance, ledger, raw_status):
    strategy, client = binance
    monkeypatch.setattr(order_audit, "fetch_exchange_open_orders", lambda *a, **kw: [])
    client.get_order.return_value = _binance_order(raw_status, executed="0.002", avg="2600.01")
    row = GridRestingOrder(id=5, strategy_id=3, symbol="ETH/USDT", exchange_order_id="8389766332", status="open")

    result = order_audit.audit_grid_orders(strategy, [row], user_id=7)

    ledger.mark_cancelled_if_unfilled.assert_not_called()
    assert result["orders"][5]["status"] == "cancelled"
    assert result["orders"][5]["filled"] == pytest.approx(0.002)
    assert "local_status" not in result["orders"][5]
    assert row.status == "open"
    assert result["terminal"] == 1


def test_filled_order_missing_from_open_orders_is_reported_not_rewritten(monkeypatch, binance, ledger):
    strategy, client = binance
    monkeypatch.setattr(order_audit, "fetch_exchange_open_orders", lambda *a, **kw: [])
    client.get_order.return_value = _binance_order("FILLED", executed="0.004", avg="2600.01")
    row = GridRestingOrder(id=5, strategy_id=3, symbol="ETH/USDT", exchange_order_id="8389766332", status="open")

    result = order_audit.audit_grid_orders(strategy, [row], user_id=7)

    ledger.mark_cancelled_if_unfilled.assert_not_called()
    assert result["orders"][5]["status"] == "filled"
    assert row.status == "open"
    assert result["error"] == 'grid_exchange_reconciliation_pending'
    assert result['orders'][5]['reconciliation_pending']


def test_unknown_order_lookup_keeps_not_open_without_ledger_write(monkeypatch, binance, ledger):
    strategy, client = binance
    monkeypatch.setattr(order_audit, "fetch_exchange_open_orders", lambda *a, **kw: [])
    client.get_order.side_effect = RuntimeError("Order does not exist")
    row = GridRestingOrder(id=5, strategy_id=3, symbol="ETH/USDT", exchange_order_id="8389766332", status="open")

    result = order_audit.audit_grid_orders(strategy, [row], user_id=7)

    ledger.mark_cancelled_if_unfilled.assert_not_called()
    assert result["orders"][5]["status"] == "not_open"
    assert row.status == "open"
    assert result["error"] == "grid_exchange_orders_unverified"


def test_ledger_race_does_not_report_a_local_cancel(monkeypatch, binance, ledger):
    strategy, client = binance
    monkeypatch.setattr(order_audit, "fetch_exchange_open_orders", lambda *a, **kw: [])
    client.get_order.return_value = _binance_order("CANCELED")
    ledger.mark_cancelled_if_unfilled.return_value = False
    row = GridRestingOrder(id=5, strategy_id=3, symbol="ETH/USDT", exchange_order_id="8389766332", status="open")

    result = order_audit.audit_grid_orders(strategy, [row], user_id=7)

    assert result["orders"][5]["status"] == "cancelled"
    assert "local_status" not in result["orders"][5]
    assert row.status == "open"


def test_grid_route_returns_reconciled_local_status(monkeypatch):
    import inspect
    from flask import Flask, g
    from app.routes import strategy_grid_routes as route

    service = MagicMock()
    service.get_strategy.return_value = {"id": 3, "trading_config": {}, "exchange_config": {"credential_id": 9}}
    monkeypatch.setattr(route, "get_strategy_service", lambda: service)
    monkeypatch.setattr("app.services.strategy_runtime.bot_type.resolve_bot_type", lambda *a, **kw: "grid")
    row = GridRestingOrder(id=1, strategy_id=3, symbol="ETH/USDT", exchange_order_id="123", status="open")
    repo = MagicMock()
    repo.list_for_strategy.return_value = [row]
    monkeypatch.setattr("app.services.grid.resting_orders_repo.GridRestingOrderRepository", lambda: repo)

    def audit(_strategy, rows, **_kw):
        rows[0].status = "cancelled"
        return {"active": 0, "terminal": 1, "unknown": 0, "completed": True, "checked_at": "2026-11-24T00:00:00+00:00",
                "orders": {1: {"status": "cancelled", "local_status": "cancelled"}}}

    monkeypatch.setattr(audit_state, "read_grid_order_audit", audit)
    app = Flask(__name__)
    with app.test_request_context("/?id=3&sync=1"):
        g.user_id = 7
        result = inspect.unwrap(route.get_grid_resting_orders)().get_json()["data"]
    assert result["orders"][0]["status"] == "cancelled"
    assert result["orders"][0]["exchange_status"] == "cancelled"
    assert result["summary"]["exchange_not_open_orders"] == 0
    assert result["summary"]["exchange_terminal_orders"] == 1
    assert result["summary"]["sync_ok"] is True
    assert result['summary']['verified_exchange_orders'] == 1
    assert result['summary']['unverified_orders'] == 0


def test_repository_cancel_only_touches_an_unfilled_open_row(monkeypatch):
    from app.services.grid import resting_orders_repo as mod

    executed = []

    class Cursor:
        rowcount = 1

        def execute(self, sql, args):
            executed.append((" ".join(sql.split()), args))

        def close(self):
            pass

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def cursor(self):
            return Cursor()

        def commit(self):
            pass

    monkeypatch.setattr(mod, "get_db_connection", lambda: Conn())
    repo = mod.GridRestingOrderRepository.__new__(mod.GridRestingOrderRepository)
    assert repo.mark_cancelled_if_unfilled(5, "8389766332") is True
    sql, args = executed[-1]
    assert "SET status = 'cancelled'" in sql
    for guard in ("status = 'open'", "exchange_order_id = %s", "filled_quantity = 0", "processed_fill_qty = 0"):
        assert guard in sql
    assert args == (5, "8389766332")
    assert repo.mark_cancelled_if_unfilled(5, "") is False


@pytest.mark.parametrize('exchange,market', [
    (exchange, market) for exchange in ('binance', 'okx', 'bitget', 'bybit', 'gate', 'htx')
    for market in ('spot', 'swap')
])
@pytest.mark.parametrize('partial', [False, True])
def test_terminal_lookup_across_six_exchanges_without_open_snapshot(monkeypatch, ledger, exchange, market, partial):
    from app.services.live_trading.binance import BinanceFuturesClient
    from app.services.live_trading.binance_spot import BinanceSpotClient
    from app.services.live_trading.bitget import BitgetMixClient
    from app.services.live_trading.bitget_spot import BitgetSpotClient
    from app.services.live_trading.bybit import BybitClient
    from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient
    from app.services.live_trading.htx import HtxClient
    from app.services.live_trading.okx import OkxClient

    classes = {'binance': (BinanceSpotClient, BinanceFuturesClient), 'okx': (OkxClient, OkxClient),
               'bitget': (BitgetSpotClient, BitgetMixClient), 'bybit': (BybitClient, BybitClient),
               'gate': (GateSpotClient, GateUsdtFuturesClient), 'htx': (HtxClient, HtxClient)}
    qty, contracts = ('.2', '2') if partial else ('0', '0')
    response = {
        'binance': {'status': 'CANCELED', 'executedQty': qty, 'avgPrice': '100'},
        'okx': {'state': 'canceled', 'accFillSz': qty if market == 'spot' else contracts, 'avgPx': '100'},
        'bitget': {'status': 'cancelled', 'filled': qty, 'baseVolume': qty, 'priceAvg': '100'},
        'bybit': {'orderStatus': 'PartiallyFilledCanceled' if partial and market == 'spot' else 'Cancelled',
                  'cumExecQty': qty, 'avgPrice': '100'},
        'gate': ({'status': 'cancelled', 'filled_amount': qty, 'avg_deal_price': '100'} if market == 'spot'
                 else {'status': 'finished', 'finish_as': 'cancelled', 'size': '10',
                       'left': '8' if partial else '10', 'fill_price': '100'}),
        'htx': ({'state': 'partial-canceled' if partial else 'canceled', 'field-amount': qty,
                 'field-cash-amount': '20' if partial else '0'} if market == 'spot'
                else {'status': 5 if partial else 7, 'trade_volume': contracts, 'trade_avg_price': '100'}),
    }[exchange]
    client = MagicMock(spec=classes[exchange][market == 'swap'])
    client.get_order.return_value = response
    if exchange == 'okx':
        client.get_instrument.return_value = {'ctVal': '.1'}
    elif exchange == 'gate' and market == 'swap':
        client.get_contract.return_value = {'quanto_multiplier': '.1'}
    elif exchange == 'htx':
        client.get_contract_info.return_value = {'contract_size': '.1'}
    config = {'exchange_id': exchange, 'credential_id': 9}
    strategy = {'id': 3, 'market_type': market, 'exchange_config': config}
    monkeypatch.setattr(order_audit, 'resolve_exchange_config', lambda cfg, **kw: cfg)
    monkeypatch.setattr(order_audit, 'bind_instrument_product_contract', lambda cfg, *a, **kw: cfg)
    monkeypatch.setattr(order_audit, 'create_client', lambda *a, **kw: client)
    monkeypatch.setattr(order_audit, 'fetch_exchange_open_orders', MagicMock(side_effect=RuntimeError('no snapshot')))
    row = GridRestingOrder(id=5, strategy_id=3, symbol='BTC/USDT', status='open',
                           exchange_order_id='12345678901234567890')
    result = order_audit.audit_grid_orders(strategy, [row], user_id=7)
    entry = result['orders'][5]
    assert entry['status'] == 'cancelled' and result['terminal'] == 1
    assert entry['filled'] == pytest.approx(float(qty))
    assert entry['reconciliation_pending'] == partial
    assert row.status == ('open' if partial else 'cancelled')
    assert ledger.mark_cancelled_if_unfilled.call_count == int(not partial)
    assert '12345678901234567890' in client.get_order.call_args.kwargs.values()


@pytest.mark.parametrize('mode', ['classic', 'uta'])
@pytest.mark.parametrize('market', ['spot', 'swap'])
def test_audit_uses_bitget_account_router_without_fill_history(monkeypatch, ledger, mode, market):
    from app.services.live_trading.bitget_compat import BitgetCompatibleMixClient, BitgetCompatibleSpotClient
    cls = BitgetCompatibleSpotClient if market == 'spot' else BitgetCompatibleMixClient
    client = cls(api_key='test', secret_key='test', passphrase='test', account_mode=mode)
    target = client._classic_client if mode == 'classic' else client._uta_client
    request = MagicMock(return_value={'code': '00000', 'data': {
        'orderId': '123', 'orderStatus': 'cancelled', 'status': 'cancelled', 'state': 'cancelled',
        'cumExecQty': '0', 'baseVolume': '0', 'avgPrice': '0'}})
    monkeypatch.setattr(target, '_signed_request', request)
    monkeypatch.setattr(order_audit, 'fetch_exchange_open_orders', MagicMock(side_effect=RuntimeError('snapshot failed')))
    monkeypatch.setattr(order_audit, 'resolve_exchange_config', lambda cfg, **kw: cfg)
    monkeypatch.setattr(order_audit, 'bind_instrument_product_contract', lambda cfg, *a, **kw: cfg)
    monkeypatch.setattr(order_audit, 'create_client', lambda *a, **kw: client)
    row = GridRestingOrder(id=5, strategy_id=3, symbol='BTC/USDT', exchange_order_id='123', status='open')
    result = order_audit.audit_grid_orders({'id': 3, 'market_type': market,
        'exchange_config': {'exchange_id': 'bitget', 'credential_id': 9}}, [row], user_id=7)
    assert result['orders'][5]['status'] == 'cancelled'
    assert request.call_count == 1
    assert request.call_args.args[1].startswith('/api/v2/' if mode == 'classic' else '/api/v3/')
    assert target.timeout_sec == 3


def test_snapshot_partial_fill_is_visible_as_pending_accounting(monkeypatch, audit_setup):
    strategy, _, _ = audit_setup
    monkeypatch.setattr(order_audit, 'fetch_exchange_open_orders', lambda *a, **kw: [
        {'exchange_order_id': '1', 'symbol': 'ETH/USDT', 'status': 'partially_filled', 'filled': .5}])
    result = order_audit.audit_grid_orders(strategy, [GridRestingOrder(id=1, symbol='ETH/USDT',
        exchange_order_id='1', status='open')], user_id=7)
    assert result['orders'][1]['reconciliation_pending']


def test_expired_budget_does_not_fall_back_to_more_order_requests(monkeypatch, audit_setup):
    strategy, _, client = audit_setup
    def exhausted(*a, **kw):
        assert not kw['request_allowed']()
        raise RuntimeError('budget exhausted')
    monkeypatch.setattr(order_audit, 'fetch_exchange_open_orders', exhausted)
    result = order_audit.audit_grid_orders(strategy, [GridRestingOrder(id=1, symbol='ETH/USDT',
        exchange_order_id='1')], user_id=7, allow_request=lambda _: False)
    assert not result['completed']
    client.get_order.assert_not_called()


def test_audit_keeps_oldest_symbol_first_instead_of_alphabetical_starvation(monkeypatch, audit_setup, ledger):
    strategy, _, _ = audit_setup
    monkeypatch.setattr(order_audit, 'fetch_exchange_open_orders', lambda *a, **kw: [])
    query = MagicMock(return_value=(0, 0, 'unknown'))
    monkeypatch.setattr(order_audit, 'query_grid_order_fill', query)
    rows = [GridRestingOrder(id=1, symbol='ZEC/USDT', exchange_order_id='1'),
            GridRestingOrder(id=2, symbol='BTC/USDT', exchange_order_id='2')]
    result = order_audit.audit_grid_orders(strategy, rows, user_id=7, max_lookups=1)
    assert list(result['orders']) == [1]
    assert query.call_args.kwargs['symbol'] == 'ZEC/USDT'


def test_empty_normalized_order_is_not_confirmation_of_an_open_order(monkeypatch, binance, ledger):
    strategy, client = binance
    client.get_order.return_value = {'executedQty': 0, 'avgPrice': 0, 'status': ''}
    monkeypatch.setattr(order_audit, 'fetch_exchange_open_orders', lambda *a, **kw: [])
    row = GridRestingOrder(id=5, strategy_id=3, symbol='ETH/USDT', exchange_order_id='123', status='open')
    result = order_audit.audit_grid_orders(strategy, [row], user_id=7)
    assert result['active'] == result['terminal'] == 0
    assert result['orders'][5]['status'] == 'not_open'
    ledger.mark_cancelled_if_unfilled.assert_not_called()
