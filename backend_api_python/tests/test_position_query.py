"""Tests for close-quantity resolution (DB + exchange fallback)."""
import requests
import pytest
from unittest.mock import MagicMock

from app.services.live_trading.position_query import (
    PositionSnapshotUnavailable,
    query_exchange_position_size,
    resolve_reduce_only_quantity,
    symbols_equivalent,
)


def test_symbols_equivalent_compact_and_slash():
    assert symbols_equivalent("DOGEUSDT", "DOGE/USDT")
    assert symbols_equivalent("btc/usdt", "BTCUSDT")
    assert not symbols_equivalent("ETH/USDT", "DOGE/USDT")


@pytest.mark.parametrize("exchange", ["binance", "okx", "bybit", "bitget", "gate", "htx"])
@pytest.mark.parametrize("payload_kind", ["missing", "malformed", "invalid_quantity", "empty"])
def test_strict_snapshot_never_treats_invalid_payload_as_flat(exchange, payload_kind):
    from app.services.live_trading.binance import BinanceFuturesClient
    from app.services.live_trading.okx import OkxClient
    from app.services.live_trading.bybit import BybitClient
    from app.services.live_trading.bitget import BitgetMixClient
    from app.services.live_trading.gate import GateUsdtFuturesClient
    from app.services.live_trading.htx import HtxClient

    classes = dict(binance=BinanceFuturesClient, okx=OkxClient, bybit=BybitClient,
                   bitget=BitgetMixClient, gate=GateUsdtFuturesClient, htx=HtxClient)
    quantities = dict(binance="positionAmt", okx="pos", bybit="size", bitget="total", gate="size", htx="volume")
    client = MagicMock(spec=classes[exchange])
    rows = [{quantities[exchange]: "NaN"}] if payload_kind == "invalid_quantity" else []
    if payload_kind == "malformed":
        rows = "unexpected payload"
    response = rows if exchange in {"binance", "gate"} else (
        {"result": {"list": rows}} if exchange == "bybit" else {"data": rows})
    client.get_positions.return_value = None if payload_kind == "missing" else response
    kwargs = dict(client=client, symbol="BTC/USDT", pos_side="long", market_type="swap",
                  exchange_config={"exchange_id": exchange}, strict=True)
    if payload_kind == "empty":
        assert query_exchange_position_size(**kwargs) == 0
    else:
        with pytest.raises(PositionSnapshotUnavailable):
            query_exchange_position_size(**kwargs)


@pytest.mark.parametrize("client", [None, object()])
def test_strict_query_requires_a_position_client(client):
    with pytest.raises(RuntimeError):
        query_exchange_position_size(client=client, symbol="BTC/USDT", pos_side="long",
            market_type="swap", strict=True)


@pytest.mark.parametrize("rows", [[{}], [{"quantity": "NaN"}], ["malformed"]])
def test_broker_invalid_position_rows_are_not_empty_holdings(rows):
    from types import SimpleNamespace
    client = SimpleNamespace(get_positions=lambda **kwargs: rows)
    with pytest.raises(PositionSnapshotUnavailable):
        query_exchange_position_size(client=client, symbol="AAPL", pos_side="long",
            market_type="usstock", exchange_config={"exchange_id": "alpaca"}, strict=True)


def test_resolve_uses_exchange_when_db_missing_only_with_explicit_fallback(monkeypatch):
    monkeypatch.setattr(
        "app.services.live_trading.position_query.fetch_position_size_for_side",
        lambda *_a, **_k: 0.0,
    )
    monkeypatch.setattr(
        "app.services.live_trading.position_query.query_exchange_position_size",
        lambda **_k: 99.0,
    )
    amount, meta = resolve_reduce_only_quantity(
        strategy_id=1,
        symbol="DOGE/USDT",
        pos_side="short",
        requested_amount=0.0,
        client=MagicMock(),
        market_type="swap",
        exchange_config={},
        allow_exchange_fallback=True,
    )
    assert amount == 99.0
    assert meta.get("filled_from") == "exchange"
    assert meta.get("db_missing") is True


def test_resolve_rejects_strategy_close_when_db_position_is_missing(monkeypatch):
    monkeypatch.setattr(
        "app.services.live_trading.position_query.fetch_position_size_for_side",
        lambda *_a, **_k: 0.0,
    )
    monkeypatch.setattr(
        "app.services.live_trading.position_query.query_exchange_position_size",
        lambda **_k: 2.0,
    )

    amount, meta = resolve_reduce_only_quantity(
        strategy_id=1,
        symbol="BTC/USDT",
        pos_side="long",
        requested_amount=0.0004,
        client=MagicMock(),
        market_type="spot",
        exchange_config={},
    )

    assert amount == 0.0
    assert meta.get("db_missing") is True
    assert meta.get("blocked_by") == "strategy_position_missing"


def test_resolve_spot_close_never_exceeds_strategy_owned_quantity(monkeypatch):
    monkeypatch.setattr(
        "app.services.live_trading.position_query.fetch_position_size_for_side",
        lambda *_a, **_k: 0.0004,
    )
    monkeypatch.setattr(
        "app.services.live_trading.position_query.query_exchange_position_size",
        lambda **_k: 2.0,
    )

    amount, meta = resolve_reduce_only_quantity(
        strategy_id=1,
        symbol="BTC/USDT",
        pos_side="long",
        requested_amount=1.0,
        client=MagicMock(),
        market_type="spot",
        exchange_config={},
    )

    assert amount == pytest.approx(0.0004)
    assert meta.get("capped_by") == "db"


def test_resolve_caps_to_db_when_smaller(monkeypatch):
    monkeypatch.setattr(
        "app.services.live_trading.position_query.fetch_position_size_for_side",
        lambda *_a, **_k: 50.0,
    )
    monkeypatch.setattr(
        "app.services.live_trading.position_query.query_exchange_position_size",
        lambda **_k: 99.0,
    )
    amount, meta = resolve_reduce_only_quantity(
        strategy_id=1,
        symbol="DOGE/USDT",
        pos_side="short",
        requested_amount=80.0,
        client=MagicMock(),
        market_type="swap",
        exchange_config={},
    )
    assert amount == 50.0
    assert meta.get("capped_by") == "db"


def test_resolve_account_surplus_does_not_reduce_strategy_exit(monkeypatch):
    monkeypatch.setattr(
        "app.services.live_trading.position_query.fetch_position_size_for_side",
        lambda *_a, **_k: 0.015,
    )
    monkeypatch.setattr(
        "app.services.live_trading.position_query.query_exchange_position_size",
        lambda **_k: 0.02,
    )
    monkeypatch.setattr(
        "app.services.live_trading.position_query.fetch_allocated_position_size",
        lambda **_k: 0.015,
    )
    amount, meta = resolve_reduce_only_quantity(
        strategy_id=1,
        symbol="BTC/USDT",
        pos_side="long",
        requested_amount=0.015,
        client=MagicMock(),
        market_type="swap",
        exchange_config={},
        user_id=1,
        credential_id=2,
    )
    assert amount == pytest.approx(0.015)
    assert meta["other_strategy_allocated_size"] == 0
    assert meta["exchange_strategy_available"] == pytest.approx(0.02)


def test_resolve_reserves_other_strategy_allocations(monkeypatch):
    monkeypatch.setattr(
        "app.services.live_trading.position_query.fetch_position_size_for_side",
        lambda *_a, **_k: 0.015,
    )
    monkeypatch.setattr(
        "app.services.live_trading.position_query.query_exchange_position_size",
        lambda **_k: 0.025,
    )

    monkeypatch.setattr(
        "app.services.live_trading.position_query.fetch_allocated_position_size",
        lambda **_k: 0.02,
    )
    amount, meta = resolve_reduce_only_quantity(
        strategy_id=1,
        symbol="BTC/USDT",
        pos_side="long",
        requested_amount=0.015,
        client=MagicMock(),
        market_type="spot",
        exchange_config={},
        user_id=1,
        credential_id=2,
    )
    assert amount == pytest.approx(0.015)
    assert meta["other_strategy_allocated_size"] == pytest.approx(0.005)
    assert meta["exchange_strategy_available"] == pytest.approx(0.02)


def test_resolve_caps_exit_when_account_cannot_cover_other_strategies(monkeypatch):
    monkeypatch.setattr(
        "app.services.live_trading.position_query.fetch_position_size_for_side",
        lambda *_a, **_k: 0.015,
    )
    monkeypatch.setattr(
        "app.services.live_trading.position_query.query_exchange_position_size",
        lambda **_k: 0.012,
    )
    monkeypatch.setattr(
        "app.services.live_trading.position_query.fetch_allocated_position_size",
        lambda **_k: 0.02,
    )
    amount, meta = resolve_reduce_only_quantity(
        strategy_id=1,
        symbol="BTC/USDT",
        pos_side="long",
        requested_amount=0.015,
        client=MagicMock(),
        market_type="spot",
        exchange_config={},
        user_id=1,
        credential_id=2,
    )
    assert amount == pytest.approx(0.007)
    assert meta["capped_by"] == "account_allocation"


def test_spot_position_query_uses_total_inventory_including_locked():
    from app.services.live_trading.binance_spot import BinanceSpotClient
    from app.services.live_trading.position_query import query_exchange_position_size

    client = MagicMock(spec=BinanceSpotClient)
    client.get_account.return_value = {
        "balances": [{"asset": "BTC", "free": "0.6", "locked": "0.4"}],
    }

    qty = query_exchange_position_size(
        client=client,
        symbol="BTC/USDT",
        pos_side="long",
        market_type="spot",
        strict=True,
    )

    assert qty == pytest.approx(1.0)


def test_strict_spot_position_query_propagates_exchange_snapshot_failure():
    from app.services.live_trading.binance_spot import BinanceSpotClient
    from app.services.live_trading.position_query import query_exchange_position_size

    client = MagicMock(spec=BinanceSpotClient)
    client.get_account.side_effect = RuntimeError("spot account unavailable")

    with pytest.raises(RuntimeError, match="spot account unavailable"):
        query_exchange_position_size(
            client=client,
            symbol="BTC/USDT",
            pos_side="long",
            market_type="spot",
            strict=True,
        )


def test_okx_net_mode_long_position(monkeypatch):
    from app.services.live_trading.okx import OkxClient
    from app.services.live_trading.position_query import query_exchange_position_size

    class FakeOkx(OkxClient):
        def __init__(self):
            pass

        def get_positions(self, *, inst_id: str = "", inst_type: str = "SWAP"):
            return {
                "data": [
                    {
                        "instId": inst_id,
                        "posSide": "net",
                        "pos": "10",
                        "ctVal": "0.01",
                    }
                ]
            }

    qty = query_exchange_position_size(
        client=FakeOkx(),
        symbol="BNB/USDT",
        pos_side="long",
        market_type="swap",
    )
    assert qty == pytest.approx(0.1)


def test_okx_net_mode_short_ignored_for_long_query(monkeypatch):
    from app.services.live_trading.okx import OkxClient
    from app.services.live_trading.position_query import query_exchange_position_size

    class FakeOkx(OkxClient):
        def __init__(self):
            pass

        def get_positions(self, *, inst_id: str = "", inst_type: str = "SWAP"):
            return {
                "data": [
                    {
                        "instId": inst_id,
                        "posSide": "net",
                        "pos": "-10",
                        "ctVal": "0.01",
                    }
                ]
            }

    qty = query_exchange_position_size(
        client=FakeOkx(),
        symbol="BNB/USDT",
        pos_side="long",
        market_type="swap",
    )
    assert qty == 0.0


def test_binance_one_way_long_query(monkeypatch):
    from app.services.live_trading.binance import BinanceFuturesClient
    from app.services.live_trading.position_query import query_exchange_position_size

    class FakeBinance(BinanceFuturesClient):
        def __init__(self):
            pass

        def get_positions(self):
            return [
                {"symbol": "BNBUSDT", "positionSide": "BOTH", "positionAmt": "2.5"},
            ]

    qty = query_exchange_position_size(
        client=FakeBinance(),
        symbol="BNB/USDT",
        pos_side="long",
        market_type="swap",
    )
    assert qty == pytest.approx(2.5)


def test_binance_one_way_short_not_returned_as_long():
    from app.services.live_trading.binance import BinanceFuturesClient
    from app.services.live_trading.position_query import query_exchange_position_size

    class FakeBinance(BinanceFuturesClient):
        def __init__(self):
            pass

        def get_positions(self):
            return [
                {"symbol": "BNBUSDT", "positionSide": "BOTH", "positionAmt": "-2.5"},
            ]

    assert query_exchange_position_size(
        client=FakeBinance(),
        symbol="BNB/USDT",
        pos_side="long",
        market_type="swap",
    ) == 0.0
    assert query_exchange_position_size(
        client=FakeBinance(),
        symbol="BNB/USDT",
        pos_side="short",
        market_type="swap",
    ) == pytest.approx(2.5)


def test_bitget_one_way_total_without_hold_side():
    from app.services.live_trading.bitget import BitgetMixClient
    from app.services.live_trading.position_query import query_exchange_position_size

    class FakeBitget(BitgetMixClient):
        def __init__(self):
            pass

        def get_positions(self, *, product_type: str = "USDT-FUTURES", symbol: str = ""):
            return {
                "data": [
                    {"symbol": "BNBUSDT", "side": "buy", "total": "1.8"},
                ]
            }

    qty = query_exchange_position_size(
        client=FakeBitget(),
        symbol="BNB/USDT",
        pos_side="long",
        market_type="swap",
    )
    assert qty == pytest.approx(1.8)


def test_gate_flat_position_query_returns_zero_in_strict_mode():
    from app.services.live_trading.gate import GateUsdtFuturesClient
    from app.services.live_trading.position_query import query_exchange_position_size

    class FakeGate(GateUsdtFuturesClient):
        def __init__(self):
            pass

        def get_positions(self):
            return []

    qty = query_exchange_position_size(
        client=FakeGate(),
        symbol="BTC/USDT",
        pos_side="long",
        market_type="swap",
        strict=True,
    )
    assert qty == 0.0


def test_gate_dual_short_mode_is_not_misread_as_long():
    from app.services.live_trading.gate import GateUsdtFuturesClient
    from app.services.live_trading.position_query import query_exchange_position_size

    class FakeGate(GateUsdtFuturesClient):
        def __init__(self):
            pass

        def get_positions(self):
            return [
                {
                    "contract": "BTC_USDT",
                    "mode": "dual_short",
                    "size": "2",
                }
            ]

        def get_contract(self, *, contract):
            return {"quanto_multiplier": "0.001"}

    assert query_exchange_position_size(
        client=FakeGate(),
        symbol="BTC/USDT",
        pos_side="short",
        market_type="swap",
    ) == pytest.approx(0.002)
    assert query_exchange_position_size(
        client=FakeGate(),
        symbol="BTC/USDT",
        pos_side="long",
        market_type="swap",
    ) == 0.0


@pytest.mark.parametrize("venue", ["okx", "binance", "bybit", "bitget", "gate", "htx"])
def test_derivative_position_snapshot_retries_one_transient_failure(monkeypatch, venue):
    from app.services.live_trading.binance import BinanceFuturesClient
    from app.services.live_trading.bitget import BitgetMixClient
    from app.services.live_trading.bybit import BybitClient
    from app.services.live_trading.gate import GateUsdtFuturesClient
    from app.services.live_trading.htx import HtxClient
    from app.services.live_trading.okx import OkxClient

    monkeypatch.setenv("LIVE_TRADING_POSITION_SNAPSHOT_RETRIES", "1")
    monkeypatch.setenv("LIVE_TRADING_POSITION_SNAPSHOT_RETRY_BACKOFF_SEC", "0")

    base = {
        "okx": OkxClient,
        "binance": BinanceFuturesClient,
        "bybit": BybitClient,
        "bitget": BitgetMixClient,
        "gate": GateUsdtFuturesClient,
        "htx": HtxClient,
    }[venue]

    class TransientClient(base):
        def __init__(self):
            self.calls = 0

        def get_positions(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise requests.exceptions.ReadTimeout("temporary timeout")
            if venue == "okx":
                return {"data": []}
            if venue == "bybit":
                return {"result": {"list": []}}
            if venue in {"bitget", "htx"}:
                return {"data": []}
            return []

    client = TransientClient()
    assert query_exchange_position_size(
        client=client,
        symbol="SOL/USDT",
        pos_side="long",
        market_type="swap",
        exchange_config={"exchange_id": venue},
        strict=True,
    ) == 0.0
    assert client.calls == 2


def test_spot_position_snapshot_retries_one_transient_failure(monkeypatch):
    from app.services.live_trading.binance_spot import BinanceSpotClient

    monkeypatch.setenv("LIVE_TRADING_POSITION_SNAPSHOT_RETRIES", "1")
    monkeypatch.setenv("LIVE_TRADING_POSITION_SNAPSHOT_RETRY_BACKOFF_SEC", "0")
    client = MagicMock(spec=BinanceSpotClient)
    client.get_account.side_effect = [
        requests.exceptions.ConnectTimeout("temporary timeout"),
        {"balances": [{"asset": "BTC", "free": "0.6", "locked": "0.4"}]},
    ]

    assert query_exchange_position_size(
        client=client,
        symbol="BTC/USDT",
        pos_side="long",
        market_type="spot",
        exchange_config={"exchange_id": "binance"},
        strict=True,
    ) == pytest.approx(1.0)
    assert client.get_account.call_count == 2


def test_broker_position_snapshot_retries_one_transient_failure(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_POSITION_SNAPSHOT_RETRIES", "1")
    monkeypatch.setenv("LIVE_TRADING_POSITION_SNAPSHOT_RETRY_BACKOFF_SEC", "0")

    class TransientBroker:
        def __init__(self):
            self.calls = 0

        def get_positions(self):
            self.calls += 1
            if self.calls == 1:
                raise requests.exceptions.ConnectionError("temporary disconnect")
            return []

    client = TransientBroker()
    assert query_exchange_position_size(
        client=client,
        symbol="AAPL",
        pos_side="long",
        market_type="usstock",
        exchange_config={"exchange_id": "alpaca"},
        strict=True,
    ) == 0.0
    assert client.calls == 2


def test_strict_broker_snapshot_requests_error_propagation(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_POSITION_SNAPSHOT_RETRIES", "1")
    monkeypatch.setenv("LIVE_TRADING_POSITION_SNAPSHOT_RETRY_BACKOFF_SEC", "0")

    class StrictBroker:
        def __init__(self):
            self.flags = []

        def get_positions(self, *, raise_on_error=False):
            self.flags.append(raise_on_error)
            if len(self.flags) == 1:
                raise requests.exceptions.ReadTimeout("temporary timeout")
            return [{"symbol": "AAPL", "quantity": "5"}]

    client = StrictBroker()
    assert query_exchange_position_size(
        client=client,
        symbol="AAPL",
        pos_side="long",
        market_type="usstock",
        exchange_config={"exchange_id": "alpaca"},
        strict=True,
    ) == pytest.approx(5.0)
    assert client.flags == [True, True]


def test_position_snapshot_exhaustion_raises_sanitized_error(monkeypatch):
    from app.services.live_trading import position_query as position_query_module

    monkeypatch.setenv("LIVE_TRADING_POSITION_SNAPSHOT_RETRIES", "1")
    monkeypatch.setenv("LIVE_TRADING_POSITION_SNAPSHOT_RETRY_BACKOFF_SEC", "0")

    class TimedOutClient:
        def __init__(self):
            self.calls = 0

        def get_positions(self):
            self.calls += 1
            raise requests.exceptions.ReadTimeout("https://user:secret@example.invalid")

    client = TimedOutClient()
    with pytest.raises(PositionSnapshotUnavailable) as captured:
        position_query_module._read_position_snapshot(
            client.get_positions,
            client=client,
            symbol="SOL/USDT",
            side="long",
            exchange_config={"exchange_id": "okx"},
        )

    assert client.calls == 2
    assert captured.value.attempts == 2
    assert captured.value.cause_name == "ReadTimeout"
    assert "secret" not in str(captured.value)


def test_position_snapshot_does_not_retry_exchange_rejection(monkeypatch):
    from app.services.live_trading import position_query as position_query_module
    from app.services.live_trading.base import LiveTradingError

    monkeypatch.setenv("LIVE_TRADING_POSITION_SNAPSHOT_RETRIES", "3")
    monkeypatch.setenv("LIVE_TRADING_POSITION_SNAPSHOT_RETRY_BACKOFF_SEC", "0")

    class RejectedClient:
        def __init__(self):
            self.calls = 0

        def get_positions(self):
            self.calls += 1
            raise LiveTradingError("invalid credentials")

    client = RejectedClient()
    with pytest.raises(LiveTradingError, match="invalid credentials"):
        position_query_module._read_position_snapshot(
            client.get_positions,
            client=client,
            symbol="SOL/USDT",
            side="long",
            exchange_config={"exchange_id": "okx"},
        )
    assert client.calls == 1


@pytest.mark.parametrize(
    "message",
    [
        "OKX HTTP 503: service unavailable",
        "Gate HTTP 429: too many requests",
        "HTX swap error: service is restarting",
    ],
)
def test_position_snapshot_retries_wrapped_transient_exchange_error(monkeypatch, message):
    from app.services.live_trading import position_query as position_query_module
    from app.services.live_trading.base import LiveTradingError

    monkeypatch.setenv("LIVE_TRADING_POSITION_SNAPSHOT_RETRIES", "1")
    monkeypatch.setenv("LIVE_TRADING_POSITION_SNAPSHOT_RETRY_BACKOFF_SEC", "0")
    calls = []

    def fetch():
        calls.append("fetch")
        if len(calls) == 1:
            raise LiveTradingError(message)
        return []

    assert position_query_module._read_position_snapshot(
        fetch,
        client=object(),
        symbol="SOL/USDT",
        side="long",
        exchange_config={"exchange_id": "exchange"},
    ) == []
    assert calls == ["fetch", "fetch"]


def test_position_snapshot_backoff_uses_jitter(monkeypatch):
    from app.services.live_trading import position_query as position_query_module

    monkeypatch.setenv("LIVE_TRADING_POSITION_SNAPSHOT_RETRIES", "1")
    monkeypatch.setenv("LIVE_TRADING_POSITION_SNAPSHOT_RETRY_BACKOFF_SEC", "0.5")
    monkeypatch.setenv("LIVE_TRADING_POSITION_SNAPSHOT_RETRY_JITTER_RATIO", "0.2")
    delays = []
    monkeypatch.setattr(position_query_module.random, "uniform", lambda low, high: high)
    monkeypatch.setattr(position_query_module.time, "sleep", delays.append)

    class TransientClient:
        def __init__(self):
            self.calls = 0

        def get_positions(self):
            self.calls += 1
            if self.calls == 1:
                raise requests.exceptions.ReadTimeout("temporary timeout")
            return {"data": []}

    client = TransientClient()
    assert position_query_module._read_position_snapshot(
        client.get_positions,
        client=client,
        symbol="SOL/USDT",
        side="long",
        exchange_config={"exchange_id": "okx"},
    ) == {"data": []}
    assert delays == pytest.approx([0.6])
