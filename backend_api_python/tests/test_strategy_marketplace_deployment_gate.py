import pytest

from app.routes import script_source_routes
from app.services.strategy_v2 import deployment
from app.services.strategy_v2 import storage
from app.services.strategy_v2.contract import StrategyV2ContractError
from app.services.strategy_v2.deployment import StrategyV2DeploymentService
from app.services.strategy_v2.storage import StrategyBacktestRepository


SOURCE = '''
def initialize(context):
    context.set_universe(["USStock:MSFT"])
    context.subscribe(frequency="1d")

def handle_data(context, data):
    pass
'''


class _AdaptedSources:
    @staticmethod
    def get_source(_source_id, user_id=None):
        return {
            'id': 31,
            'name': 'Adapted MSFT strategy',
            'code': SOURCE,
            'metadata': {
                'marketplace_adaptation': {
                    'indicator_id': 8,
                    'target_instrument': 'USStock:MSFT',
                    'requires_backtest': True,
                },
            },
        }

    @staticmethod
    def get_latest_version(_source_id, user_id=None):
        source = _AdaptedSources.get_source(_source_id, user_id=user_id)
        return {**source, 'id': 131, 'source_id': 31}


def test_adapted_strategy_deployment_is_blocked_without_current_backtest(monkeypatch):
    monkeypatch.setattr(deployment, 'get_script_source_service', lambda: _AdaptedSources())
    monkeypatch.setattr(
        StrategyV2DeploymentService,
        '_has_current_version_backtest',
        staticmethod(lambda **_kwargs: False),
    )

    with pytest.raises(StrategyV2ContractError, match='strategyV2.backtestRequiredForAdaptedStrategy'):
        StrategyV2DeploymentService().save(
            user_id=7,
            payload={
                'sourceId': 31,
                'name': 'Adapted MSFT strategy',
                'initialCapital': 10_000,
                'executionMode': 'signal',
            },
        )


class _BacktestCursor:
    def __init__(self, found):
        self.found = found
        self.params = None

    def execute(self, _sql, params=()):
        self.params = tuple(params)

    def fetchone(self):
        return {'id': 1} if self.found else None

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


@pytest.mark.parametrize('found, expected', [(False, False), (True, True)])
def test_current_source_version_backtest_lookup(monkeypatch, found, expected):
    cursor = _BacktestCursor(found)
    monkeypatch.setattr(deployment, 'get_db_connection', lambda: _Db(cursor))

    result = StrategyV2DeploymentService._has_current_version_backtest(
        user_id=7,
        source_id=31,
        code_hash='abc123',
    )

    assert result is expected
    assert cursor.params == (7, 31, 'abc123')


@pytest.mark.parametrize('found, expected', [(False, False), (True, True)])
def test_publish_readiness_lookup_is_bound_to_source_and_code(monkeypatch, found, expected):
    cursor = _BacktestCursor(found)
    monkeypatch.setattr(storage, 'get_db_connection', lambda: _Db(cursor))

    result = StrategyBacktestRepository().has_successful_run(
        user_id=7,
        source_id=27,
        code_hash='current-code-hash',
    )

    assert result is expected
    assert cursor.params == (7, 27, 'current-code-hash')


def test_publish_gate_hashes_the_current_source_code(monkeypatch):
    captured = {}

    class _Repository:
        @staticmethod
        def has_successful_run(**kwargs):
            captured.update(kwargs)
            return True

    monkeypatch.setattr(script_source_routes, 'StrategyBacktestRepository', _Repository)

    assert script_source_routes._has_successful_script_backtest(7, 27, 'print("current")') is True
    assert captured['user_id'] == 7
    assert captured['source_id'] == 27
    assert captured['code_hash'] == '30f859eebed5252de0f02a62779f5e27b2c2add6f8c5cf66dbc3dc949d3e18bf'


def test_publish_gate_uses_the_same_fingerprint_as_backtest_compilation(monkeypatch):
    captured = {}

    class _Repository:
        @staticmethod
        def has_successful_run(**kwargs):
            captured.update(kwargs)
            return True

    monkeypatch.setattr(script_source_routes, 'StrategyBacktestRepository', _Repository)

    source_with_editor_whitespace = f'\n{SOURCE}\n\n'
    assert script_source_routes._has_successful_script_backtest(
        7,
        27,
        source_with_editor_whitespace,
    ) is True
    assert captured['code_hash'] == script_source_routes.inspect_strategy_v2(
        source_with_editor_whitespace,
    ).manifest.code_hash
