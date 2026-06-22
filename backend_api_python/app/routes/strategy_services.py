"""Shared lazy service accessors for strategy route modules."""
from app.services.backtest import BacktestService
from app.services.strategy import StrategyService


_strategy_service: StrategyService | None = None
_backtest_service: BacktestService | None = None


def get_strategy_service() -> StrategyService:
    global _strategy_service
    if _strategy_service is None:
        _strategy_service = StrategyService()
    return _strategy_service


def get_backtest_service() -> BacktestService:
    global _backtest_service
    if _backtest_service is None:
        _backtest_service = BacktestService()
    return _backtest_service
