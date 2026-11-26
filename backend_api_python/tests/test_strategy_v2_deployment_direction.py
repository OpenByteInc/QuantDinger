import json
import sys

import pytest

from app.services.strategy_v2.contract import StrategyV2ContractError
from app.services.strategy_v2.deployment import StrategyV2DeploymentService
from app.services.strategy_v2 import deployment


SOURCE = """
def initialize(context):
    context.set_universe(["Crypto:BTC/USDT@okx:swap"])
    context.subscribe(frequency="1h")
    context.set_metadata(direction_mode="both")

def handle_data(context, data):
    pass
"""


class _Cursor:
    lastrowid = 41
    rowcount = 1

    def __init__(self):
        self.params = ()

    def execute(self, _query, params=()):
        self.params = params

    def close(self):
        return None


class _Db:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self._cursor

    def commit(self):
        return None


class _Sources:
    @staticmethod
    def get_source(_source_id, user_id=None):
        return {"id": 9, "name": "Dual strategy", "code": SOURCE}

    @staticmethod
    def get_latest_version(_source_id, user_id=None):
        return {"id": 109, "source_id": 9, "name": "Dual strategy", "code": SOURCE}


def _payload(direction_mode):
    return {
        "sourceId": 9,
        "name": "Dual strategy",
        "initialCapital": 1_000,
        "executionMode": "signal",
        "directionMode": direction_mode,
    }


def test_deployment_persists_manifest_direction_and_legacy_position_side(monkeypatch):
    cursor = _Cursor()
    monkeypatch.setattr(deployment, "get_script_source_service", lambda: _Sources())
    monkeypatch.setattr(deployment, "get_db_connection", lambda: _Db(cursor))

    strategy_id = StrategyV2DeploymentService().save(user_id=7, payload=_payload("both"))
    trading_config = json.loads(cursor.params[-2])

    assert strategy_id == 41
    assert trading_config["direction_mode"] == "both"
    assert trading_config["position_side"] == "neutral"
    assert trading_config["strategy_manifest"]["directionMode"] == "both"
    assert trading_config["script_source_version_id"] == 109
    assert cursor.params[-1] == 109


def test_deployment_persists_strategy_equity_limits(monkeypatch):
    cursor = _Cursor()
    monkeypatch.setattr(deployment, "get_script_source_service", lambda: _Sources())
    monkeypatch.setattr(deployment, "get_db_connection", lambda: _Db(cursor))
    payload = _payload("both")
    payload.update({
        "equityStopLossPct": 0.30,
        "equityTakeProfitPct": 0.50,
    })

    StrategyV2DeploymentService().save(user_id=7, payload=payload)
    trading_config = json.loads(cursor.params[-2])

    assert trading_config["equity_stop_loss_pct"] == pytest.approx(0.30)
    assert trading_config["equity_take_profit_pct"] == pytest.approx(0.50)
    assert trading_config["deployment_risk"] == {
        "basis": "initial_capital",
        "action": "close_and_stop",
        "equity_take_profit_pct": pytest.approx(0.50),
        "equity_stop_loss_pct": pytest.approx(0.30),
    }


def test_forthright_futures_deployment_validates_contract_and_persists_currency(monkeypatch):
    from types import SimpleNamespace
    from app.services import exchange_execution
    from app.services.live_trading import factory
    cursor = _Cursor()
    monkeypatch.setattr(sys.modules[__name__], "SOURCE", SOURCE.replace("Crypto:BTC/USDT@okx:swap", "Futures:HSIZ99"))
    monkeypatch.setattr(deployment, "get_script_source_service", lambda: _Sources())
    monkeypatch.setattr(deployment, "get_db_connection", lambda: _Db(cursor))
    monkeypatch.setattr(deployment, "is_trading_venue_enabled", lambda value: True)
    monkeypatch.setattr(StrategyV2DeploymentService, "_credential_exchange", staticmethod(lambda *a: "forthright"))
    monkeypatch.setattr(exchange_execution, "resolve_exchange_config", lambda *a, **k: {"currency": "HKD"})
    calls = []
    client = SimpleNamespace(validate_instrument=lambda *args: calls.append(args), disconnect=lambda: calls.append("disconnect"))
    monkeypatch.setattr(factory, "create_client", lambda *args: client)
    payload = {**_payload("both"), "executionMode": "live", "credentialId": 8}
    with pytest.raises(StrategyV2ContractError, match="liveMarketUnsupported"):
        StrategyV2DeploymentService().save(user_id=7, payload=payload)
    assert calls == []


@pytest.mark.parametrize(("field", "value"), [
    ("equityStopLossPct", -0.01),
    ("equityStopLossPct", 1.01),
    ("equityTakeProfitPct", -0.01),
    ("equityTakeProfitPct", 10.01),
    ("equityTakeProfitPct", "invalid"),
])
def test_deployment_rejects_invalid_strategy_equity_limits(monkeypatch, field, value):
    monkeypatch.setattr(deployment, "get_script_source_service", lambda: _Sources())
    payload = _payload("both")
    payload[field] = value

    with pytest.raises(StrategyV2ContractError, match="strategyV2.invalidEquityRiskLimit"):
        StrategyV2DeploymentService().save(user_id=7, payload=payload)


def test_explicit_deployment_edit_rebinds_the_latest_saved_source_version(monkeypatch):
    cursor = _Cursor()
    monkeypatch.setattr(deployment, "get_script_source_service", lambda: _Sources())
    monkeypatch.setattr(deployment, "get_db_connection", lambda: _Db(cursor))

    strategy_id = StrategyV2DeploymentService().save(
        user_id=7,
        payload=_payload("both"),
        strategy_id=73,
    )

    trading_config = json.loads(cursor.params[-4])
    assert strategy_id == 73
    assert trading_config["script_source_version_id"] == 109
    assert cursor.params[-3:] == (109, 73, 7)


def test_deployment_persists_one_way_without_legacy_position_side(monkeypatch):
    cursor = _Cursor()

    class _OneWaySources:
        @staticmethod
        def get_source(_source_id, user_id=None):
            return {
                "id": 9,
                "name": "Net strategy",
                "code": SOURCE.replace('direction_mode="both"', 'direction_mode="one_way"'),
            }

        @staticmethod
        def get_latest_version(_source_id, user_id=None):
            source = _OneWaySources.get_source(_source_id, user_id=user_id)
            return {**source, "id": 110, "source_id": 9}

    monkeypatch.setattr(deployment, "get_script_source_service", lambda: _OneWaySources())
    monkeypatch.setattr(deployment, "get_db_connection", lambda: _Db(cursor))

    StrategyV2DeploymentService().save(user_id=7, payload=_payload("one_way"))
    trading_config = json.loads(cursor.params[-2])

    assert trading_config["direction_mode"] == "one_way"
    assert trading_config["position_side"] == ""


def test_deployment_rejects_direction_override_that_conflicts_with_manifest(monkeypatch):
    monkeypatch.setattr(deployment, "get_script_source_service", lambda: _Sources())

    with pytest.raises(StrategyV2ContractError, match="strategyV2.directionModeMismatch"):
        StrategyV2DeploymentService().save(user_id=7, payload=_payload("long_only"))


def test_deployment_recovers_legacy_visual_grid_runtime_from_executor_type(monkeypatch):
    class _GridSources:
        @staticmethod
        def get_source(_source_id, user_id=None):
            return {
                "id": 9,
                "name": "Grid strategy",
                "code": SOURCE,
                "metadata": {
                    "last_run_config": {
                        "strategy_family": "robot",
                        "executor_type": "grid",
                        "executor_config": {"grid_count": 4},
                        "bot_params": {
                            "gridCount": 4,
                            "gridCountUnit": "cells",
                            "amountPerGrid": 2,
                        },
                    },
                },
            }

        @staticmethod
        def get_latest_version(_source_id, user_id=None):
            source = _GridSources.get_source(_source_id, user_id=user_id)
            return {**source, "id": 111, "source_id": 9}

    cursor = _Cursor()
    monkeypatch.setattr(deployment, "get_script_source_service", lambda: _GridSources())
    monkeypatch.setattr(deployment, "get_db_connection", lambda: _Db(cursor))

    StrategyV2DeploymentService().save(user_id=7, payload=_payload("both"))
    trading_config = json.loads(cursor.params[-2])

    assert trading_config["bot_type"] == "grid"
    assert trading_config["executor_type"] == "grid"
    assert trading_config["bot_params"]["amountPerGridPct"] == pytest.approx(0.25)


def test_deployment_manifest_trend_contract_removes_stale_grid_runtime(monkeypatch):
    trend_source = SOURCE.replace(
        'context.set_metadata(direction_mode="both")',
        'context.set_metadata(direction_mode="both", strategy_family="trend")',
    )

    class _TrendSources:
        @staticmethod
        def get_source(_source_id, user_id=None):
            return {
                "id": 9,
                "name": "Trend strategy",
                "code": trend_source,
                "metadata": {
                    "last_run_config": {
                        "strategy_family": "robot",
                        "executor_type": "grid",
                        "executor_config": {"grid_count": 20},
                        "bot_type": "grid",
                        "bot_params": {
                            "gridCount": 20,
                            "lowerPrice": 0.98,
                            "upperPrice": 1.02,
                        },
                    },
                },
            }

        @staticmethod
        def get_latest_version(_source_id, user_id=None):
            source = _TrendSources.get_source(_source_id, user_id=user_id)
            return {**source, "id": 114, "source_id": 9}

    cursor = _Cursor()
    monkeypatch.setattr(deployment, "get_script_source_service", lambda: _TrendSources())
    monkeypatch.setattr(deployment, "get_db_connection", lambda: _Db(cursor))

    StrategyV2DeploymentService().save(user_id=7, payload=_payload("both"))
    trading_config = json.loads(cursor.params[-2])

    assert trading_config["strategy_family"] == "trend"
    assert "executor_type" not in trading_config
    assert "executor_config" not in trading_config
    assert "bot_type" not in trading_config
    assert "bot_params" not in trading_config


def test_deployment_recovers_grid_contract_from_legacy_root_metadata(monkeypatch):
    class _LegacyGridSources:
        @staticmethod
        def get_source(_source_id, user_id=None):
            return {
                "id": 9,
                "name": "Legacy grid strategy",
                "code": SOURCE,
                "metadata": {
                    "executor_type": "grid",
                    "executor_config": {
                        "side": "long",
                        "start_price": 90,
                        "end_price": 110,
                        "grid_count": 5,
                        "total_amount_quote": 500,
                        "initial_position_pct": 0.6,
                        "max_open_orders": 4,
                        "dynamic_anchor": True,
                    },
                },
            }

        @staticmethod
        def get_latest_version(_source_id, user_id=None):
            source = _LegacyGridSources.get_source(_source_id, user_id=user_id)
            return {**source, "id": 112, "source_id": 9}

    cursor = _Cursor()
    monkeypatch.setattr(deployment, "get_script_source_service", lambda: _LegacyGridSources())
    monkeypatch.setattr(deployment, "get_db_connection", lambda: _Db(cursor))

    StrategyV2DeploymentService().save(user_id=7, payload=_payload("both"))
    trading_config = json.loads(cursor.params[-2])

    assert trading_config["strategy_family"] == "robot"
    assert trading_config["executor_type"] == "grid"
    assert trading_config["bot_type"] == "grid"
    assert trading_config["entry_trigger_mode"] == "exchange_resting_orders"
    assert trading_config["bot_params"]["gridCount"] == 5
    assert trading_config["bot_params"]["lowerPrice"] == pytest.approx(90)
    assert trading_config["bot_params"]["upperPrice"] == pytest.approx(110)
    assert trading_config["bot_params"]["amountPerGridPct"] == pytest.approx(0.2)


def test_deployment_manifest_overrides_stale_editor_symbol_and_market_type(monkeypatch):
    stock_source = """
def initialize(context):
    context.set_universe(["USStock:SPY"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    pass
"""

    class _StockSources:
        @staticmethod
        def get_source(_source_id, user_id=None):
            return {
                "id": 9,
                "name": "Stock strategy",
                "code": stock_source,
                "metadata": {
                    "last_run_config": {
                        "symbol": "BTC/USDT",
                        "market_type": "swap",
                    },
                },
            }

        @staticmethod
        def get_latest_version(_source_id, user_id=None):
            source = _StockSources.get_source(_source_id, user_id=user_id)
            return {**source, "id": 113, "source_id": 9}

    cursor = _Cursor()
    monkeypatch.setattr(deployment, "get_script_source_service", lambda: _StockSources())
    monkeypatch.setattr(deployment, "get_db_connection", lambda: _Db(cursor))

    StrategyV2DeploymentService().save(user_id=7, payload=_payload(""))
    trading_config = json.loads(cursor.params[-2])

    assert trading_config["symbol"] == "SPY"
    assert trading_config["market_type"] == "spot"
