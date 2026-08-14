"""
Futu (富途) OpenAPI trading module.

Requires a running FutuOpenD gateway (default 127.0.0.1:11111) and the
official ``futu-api`` Python package.

Supports HKStock and USStock spot (long-only) on simulate / real trade envs.
"""

from app.services.futu_trading.client import FutuClient, OrderResult
from app.services.futu_trading.config import FutuConfig, config_from_exchange_config
from app.services.futu_trading.quote_client import FutuQuoteClient
from app.services.futu_trading.session_pool import (
    FutuSessionPool,
    get_futu_session_pool,
    reset_futu_session_pool_for_tests,
)
from app.services.futu_trading.symbols import (
    format_display_symbol,
    from_futu_code,
    normalize_symbol,
    parse_symbol,
    to_futu_code,
)

__all__ = [
    "FutuClient",
    "FutuConfig",
    "FutuQuoteClient",
    "FutuSessionPool",
    "OrderResult",
    "config_from_exchange_config",
    "format_display_symbol",
    "from_futu_code",
    "get_futu_session_pool",
    "normalize_symbol",
    "parse_symbol",
    "reset_futu_session_pool_for_tests",
    "to_futu_code",
]
