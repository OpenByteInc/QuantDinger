from app.professional_report.providers import PROVIDER_CATALOG
from app.routes.settings import CONFIG_SCHEMA


def _setting_keys() -> set[str]:
    return {
        item["key"]
        for group in CONFIG_SCHEMA.values()
        for item in group.get("items", [])
    }


def test_every_active_keyed_report_provider_is_configurable_in_settings():
    setting_keys = _setting_keys()
    active_keyed = [
        provider
        for provider in PROVIDER_CATALOG
        if provider.integration_status == "active" and not provider.keyless
    ]

    missing = {
        provider.key: sorted(set(provider.api_env_keys) - setting_keys)
        for provider in active_keyed
        if not set(provider.api_env_keys).intersection(setting_keys)
    }
    assert missing == {}


def test_professional_report_runtime_controls_are_exposed_in_settings():
    assert {
        "PROFESSIONAL_REPORT_RISK_BUDGET_PCT",
        "FAST_ANALYSIS_INCLUDE_GLOBAL_NEWS",
    }.issubset(_setting_keys())


def test_report_data_tier_is_derived_instead_of_configured():
    assert "PROFESSIONAL_REPORT_DATA_TIER" not in _setting_keys()


def test_unwired_bea_settings_are_not_exposed_and_akshare_stays_with_report_data():
    assert not {"BEA_API_KEY", "BEA_BASE_URL", "BEA_TIMEOUT"}.intersection(_setting_keys())
    data_source_keys = {item["key"] for item in CONFIG_SCHEMA["data_source"]["items"]}
    macro_tool_keys = {item["key"] for item in CONFIG_SCHEMA["macro_tools"]["items"]}
    assert "AKSHARE_TIMEOUT" in data_source_keys
    assert "AKSHARE_TIMEOUT" not in macro_tool_keys
