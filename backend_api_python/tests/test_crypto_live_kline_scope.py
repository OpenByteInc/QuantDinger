"""Live crypto K-line routing: exchange + spot/swap must match execution venue."""
from app.data_sources.crypto import resolve_ccxt_for_live_trading, CryptoDataSource
from app.services.trading_executor import TradingExecutor


def test_resolve_binance_swap_uses_binanceusdm():
    ccxt_id, opts = resolve_ccxt_for_live_trading("binance", "swap")
    assert ccxt_id == "binanceusdm"
    assert opts == {}


def test_resolve_binance_spot_uses_binance():
    ccxt_id, opts = resolve_ccxt_for_live_trading("binance", "spot")
    assert ccxt_id == "binance"
    assert opts == {}


def test_resolve_okx_swap_sets_default_type():
    ccxt_id, opts = resolve_ccxt_for_live_trading("okx", "swap")
    assert ccxt_id == "okx"
    assert opts.get("defaultType") == "swap"


def test_resolve_bybit_swap_linear():
    ccxt_id, opts = resolve_ccxt_for_live_trading("bybit", "swap")
    assert ccxt_id == "bybit"
    assert opts.get("defaultType") == "linear"


def test_symbol_for_scoped_swap_appends_settle_suffix():
    ds = CryptoDataSource.for_exchange("binance", "swap")
    assert ds._symbol_for_scoped_market("BTC/USDT") == "BTC/USDT:USDT"


def test_symbol_for_scoped_spot_unchanged():
    ds = CryptoDataSource.for_exchange("binance", "spot")
    assert ds._symbol_for_scoped_market("BTC/USDT") == "BTC/USDT"


def test_live_crypto_kline_params_only_for_live_crypto():
    ex, mt = TradingExecutor._live_crypto_kline_params(
        market_category="Crypto",
        market_type="swap",
        execution_mode="live",
        exchange_config={"exchange_id": "gate"},
    )
    assert ex == "gate"
    assert mt == "swap"


def test_live_crypto_kline_params_signal_mode_uses_bound_exchange():
    ex, mt = TradingExecutor._live_crypto_kline_params(
        market_category="Crypto",
        market_type="swap",
        execution_mode="signal",
        exchange_config={"exchange_id": "binance"},
    )
    assert ex == "binance"
    assert mt == "swap"


def test_live_crypto_kline_params_signal_reads_exchange_from_trading_config():
    ex, mt = TradingExecutor._live_crypto_kline_params(
        market_category="Crypto",
        market_type="spot",
        execution_mode="signal",
        exchange_config={},
        trading_config={"exchange_id": "okx"},
    )
    assert ex == "okx"
    assert mt == "spot"


def test_live_crypto_kline_params_unknown_mode_uses_global():
    ex, mt = TradingExecutor._live_crypto_kline_params(
        market_category="Crypto",
        market_type="swap",
        execution_mode="paper",
        exchange_config={"exchange_id": "binance"},
    )
    assert ex is None
    assert mt is None


def test_live_crypto_kline_params_usstock_unchanged():
    ex, mt = TradingExecutor._live_crypto_kline_params(
        market_category="USStock",
        market_type="spot",
        execution_mode="live",
        exchange_config={"exchange_id": "binance"},
    )
    assert ex is None
    assert mt is None


def test_live_crypto_kline_params_resolves_credential_id(monkeypatch):
    def _fake_resolve(cfg, user_id=1):
        assert user_id == 42
        assert cfg.get("credential_id") == 7
        return {"exchange_id": "okx", "api_key": "k", "secret_key": "s", "passphrase": "p"}

    monkeypatch.setattr(
        "app.services.exchange_execution.resolve_exchange_config",
        _fake_resolve,
    )
    ex, mt = TradingExecutor._live_crypto_kline_params(
        market_category="Crypto",
        market_type="swap",
        execution_mode="live",
        exchange_config={"credential_id": 7},
        user_id=42,
    )
    assert ex == "okx"
    assert mt == "swap"
