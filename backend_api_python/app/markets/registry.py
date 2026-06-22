"""Canonical market module registry.

This is the first layer of the modular market architecture.  Market modules
describe what a market is, which data sources make it useful, and which live
execution adapters can route orders for it.  UI visibility still comes from
ENABLED_MARKETS / legacy SHOW_* flags via app.utils.market_visibility.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional

from dotenv import dotenv_values

from app.markets.models import DataRequirement, MarketModule
from app.services.broker_market_policy import list_supported_brokers_for_market


MARKET_ORDER = [
    "Crypto",
    "USStock",
    "CNStock",
    "HKStock",
    "Forex",
    "Futures",
    "MOEX",
]


MARKET_MODULES: Dict[str, MarketModule] = {
    "Crypto": MarketModule(
        key="Crypto",
        label="Crypto",
        description="Digital assets and crypto derivatives.",
        asset_class="crypto",
        symbol_hint="BTC/USDT",
        base_currency="USDT",
        features=["research", "backtest", "paper", "live"],
        data_requirements=[
            DataRequirement(
                key="ccxt",
                label="CCXT exchange",
                setting_keys=["CCXT_DEFAULT_EXCHANGE"],
                required=True,
                purpose="quotes and OHLCV",
            ),
            DataRequirement(
                key="coinglass",
                label="Coinglass",
                setting_keys=["COINGLASS_API_KEY"],
                recommended=True,
                purpose="derivatives metrics",
            ),
            DataRequirement(
                key="cryptoquant",
                label="CryptoQuant",
                setting_keys=["CRYPTOQUANT_API_KEY"],
                purpose="on-chain metrics",
            ),
        ],
        supports={"spot": True, "swap": True, "short": True, "session": "24/7"},
    ),
    "USStock": MarketModule(
        key="USStock",
        label="US Stocks",
        description="US equities and ETFs.",
        asset_class="equity",
        symbol_hint="AAPL",
        base_currency="USD",
        features=["research", "backtest", "paper", "live"],
        data_requirements=[
            DataRequirement(
                key="yfinance",
                label="Yahoo Finance fallback",
                built_in=True,
                purpose="basic quotes and OHLCV",
            ),
            DataRequirement(
                key="finnhub",
                label="Finnhub",
                setting_keys=["FINNHUB_API_KEY"],
                recommended=True,
                purpose="quotes, profiles, and news",
            ),
            DataRequirement(
                key="trading_economics",
                label="Trading Economics",
                setting_keys=["TRADING_ECONOMICS_CLIENT", "TRADING_ECONOMICS_KEY"],
                purpose="economic calendar",
            ),
        ],
        supports={"spot": True, "swap": False, "short": False, "session": "exchange-hours"},
    ),
    "CNStock": MarketModule(
        key="CNStock",
        label="China A-Shares",
        description="Mainland China A-share equities.",
        asset_class="equity",
        symbol_hint="300750",
        base_currency="CNY",
        features=["research", "backtest", "paper"],
        data_requirements=[
            DataRequirement(
                key="akshare",
                label="AkShare fallback",
                built_in=True,
                purpose="quotes and OHLCV",
            ),
            DataRequirement(
                key="twelve_data",
                label="Twelve Data",
                setting_keys=["TWELVE_DATA_API_KEY"],
                purpose="optional K-line enrichment",
            ),
        ],
        supports={"spot": True, "swap": False, "short": False, "session": "exchange-hours"},
    ),
    "HKStock": MarketModule(
        key="HKStock",
        label="Hong Kong Stocks",
        description="Hong Kong equities and ETFs.",
        asset_class="equity",
        symbol_hint="0700.HK",
        base_currency="HKD",
        features=["research", "backtest", "paper"],
        data_requirements=[
            DataRequirement(
                key="yfinance",
                label="Yahoo Finance fallback",
                built_in=True,
                purpose="basic quotes and OHLCV",
            ),
            DataRequirement(
                key="twelve_data",
                label="Twelve Data",
                setting_keys=["TWELVE_DATA_API_KEY"],
                recommended=True,
                purpose="quotes and K-lines",
            ),
        ],
        supports={"spot": True, "swap": False, "short": False, "session": "exchange-hours"},
    ),
    "Forex": MarketModule(
        key="Forex",
        label="Forex",
        description="FX pairs and precious metals routed through FX data sources and MT5.",
        asset_class="forex",
        symbol_hint="EURUSD",
        base_currency="USD",
        features=["research", "backtest", "paper", "live"],
        data_requirements=[
            DataRequirement(
                key="yfinance",
                label="Yahoo Finance fallback",
                built_in=True,
                purpose="basic quotes and OHLCV",
            ),
            DataRequirement(
                key="twelve_data",
                label="Twelve Data",
                setting_keys=["TWELVE_DATA_API_KEY"],
                recommended=True,
                purpose="FX quotes and K-lines",
            ),
            DataRequirement(
                key="tiingo",
                label="Tiingo",
                setting_keys=["TIINGO_API_KEY"],
                purpose="FX fallback",
            ),
        ],
        supports={"spot": True, "swap": False, "short": True, "session": "24/5"},
    ),
    "Futures": MarketModule(
        key="Futures",
        label="Futures",
        description="Generic futures research market. Live execution is not enabled yet.",
        asset_class="futures",
        symbol_hint="ES",
        base_currency="USD",
        features=["research", "backtest", "paper"],
        data_requirements=[
            DataRequirement(
                key="twelve_data",
                label="Twelve Data",
                setting_keys=["TWELVE_DATA_API_KEY"],
                purpose="futures quotes and K-lines",
            ),
        ],
        supports={"spot": False, "swap": False, "short": True, "session": "exchange-hours"},
    ),
    "MOEX": MarketModule(
        key="MOEX",
        label="MOEX",
        description="Moscow Exchange research market. Live execution is not enabled yet.",
        asset_class="equity",
        symbol_hint="SBER",
        base_currency="RUB",
        features=["research", "paper"],
        data_requirements=[],
        supports={"spot": True, "swap": False, "short": False, "session": "exchange-hours"},
    ),
}


def _backend_env_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".env"


def load_runtime_env() -> Dict[str, str]:
    """Return .env values overlaid with process env values."""
    values: Dict[str, str] = {}
    path = _backend_env_path()
    if path.exists():
        values.update({k: str(v or "") for k, v in dotenv_values(path).items()})
    values.update({k: str(v) for k, v in os.environ.items()})
    return values


def list_market_keys() -> List[str]:
    return [key for key in MARKET_ORDER if key in MARKET_MODULES]


def market_options() -> List[Dict[str, str]]:
    return [{"value": key, "label": MARKET_MODULES[key].label} for key in list_market_keys()]


def _setting_configured(env: Mapping[str, str], key: str) -> bool:
    value = env.get(key)
    if value is None:
        value = os.getenv(key, "")
    return str(value or "").strip() != ""


def _flag(env: Mapping[str, str], name: str, default: str) -> bool:
    return str(env.get(name, default) or default).strip().lower() in ("1", "true", "yes", "on")


def _enabled_from_env(env: Mapping[str, str], market: str) -> bool:
    raw = str(env.get("ENABLED_MARKETS", "") or "").strip()
    if raw:
        allowed = {part.strip() for part in raw.split(",") if part.strip()}
        return market in allowed
    if market == "CNStock":
        return _flag(env, "SHOW_CN_STOCK", "false")
    if market == "HKStock":
        return _flag(env, "SHOW_HK_STOCK", "true")
    return True


def _requirement_status(req: DataRequirement, env: Mapping[str, str]) -> Dict[str, object]:
    configured = bool(req.built_in)
    if req.setting_keys:
        configured = any(_setting_configured(env, key) for key in req.setting_keys)
    return {
        "key": req.key,
        "label": req.label,
        "setting_keys": list(req.setting_keys),
        "required": req.required,
        "recommended": req.recommended,
        "purpose": req.purpose,
        "built_in": req.built_in,
        "configured": configured,
    }


def _status_for(enabled: bool, requirements: Iterable[Dict[str, object]], live_brokers: List[str]) -> str:
    if not enabled:
        return "disabled"
    reqs = list(requirements)
    if any(r.get("required") and not r.get("configured") for r in reqs):
        return "blocked"
    if any(r.get("recommended") and not r.get("configured") for r in reqs):
        return "partial"
    return "ready" if live_brokers or reqs else "partial"


def serialize_market_module(module: MarketModule, env: Optional[Mapping[str, str]] = None) -> Dict[str, object]:
    env_map = env or load_runtime_env()
    enabled = _enabled_from_env(env_map, module.key)
    data_sources = [_requirement_status(req, env_map) for req in module.data_requirements]
    live_brokers = sorted(list_supported_brokers_for_market(module.key))
    return {
        "key": module.key,
        "label": module.label,
        "description": module.description,
        "asset_class": module.asset_class,
        "symbol_hint": module.symbol_hint,
        "base_currency": module.base_currency,
        "enabled": enabled,
        "features": list(module.features),
        "data_sources": data_sources,
        "live_brokers": live_brokers,
        "supports": dict(module.supports),
        "status": _status_for(enabled, data_sources, live_brokers),
    }


def list_market_modules(env: Optional[Mapping[str, str]] = None) -> List[Dict[str, object]]:
    env_map = env or load_runtime_env()
    return [serialize_market_module(MARKET_MODULES[key], env_map) for key in list_market_keys()]
