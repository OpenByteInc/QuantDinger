"""
Optional live smoke tests for grid order fill sync.

There is NO shared public API key — each exchange requires you to register a
**testnet / demo** key yourself (free). These tests only run when you opt in.

Quick start (Binance Futures testnet example):
  1. Register at https://testnet.binancefuture.com
  2. Copy any existing order id from the testnet UI (open or filled both work)
  3. Create backend_api_python/.env.testnet.local (gitignored) with:

       RUN_GRID_LIVE_TESTS=1
       GRID_LIVE_BINANCE_FUTURES_API_KEY=your_testnet_key
       GRID_LIVE_BINANCE_FUTURES_SECRET=your_testnet_secret
       GRID_LIVE_BINANCE_FUTURES_ORDER_ID=123456789

  4. Run:
       cd backend_api_python
       python -m pytest tests/test_grid_exchange_fill_live.py -m integration -v

Supported profiles (all optional — skip if env vars missing):
  - binance_futures   GRID_LIVE_BINANCE_FUTURES_*
  - binance_spot      GRID_LIVE_BINANCE_SPOT_*
  - okx_swap          GRID_LIVE_OKX_*  (+ passphrase)
  - bitget_mix        GRID_LIVE_BITGET_*  (+ product_type default USDT-FUTURES)
  - bybit             GRID_LIVE_BYBIT_*
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

import pytest

from app.services.grid.exchange_orders import query_grid_order_fill
from app.services.live_trading.factory import create_client


def _env(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


def _live_enabled() -> bool:
    return _env("RUN_GRID_LIVE_TESTS") == "1"


@dataclass(frozen=True)
class LiveProfile:
    profile_id: str
    required: Tuple[str, ...]
    build_config: Callable[[], Dict[str, Any]]
    symbol: str
    market_type: str
    order_id_env: str
    client_order_id_env: str = ""


def _binance_futures_cfg() -> Dict[str, Any]:
    return {
        "exchange_id": "binance",
        "api_key": _env("GRID_LIVE_BINANCE_FUTURES_API_KEY"),
        "secret_key": _env("GRID_LIVE_BINANCE_FUTURES_SECRET"),
        "market_type": "swap",
        "enable_demo_trading": True,
    }


def _binance_spot_cfg() -> Dict[str, Any]:
    return {
        "exchange_id": "binance",
        "api_key": _env("GRID_LIVE_BINANCE_SPOT_API_KEY"),
        "secret_key": _env("GRID_LIVE_BINANCE_SPOT_SECRET"),
        "market_type": "spot",
        "enable_demo_trading": True,
    }


def _okx_cfg() -> Dict[str, Any]:
    return {
        "exchange_id": "okx",
        "api_key": _env("GRID_LIVE_OKX_API_KEY"),
        "secret_key": _env("GRID_LIVE_OKX_SECRET"),
        "passphrase": _env("GRID_LIVE_OKX_PASSPHRASE"),
        "market_type": "swap",
        "simulated_trading": True,
    }


def _bitget_cfg() -> Dict[str, Any]:
    return {
        "exchange_id": "bitget",
        "api_key": _env("GRID_LIVE_BITGET_API_KEY"),
        "secret_key": _env("GRID_LIVE_BITGET_SECRET"),
        "passphrase": _env("GRID_LIVE_BITGET_PASSPHRASE"),
        "market_type": "swap",
        "simulated_trading": True,
        "product_type": _env("GRID_LIVE_BITGET_PRODUCT_TYPE") or "USDT-FUTURES",
    }


def _bybit_cfg() -> Dict[str, Any]:
    return {
        "exchange_id": "bybit",
        "api_key": _env("GRID_LIVE_BYBIT_API_KEY"),
        "secret_key": _env("GRID_LIVE_BYBIT_SECRET"),
        "market_type": "swap",
        "use_testnet": True,
    }


LIVE_PROFILES: Tuple[LiveProfile, ...] = (
    LiveProfile(
        "binance_futures",
        ("GRID_LIVE_BINANCE_FUTURES_API_KEY", "GRID_LIVE_BINANCE_FUTURES_SECRET", "GRID_LIVE_BINANCE_FUTURES_ORDER_ID"),
        _binance_futures_cfg,
        "BTC/USDT",
        "swap",
        "GRID_LIVE_BINANCE_FUTURES_ORDER_ID",
    ),
    LiveProfile(
        "binance_spot",
        ("GRID_LIVE_BINANCE_SPOT_API_KEY", "GRID_LIVE_BINANCE_SPOT_SECRET", "GRID_LIVE_BINANCE_SPOT_ORDER_ID"),
        _binance_spot_cfg,
        "BTC/USDT",
        "spot",
        "GRID_LIVE_BINANCE_SPOT_ORDER_ID",
    ),
    LiveProfile(
        "okx_swap",
        ("GRID_LIVE_OKX_API_KEY", "GRID_LIVE_OKX_SECRET", "GRID_LIVE_OKX_PASSPHRASE", "GRID_LIVE_OKX_ORDER_ID"),
        _okx_cfg,
        "BTC/USDT",
        "swap",
        "GRID_LIVE_OKX_ORDER_ID",
        "GRID_LIVE_OKX_CLIENT_ORDER_ID",
    ),
    LiveProfile(
        "bitget_mix",
        ("GRID_LIVE_BITGET_API_KEY", "GRID_LIVE_BITGET_SECRET", "GRID_LIVE_BITGET_PASSPHRASE", "GRID_LIVE_BITGET_ORDER_ID"),
        _bitget_cfg,
        "BTC/USDT",
        "swap",
        "GRID_LIVE_BITGET_ORDER_ID",
    ),
    LiveProfile(
        "bybit",
        ("GRID_LIVE_BYBIT_API_KEY", "GRID_LIVE_BYBIT_SECRET", "GRID_LIVE_BYBIT_ORDER_ID"),
        _bybit_cfg,
        "BTC/USDT",
        "swap",
        "GRID_LIVE_BYBIT_ORDER_ID",
    ),
)


def _profile_skip_reason(profile: LiveProfile) -> Optional[str]:
    if not _live_enabled():
        return "set RUN_GRID_LIVE_TESTS=1 to enable live exchange smoke tests"
    missing = [k for k in profile.required if not _env(k)]
    if missing:
        return f"missing env: {', '.join(missing)}"
    return None


@pytest.mark.integration
@pytest.mark.parametrize("profile", LIVE_PROFILES, ids=lambda p: p.profile_id)
def test_live_query_grid_order_fill_not_unknown(profile: LiveProfile):
    reason = _profile_skip_reason(profile)
    if reason:
        pytest.skip(reason)

    cfg = profile.build_config()
    client = create_client(cfg, market_type=profile.market_type)
    order_id = _env(profile.order_id_env)
    client_order_id = _env(profile.client_order_id_env) if profile.client_order_id_env else ""

    filled, avg, status = query_grid_order_fill(
        client,
        symbol=profile.symbol,
        market_type=profile.market_type,
        exchange_order_id=order_id,
        client_order_id=client_order_id,
        exchange_config=cfg,
    )
    assert status != "unknown", (
        f"{profile.profile_id}: query_grid_order_fill returned unknown — "
        "check testnet keys, order id, and demo/testnet flags"
    )
    assert status in ("open", "partial", "filled", "cancelled")
    assert filled >= 0.0
    assert avg >= 0.0
