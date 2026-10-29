"""Static provider catalogue for professional report data sourcing.

This module describes data-source choices; it intentionally does not create
provider clients or perform network requests.  A lower ``default_priority``
means that a provider should be considered earlier within the same tier.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Mapping


SUPPORTED_MARKETS = frozenset({"USStock", "HKStock", "Crypto", "Forex", "Futures"})
SUPPORTED_TIERS = frozenset({"community", "professional"})
SUPPORTED_COST_LEVELS = frozenset({"free", "low", "medium", "high", "enterprise"})
SUPPORTED_INTEGRATION_STATUSES = frozenset({"active", "planned"})

CAPABILITY_ALIASES = {
    "quotes": "market_data",
    "prices": "market_data",
    "ohlcv": "market_data",
    "company_actions": "corporate_actions",
    "estimates": "analyst_estimates",
    "announcements": "filings",
    "company_announcements": "filings",
    "fund_flow": "fund_flows",
    "short_interest": "short_data",
    "short_volume": "short_data",
    "onchain": "on_chain",
}


@dataclass(frozen=True)
class ProviderSpec:
    """One provider and the report capabilities it may supply."""

    key: str
    name: str
    tier: str
    markets: frozenset[str]
    capabilities: frozenset[str]
    api_env_keys: tuple[str, ...] = ()
    keyless: bool = False
    cost_level: str = "free"
    license_warning: str = ""
    default_priority: int = 100
    require_all_env_keys: bool = False
    integration_status: str = "planned"

    def __post_init__(self) -> None:
        if not self.key or not self.name:
            raise ValueError("provider key and name are required")
        if self.tier not in SUPPORTED_TIERS:
            raise ValueError(f"unsupported provider tier: {self.tier}")
        if not self.markets or not self.markets.issubset(SUPPORTED_MARKETS):
            raise ValueError(f"unsupported provider markets: {sorted(self.markets)}")
        if not self.capabilities:
            raise ValueError("provider must declare at least one capability")
        if self.cost_level not in SUPPORTED_COST_LEVELS:
            raise ValueError(f"unsupported provider cost level: {self.cost_level}")
        if self.default_priority < 0:
            raise ValueError("default_priority must be non-negative")
        if self.integration_status not in SUPPORTED_INTEGRATION_STATUSES:
            raise ValueError(f"unsupported integration status: {self.integration_status}")
        if not self.keyless and not self.api_env_keys:
            raise ValueError("a keyed provider must declare api_env_keys")

    @property
    def can_run_without_key(self) -> bool:
        """Compatibility-friendly name for the keyless flag."""

        return self.keyless

    @property
    def capability(self) -> frozenset[str]:
        """Expose the declared capability set under the singular label too."""

        return self.capabilities


PROVIDER_CATALOG: tuple[ProviderSpec, ...] = (
    # Community sources are intentionally simple and free/low-cost.  They are
    # suitable defaults for research workflows, not implicit redistribution
    # licences for a customer-facing product.
    ProviderSpec(
        key="yahoo_finance",
        name="Yahoo Finance",
        tier="community",
        markets=frozenset({"USStock", "HKStock", "Forex", "Futures"}),
        capabilities=frozenset({
            "market_data", "corporate_actions", "fundamentals", "options",
            "analyst_estimates", "short_data", "ownership",
        }),
        keyless=True,
        cost_level="free",
        license_warning=(
            "Consumer-facing data may be delayed and is not an unrestricted commercial "
            "redistribution licence; confirm terms before customer-facing use."
        ),
        default_priority=10,
        integration_status="active",
    ),
    ProviderSpec(
        key="finnhub",
        name="Finnhub",
        tier="community",
        markets=frozenset({"USStock"}),
        capabilities=frozenset({"market_data", "fundamentals", "news", "analyst_estimates"}),
        api_env_keys=("FINNHUB_API_KEY",),
        cost_level="free",
        license_warning="Free and paid plans differ in exchange coverage, delay, display and redistribution rights.",
        default_priority=20,
        integration_status="active",
    ),
    ProviderSpec(
        key="twelve_data",
        name="Twelve Data",
        tier="community",
        markets=frozenset({"USStock", "HKStock", "Forex", "Futures"}),
        capabilities=frozenset({"market_data", "fundamentals"}),
        api_env_keys=("TWELVE_DATA_API_KEY",),
        cost_level="low",
        license_warning="Confirm plan-level exchange entitlements, request quotas and external display rights.",
        default_priority=20,
        integration_status="active",
    ),
    ProviderSpec(
        key="alpha_vantage",
        name="Alpha Vantage",
        tier="community",
        markets=frozenset({"USStock"}),
        capabilities=frozenset({"market_data", "fundamentals", "news", "earnings_transcripts"}),
        api_env_keys=("ALPHA_VANTAGE_API_KEY",),
        cost_level="free",
        license_warning="Free access is rate-limited; verify commercial display and redistribution terms.",
        default_priority=30,
        integration_status="active",
    ),
    ProviderSpec(
        key="cftc_cot",
        name="CFTC Commitments of Traders",
        tier="community",
        markets=frozenset({"Futures"}),
        capabilities=frozenset({"positioning"}),
        keyless=True,
        cost_level="free",
        license_warning="Weekly public positioning is delayed and must not be presented as real-time order flow.",
        default_priority=5,
        integration_status="active",
    ),
    ProviderSpec(
        key="sec_edgar",
        name="SEC EDGAR",
        tier="community",
        markets=frozenset({"USStock"}),
        capabilities=frozenset({"filings", "fundamentals", "corporate_actions"}),
        keyless=True,
        cost_level="free",
        license_warning=(
            "Follow SEC fair-access limits and identify the application with a User-Agent; "
            "individual filing exhibits can carry third-party rights."
        ),
        default_priority=5,
        integration_status="active",
    ),
    ProviderSpec(
        key="fred",
        name="FRED / ALFRED",
        tier="community",
        markets=frozenset({"USStock", "Forex", "Futures"}),
        capabilities=frozenset({"macro"}),
        api_env_keys=("FRED_API_KEY",),
        cost_level="free",
        license_warning=(
            "Attribute FRED and each underlying series source; third-party series can have "
            "additional commercial-use restrictions."
        ),
        default_priority=10,
        integration_status="active",
    ),
    ProviderSpec(
        key="finra_public",
        name="FINRA Public Data",
        tier="community",
        markets=frozenset({"USStock"}),
        capabilities=frozenset({"short_data"}),
        keyless=True,
        cost_level="free",
        license_warning=(
            "Public short-volume files cover off-exchange reported activity, are not short "
            "interest, and are described by FINRA as free for non-commercial use."
        ),
        default_priority=10,
        integration_status="active",
    ),
    ProviderSpec(
        key="hkex_public",
        name="HKEX Public Information",
        tier="community",
        markets=frozenset({"HKStock"}),
        capabilities=frozenset({"filings", "corporate_actions", "short_data", "fund_flows"}),
        keyless=True,
        cost_level="free",
        license_warning=(
            "Public pages are useful for source verification, not bulk commercial redistribution; "
            "use licensed HKEX feeds or Data Marketplace products in production."
        ),
        default_priority=5,
    ),
    ProviderSpec(
        key="eastmoney_hk",
        name="Eastmoney Hong Kong via AkShare",
        tier="community",
        markets=frozenset({"HKStock"}),
        capabilities=frozenset({"fundamentals", "fund_flows", "market_data"}),
        keyless=True,
        cost_level="free",
        license_warning=(
            "Best-effort community data intended for research and source verification; "
            "confirm display and redistribution rights before commercial use."
        ),
        default_priority=8,
        integration_status="active",
    ),
    ProviderSpec(
        key="etnet_hk",
        name="ET Net Hong Kong via AkShare",
        tier="community",
        markets=frozenset({"HKStock"}),
        capabilities=frozenset({"analyst_estimates"}),
        keyless=True,
        cost_level="free",
        license_warning=(
            "Broker consensus is a secondary-source snapshot and may be delayed or incomplete; "
            "do not redistribute raw records without confirming the applicable terms."
        ),
        default_priority=9,
        integration_status="active",
    ),
    ProviderSpec(
        key="hkma_open_api",
        name="HKMA Open API",
        tier="community",
        markets=frozenset({"HKStock"}),
        capabilities=frozenset({"macro"}),
        keyless=True,
        cost_level="free",
        license_warning="Retain source attribution, observation dates, and revision timestamps.",
        default_priority=10,
        integration_status="active",
    ),
    ProviderSpec(
        key="gate_public",
        name="Gate Public Futures API",
        tier="community",
        markets=frozenset({"Crypto"}),
        capabilities=frozenset({"derivatives", "market_data"}),
        keyless=True,
        cost_level="free",
        license_warning="Public endpoints remain subject to Gate rate limits, availability and data-use terms.",
        default_priority=4,
        integration_status="active",
    ),
    ProviderSpec(
        key="okx_public",
        name="OKX Public Data API",
        tier="community",
        markets=frozenset({"Crypto"}),
        capabilities=frozenset({"derivatives", "market_data"}),
        keyless=True,
        cost_level="free",
        license_warning="Public endpoints remain subject to OKX regional availability, rate limits and data-use terms.",
        default_priority=5,
        integration_status="active",
    ),
    ProviderSpec(
        key="native_crypto_public",
        name="Native Crypto Public Market Data",
        tier="community",
        markets=frozenset({"Crypto"}),
        capabilities=frozenset({"market_data", "derivatives"}),
        keyless=True,
        cost_level="free",
        license_warning=(
            "Exchange-specific market-data terms, rate limits, symbol coverage, and retention rules "
            "still apply even when public endpoints require no key."
        ),
        default_priority=5,
        integration_status="active",
    ),
    ProviderSpec(
        key="coingecko",
        name="CoinGecko Public API",
        tier="community",
        markets=frozenset({"Crypto"}),
        capabilities=frozenset({"market_data", "fundamentals"}),
        keyless=True,
        cost_level="free",
        license_warning="Public endpoints are rate-limited; attribute estimates and verify commercial usage terms.",
        default_priority=10,
        integration_status="active",
    ),
    ProviderSpec(
        key="defillama",
        name="DefiLlama",
        tier="community",
        markets=frozenset({"Crypto"}),
        capabilities=frozenset({"on_chain", "fund_flows"}),
        keyless=True,
        cost_level="free",
        license_warning="Treat methodology and protocol mappings as third-party estimates and attribute the source.",
        default_priority=15,
    ),
    # Optional professional sources.  Selecting one never implies that the
    # configured commercial contract includes display or redistribution rights.
    ProviderSpec(
        key="massive",
        name="Massive (Polygon.io)",
        tier="professional",
        markets=frozenset({"USStock"}),
        capabilities=frozenset(
            {"market_data", "options", "corporate_actions", "fundamentals", "filings", "short_data"}
        ),
        api_env_keys=("MASSIVE_API_KEY", "POLYGON_API_KEY"),
        cost_level="high",
        license_warning=(
            "Individual plans are not licensed for professional/customer-facing use; obtain the "
            "appropriate Business or Enterprise display and redistribution rights."
        ),
        default_priority=10,
    ),
    ProviderSpec(
        key="intrinio",
        name="Intrinio",
        tier="professional",
        markets=frozenset({"USStock"}),
        capabilities=frozenset(
            {"market_data", "options", "fundamentals", "analyst_estimates", "news", "corporate_actions"}
        ),
        api_env_keys=("INTRINIO_API_KEY",),
        cost_level="high",
        license_warning=(
            "Commercial display, raw redistribution, exchange data, estimates, and news rights are "
            "separate entitlements; verify the contracted feed bundle."
        ),
        default_priority=20,
    ),
    ProviderSpec(
        key="hkex_data_marketplace",
        name="HKEX Data Marketplace",
        tier="professional",
        markets=frozenset({"HKStock"}),
        capabilities=frozenset(
            {"market_data", "options", "corporate_actions", "filings", "short_data", "fund_flows"}
        ),
        api_env_keys=("HKEX_DATA_MARKETPLACE_USERNAME", "HKEX_DATA_MARKETPLACE_PASSWORD"),
        cost_level="medium",
        license_warning=(
            "Purchase the matching dataset and usage rights; an end-user subscription does not "
            "automatically grant external redistribution rights."
        ),
        default_priority=10,
        require_all_env_keys=True,
    ),
    ProviderSpec(
        key="hkex_omd_iis",
        name="HKEX OMD / IIS",
        tier="professional",
        markets=frozenset({"HKStock"}),
        capabilities=frozenset({"market_data", "options", "filings"}),
        api_env_keys=("HKEX_OMD_LICENSE_ID", "HKEX_IIS_LICENSE_ID"),
        cost_level="enterprise",
        license_warning=(
            "Requires the relevant HKEX vendor/end-user licence, connection approval, subscriber "
            "reporting, and any applicable display or redistribution fees."
        ),
        default_priority=20,
    ),
    ProviderSpec(
        key="lseg",
        name="LSEG Data & Analytics",
        tier="professional",
        markets=frozenset({"USStock", "HKStock"}),
        capabilities=frozenset({"market_data", "fundamentals", "analyst_estimates", "news", "macro"}),
        api_env_keys=("LSEG_CLIENT_ID", "LSEG_CLIENT_SECRET"),
        cost_level="enterprise",
        license_warning=(
            "Standard news and data-feed licences are commonly internal-only; external reports, "
            "derived data, AI use, and redistribution require explicit contractual rights."
        ),
        default_priority=30,
        require_all_env_keys=True,
    ),
    ProviderSpec(
        key="kaiko",
        name="Kaiko",
        tier="professional",
        markets=frozenset({"Crypto"}),
        capabilities=frozenset({"market_data", "derivatives"}),
        api_env_keys=("KAIKO_API_KEY",),
        cost_level="enterprise",
        license_warning="Confirm venue-level display, derived-data, redistribution, and historical retention rights.",
        default_priority=10,
    ),
    ProviderSpec(
        key="coinglass",
        name="CoinGlass",
        tier="professional",
        markets=frozenset({"Crypto"}),
        capabilities=frozenset({"derivatives", "fund_flows"}),
        api_env_keys=("COINGLASS_API_KEY",),
        cost_level="medium",
        license_warning="API history, rate limits, caching, external display and redistribution depend on the plan.",
        default_priority=15,
        integration_status="active",
    ),
    ProviderSpec(
        key="coin_metrics",
        name="Coin Metrics",
        tier="professional",
        markets=frozenset({"Crypto"}),
        capabilities=frozenset({"market_data", "on_chain", "fund_flows"}),
        api_env_keys=("COINMETRICS_API_KEY",),
        cost_level="enterprise",
        license_warning="Community endpoints and institutional feeds have different commercial and redistribution rights.",
        default_priority=20,
    ),
    ProviderSpec(
        key="cryptoquant",
        name="CryptoQuant",
        tier="professional",
        markets=frozenset({"Crypto"}),
        capabilities=frozenset({"on_chain", "fund_flows", "derivatives"}),
        api_env_keys=("CRYPTOQUANT_API_KEY",),
        cost_level="high",
        license_warning="External display, derived metrics, caching, and redistribution depend on the purchased plan.",
        default_priority=30,
        integration_status="active",
    ),
)

PROVIDERS_BY_KEY = {provider.key: provider for provider in PROVIDER_CATALOG}


def _normalize_capability(capability: str | None) -> str | None:
    if capability is None:
        return None
    normalized = str(capability).strip().lower()
    if not normalized:
        raise ValueError("capability cannot be blank")
    return CAPABILITY_ALIASES.get(normalized, normalized)


def get_provider(provider: str | ProviderSpec) -> ProviderSpec:
    """Resolve a provider key or return an already-resolved specification."""

    if isinstance(provider, ProviderSpec):
        return provider
    try:
        return PROVIDERS_BY_KEY[str(provider)]
    except KeyError as exc:
        raise KeyError(f"unknown professional report provider: {provider}") from exc


def list_providers(
    *,
    market: str | None = None,
    capability: str | None = None,
    tier: str | None = None,
    configured: bool | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[ProviderSpec]:
    """Filter providers and return them in deterministic default order."""

    if market is not None and market not in SUPPORTED_MARKETS:
        raise ValueError(f"unsupported market: {market}")
    if tier is not None and tier not in SUPPORTED_TIERS:
        raise ValueError(f"unsupported tier: {tier}")
    normalized_capability = _normalize_capability(capability)

    matches = []
    for provider in PROVIDER_CATALOG:
        if market is not None and market not in provider.markets:
            continue
        if tier is not None and provider.tier != tier:
            continue
        if normalized_capability is not None and normalized_capability not in provider.capabilities:
            continue
        if configured is not None and is_provider_configured(provider, environ=environ) is not configured:
            continue
        matches.append(provider)

    tier_order = {"community": 0, "professional": 1}
    return sorted(matches, key=lambda item: (tier_order[item.tier], item.default_priority, item.key))


def providers_for(
    market: str,
    capability: str,
    *,
    tier: str | None = None,
    configured: bool | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[ProviderSpec]:
    """Convenience wrapper for the common market-and-capability query."""

    return list_providers(
        market=market,
        capability=capability,
        tier=tier,
        configured=configured,
        environ=environ,
    )


filter_providers = list_providers


def provider_configuration_status(
    provider: str | ProviderSpec,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return key-only configuration status without initializing a client."""

    spec = get_provider(provider)
    if environ is None:
        # The open-source settings page may persist keys in addon_config rather
        # than process environment variables.  Reuse the central resolver so
        # the report UI reflects both supported configuration paths.
        from app.config.api_keys import APIKeys

        values = {
            key: os.environ.get(key, "") or APIKeys.get(key, "")
            for key in spec.api_env_keys
        }
    else:
        values = environ
    configured_keys = tuple(key for key in spec.api_env_keys if str(values.get(key, "")).strip())
    missing_keys = tuple(key for key in spec.api_env_keys if key not in configured_keys)

    if spec.keyless:
        is_configured = True
    elif spec.require_all_env_keys:
        is_configured = not missing_keys
    else:
        is_configured = bool(configured_keys)

    return {
        "provider": spec.key,
        "tier": spec.tier,
        "configured": is_configured,
        "keyless": spec.keyless,
        "can_run_without_key": spec.keyless,
        "api_env_keys": spec.api_env_keys,
        "configured_env_keys": configured_keys,
        "missing_env_keys": missing_keys,
    }


def is_provider_configured(
    provider: str | ProviderSpec,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return whether the provider can be selected with the current keys."""

    return bool(provider_configuration_status(provider, environ=environ)["configured"])


def configuration_statuses(
    providers: Iterable[str | ProviderSpec] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> list[dict[str, object]]:
    """Return configuration status for a provider collection or the full catalog."""

    selected = PROVIDER_CATALOG if providers is None else providers
    return [provider_configuration_status(provider, environ=environ) for provider in selected]


get_provider_configuration_status = provider_configuration_status


__all__ = [
    "CAPABILITY_ALIASES",
    "PROVIDER_CATALOG",
    "PROVIDERS_BY_KEY",
    "SUPPORTED_COST_LEVELS",
    "SUPPORTED_INTEGRATION_STATUSES",
    "SUPPORTED_MARKETS",
    "SUPPORTED_TIERS",
    "ProviderSpec",
    "configuration_statuses",
    "filter_providers",
    "get_provider",
    "get_provider_configuration_status",
    "is_provider_configured",
    "list_providers",
    "provider_configuration_status",
    "providers_for",
]
