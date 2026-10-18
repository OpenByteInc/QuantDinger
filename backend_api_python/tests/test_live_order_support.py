from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from app.services import pending_order_worker as worker_module
from app.services.live_trading.base import LiveTradingError
from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.gate import GateSpotClient, GateStockClient, GateUsdtFuturesClient
from app.services.live_trading.htx import HtxClient
from app.services.live_trading.okx import OkxClient
from app.services.market import product_catalog
from app.services.pending_orders import live_order_phases
from app.services.pending_orders.live_order_support import (
    FillAccumulator,
    LiveOrderRejected,
    apply_execution_result,
    attach_instrument_product_contracts,
    build_live_order_context,
    make_client_order_id,
    signal_to_side_pos_reduce,
)


def test_attach_instrument_product_contracts_uses_immutable_native_id():
    candidates = [{
        "market": "Crypto",
        "symbol": "RAAPL/USDT",
        "exchange_id": "bitget",
        "market_type": "spot",
        "key": "Crypto:RAAPL/USDT@bitget:spot",
    }]

    attach_instrument_product_contracts(
        candidates,
        {"instrument_products": [{
            "symbol": "RAAPL/USDT",
            "exchange_id": "bitget",
            "market_type": "spot",
            "instrument_id": "rAAPLUSDT",
            "product_type": "tokenized_equity",
            "api_family": "reality",
        }]},
        exchange_id="bitget",
    )

    assert candidates[0]["instrument_id"] == "rAAPLUSDT"
    assert candidates[0]["api_family"] == "reality"
    assert candidates[0]["product_type"] == "tokenized_equity"


def test_attach_instrument_product_contracts_hydrates_legacy_empty_contract(monkeypatch):
    candidates = [{
        "market": "Crypto",
        "symbol": "NVDA/USD",
        "exchange_id": "gate",
        "market_type": "spot",
        "key": "Crypto:NVDA/USD@gate:spot",
    }]
    trading_config = {"instrument_products": []}
    monkeypatch.setattr(
        product_catalog,
        "get_catalog_product",
        lambda **kwargs: {
            "instrument_id": "NVDA",
            "product_type": "direct_equity",
            "api_family": "stock",
            "underlying_market": "USStock",
            "underlying_symbol": "NVDA",
            "product_meta": {"quote_currency": "USD"},
        },
    )

    attach_instrument_product_contracts(
        candidates,
        trading_config,
        exchange_id="gate",
    )

    assert candidates[0]["instrument_id"] == "NVDA"
    assert candidates[0]["api_family"] == "stock"
    assert trading_config["instrument_products"][0]["product_type"] == "direct_equity"


def test_attach_instrument_product_contracts_repairs_legacy_generic_contract(monkeypatch):
    candidates = [{
        "market": "Crypto",
        "symbol": "NVDA/USD",
        "exchange_id": "gate",
        "market_type": "spot",
        "key": "Crypto:NVDA/USD@gate:spot",
    }]
    trading_config = {"instrument_products": [{
        "market": "Crypto",
        "symbol": "NVDA/USD",
        "exchange_id": "gate",
        "market_type": "spot",
        "instrument_id": "NVDA_USD",
        "product_type": "crypto",
        "api_family": "spot",
    }]}
    monkeypatch.setattr(
        product_catalog,
        "get_catalog_product",
        lambda **kwargs: {
            "instrument_id": "NVDA",
            "product_type": "direct_equity",
            "api_family": "stock",
            "underlying_market": "USStock",
            "underlying_symbol": "NVDA",
            "product_meta": {"quote_currency": "USD"},
        },
    )

    attach_instrument_product_contracts(
        candidates,
        trading_config,
        exchange_id="gate",
    )

    assert candidates[0]["instrument_id"] == "NVDA"
    assert candidates[0]["api_family"] == "stock"
    assert candidates[0]["underlying_market"] == "USStock"
    assert trading_config["instrument_products"] == [{
        "market": "Crypto",
        "symbol": "NVDA/USD",
        "exchange_id": "gate",
        "market_type": "spot",
        "instrument_id": "NVDA",
        "product_type": "direct_equity",
        "api_family": "stock",
        "underlying_market": "USStock",
        "underlying_symbol": "NVDA",
        "product_meta": {"quote_currency": "USD"},
    }]


def test_attach_instrument_product_contracts_keeps_unresolved_legacy_crypto(monkeypatch):
    candidates = [{
        "market": "Crypto",
        "symbol": "NVDA/USD",
        "exchange_id": "gate",
        "market_type": "spot",
    }]
    monkeypatch.setattr(product_catalog, "get_catalog_product", lambda **kwargs: None)

    trading_config = {"instrument_products": []}
    attach_instrument_product_contracts(
        candidates,
        trading_config,
        exchange_id="gate",
    )

    assert "api_family" not in candidates[0]
    assert trading_config["instrument_products"] == []


def test_make_client_order_id_okx_is_compact_alphanumeric():
    oid = make_client_order_id(exchange_id="okx", strategy_id=123456789, order_id=987654321, phase="lmt")

    assert oid.isalnum()
    assert len(oid) <= 32
    assert oid.startswith("qd")


def test_make_client_order_id_default_keeps_phase():
    assert make_client_order_id(exchange_id="bitget", strategy_id=12, order_id=34, phase="mkt") == "qd_12_34_mkt"


def test_all_worker_client_order_id_calls_match_helper_signature():
    """The common path runs before venue dispatch, so every call must obey the helper contract."""
    source = textwrap.dedent(inspect.getsource(worker_module.PendingOrderWorker._execute_live_order))
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "make_client_order_id"
    ]
    supported = set(inspect.signature(make_client_order_id).parameters)

    assert len(calls) == 4
    for call in calls:
        passed = {keyword.arg for keyword in call.keywords if keyword.arg}
        assert passed <= supported


def _isinstance_class_names(test):
    for node in ast.walk(test):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            and len(node.args) == 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "client"
        ):
            continue
        target = node.args[1]
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, ast.Tuple):
            return [item.id for item in target.elts if isinstance(item, ast.Name)]
    return []


def test_all_crypto_order_phase_calls_match_exchange_client_signatures():
    """Audit spot/swap limit, market, fill and cancel calls for every supported crypto venue."""
    clients = {
        cls.__name__: cls
        for cls in (
            BinanceFuturesClient,
            BinanceSpotClient,
            OkxClient,
            BitgetMixClient,
            BitgetSpotClient,
                BybitClient,
                GateSpotClient,
                GateStockClient,
                GateUsdtFuturesClient,
            HtxClient,
        )
    }
    helpers = (
        live_order_phases.place_live_limit_order,
        live_order_phases.place_live_market_order,
        live_order_phases.wait_live_order_fill,
        live_order_phases.cancel_live_limit_order,
    )
    checked = set()

    for helper in helpers:
        tree = ast.parse(textwrap.dedent(inspect.getsource(helper)))
        for branch in (node for node in ast.walk(tree) if isinstance(node, ast.If)):
            class_names = _isinstance_class_names(branch.test)
            if not class_names:
                continue
            calls = []
            for statement in branch.body:
                calls.extend(
                    node
                    for node in ast.walk(statement)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "client"
                )
            for class_name in class_names:
                client_class = clients[class_name]
                for call in calls:
                    method_name = call.func.attr
                    method = getattr(client_class, method_name)
                    supported = set(inspect.signature(method).parameters)
                    passed = {keyword.arg for keyword in call.keywords if keyword.arg}
                    assert passed <= supported, (
                        f"{helper.__name__}: {class_name}.{method_name} received unsupported "
                        f"keywords {sorted(passed - supported)}"
                    )
                    checked.add((helper.__name__, class_name, method_name))

    expected_dispatches = {
        (helper.__name__, client.__name__)
        for helper in helpers
        for client in clients.values()
    }
    actual_dispatches = {(helper, client) for helper, client, _method in checked}
    assert expected_dispatches <= actual_dispatches


def test_signal_to_side_pos_reduce_exit_aliases():
    assert signal_to_side_pos_reduce("close_long_trailing") == ("sell", "long", True)
    assert signal_to_side_pos_reduce("close_short_profit") == ("buy", "short", True)
    assert signal_to_side_pos_reduce("open_short") == ("sell", "short", False)


def test_signal_to_side_pos_reduce_rejects_unknown():
    try:
        signal_to_side_pos_reduce("flip_sideways")
    except LiveTradingError as exc:
        assert "Unsupported signal_type" in str(exc)
    else:
        raise AssertionError("expected LiveTradingError")


def test_fill_accumulator_tracks_weighted_average_and_signed_fees_by_currency():
    fills = FillAccumulator()

    fills.apply_fill(0.1, 100)
    fills.apply_fill(0.2, 200)
    fills.apply_fee("-0.003", "USDT")
    fills.apply_fee(0.002, "BTC")

    assert round(fills.total_base, 8) == 0.3
    assert round(fills.total_quote, 8) == 50
    assert round(fills.avg_price(), 8) == round(50 / 0.3, 8)
    assert fills.total_fee == 0
    assert fills.fee_ccy == "MIXED"
    assert fills.fees_by_ccy == {"USDT": -0.003, "BTC": 0.002}


def test_apply_execution_result_does_not_drop_exchange_fee():
    result = type(
        "ExecutionResult",
        (),
        {
            "filled_qty": 0.25,
            "avg_price": 64000.0,
            "fees_by_ccy": {"USDT": 8.0, "BNB": 0.001},
        },
    )()
    fills = FillAccumulator()

    apply_execution_result(fills, result)

    assert fills.total_base == 0.25
    assert fills.avg_price() == 64000.0
    assert fills.fees_by_ccy == {"USDT": 8.0, "BNB": 0.001}
    assert fills.fee_status == "actual"


def test_apply_execution_result_preserves_authoritative_zero_fee():
    result = type(
        "ExecutionResult",
        (),
        {
            "filled_qty": 0.25,
            "avg_price": 64000.0,
            "fees_by_ccy": {},
            "fee_status": "actual_zero",
        },
    )()
    fills = FillAccumulator()

    apply_execution_result(fills, result)

    assert fills.fee_status == "actual_zero"
    assert fills.fees_by_ccy == {}


def test_maker_limit_price_offsets_buy_and_sell():
    assert live_order_phases.maker_limit_price(ref_price=100, side="buy", maker_offset=0.01) == 99
    assert live_order_phases.maker_limit_price(ref_price=100, side="sell", maker_offset=0.01) == 101

    try:
        live_order_phases.maker_limit_price(ref_price=0, side="buy", maker_offset=0.01)
    except LiveTradingError as exc:
        assert "missing_ref_price" in str(exc)
    else:
        raise AssertionError("expected LiveTradingError")

    try:
        live_order_phases.maker_limit_price(ref_price=100, side="hold", maker_offset=0.01)
    except LiveTradingError as exc:
        assert "unsupported_order_side" in str(exc)
    else:
        raise AssertionError("expected LiveTradingError")


def test_place_live_market_order_spot_buy_uses_quote_amount(monkeypatch):
    class FakeBitgetSpot:
        def place_market_order(self, **kwargs):
            self.kwargs = kwargs
            return type("Result", (), {"exchange_order_id": "1", "raw": kwargs})()

    monkeypatch.setattr(live_order_phases, "BitgetSpotClient", FakeBitgetSpot)
    client = FakeBitgetSpot()

    live_order_phases.place_live_market_order(
        client=client,
        symbol="BTC/USDT",
        side="buy",
        amount=0.01,
        reduce_only=False,
        pos_side="long",
        client_order_id="oid",
        market_type="spot",
        payload={},
        exchange_config={},
        leverage=1,
        ref_price=100,
        spot_quote_amt=25,
        spot_market_buy_uses_quote=True,
    )

    assert client.kwargs["size"] == 25
    assert client.kwargs["client_order_id"] == "oid"


def test_wait_live_order_fill_enforces_slow_exchange_limit_wait(monkeypatch):
    class FakeGateSpot:
        def wait_for_fill(self, **kwargs):
            self.kwargs = kwargs
            return {"filled": 1, "avg_price": 10, "fee": 0, "fee_ccy": "USDT"}

    monkeypatch.setattr(live_order_phases, "GateSpotClient", FakeGateSpot)
    client = FakeGateSpot()

    snapshot = live_order_phases.wait_live_order_fill(
        client=client,
        symbol="BTC/USDT",
        order_id="ex-1",
        client_order_id="client-1",
        market_type="spot",
        exchange_config={},
        max_wait_sec=1,
        phase="limit",
    )

    assert snapshot["filled"] == 1
    assert client.kwargs["max_wait_sec"] == 8.0


def test_wait_live_order_fill_rejects_unknown_client():
    try:
        live_order_phases.wait_live_order_fill(
            client=object(),
            symbol="BTC/USDT",
            order_id="ex-1",
            client_order_id="client-1",
            market_type="spot",
            exchange_config={},
            max_wait_sec=1,
            phase="limit",
        )
    except LiveTradingError as exc:
        assert "Unsupported client type" in str(exc)
    else:
        raise AssertionError("expected LiveTradingError")


def test_build_live_order_context_normalizes_and_validates():
    ctx = build_live_order_context(
        order_id=99,
        order_row={"strategy_id": 12, "symbol": "BTC/USDT", "signal_type": "open_long", "market_type": "futures", "execution_mode": "live"},
        payload={"amount": 0.01, "execution_mode": "live"},
        load_strategy_configs=lambda strategy_id: {
            "user_id": 7,
            "execution_mode": "live",
            "market_category": "Crypto",
            "market_type": "futures",
            "trading_config": {"trade_direction": "long", "bot_type": "trend"},
            "exchange_config": {"exchange_id": "binance"},
        },
        resolve_exchange_config=lambda cfg, user_id: dict(cfg, credential_user_id=user_id),
        safe_exchange_config_for_log=lambda cfg: {"exchange_id": cfg.get("exchange_id")},
    )

    assert ctx.strategy_id == 12
    assert ctx.strategy_user_id == 7
    assert ctx.exchange_id == "binance"
    assert ctx.market_type == "swap"
    assert ctx.safe_exchange_config == {"exchange_id": "binance"}


def test_live_context_rejects_signal_order_even_if_strategy_is_live():
    with pytest.raises(LiveOrderRejected, match="execution_mode_mismatch"):
        build_live_order_context(
            order_id=99,
            order_row={
                "strategy_id": 12,
                "symbol": "BTC/USDT",
                "signal_type": "open_long",
                "execution_mode": "signal",
            },
            payload={"amount": 0.01, "execution_mode": "signal"},
            load_strategy_configs=lambda strategy_id: {"execution_mode": "live"},
            resolve_exchange_config=lambda *_args, **_kwargs: pytest.fail("live credentials must not be resolved"),
            safe_exchange_config_for_log=lambda cfg: cfg,
        )


def test_live_context_rejects_live_order_when_stored_strategy_is_signal():
    with pytest.raises(LiveOrderRejected, match="execution_mode_mismatch"):
        build_live_order_context(
            order_id=99,
            order_row={
                "strategy_id": 12,
                "symbol": "BTC/USDT",
                "signal_type": "open_long",
                "execution_mode": "live",
            },
            payload={"amount": 0.01, "execution_mode": "live"},
            load_strategy_configs=lambda strategy_id: {"execution_mode": "signal"},
            resolve_exchange_config=lambda *_args, **_kwargs: pytest.fail("live credentials must not be resolved"),
            safe_exchange_config_for_log=lambda cfg: cfg,
        )


def test_live_context_rejects_strategy_identity_mismatch():
    with pytest.raises(LiveOrderRejected, match="strategy_id_mismatch"):
        build_live_order_context(
            order_id=99,
            order_row={
                "strategy_id": 12,
                "symbol": "BTC/USDT",
                "signal_type": "open_long",
                "execution_mode": "live",
            },
            payload={
                "strategy_id": 13,
                "amount": 0.01,
                "execution_mode": "live",
            },
            load_strategy_configs=lambda strategy_id: pytest.fail("strategy mode must not be loaded"),
            resolve_exchange_config=lambda *_args, **_kwargs: pytest.fail("live credentials must not be resolved"),
            safe_exchange_config_for_log=lambda cfg: cfg,
        )


def test_build_live_order_context_injects_immutable_reality_api_family():
    ctx = build_live_order_context(
        order_id=99,
        order_row={"strategy_id": 12, "symbol": "RAAPL/USDT", "signal_type": "open_long", "execution_mode": "live"},
        payload={"amount": 50, "execution_mode": "live"},
        load_strategy_configs=lambda strategy_id: {
            "user_id": 7,
            "execution_mode": "live",
            "market_category": "Crypto",
            "market_type": "spot",
            "status": "running",
            "trading_config": {"instrument_products": [{
                "symbol": "RAAPL/USDT",
                "exchange_id": "bitget",
                "market_type": "spot",
                "instrument_id": "RAAPLUSDT",
                "product_type": "tokenized_equity",
                "api_family": "reality",
            }]},
            "exchange_config": {"exchange_id": "bitget"},
        },
        resolve_exchange_config=lambda cfg, user_id: dict(cfg, api_key="secret"),
        safe_exchange_config_for_log=lambda cfg: {
            "exchange_id": cfg.get("exchange_id"),
            "api_family": cfg.get("api_family"),
        },
    )

    assert ctx.exchange_config["api_family"] == "reality"
    assert ctx.exchange_config["instrument_id"] == "RAAPLUSDT"
    assert ctx.safe_exchange_config == {"exchange_id": "bitget", "api_family": "reality"}


def test_build_live_order_context_rejects_product_contract_mismatch():
    with pytest.raises(LiveOrderRejected, match="strategyV2.instrumentProductContractMismatch"):
        build_live_order_context(
            order_id=99,
            order_row={"strategy_id": 12, "symbol": "RNVD/USDT", "signal_type": "open_long", "execution_mode": "live"},
            payload={"amount": 50, "execution_mode": "live"},
            load_strategy_configs=lambda strategy_id: {
                "user_id": 7,
                "execution_mode": "live",
                "market_category": "Crypto",
                "market_type": "spot",
                "status": "running",
                "trading_config": {"instrument_products": [{
                    "symbol": "RAAPL/USDT",
                    "exchange_id": "bitget",
                    "market_type": "spot",
                    "product_type": "tokenized_equity",
                    "api_family": "reality",
                }]},
                "exchange_config": {"exchange_id": "bitget"},
            },
            resolve_exchange_config=lambda cfg, user_id: cfg,
            safe_exchange_config_for_log=lambda cfg: cfg,
        )


def test_build_live_order_context_rejects_missing_symbol():
    try:
        build_live_order_context(
            order_id=99,
            order_row={"strategy_id": 12, "signal_type": "open_long"},
            payload={"amount": 0.01},
            load_strategy_configs=lambda strategy_id: {},
            resolve_exchange_config=lambda cfg, user_id: {},
            safe_exchange_config_for_log=lambda cfg: {},
        )
    except LiveOrderRejected as exc:
        assert exc.error == "missing_symbol_or_signal_type"
        assert exc.strategy_id == 12
        assert "missing symbol" in exc.console_message
    else:
        raise AssertionError("expected LiveOrderRejected")


def test_build_live_order_context_rejects_entry_after_strategy_stops():
    with pytest.raises(LiveOrderRejected, match="strategy_not_running"):
        build_live_order_context(
            order_id=1,
            order_row={"strategy_id": 9, "symbol": "SOL/USDT", "signal_type": "add_long", "execution_mode": "live"},
            payload={"execution_mode": "live"},
            load_strategy_configs=lambda strategy_id: {
                "user_id": 7,
                "execution_mode": "live",
                "status": "stopped",
                "market_category": "Crypto",
                "market_type": "swap",
                "exchange_config": {"exchange_id": "binance"},
                "trading_config": {},
            },
            resolve_exchange_config=lambda cfg, user_id: cfg,
            safe_exchange_config_for_log=lambda cfg: cfg,
        )


def test_build_live_order_context_allows_close_after_strategy_stops():
    ctx = build_live_order_context(
        order_id=1,
        order_row={"strategy_id": 9, "symbol": "SOL/USDT", "signal_type": "close_long", "execution_mode": "live"},
        payload={"execution_mode": "live"},
        load_strategy_configs=lambda strategy_id: {
            "user_id": 7,
            "execution_mode": "live",
            "status": "stopped",
            "market_category": "Crypto",
            "market_type": "swap",
            "exchange_config": {"exchange_id": "binance"},
            "trading_config": {},
        },
        resolve_exchange_config=lambda cfg, user_id: cfg,
        safe_exchange_config_for_log=lambda cfg: cfg,
    )

    assert ctx.signal_type == "close_long"


def test_build_live_order_context_rejects_policy_violation():
    try:
        build_live_order_context(
            order_id=99,
            order_row={"strategy_id": 12, "symbol": "BTC/USDT", "signal_type": "open_long", "market_type": "swap", "execution_mode": "live"},
            payload={"amount": 0.01, "execution_mode": "live"},
            load_strategy_configs=lambda strategy_id: {
                "user_id": 7,
                "execution_mode": "live",
                "market_category": "Crypto",
                "market_type": "swap",
                "trading_config": {"trade_direction": "long", "bot_type": "trend"},
                "exchange_config": {"exchange_id": "retiredexchange"},
            },
            resolve_exchange_config=lambda cfg, user_id: cfg,
            safe_exchange_config_for_log=lambda cfg: cfg,
        )
    except LiveOrderRejected as exc:
        assert exc.error.startswith("policy_violation:")
        assert exc.strategy_id == 12
    else:
        raise AssertionError("expected LiveOrderRejected")
