"""
List all non-zero spot wallet coins for account snapshot UI.

Spot holdings are wallet balances, not derivative ``/positions`` rows.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.services.live_trading.records import normalize_strategy_symbol
from app.services.live_trading.spot_sizing import _pick_cost_from_row, _pick_free_from_row
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _spot_position_row(ccy: str, qty: float, *, entry_price: float = 0.0) -> Dict[str, Any]:
    ccy_u = str(ccy or "").strip().upper()
    if not ccy_u or qty <= 1e-10:
        return {}
    if ccy_u == "USDT":
        symbol, inst_id = "USDT", "USDT"
    else:
        symbol = normalize_strategy_symbol(f"{ccy_u}/USDT") or f"{ccy_u}/USDT"
        inst_id = f"{ccy_u}-USDT"
    return {
        "symbol": symbol,
        "side": "long",
        "size": float(qty),
        "entry_price": float(entry_price or 0.0),
        "market_type": "spot",
        "inst_id": inst_id,
    }


def _append_row(rows: List[Dict[str, Any]], ccy: str, qty: float, *, entry_price: float = 0.0) -> None:
    row = _spot_position_row(ccy, qty, entry_price=entry_price)
    if row:
        rows.append(row)


def _from_okx_balance(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    data = (raw.get("data") or []) if isinstance(raw, dict) else []
    first = data[0] if isinstance(data, list) and data else {}
    if not isinstance(first, dict):
        return rows
    for det in first.get("details") or []:
        if not isinstance(det, dict):
            continue
        ccy = str(det.get("ccy") or "").strip().upper()
        if not ccy:
            continue
        total = _pick_free_from_row(det, "eq", "cashBal")
        avail = _pick_free_from_row(det, "availBal", "cashBal")
        frozen = _pick_free_from_row(det, "frozenBal")
        qty = total if total > 0 else (avail + frozen if avail > 0 or frozen > 0 else avail)
        entry = _pick_cost_from_row(det, "openAvgPx", "accAvgPx", "avgPx", "avgCost")
        _append_row(rows, ccy, qty, entry_price=entry)
    return rows


def _from_binance_spot_account(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for bal in raw.get("balances") or []:
        if not isinstance(bal, dict):
            continue
        ccy = str(bal.get("asset") or "").strip().upper()
        if not ccy:
            continue
        free = _pick_free_from_row(bal, "free")
        locked = _pick_free_from_row(bal, "locked")
        qty = free + locked
        _append_row(rows, ccy, qty)
    return rows


def _from_bitget_assets(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    data = (raw.get("data") or []) if isinstance(raw, dict) else []
    for row in data if isinstance(data, list) else []:
        if not isinstance(row, dict):
            continue
        ccy = str(row.get("coin") or row.get("currency") or "").strip().upper()
        if not ccy:
            continue
        avail = _pick_free_from_row(row, "available", "avail", "free")
        frozen = _pick_free_from_row(row, "frozen", "lock")
        total = _pick_free_from_row(row, "total", "balance")
        qty = total if total > 0 else avail + frozen
        entry = _pick_cost_from_row(row, "averageOpenPrice", "avgOpenPrice", "avgCost", "openAvgPx")
        _append_row(rows, ccy, qty, entry_price=entry)
    return rows


def _from_bybit_spot_holdings(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    lst = ((raw.get("result") or {}).get("list") or []) if isinstance(raw, dict) else []
    for item in lst if isinstance(lst, list) else []:
        if not isinstance(item, dict):
            continue
        sym = str(item.get("symbol") or "")
        ccy = sym.split("/")[0].split(":")[0].strip().upper() if sym else ""
        if not ccy:
            continue
        qty = _pick_free_from_row(item, "bal", "walletBalance", "equity", "availableToWithdraw")
        _append_row(rows, ccy, qty)
    return rows


def _from_gate_spot_accounts(raw: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    items = raw if isinstance(raw, list) else []
    for row in items:
        if not isinstance(row, dict):
            continue
        ccy = str(row.get("currency") or "").strip().upper()
        if not ccy:
            continue
        avail = _pick_free_from_row(row, "available", "available_balance")
        locked = _pick_free_from_row(row, "locked", "freeze", "locked_amount")
        qty = avail + locked
        _append_row(rows, ccy, qty)
    return rows



def _from_htx_spot_balance(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    items = (((raw.get("data") or {}).get("list")) if isinstance(raw, dict) else None) or []
    for row in items if isinstance(items, list) else []:
        if not isinstance(row, dict):
            continue
        ccy = str(row.get("currency") or "").strip().upper()
        if not ccy:
            continue
        qty = _pick_free_from_row(row, "balance", "available")
        _append_row(rows, ccy, qty)
    return rows


def _from_kraken_balance(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    result = raw.get("result") if isinstance(raw, dict) else None
    if not isinstance(result, dict):
        return rows
    for key, val in result.items():
        try:
            qty = float(val or 0.0)
        except (TypeError, ValueError):
            continue
        if qty <= 1e-10:
            continue
        ccy = str(key or "").strip().upper().lstrip("X").lstrip("Z")
        if not ccy:
            continue
        _append_row(rows, ccy, qty)
    return rows



def list_spot_wallet_positions(client: Any) -> List[Dict[str, Any]]:
    """
    Best-effort: all non-zero spot wallet coins for account snapshot / UI.
    Returns rows compatible with ``account_snapshot`` (symbol, side, size, entry_price, market_type).
    """
    if client is None:
        return []

    try:
        from app.services.live_trading.okx import OkxClient

        if isinstance(client, OkxClient):
            return _from_okx_balance(client.get_balance() or {})
    except Exception as e:
        logger.debug("spot wallet okx: %s", e)

    try:
        from app.services.live_trading.binance_spot import BinanceSpotClient

        if isinstance(client, BinanceSpotClient):
            return _from_binance_spot_account(client.get_account() or {})
    except Exception as e:
        logger.debug("spot wallet binance: %s", e)

    try:
        from app.services.live_trading.bitget_spot import BitgetSpotClient

        if isinstance(client, BitgetSpotClient):
            return _from_bitget_assets(client.get_assets() or {})
    except Exception as e:
        logger.debug("spot wallet bitget: %s", e)

    try:
        from app.services.live_trading.bybit import BybitClient

        if isinstance(client, BybitClient):
            return _from_bybit_spot_holdings(client.get_spot_holdings() or {})
    except Exception as e:
        logger.debug("spot wallet bybit: %s", e)

    try:
        from app.services.live_trading.gate import GateSpotClient

        if isinstance(client, GateSpotClient):
            return _from_gate_spot_accounts(client.get_accounts() or [])
    except Exception as e:
        logger.debug("spot wallet gate: %s", e)

    try:
        from app.services.live_trading.htx import HtxClient

        if isinstance(client, HtxClient) and str(getattr(client, "market_type", "") or "").strip().lower() == "spot":
            return _from_htx_spot_balance(client.get_balance() or {})
    except Exception as e:
        logger.debug("spot wallet htx: %s", e)

    try:
        from app.services.live_trading.kraken import KrakenClient

        if isinstance(client, KrakenClient):
            return _from_kraken_balance(client.get_balance() or {})
    except Exception as e:
        logger.debug("spot wallet kraken: %s", e)

    return []
