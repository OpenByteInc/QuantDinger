"""Strategy leg context for ledger rows (market_type, credential, inst_id)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.services.live_trading.records import normalize_strategy_symbol
from app.utils.db import get_db_connection


@dataclass(frozen=True)
class LegContext:
    market_type: str = "swap"
    credential_id: int = 0
    inst_id: str = ""
    fill_source: str = ""
    pending_order_id: int = 0

    def normalized_market_type(self) -> str:
        mt = str(self.market_type or "swap").strip().lower()
        if mt in ("futures", "future", "perp", "perpetual"):
            return "swap"
        return mt or "swap"


def credential_id_from_exchange_config(exchange_config: Optional[Dict[str, Any]]) -> int:
    cfg = exchange_config if isinstance(exchange_config, dict) else {}
    for key in ("credential_id", "credentials_id"):
        try:
            v = int(cfg.get(key) or 0)
        except Exception:
            v = 0
        if v > 0:
            return v
    return 0


def inst_id_for_symbol(symbol: str, market_type: str, exchange_id: str = "") -> str:
    """Best-effort instId for OKX-style venues."""
    canon = normalize_strategy_symbol(symbol) or str(symbol or "").strip()
    if not canon or "/" not in canon:
        return ""
    base, quote = canon.split("/", 1)
    mt = str(market_type or "swap").strip().lower()
    ex = str(exchange_id or "").strip().lower()
    if mt == "spot":
        if ex in ("okx", "okex"):
            return f"{base}-{quote}"
        return canon.replace("/", "")
    # swap / perp
    if ex in ("okx", "okex"):
        return f"{base}-{quote}-SWAP"
    if ex in ("binance", "binanceusdm", "binancefutures"):
        return f"{base}{quote}"
    return f"{base}-{quote}-SWAP"


def resolve_leg_context(
    *,
    strategy_id: int,
    symbol: str = "",
    exchange_config: Optional[Dict[str, Any]] = None,
    market_type: Optional[str] = None,
    inst_id: str = "",
    fill_source: str = "",
    pending_order_id: int = 0,
) -> LegContext:
    """Load leg metadata from strategy row + call-site hints."""
    sid = int(strategy_id or 0)
    mt = str(market_type or "").strip().lower()
    cred = credential_id_from_exchange_config(exchange_config)
    exchange_id = str((exchange_config or {}).get("exchange_id") or "").strip().lower()

    if sid > 0 and (not mt or cred <= 0):
        try:
            with get_db_connection() as db:
                cur = db.cursor()
                cur.execute(
                    """
                    SELECT market_type, exchange_config
                    FROM qd_strategies_trading
                    WHERE id = %s
                    """,
                    (sid,),
                )
                row = cur.fetchone() or {}
                cur.close()
            if not mt:
                mt = str(row.get("market_type") or "swap").strip().lower()
            if cred <= 0:
                import json

                raw_cfg = row.get("exchange_config") or ""
                try:
                    parsed = json.loads(raw_cfg) if isinstance(raw_cfg, str) and raw_cfg.strip() else {}
                except Exception:
                    parsed = {}
                if isinstance(parsed, dict):
                    cred = credential_id_from_exchange_config(parsed)
                    if not exchange_id:
                        exchange_id = str(parsed.get("exchange_id") or "").strip().lower()
        except Exception:
            pass

    mt_norm = mt if mt not in ("futures", "future", "perp", "perpetual") else "swap"
    if not mt_norm:
        mt_norm = "swap"

    iid = str(inst_id or "").strip()
    if not iid and symbol:
        iid = inst_id_for_symbol(symbol, mt_norm, exchange_id)

    return LegContext(
        market_type=mt_norm,
        credential_id=int(cred or 0),
        inst_id=iid,
        fill_source=str(fill_source or "").strip(),
        pending_order_id=int(pending_order_id or 0),
    )
