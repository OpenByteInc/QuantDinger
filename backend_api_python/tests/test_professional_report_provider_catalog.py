from dataclasses import FrozenInstanceError

import pytest

from app.professional_report.providers import (
    PROVIDER_CATALOG,
    SUPPORTED_MARKETS,
    SUPPORTED_TIERS,
    get_provider,
    is_provider_configured,
    list_providers,
    provider_configuration_status,
    providers_for,
)


def test_catalog_only_contains_supported_markets_and_two_tiers():
    assert SUPPORTED_MARKETS == {"USStock", "HKStock", "Crypto", "Forex", "Futures"}
    assert SUPPORTED_TIERS == {"community", "professional"}
    assert {provider.tier for provider in PROVIDER_CATALOG} == SUPPORTED_TIERS
    assert set().union(*(provider.markets for provider in PROVIDER_CATALOG)) == SUPPORTED_MARKETS


def test_community_defaults_are_simple_free_or_low_cost():
    for market in SUPPORTED_MARKETS:
        defaults = list_providers(market=market, tier="community", capability="market_data")
        assert defaults, market
        assert defaults[0].keyless is True
        assert defaults[0].cost_level in {"free", "low"}

    assert all(provider.cost_level in {"free", "low"} for provider in list_providers(tier="community"))


def test_market_and_capability_filter_is_prioritized_and_supports_aliases():
    market_data = providers_for("USStock", "quotes")
    assert market_data[0].key == "yahoo_finance"
    assert [provider.tier for provider in market_data] == sorted(
        (provider.tier for provider in market_data),
        key={"community": 0, "professional": 1}.get,
    )
    assert all("USStock" in provider.markets for provider in market_data)
    assert all("market_data" in provider.capabilities for provider in market_data)

    estimates = providers_for("HKStock", "estimates", tier="professional")
    assert estimates == []


def test_catalog_entries_are_immutable_and_include_license_warnings():
    provider = get_provider("coinglass")
    assert provider.api_env_keys == ("COINGLASS_API_KEY",)
    assert provider.license_warning
    assert provider.capability == provider.capabilities
    assert provider.can_run_without_key is False
    assert provider.integration_status == "active"
    with pytest.raises(FrozenInstanceError):
        provider.default_priority = 1


def test_catalog_contains_only_active_adapters():
    active = {provider.key for provider in PROVIDER_CATALOG if provider.integration_status == "active"}
    assert {
        "yahoo_finance", "finnhub", "twelve_data", "alpha_vantage",
        "fred", "sec_edgar", "gate_public", "okx_public", "native_crypto_public",
        "coingecko", "coinglass", "cryptoquant", "cftc_cot",
    }.issubset(active)
    assert active == {provider.key for provider in PROVIDER_CATALOG}


def test_configuration_status_supports_keyless_and_keyed_sources():
    assert is_provider_configured("sec_edgar", environ={}) is True

    coinglass = provider_configuration_status("coinglass", environ={"COINGLASS_API_KEY": "key"})
    assert coinglass["configured"] is True
    assert coinglass["configured_env_keys"] == ("COINGLASS_API_KEY",)
    assert coinglass["missing_env_keys"] == ()


def test_configured_filter_does_not_read_network_or_require_process_env():
    configured = list_providers(
        market="Crypto",
        capability="onchain",
        configured=True,
        environ={},
    )
    assert configured == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"market": "CNStock"}, "unsupported market"),
        ({"tier": "premium"}, "unsupported tier"),
        ({"capability": "  "}, "capability cannot be blank"),
    ],
)
def test_invalid_filters_fail_closed(kwargs, message):
    with pytest.raises(ValueError, match=message):
        list_providers(**kwargs)
