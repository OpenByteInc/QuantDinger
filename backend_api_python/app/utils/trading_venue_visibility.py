"""Global trading venue visibility controlled by the Settings UI."""

from __future__ import annotations

from typing import Mapping, Optional, Set


TRADING_VENUE_IDS = (
    "alpaca",
    "ibkr",
    "tiger",
    "webull",
    "binance",
    "okx",
    "bybit",
    "bitget",
    "gate",
    "htx",
)

_ALIASES = {
    "interactive_brokers": "ibkr",
    "interactivebrokers": "ibkr",
    "gateio": "gate",
    "gate.io": "gate",
    "huobi": "htx",
}


def normalize_trading_venue_id(value: object) -> str:
    venue = str(value or "").strip().lower().replace("-", "_")
    return _ALIASES.get(venue, venue)


def enabled_trading_venues(env: Optional[Mapping[str, str]] = None) -> Set[str]:
    """Return enabled venues, defaulting to all only when the key is absent."""
    if env is None:
        from app.services.settings.env_file import read_env_file

        env = read_env_file()

    if "ENABLED_TRADING_VENUES" not in env:
        return set(TRADING_VENUE_IDS)

    raw = str(env.get("ENABLED_TRADING_VENUES") or "")
    return {
        venue
        for venue in (normalize_trading_venue_id(item) for item in raw.split(","))
        if venue in TRADING_VENUE_IDS
    }


def is_trading_venue_enabled(venue_id: object, env: Optional[Mapping[str, str]] = None) -> bool:
    return normalize_trading_venue_id(venue_id) in enabled_trading_venues(env)


def trading_venue_options() -> list[dict[str, str]]:
    return [
        {"value": venue_id, "label": venue_id}
        for venue_id in TRADING_VENUE_IDS
    ]
