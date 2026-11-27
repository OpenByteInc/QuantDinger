from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import json
from pathlib import Path

import pandas as pd
import pytest

from app.services.strategy_v2.contract import inspect_strategy_v2
from app.services.strategy_v2.deployment import StrategyV2DeploymentService
from app.services.strategy_v2.instruments import parse_instrument
from app.services.strategy_v2.service import StrategyV2BacktestService, _member_key
from app.services.strategy_runtime import live_portfolio
from app.services.openapi_brokers.forthright import ForthrightClient
from app.services.market import hk_board_lots


SOURCE = '''
def initialize(context):
    context.set_universe(pool="hk_stock_connect")
    context.subscribe(frequency="1d", fields=["close"])
    context.set_benchmark("HKStock:02800.HK")
    context.set_metadata(direction_mode="long_only")

def on_rebalance(context, data):
    pass
'''
NOW = datetime(2026, 11, 27, tzinfo=timezone.utc)


@pytest.mark.parametrize("symbol", ["00700", "700", "700.HK", "HK00700", "HK.00700", "00700.XHKG", "00700.HK"])
def test_hk_strategy_keys_match_canonical_equity_ledger(symbol):
    from app.services.live_trading.records import normalize_strategy_symbol

    spec = parse_instrument(f"HKStock:{symbol}")
    assert spec.symbol == "00700.HK"
    assert spec.key == "HKStock:00700.HK"
    assert normalize_strategy_symbol(spec.symbol) == spec.symbol


@pytest.mark.parametrize("value", [
    "USStock:BRK.B", "CNStock:600519.SH", "Futures:HSIZ26",
    "Crypto:00700/HKD@gate:spot", "Crypto:BTC/USDT@okx:swap",
])
def test_other_markets_keep_their_execution_identity(value):
    assert parse_instrument(value).key == value


def make_service(monkeypatch, members, code="hk_stock_connect", source_ref=""):
    universe = SimpleNamespace(
        list_universes=lambda user_id: [{"id": 7, "code": code, "source_ref": source_ref}],
        candidate_members=lambda *args, **kwargs: members,
    )
    service = StrategyV2BacktestService(universe_service=universe)
    monkeypatch.setattr("app.services.strategy_v2.service.get_universe_service", lambda: universe)
    return service


@pytest.mark.parametrize("code,source_ref", [
    ("hk_stock_connect", ""), ("hk_hsi", "HSI"), ("hk_tech30", "HSTECH"),
    ("hk_china_enterprises50", "HSCEI"), ("hk_etf", ""), ("watchlist", ""),
    ("user_hk_pool", ""),
])
def test_every_hk_pool_and_index_uses_the_same_strategy_identity(monkeypatch, code, source_ref):
    members = [{"market": "HKStock", "symbol": symbol} for symbol in ("07709", "HK07709", "7709.HK")]
    service = make_service(monkeypatch, members, code, source_ref)
    declaration = f'index="INDEX:{source_ref}"' if source_ref else f'pool="{code}"'
    manifest = inspect_strategy_v2(SOURCE.replace('pool="hk_stock_connect"', declaration)).manifest
    candidates, _ = service.resolve_candidates(user_id=5, manifest=manifest, start_date=NOW, end_date=NOW)
    assert [(row["key"], row["symbol"]) for row in candidates] == [("HKStock:07709.HK", "07709.HK")]


def test_all_bundled_hk_pool_members_have_valid_canonical_stock_codes():
    from app.services.strategy_v2.service import _canonical_members

    pools = {}
    for filename in ("default_cn_hk_universes.json", "default_global_universes.json"):
        data = json.loads((Path(__file__).parents[1] / "app" / "data" / filename).read_text(encoding="utf-8"))
        pools.update({code: value for code, value in data["universes"].items() if code.startswith("hk_")})
    assert {"hk_stock_connect", "hk_hsi", "hk_tech30", "hk_china_enterprises50", "hk_etf"} <= set(pools)
    for pool in pools.values():
        members = pool["members"]
        original = deepcopy(members)
        canonical = _canonical_members(members)
        assert canonical
        assert len({row["key"] for row in canonical}) == len(canonical)
        assert all(len(row["symbol"]) == 8 and row["symbol"][:5].isdigit() and row["symbol"].endswith(".HK") for row in canonical)
        assert members == original


def test_mixed_watchlist_keeps_us_share_classes_and_exchange_products_separate(monkeypatch):
    members = [
        {"market": "HKStock", "symbol": "00700"},
        {"market": "USStock", "symbol": "BRK.B"},
        {"market": "USStock", "symbol": "BRK-B"},
        {"market": "Crypto", "symbol": "00700/HKD", "exchange_id": "gate", "market_type": "spot"},
        {"market": "Crypto", "symbol": "HK0700/USDT", "exchange_id": "binance", "market_type": "swap"},
    ]
    service = make_service(monkeypatch, members)
    candidates, _ = service.resolve_candidates(user_id=5, manifest=inspect_strategy_v2(SOURCE).manifest, start_date=NOW, end_date=NOW)
    assert [row["symbol"] for row in candidates] == ["00700.HK", "BRK.B", "BRK-B", "00700/HKD", "HK0700/USDT"]


def test_generation_repair_copilot_and_agent_contract_share_hk_pool_rules():
    from app.services.ai_generation_contracts import SCRIPT_STRATEGY_REPAIR_REQUIREMENTS, SCRIPT_STRATEGY_QUICK_TOOL_SYSTEM_PROMPT
    from app.services.ai_skill_registry import get_skill
    from app.services.strategy_authoring import get_strategy_authoring_contract
    from app.services.strategy_ai_generation import build_strategy_system_prompt

    prompts = [SCRIPT_STRATEGY_REPAIR_REQUIREMENTS, SCRIPT_STRATEGY_QUICK_TOOL_SYSTEM_PROMPT,
               get_skill("script_strategy").system_instruction, get_strategy_authoring_contract()["system_contract"]]
    for mode, asset in [("authoring", "script"), ("authoring", "portfolio_strategy"), ("indicator_conversion", "script")]:
        prompts.append(build_strategy_system_prompt(prompt="HK stock strategy", asset_type=asset, generation_mode=mode)[0])
    for prompt in prompts:
        assert "HKStock:00700.HK" in prompt
        assert "hk_stock_connect" in prompt and "hk_etf" in prompt
        assert "results unchanged for data, positions, and orders" in prompt
        assert "does not rewrite US share classes" in prompt


def test_pool_aliases_share_data_identity_and_preserve_membership_periods(monkeypatch):
    first = {"valid_from": "2026-01-01", "valid_to": "2026-03-01"}
    second = {"valid_from": "2026-06-01", "valid_to": None}
    members = [
        {"market": "HKStock", "symbol": "00700", "membership_periods": [first]},
        {"market": "HKStock", "symbol": "700.HK", "membership_periods": [second]},
    ]
    original = deepcopy(members)
    service = make_service(monkeypatch, members)
    manifest = inspect_strategy_v2(SOURCE).manifest
    candidates, universe_id = service.resolve_candidates(
        user_id=5, manifest=manifest, start_date=NOW, end_date=NOW,
    )
    assert universe_id == 7
    assert len(candidates) == 1
    assert candidates[0]["symbol"] == "00700.HK"
    assert candidates[0]["membership_periods"] == [first, second]
    assert {_member_key(member) for member in members} == {candidates[0]["key"]}
    assert members == original
    calls = []
    service.frame_fetcher = lambda market, symbol, *args, **kwargs: (
        calls.append((market, symbol)) or pd.DataFrame({"close": [350.0]})
    )
    frames, skipped = service.fetch_frames(candidates, "1d", NOW, NOW)
    assert calls == [("HKStock", "00700.HK")]
    assert set(frames) == {"HKStock:00700.HK"}
    assert skipped == []


def test_dynamic_deployment_passes_real_forthright_validation_and_lot_rules(monkeypatch):
    make_service(monkeypatch, [{"market": "HKStock", "symbol": "00700"}])
    manifest = inspect_strategy_v2(SOURCE).manifest
    candidates = StrategyV2DeploymentService._resolve_live_instruments(
        user_id=5, manifest=manifest, execution_mode="live",
    )
    client = object.__new__(ForthrightClient)
    client.is_futures = False
    client.strategy_units = True
    monkeypatch.setattr(hk_board_lots, "board_lot_rule", lambda symbol: {"lot_size": 100})
    instrument = candidates[0]
    client.validate_instrument(instrument["symbol"], instrument["market"])
    assert client.normalize_strategy_quantity(instrument["symbol"], 150) == 100
    calls = []
    monkeypatch.setattr(client, "_trade", lambda *args, **kwargs: calls.append(kwargs) or {"order_id": "test"})
    client.place_limit_order(symbol=instrument["symbol"], market_type="HKStock", side="buy", quantity=100, price=350)
    assert calls[0]["product_code"] == "00700"
    with pytest.raises(ValueError, match="belowBoardLot"):
        client.normalize_strategy_quantity(instrument["symbol"], 99)
    with pytest.raises(ValueError, match="canonicalSymbolRequired"):
        client.validate_instrument("00700", "HKStock")


@pytest.mark.parametrize("instrument,ticker,currency", [
    ("HKStock:00700", "00700", "HKD"), ("USStock:BRK.B", "BRK.B", "USD"),
])
def test_canonical_strategy_symbol_reaches_tiger_with_original_market_and_quantity(monkeypatch, instrument, ticker, currency):
    from app.services.openapi_brokers.tiger import TigerClient

    spec = parse_instrument(instrument)
    client = object.__new__(TigerClient)
    client.account_id = "test-account"
    sent = []
    monkeypatch.setattr(hk_board_lots, "board_lot_rule", lambda _: {"lot_size": 100})
    monkeypatch.setattr(client, "_sdk", lambda: {
        "stock_contract": lambda symbol, ccy: SimpleNamespace(symbol=symbol, currency=ccy),
        "limit_order": lambda account, contract, action, quantity, price, **kw:
            SimpleNamespace(account=account, contract=contract, action=action, quantity=quantity, price=price),
    })
    monkeypatch.setattr(client, "_ensure_client", lambda: SimpleNamespace(place_order=lambda order: sent.append(order) or 123))
    result = client.place_limit_order(symbol=spec.symbol, market_type=spec.market, side="buy", quantity=100, price=350)
    assert result.success
    assert (sent[0].contract.symbol, sent[0].contract.currency, sent[0].quantity) == (ticker, currency, 100)
    assert sent[0].account == "test-account"


def test_live_refresh_keeps_owned_positions_outside_pool_without_duplicates(monkeypatch):
    service = make_service(monkeypatch, [{"market": "HKStock", "symbol": "00700"}])
    cursor = SimpleNamespace(
        execute=lambda *args: None,
        fetchall=lambda: [{"symbol": "00700.HK"}, {"symbol": "00005.HK"}],
        close=lambda: None,
    )

    @contextmanager
    def connection():
        yield SimpleNamespace(cursor=lambda: cursor)

    monkeypatch.setattr(live_portfolio, "get_db_connection", connection)
    candidates = []
    live_portfolio.refresh_members(service, candidates, inspect_strategy_v2(SOURCE).manifest, 5, 10, NOW)
    assert [item["key"] for item in candidates] == ["HKStock:00005.HK", "HKStock:00700.HK"]
    executor = SimpleNamespace(_get_current_positions=lambda *args: [
        {"symbol": "00700.HK", "size": 100, "side": "long", "entry_price": 350},
        {"symbol": "00005.HK", "size": 400, "side": "long", "entry_price": 70},
    ])
    positions = live_portfolio.positions_by_symbol(executor, 10, candidates, {})
    assert positions["HKStock:00700.HK"]["amount"] == 100
    assert positions["HKStock:00005.HK"]["amount"] == 400


@pytest.mark.parametrize("legacy_symbol", ["00700", "700", "HK00700", "HKStock:00700", "00700.HK"])
def test_legacy_hk_position_remains_visible_to_canonical_strategy(legacy_symbol):
    executor = SimpleNamespace(_get_current_positions=lambda *args: [
        {"symbol": legacy_symbol, "size": 100, "side": "long", "entry_price": 350},
    ])
    positions = live_portfolio.positions_by_symbol(executor, 10, [
        {"key": "HKStock:00700.HK", "symbol": "00700.HK", "market": "HKStock"},
    ], {})
    assert positions["HKStock:00700.HK"]["amount"] == 100


def test_signal_position_alias_lookup_keeps_existing_key_and_rejects_duplicate_allocations():
    from app.services import virtual_trading

    calls = []
    rows = [{"symbol_canonical": "00700", "size": 100}]
    cur = SimpleNamespace(execute=lambda *args: calls.append(args), fetchall=lambda: rows)
    position = virtual_trading._locked_virtual_position(cur, 10, "00700.HK", "long")
    assert position == rows[0]
    assert calls[0][1][0] == 10 and calls[0][1][2] == "long"
    assert "00700" in calls[0][1][1] and "00700.HK" in calls[0][1][1]
    assert virtual_trading._price_for_symbol({"00700.HK": 350}, "00700") == 350
    assert virtual_trading._symbol_aliases("BRK.B") == ["BRK.B"]
    assert virtual_trading._symbol_aliases("HSIZ26") == ["HSIZ26"]
    rows.append({"symbol_canonical": "00700.HK", "size": 200})
    with pytest.raises(ValueError, match="virtualTrading.invalidIdentity"):
        virtual_trading._locked_virtual_position(cur, 10, "00700.HK", "long")


def test_factor_research_does_not_count_hk_aliases_as_separate_stocks(monkeypatch):
    service = make_service(monkeypatch, [
        {"market": "HKStock", "symbol": symbol} for symbol in ("00700", "700.HK", "HK00700")
    ])
    service.universe_service.get_universe = lambda *args: {"id": 7, "code": "hk_stock_connect"}
    with pytest.raises(ValueError, match="factorResearchUniverseTooSmall"):
        service.research_universe_factor(user_id=5, universe_id=7, frequency="1d", start_date=NOW,
                                        end_date=NOW, factor_id="momentum_20")


@pytest.mark.parametrize("symbol", ["00700", "HKStock:00700.HK"])
def test_signal_close_consumes_the_legacy_allocation_and_preserves_pnl(monkeypatch, symbol):
    from app.services import virtual_trading
    from app.services.virtual_execution_costs import VirtualExecutionCostPolicy

    class Cursor:
        query = ""
        writes = []

        def execute(self, query, params=()):
            self.query = " ".join(query.split())
            self.writes.append((self.query, params))

        def fetchone(self):
            if "FROM qd_strategies_trading" in self.query:
                return {"id": 10, "user_id": 5, "execution_mode": "signal", "initial_capital": 100000,
                        "market_category": "HKStock"}
            if "FROM qd_strategy_virtual_accounts" in self.query:
                return {"initial_cash": 100000, "cash_balance": 100000}
            if "RETURNING id" in self.query:
                return {"id": 91}
            return {}

        def fetchall(self):
            assert "symbol_canonical = ANY" in self.query
            return [{"symbol_canonical": "00700", "size": 100, "entry_price": 300}]

        def close(self):
            pass

    cur = Cursor()

    @contextmanager
    def connection():
        yield SimpleNamespace(cursor=lambda: cur, commit=lambda: None)

    monkeypatch.setattr(virtual_trading, "get_db_connection", connection)
    monkeypatch.setattr(virtual_trading, "is_equity_market_open", lambda _: True)
    monkeypatch.setattr(virtual_trading, "resolve_virtual_execution_cost_policy", lambda **kw:
                        VirtualExecutionCostPolicy("", "spot", 1, 0, 0))
    result = virtual_trading.execute_virtual_signal_order({
        "id": 14, "user_id": 5, "strategy_id": 10, "symbol": symbol,
        "execution_mode": "signal",
        "signal_type": "close_long", "amount": 100, "price": 350,
    }, {})
    assert result["status"] == "filled"
    assert result["fill_quantity"] == 100
    deletes = [params for query, params in cur.writes if query.startswith("DELETE FROM qd_strategy_virtual_positions")]
    assert deletes == [(10, "00700", "long")]
    trades = [params for query, params in cur.writes if query.startswith("INSERT INTO qd_strategy_virtual_trades")]
    assert trades[0][7] == "00700"
    assert trades[0][15] == 5000
