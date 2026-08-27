"""
Factory for direct exchange clients.

Supports:
- Crypto exchanges: Binance, OKX, Bitget, Bybit, Gate, HTX
- Traditional brokers: Interactive Brokers (IBKR), Alpaca, and Futu
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Union

logger = logging.getLogger(__name__)

from app.services.live_trading.base import BaseRestClient, LiveTradingError
from app.services.live_trading.binance import BinanceFuturesClient
from app.services.live_trading.binance_spot import BinanceSpotClient
from app.services.live_trading.okx import OkxClient
from app.services.live_trading.bitget import BitgetMixClient
from app.services.live_trading.bitget_spot import BitgetSpotClient
from app.services.live_trading.bybit import BybitClient
from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient
from app.services.live_trading.htx import HtxClient

# Lazy import IBKR to avoid ImportError if ib_insync not installed
IBKRClient = None
IBKRConfig = None

# Lazy import Alpaca to avoid ImportError if alpaca-py not installed
AlpacaClient = None
AlpacaConfig = None

# Lazy import Futu to avoid ImportError if futu-api not installed
FutuClient = None
FutuConfig = None


def _get(cfg: Dict[str, Any], *keys: str) -> str:
    for k in keys:
        v = cfg.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


# Merged from HTTP JSON root into nested `exchange_config` for /strategies/test-connection
# when the UI sends demo/testnet toggles next to the nested object.
EXCHANGE_CONFIG_ROOT_OVERLAY_KEYS = (
    "enable_demo_trading",
    "enableDemoTrading",
    "simulated_trading",
    "simulatedTrading",
    "use_testnet",
    "is_testnet",
    "isTestnet",
    "sandbox",
    "paper_trading",
    "paperTrading",
    "network",
    "environment",
    "env",
    "market_scope",
    "marketScope",
    "base_url",
    "baseUrl",
    "futures_base_url",
    "futuresBaseUrl",
)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) == 1
    return str(value or "").strip().lower() in ("true", "1", "yes", "on")


def exchange_trading_environment(cfg: Dict[str, Any], exchange_id: str = "") -> str:
    """Return the canonical credential environment: live, demo, or testnet."""
    if not isinstance(cfg, dict):
        return "live"
    ex = str(exchange_id or cfg.get("exchange_id") or cfg.get("exchangeId") or "").strip().lower()
    raw = str(cfg.get("environment") or cfg.get("network") or cfg.get("env") or "").strip().lower()
    if raw in ("live", "mainnet", "production", "prod", "real"):
        environment = "live"
    elif raw in ("demo", "paper", "simulate", "simulation", "simulated"):
        environment = "demo"
    elif raw in ("testnet", "sandbox", "test"):
        environment = "testnet"
    elif raw:
        return raw
    else:
        legacy_demo = any(
            _truthy(cfg.get(key))
            for key in (
                "enable_demo_trading",
                "enableDemoTrading",
                "simulated_trading",
                "simulatedTrading",
                "use_testnet",
                "is_testnet",
                "isTestnet",
                "sandbox",
                "paper_trading",
                "paperTrading",
                "paper",
                "is_paper",
                "demo",
                "testnet",
            )
        )
        if not legacy_demo:
            return "live"
        environment = "testnet" if ex == "gate" else "demo"

    if ex == "gate" and environment == "demo":
        return "testnet"
    if ex in ("okx", "bitget") and environment == "testnet":
        return "demo"
    return environment


def exchange_market_scope(cfg: Dict[str, Any]) -> str:
    raw = str(cfg.get("market_scope") or cfg.get("marketScope") or "both").strip().lower()
    if raw in ("future", "futures", "perp", "perpetual", "contract", "contracts"):
        return "swap"
    if raw in ("spot", "swap", "both"):
        return raw
    return raw


def validate_exchange_environment(exchange_id: str, environment: str, market_scope: str = "both") -> None:
    ex = str(exchange_id or "").strip().lower()
    env = str(environment or "live").strip().lower()
    scope = str(market_scope or "both").strip().lower()
    allowed = {
        "binance": {"live", "demo"},
        "okx": {"live", "demo"},
        "bitget": {"live", "demo"},
        "bybit": {"live", "demo"},
        "gate": {"live", "testnet"},
        "htx": {"live"},
        "futu": {"live", "demo"},
    }
    if env not in allowed.get(ex, {"live"}):
        if ex == "htx" and env != "live":
            raise LiveTradingError("HTX_DEMO_NOT_SUPPORTED")
        raise LiveTradingError("UNSUPPORTED_TRADING_ENVIRONMENT")
    if scope not in ("spot", "swap", "both"):
        raise LiveTradingError("INVALID_CREDENTIAL_MARKET_SCOPE")


def merge_root_exchange_config_overlay(*, root: Dict[str, Any], exchange_config: Dict[str, Any]) -> Dict[str, Any]:
    """Overlay selected keys from the request root onto exchange_config (copying the latter)."""
    out = dict(exchange_config or {})
    if not isinstance(root, dict):
        return out
    for k in EXCHANGE_CONFIG_ROOT_OVERLAY_KEYS:
        if k in root:
            out[k] = root[k]
    return out


def _legacy_exchange_demo_mode_enabled(cfg: Dict[str, Any]) -> bool:
    """
    Whether config indicates demo / testnet / simulated / paper mode for live-trading clients.

    Accepts common frontend / exchange naming variants so test-connection matches create_client.
    """
    if not isinstance(cfg, dict):
        return False
    env = str(cfg.get("network") or cfg.get("environment") or cfg.get("env") or "").strip().lower()
    if env in ("testnet", "sandbox", "demo", "paper", "simulate", "simulation"):
        return True
    for k in (
        "enable_demo_trading",
        "enableDemoTrading",
        "simulated_trading",
        "simulatedTrading",
        "use_testnet",
        "is_testnet",
        "isTestnet",
        "sandbox",
        "paper_trading",
        "paperTrading",
        # Alpaca stores its paper/live flag as a bare `paper` boolean — alias it
        # so /api/credentials/list shows the right paper badge on Alpaca rows.
        "paper",
        "is_paper",
    ):
        v = cfg.get(k)
        if v is None:
            continue
        if isinstance(v, bool) and v:
            return True
        if isinstance(v, (int, float)) and int(v) == 1:
            return True
        if isinstance(v, str) and str(v).strip().lower() in ("true", "1", "yes", "on"):
            return True
    return False


def exchange_demo_mode_enabled(cfg: Dict[str, Any]) -> bool:
    return exchange_trading_environment(cfg) != "live"


def _demo_enabled(cfg: Dict[str, Any]) -> bool:
    return exchange_demo_mode_enabled(cfg)


def create_client(
    exchange_config: Dict[str, Any],
    *,
    market_type: str = "swap",
    pooled: bool = True,
    need_quote: bool = True,
) -> BaseRestClient:
    if not isinstance(exchange_config, dict):
        raise LiveTradingError("Invalid exchange_config")
    exchange_id = _get(exchange_config, "exchange_id", "exchangeId").lower()
    api_key = _get(exchange_config, "api_key", "apiKey")
    secret_key = _get(exchange_config, "secret_key", "secret")
    passphrase = _get(exchange_config, "passphrase", "password")

    mt = (market_type or exchange_config.get("market_type") or exchange_config.get("defaultType") or "swap").strip().lower()
    if mt in ("futures", "future", "perp", "perpetual"):
        mt = "swap"
    # Futu is equities spot-only; callers often omit market_type (defaults to swap).
    if exchange_id == "futu":
        mt = "spot"

    environment = exchange_trading_environment(exchange_config, exchange_id)
    if environment not in ("live", "demo", "testnet"):
        raise LiveTradingError("UNSUPPORTED_TRADING_ENVIRONMENT")
    is_demo = environment != "live"
    market_scope = exchange_market_scope(exchange_config)
    validate_exchange_environment(exchange_id, environment, market_scope)
    if market_scope != "both" and market_scope != mt:
        raise LiveTradingError("CREDENTIAL_MARKET_SCOPE_MISMATCH")

    if exchange_id == "binance":
        if mt == "spot":
            default_url = "https://demo-api.binance.com" if is_demo else "https://api.binance.com"
            base_url = default_url if is_demo else (_get(exchange_config, "base_url", "baseUrl") or default_url)
            return BinanceSpotClient(api_key=api_key, secret_key=secret_key, base_url=base_url, enable_demo_trading=is_demo)
        # Default to USDT-M futures
        # Binance USD-M Futures demo REST endpoint.
        default_url = "https://demo-fapi.binance.com" if is_demo else "https://fapi.binance.com"
        base_url = default_url if is_demo else (_get(exchange_config, "base_url", "baseUrl") or default_url)
        return BinanceFuturesClient(api_key=api_key, secret_key=secret_key, base_url=base_url, enable_demo_trading=is_demo)
    if exchange_id == "okx":
        base_url = "https://openapi.okx.com" if is_demo else (_get(exchange_config, "base_url", "baseUrl") or "https://openapi.okx.com")
        return OkxClient(
            api_key=api_key,
            secret_key=secret_key,
            passphrase=passphrase,
            base_url=base_url,
            simulated_trading=is_demo,
        )
    if exchange_id == "bitget":
        # Bitget simulated trading uses the same REST host; keys must be created in Bitget demo trading.
        base_url = _get(exchange_config, "base_url", "baseUrl") or "https://api.bitget.com"
        if mt == "spot":
            return BitgetSpotClient(
                api_key=api_key,
                secret_key=secret_key,
                passphrase=passphrase,
                base_url=base_url,
                simulated_trading=is_demo,
            )
        return BitgetMixClient(
            api_key=api_key,
            secret_key=secret_key,
            passphrase=passphrase,
            base_url=base_url,
            simulated_trading=is_demo,
        )

    if exchange_id == "bybit":
        if environment == "demo":
            default_bybit = "https://api-demo.bybit.com"
        else:
            default_bybit = "https://api.bybit.com"
        base_url = default_bybit if is_demo else (_get(exchange_config, "base_url", "baseUrl") or default_bybit)
        category = "spot" if mt == "spot" else "linear"
        recv_window_ms = int(exchange_config.get("recv_window_ms") or exchange_config.get("recvWindow") or 12000)
        hedge_mode_raw = exchange_config.get("hedge_mode")
        if hedge_mode_raw is None:
            hedge_mode_raw = exchange_config.get("hedgeMode")
        if hedge_mode_raw is None:
            hedge_mode_raw = exchange_config.get("position_mode") or exchange_config.get("positionMode")
        hedge_mode = False
        if isinstance(hedge_mode_raw, bool):
            hedge_mode = hedge_mode_raw
        else:
            hedge_mode = str(hedge_mode_raw or "").strip().lower() in ("true", "1", "yes", "hedge", "both_side")
        return BybitClient(
            api_key=api_key,
            secret_key=secret_key,
            base_url=base_url,
            category=category,
            recv_window_ms=recv_window_ms,
            hedge_mode=hedge_mode,
        )

    if exchange_id == "gate":
        if mt == "spot":
            default_gate = "https://api-testnet.gateapi.io" if is_demo else "https://api.gateio.ws"
            base_url = default_gate if is_demo else (_get(exchange_config, "base_url", "baseUrl") or default_gate)
            return GateSpotClient(api_key=api_key, secret_key=secret_key, base_url=base_url)
        default_fut = "https://api-testnet.gateapi.io" if is_demo else "https://fx-api.gateio.ws"
        base_url = default_fut if is_demo else (_get(exchange_config, "base_url", "baseUrl") or default_fut)
        return GateUsdtFuturesClient(api_key=api_key, secret_key=secret_key, base_url=base_url)

    if exchange_id == "htx":
        spot_url = _get(exchange_config, "base_url", "baseUrl") or "https://api.htx.com"
        futures_url = _get(exchange_config, "futures_base_url", "futuresBaseUrl") or "https://api.hbdm.com"
        return HtxClient(
            api_key=api_key,
            secret_key=secret_key,
            base_url=spot_url,
            futures_base_url=futures_url,
            market_type=mt,
            margin_mode=_get(exchange_config, "margin_mode", "marginMode") or "cross",
        )

    # Traditional brokers (IBKR for US stocks only)
    if exchange_id == "ibkr":
        # Note: Market category validation should be done at the caller level
        # This factory only creates clients based on exchange_id
        return create_ibkr_client(exchange_config)

    # Alpaca: REST broker for US stocks + crypto (no local terminal needed).
    # Caller is responsible for validating market_category in (USStock, Crypto).
    if exchange_id == "alpaca":
        return create_alpaca_client(exchange_config)

    # Futu: local OpenD gateway for HKStock / USStock spot.
    if exchange_id == "futu":
        return create_futu_client(exchange_config, pooled=pooled, need_quote=need_quote)

    raise LiveTradingError(f"Unsupported exchange_id: {exchange_id}")


def create_ibkr_client(exchange_config: Dict[str, Any]):
    """
    Create IBKR client for US stock trading.

    exchange_config should contain:
    - ibkr_host: TWS/Gateway host (default: 127.0.0.1)
    - ibkr_port: TWS/Gateway port (default 7497 = TWS Paper per IB; Live TWS default 7496)
    - ibkr_client_id: Client ID (see below — must not collide with /api/ibkr UI)
    - ibkr_account: Account ID (optional, auto-select if empty)

    TWS allows one TCP session per clientId. The admin UI ``POST /api/ibkr/connect``
    defaults to clientId=1; live orders therefore default to ``IBKR_ORDER_CLIENT_ID``
    (7) when credentials omit ibkr_client_id, so manual testing does not evict the worker
    (and vice versa).
    """
    global IBKRClient, IBKRConfig

    # Lazy import to avoid ImportError if ib_insync not installed
    if IBKRClient is None or IBKRConfig is None:
        try:
            from app.services.ibkr_trading import IBKRClient as _IBKRClient, IBKRConfig as _IBKRConfig
            IBKRClient = _IBKRClient
            IBKRConfig = _IBKRConfig
        except ImportError:
            raise LiveTradingError("IBKR trading requires ib_insync. Run: pip install ib_insync")

    host = str(exchange_config.get("ibkr_host") or "127.0.0.1").strip()
    port = int(exchange_config.get("ibkr_port") or 7497)
    default_order_cid = int(os.getenv("IBKR_ORDER_CLIENT_ID", "7"))
    _cid_raw = exchange_config.get("ibkr_client_id")
    if _cid_raw is None or (isinstance(_cid_raw, str) and not str(_cid_raw).strip()):
        client_id = default_order_cid
    else:
        try:
            client_id = int(_cid_raw)
        except (TypeError, ValueError):
            client_id = default_order_cid
    account = str(exchange_config.get("ibkr_account") or "").strip()

    if client_id == 1:
        logger.warning(
            "IBKR strategy/order client uses clientId=1 — same default as POST /api/ibkr/connect; "
            "TWS will drop the other session. Prefer ibkr_client_id=7 or IBKR_ORDER_CLIENT_ID."
        )

    config = IBKRConfig(
        host=host,
        port=port,
        client_id=client_id,
        account=account,
        readonly=False,
    )

    client = IBKRClient(config)

    # Connect immediately (IBKR requires active connection)
    if not client.connect():
        raise LiveTradingError("Failed to connect to IBKR TWS/Gateway. Please check if it's running.")

    return client


def create_alpaca_client(exchange_config: Dict[str, Any]):
    """
    Create Alpaca client for US stock + crypto trading.

    exchange_config should contain:
    - api_key:    Alpaca API key (PK*=paper, AK*=live)
    - secret_key: Alpaca API secret
    - paper:      Boolean (default True). 'true'/'false' strings also accepted.
    - base_url:   Optional explicit URL override (otherwise paper/live decides)

    Unlike IBKR, Alpaca is stateless REST — no terminal/gateway needed,
    so it's the recommended USStock broker on cloud / SaaS deployments where
    ALLOW_LOCAL_DESKTOP_BROKERS is false.
    """
    global AlpacaClient, AlpacaConfig

    if AlpacaClient is None or AlpacaConfig is None:
        try:
            from app.services.alpaca_trading import AlpacaClient as _AlpacaClient, AlpacaConfig as _AlpacaConfig
            AlpacaClient = _AlpacaClient
            AlpacaConfig = _AlpacaConfig
        except ImportError:
            raise LiveTradingError("Alpaca trading requires alpaca-py. Run: pip install alpaca-py")

    api_key = (_get(exchange_config, "api_key", "apiKey") or "").strip()
    secret_key = (_get(exchange_config, "secret_key", "secret", "secretKey") or "").strip()
    if not api_key or not secret_key:
        raise LiveTradingError("Alpaca requires api_key and secret_key")

    # Paper mode: explicit flag wins; otherwise infer from key prefix (PK = paper).
    paper_raw = exchange_config.get("paper")
    if paper_raw is None:
        paper_raw = exchange_config.get("is_paper")
    if isinstance(paper_raw, bool):
        paper = paper_raw
    elif isinstance(paper_raw, str) and paper_raw.strip():
        paper = paper_raw.strip().lower() in ("1", "true", "yes", "on", "paper")
    else:
        paper = api_key.upper().startswith("PK")

    base_url = _get(exchange_config, "base_url", "baseUrl") or None

    config = AlpacaConfig(
        api_key=api_key,
        secret_key=secret_key,
        paper=paper,
        base_url=base_url,
    )

    client = AlpacaClient(config)
    if not client.connect():
        raise LiveTradingError(
            "Failed to connect to Alpaca (REST trading API). Check api_key/secret, "
            "paper/live (PK*=paper, AK*=live), and network access. "
            "HTTP 400 'invalid syntax' on market-data WebSocket is usually a bad "
            "auth/subscribe JSON or symbol (use BTC/USD not BTC/USDT for crypto)."
        )
    return client


def create_futu_client(
    exchange_config: Dict[str, Any],
    *,
    pooled: bool = True,
    need_quote: bool = True,
):
    """
    Create Futu client for HK / US stock trading via FutuOpenD.

    exchange_config should contain:
    - futu_host / host: OpenD host (default 127.0.0.1)
    - futu_port / port: OpenD port (default 11111)
    - trade_env / environment: demo | live
    - trade_market: HK | US
    - security_firm: FUTUSECURITIES | FUTUINC | FUTUSG | ...
    - acc_id: optional account id
    - unlock_password: optional (prefer GUI unlock for live)

    By default the process-wide session pool reuses one OpenD session per
    credential key. Pass ``pooled=False`` for a one-shot probe.
    """
    global FutuClient, FutuConfig

    if FutuClient is None or FutuConfig is None:
        try:
            from app.services.futu_trading import FutuClient as _FutuClient
            from app.services.futu_trading.config import (
                FutuConfig as _FutuConfig,
            )
            FutuClient = _FutuClient
            FutuConfig = _FutuConfig
        except ImportError:
            raise LiveTradingError("Futu trading requires futu-api. Run: pip install futu-api")

    from app.services.futu_trading.config import (
        config_from_exchange_config,
        validate_opend_host,
    )
    from app.utils.local_brokers import desktop_broker_cloud_reject_message, local_desktop_brokers_allowed

    if not local_desktop_brokers_allowed():
        raise LiveTradingError(desktop_broker_cloud_reject_message("futu"))

    host = str(exchange_config.get("futu_host") or exchange_config.get("host") or "127.0.0.1").strip()
    try:
        validate_opend_host(host)
    except ValueError as exc:
        raise LiveTradingError(str(exc)) from exc

    config = config_from_exchange_config(exchange_config)
    if pooled:
        from app.services.futu_trading.session_pool import get_futu_session_pool

        mode = "both" if need_quote else "trade"
        try:
            return get_futu_session_pool().acquire(exchange_config, mode=mode)
        except Exception as exc:
            raise LiveTradingError(
                "Failed to connect to FutuOpenD. Ensure OpenD is running and reachable "
                f"at {config.host}:{config.port}."
            ) from exc

    client = FutuClient(config)
    if not client.connect(need_quote=need_quote):
        raise LiveTradingError(
            "Failed to connect to FutuOpenD. Ensure OpenD is running and reachable "
            f"at {config.host}:{config.port}."
        )
    return client


def query_fee_rate(
    exchange_config: Dict[str, Any],
    symbol: str,
    market_type: str = "swap",
) -> Optional[Dict[str, float]]:
    """
    Best-effort: create a temporary client and query the account's fee tier
    for the given symbol.  Returns {"maker": 0.0002, "taker": 0.0005} or None.
    """
    try:
        client = create_client(exchange_config, market_type=market_type)
        return client.get_fee_rate(symbol, market_type=market_type)
    except Exception as e:
        logger.debug(f"query_fee_rate failed for {symbol}: {e}")
        return None


