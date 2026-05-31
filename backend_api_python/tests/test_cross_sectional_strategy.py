"""Cross-sectional strategy: config normalization, signal generation, indicator sandbox."""

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from app.services.strategy import (
    _apply_cross_sectional_trading_config,
    _normalize_cross_sectional_symbol_list,
)
from app.services.trading_executor import TradingExecutor


def _fake_ohlcv(n: int = 30) -> list:
    base = 100.0
    rows = []
    for i in range(n):
        c = base + i * 0.5
        rows.append(
            {
                "time": 1_700_000_000 + i * 3600,
                "open": c - 0.2,
                "high": c + 0.5,
                "low": c - 0.5,
                "close": c,
                "volume": 1000.0,
            }
        )
    return rows


class TestCrossSectionalConfig:
    def test_normalize_symbol_list_crypto(self):
        out = _normalize_cross_sectional_symbol_list(
            ["Crypto:BTCUSDT", "Crypto:ETH/USDT"],
            "Crypto",
        )
        assert out == ["Crypto:BTC/USDT", "Crypto:ETH/USDT"]

    def test_apply_config_requires_two_symbols(self):
        with pytest.raises(ValueError, match="at least 2"):
            _apply_cross_sectional_trading_config(
                {},
                cs_strategy_type="cross_sectional",
                symbol_list=["Crypto:BTC/USDT"],
                portfolio_size=1,
                long_ratio=1.0,
                rebalance_frequency="daily",
                market_category="Crypto",
                market_type="swap",
            )

    def test_apply_config_spot_long_only(self):
        tc = _apply_cross_sectional_trading_config(
            {},
            cs_strategy_type="cross_sectional",
            symbol_list=["Crypto:BTC/USDT", "Crypto:ETH/USDT"],
            portfolio_size=2,
            long_ratio=0.5,
            rebalance_frequency="weekly",
            market_category="Crypto",
            market_type="spot",
        )
        assert tc["long_ratio"] == 1.0
        assert tc["symbol"] == "BTC/USDT"
        assert len(tc["symbol_list"]) == 2


class TestCrossSectionalSignals:
    def setup_method(self):
        self.executor = TradingExecutor()

    def test_generate_open_long_for_top_ranked(self):
        rankings = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]
        scores = {s: float(i) for i, s in enumerate(rankings)}
        trading_config = {
            "portfolio_size": 2,
            "long_ratio": 1.0,
            "market_type": "swap",
        }
        with patch.object(self.executor, "_get_all_positions", return_value=[]):
            signals = self.executor._generate_cross_sectional_signals(
                1, rankings, scores, trading_config
            )
        types = [s["type"] for s in signals]
        assert "open_long" in types
        open_syms = [s["symbol"] for s in signals if s["type"] == "open_long"]
        assert set(open_syms) == {"BTC/USDT", "ETH/USDT"}
        for s in signals:
            if s["type"] == "open_long":
                assert s["position_size"] == pytest.approx(0.5)

    def test_spot_skips_short_legs(self):
        rankings = ["A/USDT", "B/USDT", "C/USDT", "D/USDT"]
        scores = {s: 1.0 for s in rankings}
        trading_config = {
            "portfolio_size": 4,
            "long_ratio": 0.5,
            "market_type": "spot",
        }
        with patch.object(self.executor, "_get_all_positions", return_value=[]):
            signals = self.executor._generate_cross_sectional_signals(
                99, rankings, scores, trading_config
            )
        assert not any(s["type"] == "open_short" for s in signals)

    def test_position_key_matches_bare_symbol(self):
        rankings = ["BTC/USDT", "ETH/USDT"]
        scores = {"BTC/USDT": 2.0, "ETH/USDT": 1.0}
        trading_config = {"portfolio_size": 1, "long_ratio": 1.0, "market_type": "swap"}
        positions = [{"symbol": "ETH/USDT", "side": "long", "size": 1.0}]
        with patch.object(self.executor, "_get_all_positions", return_value=positions):
            signals = self.executor._generate_cross_sectional_signals(
                1, rankings, scores, trading_config
            )
        assert not any(s["type"] == "open_long" and s["symbol"] == "ETH/USDT" for s in signals)
        assert any(s["type"] == "open_long" and s["symbol"] == "BTC/USDT" for s in signals)


class TestCrossSectionalIndicator:
    def test_indicator_scores_produce_rankings(self):
        executor = TradingExecutor()
        code = """
scores = {}
for symbol, df in data.items():
    scores[symbol] = float(df['close'].iloc[-1])
"""
        symbols = ["BTC/USDT", "ETH/USDT"]
        with patch.object(
            executor,
            "_fetch_latest_kline",
            side_effect=lambda sym, *a, **k: _fake_ohlcv(),
        ):
            result = executor._execute_cross_sectional_indicator(
                code,
                symbols,
                {},
                "Crypto",
                "1H",
            )
        assert result is not None
        assert set(result["scores"].keys()) == set(symbols)
        assert set(result["rankings"]) == set(symbols)
        assert len(result["rankings"]) == 2


class TestCrossSectionalRouting:
    def test_cs_bare_symbol(self):
        assert TradingExecutor._cs_bare_symbol("Crypto:BTC/USDT") == "BTC/USDT"

    def test_normalize_cs_symbol_list(self):
        out = TradingExecutor._normalize_cs_symbol_list(
            ["Crypto:BTC/USDT", "ETH/USDT"],
            "Crypto",
        )
        assert out == ["BTC/USDT", "ETH/USDT"]
