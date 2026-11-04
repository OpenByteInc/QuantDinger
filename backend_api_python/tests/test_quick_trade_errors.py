"""Tests for quick trade exchange error normalization."""

from app.services.quick_trade.errors import (
    exchange_error_user_message,
    merge_balance_leg_errors,
    parse_trade_error_hint,
)
from app.services.quick_trade.symbols import is_supported_crypto_exchange, symbols_match


def test_parse_trade_error_hint_for_insufficient_balance():
    assert (
        parse_trade_error_hint("insufficient balance for order")
        == "quickTrade.errorHints.insufficientBalance"
    )


def test_exchange_error_user_message_extracts_bitget_ip_whitelist_error():
    meta = exchange_error_user_message(
        exchange_id="bitget",
        err="40018 Invalid IP. Current request IP 203.0.113.9",
    )

    assert meta["hint_key"] == "quickTrade.errorHints.ipWhitelist"
    assert meta["request_ip"] == "203.0.113.9"
    assert "Bitget rejected" in meta["message"]


def test_merge_balance_leg_errors_prefers_first_leg_error():
    meta = merge_balance_leg_errors(
        {"error": "not enough balance"},
        {"error": "rate limit exceeded"},
        exchange_id="okx",
    )

    assert meta["error_hint_key"] == "quickTrade.errorHints.insufficientBalance"
    assert len(meta["errors"]) == 2
    assert meta["error"] == meta["errors"][0]


def test_okx_spot_account_mode_error_is_actionable():
    meta = exchange_error_user_message(
        exchange_id="okx",
        err="OKX_SWAP_ACCOUNT_MODE_REQUIRED",
    )

    assert meta["hint_key"] == "quickTrade.errorHints.okxAccountMode"


def test_http_451_is_reported_as_a_region_restriction():
    assert (
        parse_trade_error_hint("HTTP 451 Service unavailable from a restricted location")
        == "quickTrade.errorHints.regionRestricted"
    )


def test_binance_tradfi_agreement_error_is_not_misclassified_as_rate_limit():
    error = (
        "binanceusdm {\"code\":-4411,\"msg\":\"Please sign TradFi-Perps agreement contract fapi.\","
        "\"rateLimits\":[{\"rateLimitType\":\"ORDERS\"}]}"
    )

    assert (
        parse_trade_error_hint(error)
        == "quickTrade.errorHints.binanceTradFiAgreement"
    )


def test_rate_limit_metadata_alone_is_not_a_rate_limit_error():
    assert parse_trade_error_hint('{"rateLimits":[{"rateLimitType":"ORDERS"}]}') == ""


def test_actual_rate_limit_error_is_still_recognized():
    assert (
        parse_trade_error_hint("HTTP 429 Too many requests")
        == "quickTrade.errorHints.rateLimit"
    )


def test_symbols_match_exchange_native_suffixes():
    assert symbols_match("ETH/USDT", "ETH-USDT-SWAP")
    assert symbols_match("BTC/USDT", "BTC_USDT_PERP")
    assert not symbols_match("ETH/USDT", "ETHW-USDT-SWAP")


def test_supported_crypto_exchange_policy():
    assert is_supported_crypto_exchange("okx")
    assert not is_supported_crypto_exchange("alpaca")
