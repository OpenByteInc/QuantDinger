from __future__ import annotations

import pytest

from app.services import pending_order_position_sync as sync_module
from app.services import pending_order_worker as worker_module
from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.gate import GateUsdtFuturesClient
from app.services.live_trading.htx import HtxClient
from app.services.live_trading.okx import OkxClient


class _Cursor:
    def __init__(self):
        self._rows = []

    def execute(self, sql, _params=None):
        if "FROM qd_strategy_positions" in sql:
            self._rows = [
                {
                    "id": 2,
                    "strategy_id": 90,
                    "symbol": "BTC/USDT",
                    "side": "long",
                    "size": 0.005,
                    "entry_price": 84000,
                }
            ]
        elif "FROM qd_strategies_trading" in sql:
            self._rows = []
        else:
            raise AssertionError(f"Unexpected SQL: {sql}")

    def fetchall(self):
        return list(self._rows)

    def close(self):
        return None


class _Database:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    @staticmethod
    def cursor():
        return _Cursor()


class _Binance(BinanceFuturesClient):
    def __init__(self):
        pass

    def get_positions(self):
        return [
            {
                "symbol": "BTCUSDT",
                "positionAmt": "0.005",
                "entryPrice": "84589.1",
            }
        ]


class _Okx(OkxClient):
    def __init__(self):
        pass

    def get_positions(self):
        return {
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "posSide": "long",
                    "pos": "5",
                    "avgPx": "84589.1",
                }
            ]
        }

    def get_instrument(self, *, inst_type, inst_id):
        assert inst_type == "SWAP"
        assert inst_id == "BTC-USDT-SWAP"
        return {"ctVal": "0.001"}


class _Bitget(BitgetMixClient):
    def __init__(self):
        pass

    def get_positions(self, *, product_type, symbol=""):
        assert product_type == "USDT-FUTURES"
        return {
            "data": [
                {
                    "symbol": "BTCUSDT",
                    "holdSide": "long",
                    "total": "0.005",
                    "openPriceAvg": "84589.1",
                }
            ]
        }


class _Bybit(BybitClient):
    def __init__(self):
        pass

    def get_positions(self, *, settle_coin):
        assert settle_coin == "USDT"
        return {
            "result": {
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "side": "Buy",
                        "size": "0.005",
                        "avgPrice": "84589.1",
                    }
                ]
            }
        }


class _Gate(GateUsdtFuturesClient):
    def __init__(self):
        pass

    def get_positions(self):
        return [
            {
                "contract": "BTC_USDT",
                "mode": "dual_long",
                "size": "5",
                "entry_price": "84589.1",
            }
        ]

    def get_contract(self, *, contract):
        assert contract == "BTC_USDT"
        return {"quanto_multiplier": "0.001"}


class _Htx(HtxClient):
    def __init__(self):
        pass

    def get_positions(self):
        return {
            "data": [
                {
                    "contract_code": "BTC-USDT",
                    "direction": "buy",
                    "volume": "5",
                    "cost_open": "84589.1",
                }
            ]
        }

    def get_contract_info(self, *, symbol):
        assert symbol == "BTC/USDT"
        return {"contract_size": "0.001"}


def _run_sync(monkeypatch, client, exchange_id, market_type="swap", symbol="BTC/USDT"):
    monkeypatch.setattr(sync_module, "get_db_connection", lambda: _Database())
    monkeypatch.setattr(sync_module, "should_skip_position_sync", lambda _strategy_id: False)
    monkeypatch.setattr(
        sync_module,
        "load_strategy_configs",
        lambda _strategy_id: {
            "status": "running",
            "execution_mode": "live",
            "user_id": 7,
            "market_type": market_type,
            "symbol": symbol,
            "exchange_config": {
                "exchange_id": exchange_id,
                "credential_id": 11,
            },
        },
    )
    monkeypatch.setattr(sync_module, "resolve_exchange_config", lambda cfg, user_id: dict(cfg))
    monkeypatch.setattr(sync_module, "safe_exchange_config_for_log", lambda cfg: dict(cfg))
    monkeypatch.setattr(sync_module, "strategy_allowed_symbols", lambda _cfg: {symbol})
    monkeypatch.setattr(sync_module, "position_sync_cache_key", lambda *_args: "position-cache")
    monkeypatch.setattr(sync_module, "get_position_sync_snapshot", lambda _key: None)
    monkeypatch.setattr(sync_module, "is_exchange_sync_backoff", lambda _key: False)
    monkeypatch.setattr(sync_module, "create_client", lambda *_args, **_kwargs: client)
    monkeypatch.setattr(sync_module, "credential_id_from_exchange_config", lambda _cfg: 11)

    snapshots = []
    account_legs = []
    purges = []
    monkeypatch.setattr(
        sync_module,
        "set_position_sync_snapshot",
        lambda key, sizes, prices, inst_ids: snapshots.append(
            (key, sizes, prices, inst_ids)
        ),
    )
    monkeypatch.setattr(
        sync_module,
        "sync_account_positions",
        lambda **kwargs: account_legs.extend(kwargs["legs"]),
    )
    monkeypatch.setattr(
        sync_module,
        "_purge_flat_strategy_positions_from_exchange",
        lambda **kwargs: purges.append(kwargs["exch_size"]) or 0,
    )

    worker = object.__new__(worker_module.PendingOrderWorker)
    worker._sync_positions_best_effort(target_strategy_id=90)
    return snapshots, account_legs, purges


@pytest.mark.parametrize("broker", ["tiger", "webull", "forthright"])
def test_stock_broker_sync_never_falls_through_to_crypto_wallet(monkeypatch, broker):
    from types import SimpleNamespace
    def positions(**kwargs):
        assert kwargs == {"raise_on_error": True}
        return [{"symbol": "AAPL", "quantity": 10, "avg_entry_price": 200}]
    snapshots, legs, purges = _run_sync(monkeypatch, SimpleNamespace(get_positions=positions), broker, "spot", "AAPL")
    assert snapshots[0][1]["AAPL"]["long"] == 10
    assert legs[0].size == 10
    assert purges[0]["AAPL"]["long"] == 10


@pytest.mark.parametrize("broker", ["tiger", "webull", "forthright"])
def test_failed_broker_sync_does_not_publish_an_empty_snapshot(monkeypatch, broker):
    from types import SimpleNamespace
    def positions(**kwargs):
        raise TimeoutError("offline")
    snapshots, legs, purges = _run_sync(monkeypatch, SimpleNamespace(get_positions=positions), broker, "spot", "AAPL")
    assert snapshots == [] and legs == [] and purges == []


@pytest.mark.parametrize(
    ("client", "exchange_id", "expected_inst_id"),
    [
        (_Binance(), "binance", ""),
        (_Okx(), "okx", "BTC-USDT-SWAP"),
        (_Bitget(), "bitget", ""),
        (_Bybit(), "bybit", ""),
        (_Gate(), "gate", "BTC_USDT"),
        (_Htx(), "htx", "BTC-USDT"),
    ],
)
def test_derivative_position_sync_preserves_each_exchange_position(
    monkeypatch,
    client,
    exchange_id,
    expected_inst_id,
):
    snapshots, account_legs, purges = _run_sync(monkeypatch, client, exchange_id)

    assert snapshots[0][0] == "position-cache"
    assert snapshots[0][1]["BTC/USDT"]["long"] == pytest.approx(0.005)
    assert snapshots[0][2]["BTC/USDT"]["long"] == pytest.approx(84589.1)
    if expected_inst_id:
        assert snapshots[0][3]["BTC/USDT"]["long"] == expected_inst_id
    assert len(account_legs) == 1
    assert account_legs[0].symbol == "BTC/USDT"
    assert account_legs[0].side == "long"
    assert account_legs[0].size == pytest.approx(0.005)
    assert purges[0]["BTC/USDT"]["long"] == pytest.approx(0.005)
