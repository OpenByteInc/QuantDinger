"""Tests for app.services.broker_market_policy.

This is the single source of truth for which broker x market x market_type
combinations QuantDinger accepts. Every test here is paired against either:

  - a UI control in views/trading-assistant/index.vue or
    views/trading-bot/components/BotCreateWizard.vue, or
  - a runtime guard in pending_order_worker._execute_live_order, or
  - a CRUD validator in app/services/strategy.py.

If you change any of those rules, update both the policy module and these
tests so the matrix stays consistent across all 4 layers.
"""
import pytest

from app.services.broker_market_policy import (
    BROKER_MARKETS,
    LONG_ONLY_BROKERS,
    BOT_TYPE_MARKETS,
    LIVE_MARKET_CATEGORIES,
    allowed_bot_types,
    allowed_market_types,
    is_compatible_credential,
    is_long_only_broker,
    list_supported_brokers_for_market,
    to_dict,
    validate_strategy_config,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_is_long_only_broker_truthy(self):
        assert is_long_only_broker("ibkr") is True
        assert is_long_only_broker("alpaca") is True
        assert is_long_only_broker("ALPACA") is True  # case-insensitive
        assert is_long_only_broker("  ibkr  ") is True  # whitespace tolerant

    def test_is_long_only_broker_falsy(self):
        assert is_long_only_broker("binance") is False
        assert is_long_only_broker("mt5") is False
        assert is_long_only_broker("") is False
        assert is_long_only_broker(None) is False

    def test_is_compatible_credential_known(self):
        assert is_compatible_credential("ibkr", "USStock") is True
        assert is_compatible_credential("mt5", "Forex") is True
        assert is_compatible_credential("alpaca", "Crypto") is True
        assert is_compatible_credential("alpaca", "USStock") is True
        assert is_compatible_credential("binance", "Crypto") is True

    def test_is_compatible_credential_mismatch(self):
        assert is_compatible_credential("ibkr", "Crypto") is False
        assert is_compatible_credential("mt5", "USStock") is False
        assert is_compatible_credential("binance", "Forex") is False

    def test_is_compatible_credential_unknown_broker(self):
        assert is_compatible_credential("randomexchange", "Crypto") is False
        assert is_compatible_credential("", "Crypto") is False
        assert is_compatible_credential(None, "Crypto") is False

    def test_allowed_market_types_crypto_perp_exchange(self):
        # Major perp exchanges support both spot and swap.
        assert allowed_market_types("binance", "Crypto") == {"spot", "swap"}
        assert allowed_market_types("okx", "Crypto") == {"spot", "swap"}
        assert allowed_market_types("bybit", "Crypto") == {"spot", "swap"}

    def test_allowed_market_types_spot_only(self):
        # Coinbase Exchange institutional + Alpaca crypto + IBKR + MT5 are spot-only.
        assert allowed_market_types("coinbaseexchange", "Crypto") == {"spot"}
        assert allowed_market_types("alpaca", "Crypto") == {"spot"}
        assert allowed_market_types("alpaca", "USStock") == {"spot"}
        assert allowed_market_types("ibkr", "USStock") == {"spot"}
        assert allowed_market_types("mt5", "Forex") == {"spot"}

    def test_allowed_market_types_invalid_combo(self):
        # Returns empty set rather than raising — caller decides how to react.
        assert allowed_market_types("ibkr", "Crypto") == set()
        assert allowed_market_types("mt5", "USStock") == set()

    def test_allowed_bot_types_per_market(self):
        # Crypto: every bot type is supported.
        assert allowed_bot_types("Crypto") == {"grid", "martingale", "dca", "trend"}
        # Forex: no martingale (gap risk), no perp shorts.
        assert allowed_bot_types("Forex") == {"grid", "dca", "trend"}
        # USStock: no grid (overnight gaps), no martingale.
        assert allowed_bot_types("USStock") == {"dca", "trend"}

    def test_list_supported_brokers_for_market(self):
        usstock_brokers = list_supported_brokers_for_market("USStock")
        assert "ibkr" in usstock_brokers
        assert "alpaca" in usstock_brokers
        assert "binance" not in usstock_brokers
        assert list_supported_brokers_for_market("Forex") == {"mt5"}


# ---------------------------------------------------------------------------
# validate_strategy_config — happy paths
# ---------------------------------------------------------------------------

class TestValidateLegalCombos:
    """All of these must NOT raise — they are real combinations users build."""

    @pytest.mark.parametrize("exchange_id,market_category,market_type,trade_direction", [
        # Crypto perp exchanges
        ("binance", "Crypto", "swap", "long"),
        ("binance", "Crypto", "swap", "short"),
        ("binance", "Crypto", "swap", "both"),
        ("binance", "Crypto", "spot", "long"),
        ("okx", "Crypto", "swap", "short"),
        ("bybit", "Crypto", "spot", "long"),
        ("bitget", "Crypto", "swap", "both"),
        # Crypto spot-only exchanges
        ("coinbaseexchange", "Crypto", "spot", "long"),
        ("kraken", "Crypto", "spot", "long"),
        # Alpaca dual desk
        ("alpaca", "USStock", "spot", "long"),
        ("alpaca", "Crypto", "spot", "long"),
        # IBKR US stocks
        ("ibkr", "USStock", "spot", "long"),
        # MT5 Forex (mt5 + spot is how we store CFDs internally)
        ("mt5", "Forex", "spot", "long"),
        ("mt5", "Forex", "spot", "short"),  # MT5 can short forex via SELL
        ("mt5", "Forex", "spot", "both"),
    ])
    def test_valid_strategy_combo_raises_nothing(
        self, exchange_id, market_category, market_type, trade_direction
    ):
        validate_strategy_config(
            exchange_id=exchange_id,
            market_category=market_category,
            market_type=market_type,
            trade_direction=trade_direction,
        )

    def test_signal_mode_does_not_require_exchange(self):
        # Signal-only strategies skip broker / direction checks.
        validate_strategy_config(
            exchange_id=None,
            market_category="Crypto",
            market_type="swap",
            trade_direction="short",
            require_exchange=False,
        )

    def test_market_type_alias_futures_normalized_to_swap(self):
        # 'futures' / 'perp' / 'perpetual' should be accepted as 'swap' aliases.
        validate_strategy_config(
            exchange_id="binance",
            market_category="Crypto",
            market_type="futures",
            trade_direction="short",
        )
        validate_strategy_config(
            exchange_id="okx",
            market_category="Crypto",
            market_type="perpetual",
            trade_direction="both",
        )


# ---------------------------------------------------------------------------
# validate_strategy_config — illegal combos
# ---------------------------------------------------------------------------

class TestValidateIllegalCombos:

    def test_unknown_market_category(self):
        with pytest.raises(ValueError, match="not supported for live trading"):
            validate_strategy_config(
                exchange_id="binance",
                market_category="CNStock",
                market_type="spot",
            )

    def test_missing_exchange_when_required(self):
        with pytest.raises(ValueError, match="exchange_id is required"):
            validate_strategy_config(
                exchange_id="",
                market_category="Crypto",
                require_exchange=True,
            )

    def test_unknown_exchange(self):
        with pytest.raises(ValueError, match="Unknown exchange_id"):
            validate_strategy_config(
                exchange_id="unsupportedfutures",  # not in our matrix
                market_category="Crypto",
                market_type="spot",
            )

    def test_ibkr_cannot_trade_crypto(self):
        with pytest.raises(ValueError, match="IBKR cannot trade"):
            validate_strategy_config(
                exchange_id="ibkr",
                market_category="Crypto",
                market_type="spot",
                trade_direction="long",
            )

    def test_mt5_cannot_trade_us_stocks(self):
        with pytest.raises(ValueError, match="MT5 cannot trade"):
            validate_strategy_config(
                exchange_id="mt5",
                market_category="USStock",
                market_type="spot",
                trade_direction="long",
            )

    def test_binance_cannot_trade_forex(self):
        with pytest.raises(ValueError, match="BINANCE cannot trade"):
            validate_strategy_config(
                exchange_id="binance",
                market_category="Forex",
                market_type="spot",
            )

    def test_alpaca_crypto_swap_has_helpful_error(self):
        # The most common confusion - error must explain why and how to fix.
        with pytest.raises(ValueError) as excinfo:
            validate_strategy_config(
                exchange_id="alpaca",
                market_category="Crypto",
                market_type="swap",
                trade_direction="long",
            )
        msg = str(excinfo.value)
        assert "Alpaca crypto desk is spot-only" in msg
        assert "Binance/OKX/Bybit" in msg

    def test_coinbase_crypto_swap_rejected(self):
        with pytest.raises(ValueError, match="does not support market_type='swap'"):
            validate_strategy_config(
                exchange_id="coinbaseexchange",
                market_category="Crypto",
                market_type="swap",
            )

    def test_ibkr_short_rejected(self):
        with pytest.raises(ValueError, match="long-only"):
            validate_strategy_config(
                exchange_id="ibkr",
                market_category="USStock",
                market_type="spot",
                trade_direction="short",
            )

    def test_alpaca_short_rejected(self):
        with pytest.raises(ValueError, match="long-only"):
            validate_strategy_config(
                exchange_id="alpaca",
                market_category="USStock",
                market_type="spot",
                trade_direction="both",
            )

    def test_crypto_short_on_spot_rejected(self):
        # Even on a perp exchange, asking for short while staying on crypto
        # spot must be rejected (no spot shorts in crypto).
        with pytest.raises(ValueError, match="Short selling crypto requires market_type='swap'"):
            validate_strategy_config(
                exchange_id="binance",
                market_category="Crypto",
                market_type="spot",
                trade_direction="short",
            )


# ---------------------------------------------------------------------------
# bot_type compatibility
# ---------------------------------------------------------------------------

class TestBotTypeRules:

    @pytest.mark.parametrize("bot_type,market_category", [
        ("grid", "Crypto"),
        ("grid", "Forex"),
        ("martingale", "Crypto"),
        ("dca", "Crypto"),
        ("dca", "USStock"),
        ("dca", "Forex"),
        ("trend", "USStock"),
        ("trend", "Forex"),
    ])
    def test_valid_bot_market_pair(self, bot_type, market_category):
        # Combine with a broker that supports the market.
        broker = next(iter(list_supported_brokers_for_market(market_category)))
        # Pick a market_type the broker actually supports.
        mt = next(iter(allowed_market_types(broker, market_category)))
        # Pick a direction long-only brokers will accept.
        direction = "long"
        validate_strategy_config(
            exchange_id=broker,
            market_category=market_category,
            market_type=mt,
            trade_direction=direction,
            bot_type=bot_type,
        )

    def test_grid_on_us_stocks_rejected(self):
        # Grid bots can't survive the 16h overnight gap between sessions.
        with pytest.raises(ValueError, match="bot_type='grid' cannot run"):
            validate_strategy_config(
                exchange_id="ibkr",
                market_category="USStock",
                market_type="spot",
                trade_direction="long",
                bot_type="grid",
            )

    def test_martingale_on_forex_rejected(self):
        # Martingale on forex spreads quickly hits margin call territory.
        with pytest.raises(ValueError, match="bot_type='martingale' cannot run"):
            validate_strategy_config(
                exchange_id="mt5",
                market_category="Forex",
                market_type="spot",
                trade_direction="long",
                bot_type="martingale",
            )

    def test_unknown_bot_type_silently_allowed(self):
        # Unknown bot types (e.g. legacy 'arbitrage' on existing strategies)
        # must not block updates. Only known bot types get strict market
        # compatibility enforcement; everything else falls through.
        validate_strategy_config(
            exchange_id="binance",
            market_category="Crypto",
            market_type="swap",
            trade_direction="long",
            bot_type="arbitrage",
        )
        validate_strategy_config(
            exchange_id="binance",
            market_category="Crypto",
            market_type="swap",
            trade_direction="long",
            bot_type="custom_user_strategy",
        )


# ---------------------------------------------------------------------------
# Snapshot serialization (consumed by GET /api/policy/broker-market)
# ---------------------------------------------------------------------------

class TestToDictSnapshot:
    def test_all_top_level_keys_present(self):
        snapshot = to_dict()
        assert set(snapshot.keys()) == {
            "broker_markets",
            "long_only_brokers",
            "bot_type_markets",
            "live_market_categories",
        }

    def test_market_types_serialize_as_sorted_lists(self):
        snapshot = to_dict()
        bm = snapshot["broker_markets"]
        # JSON-serializable: every leaf must be a list, not a set.
        assert isinstance(bm["binance"]["Crypto"], list)
        assert bm["binance"]["Crypto"] == ["spot", "swap"]
        assert bm["alpaca"]["Crypto"] == ["spot"]

    def test_long_only_brokers_serialized(self):
        assert sorted(to_dict()["long_only_brokers"]) == ["alpaca", "ibkr"]

    def test_bot_type_markets_serialized(self):
        bot_markets = to_dict()["bot_type_markets"]
        assert sorted(bot_markets["grid"]) == ["Crypto", "Forex"]
        assert sorted(bot_markets["martingale"]) == ["Crypto"]

    def test_live_market_categories_serialized(self):
        assert sorted(to_dict()["live_market_categories"]) == ["Crypto", "Forex", "USStock"]

    def test_matrix_internal_consistency(self):
        # Every long-only broker must be present in BROKER_MARKETS.
        for broker in LONG_ONLY_BROKERS:
            assert broker in BROKER_MARKETS, f"long-only broker {broker} missing from matrix"

        # Every bot's market list must be a subset of LIVE_MARKET_CATEGORIES.
        for bot, markets in BOT_TYPE_MARKETS.items():
            assert markets.issubset(LIVE_MARKET_CATEGORIES), (
                f"bot {bot} references unknown market: {markets - LIVE_MARKET_CATEGORIES}"
            )


# ---------------------------------------------------------------------------
# /api/policy/broker-market endpoint smoke test
# ---------------------------------------------------------------------------

class TestPolicyEndpoint:
    def test_get_broker_market_returns_full_snapshot(self, client):
        resp = client.get("/api/policy/broker-market")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["code"] == 1
        data = body["data"]
        # Smoke-check the structure the frontend store expects.
        assert "broker_markets" in data
        assert "long_only_brokers" in data
        assert "bot_type_markets" in data
        assert data["broker_markets"]["binance"]["Crypto"] == ["spot", "swap"]
        assert data["broker_markets"]["alpaca"]["Crypto"] == ["spot"]
        assert "alpaca" in data["long_only_brokers"]
