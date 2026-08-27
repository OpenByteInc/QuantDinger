"""Futu OpenD connection configuration."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

_LOCAL_OPEND_HOSTNAMES = {"localhost", "host.docker.internal"}
_PRIVATE_OPEND_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fd00::/8")
)


def is_local_or_private_opend_host(host: Any) -> bool:
    """Allow loopback, approved local names, RFC1918 IPv4, and IPv6 ULA only."""
    value = str(host or "").strip().lower()
    if value in _LOCAL_OPEND_HOSTNAMES:
        return True
    try:
        address = ipaddress.ip_address(value.strip("[]"))
    except ValueError:
        return False
    return address.is_loopback or any(address in network for network in _PRIVATE_OPEND_NETWORKS)


def remote_opend_allowed() -> bool:
    return _truthy(os.getenv("FUTU_ALLOW_REMOTE_OPEND"))


def validate_opend_host(host: Any) -> str:
    """Validate every OpenD connection target before the SDK opens a socket."""
    value = str(host or "").strip()
    if remote_opend_allowed() or is_local_or_private_opend_host(value):
        return value
    raise ValueError(
        "FutuOpenD host must be localhost / private LAN unless "
        "FUTU_ALLOW_REMOTE_OPEND=true"
    )


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) == 1
    return str(value or "").strip().lower() in ("true", "1", "yes", "on")


def normalize_trade_env(value: Any, *, default: str = "demo") -> str:
    """Map credential / request values to QuantDinger env: demo | live."""
    raw = str(value or "").strip().lower()
    if raw in ("live", "real", "prod", "production", "mainnet"):
        return "live"
    if raw in ("demo", "paper", "simulate", "simulation", "simulated", "sim", "testnet", "sandbox"):
        return "demo"
    if _truthy(value) and raw in ("true", "1", "yes", "on"):
        # Bare true flags are treated as demo for safety.
        return "demo"
    return default if not raw else default


def normalize_security_firm(value: Any) -> str:
    raw = str(value or "").strip().upper()
    aliases = {
        "": "FUTUSECURITIES",
        "NONE": "NONE",
        "FUTU": "FUTUSECURITIES",
        "FUTUSECURITIES": "FUTUSECURITIES",
        "FUTUINC": "FUTUINC",
        "FUTUSG": "FUTUSG",
        "FUTUAU": "FUTUAU",
        "FUTUMY": "FUTUMY",
        "FUTUJP": "FUTUJP",
        "FUTUCA": "FUTUCA",
    }
    return aliases.get(raw, raw or "FUTUSECURITIES")


def normalize_trade_market(value: Any, *, market_category: str = "") -> str:
    """Return Futu TrdMarket name: HK | US | NONE."""
    raw = str(value or "").strip().upper()
    mc = str(market_category or "").strip()
    if raw in ("HK", "HKSTOCK", "HONGKONG", "HK_STOCK"):
        return "HK"
    if raw in ("US", "USSTOCK", "US_STOCK", "NYSE", "NASDAQ"):
        return "US"
    if raw in ("NONE", "ALL", ""):
        if mc == "HKStock":
            return "HK"
        if mc == "USStock":
            return "US"
        return "NONE"
    if mc == "HKStock":
        return "HK"
    if mc == "USStock":
        return "US"
    return raw or "NONE"


@dataclass
class FutuConfig:
    """Connection settings for FutuOpenD."""

    host: str = "127.0.0.1"
    port: int = 11111
    trade_env: str = "demo"  # demo -> SIMULATE, live -> REAL
    trade_market: str = "HK"  # HK | US | NONE
    security_firm: str = "FUTUSECURITIES"
    acc_id: int = 0
    unlock_password: str = ""
    is_encrypt: Optional[bool] = None
    timeout: float = 20.0
    market_category: str = ""

    def __post_init__(self) -> None:
        self.host = str(self.host or "127.0.0.1").strip() or "127.0.0.1"
        self.port = int(self.port or 11111)
        self.trade_env = normalize_trade_env(self.trade_env, default="demo")
        self.trade_market = normalize_trade_market(self.trade_market, market_category=self.market_category)
        self.security_firm = normalize_security_firm(self.security_firm)
        self.unlock_password = str(self.unlock_password or "")
        try:
            self.acc_id = int(self.acc_id or 0)
        except (TypeError, ValueError):
            self.acc_id = 0

    @property
    def is_simulate(self) -> bool:
        return self.trade_env != "live"

    def redacted_dict(self) -> Dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "trade_env": self.trade_env,
            "trade_market": self.trade_market,
            "security_firm": self.security_firm,
            "acc_id": self.acc_id or None,
            "is_encrypt": self.is_encrypt,
            "has_unlock_password": bool(self.unlock_password),
            "market_category": self.market_category or None,
        }


def config_from_exchange_config(exchange_config: Dict[str, Any]) -> FutuConfig:
    """Build FutuConfig from qd_exchange_credentials / exchange_config blob."""
    cfg = exchange_config if isinstance(exchange_config, dict) else {}
    env = normalize_trade_env(
        cfg.get("trade_env")
        or cfg.get("environment")
        or cfg.get("env")
        or cfg.get("network")
        or ("demo" if (
            _truthy(cfg.get("paper"))
            or _truthy(cfg.get("paper_trading"))
            or _truthy(cfg.get("enable_demo_trading"))
            or _truthy(cfg.get("simulated_trading"))
        ) else ""),
        default="demo",
    )
    market_category = str(
        cfg.get("market_category") or cfg.get("marketCategory") or ""
    ).strip()
    encrypt_raw = cfg.get("is_encrypt")
    if encrypt_raw is None:
        encrypt_raw = cfg.get("isEncrypt")
    is_encrypt: Optional[bool]
    if encrypt_raw is None or encrypt_raw == "":
        is_encrypt = None
    else:
        is_encrypt = _truthy(encrypt_raw)

    return FutuConfig(
        host=str(cfg.get("futu_host") or cfg.get("host") or "127.0.0.1").strip(),
        port=int(cfg.get("futu_port") or cfg.get("port") or 11111),
        trade_env=env,
        trade_market=normalize_trade_market(
            cfg.get("trade_market") or cfg.get("tradeMarket") or cfg.get("filter_trdmarket"),
            market_category=market_category,
        ),
        security_firm=normalize_security_firm(
            cfg.get("security_firm") or cfg.get("securityFirm")
        ),
        acc_id=int(cfg.get("acc_id") or cfg.get("accId") or 0),
        unlock_password=str(
            cfg.get("unlock_password")
            or cfg.get("unlockPassword")
            or cfg.get("trade_password")
            or cfg.get("password")
            or ""
        ),
        is_encrypt=is_encrypt,
        market_category=market_category,
    )
