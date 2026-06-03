"""
Live account snapshot for a saved credential: swap/spot positions + open orders.
Used by broker-accounts UI (not strategy L3 ledger).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from app.services.exchange_execution import resolve_exchange_config
from app.services.live_trading.factory import create_client
from app.services.live_trading.records import normalize_strategy_symbol
from app.services.live_trading.spot_wallet_snapshot import list_spot_wallet_positions
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _user_facing_exchange_error(exc: Exception, *, context: str) -> str:
    """Map raw exchange exception to a short UI message."""
    msg = str(exc or "")
    low = msg.lower()
    if "451" in msg or "restricted location" in low or "service unavailable from a restricted location" in low:
        return (
            "Binance 地区限制 (HTTP 451)：当前 backend 服务器/IP 所在地区不在 Binance 服务范围内。"
            "请将 Docker 部署到 Binance 允许的地区，或为 backend 配置 HTTP 代理后重试；"
            "也可改用 OKX / Bitget 等交易所。"
        )
    if "50119" in msg or "api key doesn't exist" in low:
        return f"{context}：API Key 无效或已在 OKX 删除，请到个人中心重新绑定凭证"
    if "50111" in msg or "invalid ok-access-key" in low:
        return f"{context}：API Key 无效，请检查凭证是否正确"
    if "50113" in msg or "invalid sign" in low:
        return f"{context}：签名错误，请检查 Secret Key 与 Passphrase"
    if "401" in msg or "403" in msg:
        return f"{context}：交易所鉴权失败，请更新 API Key / Secret / Passphrase"
    if "418" in msg or "rate limit" in low or "too many requests" in low:
        return f"{context}：请求过于频繁，请稍后再试"
    short = msg.replace("\n", " ").strip()
    if len(short) > 160:
        short = short[:160] + "…"
    return f"{context}：{short}" if short else f"{context}：拉取失败"


def _error_fingerprint(line: str) -> str:
    """Group equivalent exchange errors (e.g. same 451 on swap + spot)."""
    low = str(line or "").lower()
    if "451" in line or "restricted location" in low:
        return "geo:451"
    if "50119" in line or "api key doesn't exist" in low:
        return "auth:50119"
    if "50111" in line or "invalid ok-access-key" in low:
        return "auth:50111"
    if "401" in line or "403" in line or "鉴权失败" in line:
        return "auth:http"
    if "418" in line or "rate limit" in low:
        return "rate:limit"
    return line.strip()


def _append_snapshot_error(errors: List[str], exc: Exception, *, context: str) -> None:
    line = _user_facing_exchange_error(exc, context=context)
    if line not in errors:
        errors.append(line)
    logger.warning("%s: %s", context, exc)


def _parse_okx_positions(
    data: List[Dict[str, Any]],
    *,
    market_type: str,
    client: Any = None,
) -> List[Dict[str, Any]]:
    from app.services.grid.fill_units import okx_swap_position_base_size

    okx_client = client if client is not None and hasattr(client, "get_instrument") else None
    out: List[Dict[str, Any]] = []
    for p in data or []:
        if not isinstance(p, dict):
            continue
        inst_id = str(p.get("instId") or "")
        pos_side = str(p.get("posSide") or "").lower()
        try:
            pos = float(p.get("pos") or 0.0)
        except Exception:
            pos = 0.0
        if not inst_id or abs(pos) <= 0:
            continue
        hb_sym = inst_id.replace("-SWAP", "").replace("-", "/")
        if pos_side == "long":
            side = "long"
        elif pos_side == "short":
            side = "short"
        elif pos_side == "net":
            side = "long" if pos > 0 else "short"
        else:
            side = "long" if pos > 0 else "short"
        try:
            entry = float(p.get("avgPx") or 0.0)
        except Exception:
            entry = 0.0
        if str(market_type or "").strip().lower() == "swap" and okx_client is not None:
            qty_base = okx_swap_position_base_size(p, client=okx_client)
        else:
            qty_base = abs(float(pos))
        if qty_base <= 0:
            continue
        out.append(
            {
                "symbol": normalize_strategy_symbol(hb_sym) or hb_sym,
                "side": side,
                "size": qty_base,
                "entry_price": entry,
                "market_type": market_type,
                "inst_id": inst_id,
            }
        )
    return out


def _parse_okx_spot_balances(balance_resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Backward-compatible wrapper; prefer ``list_spot_wallet_positions(OkxClient)``."""
    from app.services.live_trading.spot_wallet_snapshot import _from_okx_balance

    return _from_okx_balance(balance_resp)


def _symbol_from_position_item(item: Dict[str, Any]) -> str:
    sym_raw = str(
        item.get("instId")
        or item.get("symbol")
        or item.get("contract")
        or item.get("contract_code")
        or ""
    ).strip()
    if not sym_raw:
        return ""
    hb = sym_raw.replace("-SWAP", "").replace("_", "/").replace("-", "/")
    if hb.endswith("USDT") and "/" not in hb and len(hb) > 4:
        hb = f"{hb[:-4]}/USDT"
    return normalize_strategy_symbol(hb) or hb


def _parse_swap_position_items(items: List[Dict[str, Any]], *, market_type: str = "swap") -> List[Dict[str, Any]]:
    from app.services.live_trading.position_row_parse import (
        extract_signed_position_qty,
        infer_position_side_from_row,
    )

    out: List[Dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        side = infer_position_side_from_row(item)
        qty = abs(extract_signed_position_qty(item))
        if qty <= 1e-10:
            continue
        sym = _symbol_from_position_item(item)
        if not sym:
            continue
        try:
            entry = float(
                item.get("avgPx")
                or item.get("entryPrice")
                or item.get("openPriceAvg")
                or item.get("avgEntryPrice")
                or 0.0
            )
        except (TypeError, ValueError):
            entry = 0.0
        inst_id = str(item.get("instId") or item.get("symbol") or item.get("contract") or sym)
        out.append(
            {
                "symbol": sym,
                "side": side,
                "size": qty,
                "entry_price": entry,
                "market_type": market_type,
                "inst_id": inst_id,
            }
        )
    return out


def _fetch_spot_wallet(client: Any, errors: List[str], *, label: str) -> List[Dict[str, Any]]:
    if client is None:
        return []
    try:
        return list_spot_wallet_positions(client)
    except Exception as e:
        _append_snapshot_error(errors, e, context=label)
        return []


def _fetch_swap_positions_snapshot(client: Any, exchange_id: str, errors: List[str]) -> List[Dict[str, Any]]:
    if client is None:
        return []
    ex = str(exchange_id or "").strip().lower()
    label = f"{ex.upper() or 'EXCHANGE'} 合约持仓"
    try:
        from app.services.live_trading.binance import BinanceFuturesClient
        from app.services.live_trading.bitget import BitgetMixClient
        from app.services.live_trading.bybit import BybitClient
        from app.services.live_trading.gate import GateUsdtFuturesClient
        from app.services.live_trading.htx import HtxClient
        from app.services.live_trading.okx import OkxClient

        if isinstance(client, OkxClient):
            resp = client.get_positions(inst_type="SWAP") or {}
            data = (resp.get("data") or []) if isinstance(resp, dict) else []
            return _parse_okx_positions(data, market_type="swap", client=client)
        if isinstance(client, BinanceFuturesClient):
            rows = client.get_positions() or []
            if isinstance(rows, dict) and "raw" in rows:
                rows = rows["raw"]
            return _parse_binance_futures_positions(rows if isinstance(rows, list) else [])
        if isinstance(client, BybitClient):
            resp = client.get_positions() or {}
            items = ((resp.get("result") or {}).get("list") or []) if isinstance(resp, dict) else []
            return _parse_swap_position_items(items if isinstance(items, list) else [], market_type="swap")
        if isinstance(client, BitgetMixClient):
            resp = client.get_positions(product_type="USDT-FUTURES") or {}
            items = (resp.get("data") or []) if isinstance(resp, dict) else []
            return _parse_swap_position_items(items if isinstance(items, list) else [], market_type="swap")
        if isinstance(client, GateUsdtFuturesClient):
            resp = client.get_positions()
            items = resp if isinstance(resp, list) else []
            parsed: List[Dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                contract = str(item.get("contract") or "").strip()
                try:
                    sz_ct = float(item.get("size") or 0.0)
                except (TypeError, ValueError):
                    sz_ct = 0.0
                if abs(sz_ct) <= 0:
                    continue
                row = dict(item)
                row["size"] = sz_ct
                row["symbol"] = contract
                qm = 1.0
                try:
                    meta = client.get_contract(contract=contract) or {}
                    qm = float(meta.get("quanto_multiplier") or meta.get("contract_size") or 0.0) or 1.0
                except Exception:
                    qm = 1.0
                for leg in _parse_swap_position_items([row], market_type="swap"):
                    leg["size"] = float(leg.get("size") or 0) * qm
                    parsed.append(leg)
            return parsed
        if isinstance(client, HtxClient):
            resp = client.get_positions() or {}
            data = (resp.get("data") or []) if isinstance(resp, dict) else []
            return _parse_swap_position_items(data if isinstance(data, list) else [], market_type="swap")
        if hasattr(client, "get_positions"):
            resp = client.get_positions() or {}
            if isinstance(resp, list):
                return _parse_swap_position_items(resp, market_type="swap")
            data = resp.get("data") if isinstance(resp, dict) else resp
            if isinstance(data, dict):
                data = data.get("list") or [data]
            if isinstance(data, list):
                return _parse_swap_position_items(data, market_type="swap")
    except Exception as e:
        _append_snapshot_error(errors, e, context=label)
    return []


def _fetch_multi_crypto_snapshot(
    exchange_config: Dict[str, Any],
    exchange_id: str,
    errors: List[str],
) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    ex = str(exchange_id or "").strip().lower()
    swap_pos: List[Dict[str, Any]] = []
    spot_pos: List[Dict[str, Any]] = []
    orders: List[Dict[str, Any]] = []

    swap_client = None
    try:
        swap_client = create_client(exchange_config, market_type="swap")
        swap_pos = _fetch_swap_positions_snapshot(swap_client, ex, errors)
    except Exception as e:
        _append_snapshot_error(errors, e, context=f"{ex.upper()} 合约连接")

    spot_client = None
    try:
        spot_client = create_client(exchange_config, market_type="spot")
    except Exception:
        spot_client = swap_client
    spot_pos = _fetch_spot_wallet(spot_client, errors, label=f"{ex.upper()} 现货持仓")
    if not spot_pos and swap_client is not None and spot_client is not swap_client:
        spot_pos = _fetch_spot_wallet(swap_client, errors, label=f"{ex.upper()} 现货持仓")

    return swap_pos, spot_pos, orders


def _parse_binance_futures_positions(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for p in rows or []:
        if not isinstance(p, dict):
            continue
        sym = str(p.get("symbol") or "").strip().upper()
        try:
            amt = float(p.get("positionAmt") or 0.0)
            ep = float(p.get("entryPrice") or 0.0)
        except Exception:
            amt = 0.0
            ep = 0.0
        if not sym or abs(amt) <= 0:
            continue
        hb_sym = sym
        if hb_sym.endswith("USDT") and len(hb_sym) > 4 and "/" not in hb_sym:
            hb_sym = f"{hb_sym[:-4]}/USDT"
        side = "long" if amt > 0 else "short"
        out.append(
            {
                "symbol": normalize_strategy_symbol(hb_sym) or hb_sym,
                "side": side,
                "size": abs(float(amt)),
                "entry_price": ep,
                "market_type": "swap",
                "inst_id": sym,
            }
        )
    return out


def _parse_okx_orders(data: List[Dict[str, Any]], *, market_type: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for o in data or []:
        if not isinstance(o, dict):
            continue
        inst_id = str(o.get("instId") or "")
        if not inst_id:
            continue
        sym = inst_id.replace("-SWAP", "").replace("-", "/")
        try:
            px = float(o.get("px") or 0.0)
            sz = float(o.get("sz") or 0.0)
            filled = float(o.get("accFillSz") or 0.0)
        except Exception:
            px, sz, filled = 0.0, 0.0, 0.0
        side_raw = str(o.get("side") or "").lower()
        side = "buy" if side_raw == "buy" else "sell" if side_raw == "sell" else side_raw
        out.append(
            {
                "symbol": normalize_strategy_symbol(sym) or sym,
                "side": side,
                "market_type": market_type,
                "order_type": str(o.get("ordType") or ""),
                "price": px,
                "amount": sz,
                "filled": filled,
                "exchange_order_id": str(o.get("ordId") or ""),
                "status": str(o.get("state") or "live"),
                "inst_id": inst_id,
            }
        )
    return out


def _parse_binance_orders(rows: List[Dict[str, Any]], *, market_type: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for o in rows or []:
        if not isinstance(o, dict):
            continue
        sym = str(o.get("symbol") or "")
        if not sym:
            continue
        hb = sym
        if hb.endswith("USDT") and "/" not in hb and len(hb) > 4:
            hb = f"{hb[:-4]}/USDT"
        try:
            px = float(o.get("price") or 0.0)
            sz = float(o.get("origQty") or 0.0)
            filled = float(o.get("executedQty") or 0.0)
        except Exception:
            px, sz, filled = 0.0, 0.0, 0.0
        side = str(o.get("side") or "").lower()
        out.append(
            {
                "symbol": normalize_strategy_symbol(hb) or hb,
                "side": side,
                "market_type": market_type,
                "order_type": str(o.get("type") or ""),
                "price": px,
                "amount": sz,
                "filled": filled,
                "exchange_order_id": str(o.get("orderId") or ""),
                "status": str(o.get("status") or "NEW"),
                "inst_id": sym,
            }
        )
    return out


def _fetch_okx_snapshot(
    client, exchange_id: str, errors: List[str]
) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    from app.services.live_trading.okx import OkxClient

    if not isinstance(client, OkxClient):
        return [], [], []
    swap_pos: List[Dict[str, Any]] = []
    spot_pos: List[Dict[str, Any]] = []
    orders: List[Dict[str, Any]] = []
    try:
        resp = client.get_positions(inst_type="SWAP")
        data = (resp.get("data") or []) if isinstance(resp, dict) else []
        swap_pos = _parse_okx_positions(data, market_type="swap", client=client)
    except Exception as e:
        _append_snapshot_error(errors, e, context="OKX 合约持仓")
    try:
        spot_pos = _fetch_spot_wallet(client, errors, label="OKX 现货持仓")
    except Exception as e:
        _append_snapshot_error(errors, e, context="OKX 现货持仓")
    for inst_type, mt, label in (
        ("SWAP", "swap", "OKX 合约挂单"),
        ("SPOT", "spot", "OKX 现货挂单"),
    ):
        try:
            resp = client._signed_request(
                "GET", "/api/v5/trade/orders-pending", params={"instType": inst_type}
            )
            data = (resp.get("data") or []) if isinstance(resp, dict) else []
            orders.extend(_parse_okx_orders(data, market_type=mt))
        except Exception as e:
            _append_snapshot_error(errors, e, context=label)
    return swap_pos, spot_pos, orders


def _as_list_payload(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        raw = data.get("raw")
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
        inner = data.get("data")
        if isinstance(inner, list):
            return [x for x in inner if isinstance(x, dict)]
    return []


def _fetch_binance_snapshot(
    client, market_type: str, errors: List[str]
) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    from app.services.live_trading.binance import BinanceFuturesClient
    from app.services.live_trading.binance_spot import BinanceSpotClient

    swap_pos: List[Dict[str, Any]] = []
    spot_pos: List[Dict[str, Any]] = []
    orders: List[Dict[str, Any]] = []
    if isinstance(client, BinanceFuturesClient):
        try:
            rows = client.get_positions() or []
            if isinstance(rows, dict) and "raw" in rows:
                rows = rows["raw"]
            swap_pos = _parse_binance_futures_positions(rows if isinstance(rows, list) else [])
        except Exception as e:
            _append_snapshot_error(errors, e, context="Binance 合约持仓")
        try:
            rows = client._signed_request("GET", "/fapi/v1/openOrders", params={})
            orders = _parse_binance_orders(_as_list_payload(rows), market_type="swap")
        except Exception as e:
            _append_snapshot_error(errors, e, context="Binance 合约挂单")
    elif isinstance(client, BinanceSpotClient):
        try:
            spot_pos = _fetch_spot_wallet(client, errors, label="Binance 现货持仓")
        except Exception as e:
            _append_snapshot_error(errors, e, context="Binance 现货持仓")
        try:
            rows = client._signed_request("GET", "/api/v3/openOrders", params={})
            orders = _parse_binance_orders(_as_list_payload(rows), market_type="spot")
        except Exception as e:
            _append_snapshot_error(errors, e, context="Binance 现货挂单")
    return swap_pos, spot_pos, orders


def fetch_account_snapshot(*, user_id: int, credential_id: int) -> Dict[str, Any]:
    """Live fetch swap/spot legs + open orders for one credential."""
    cred = int(credential_id or 0)
    errors: List[str] = []
    if cred <= 0:
        return {
            "swap_positions": [],
            "spot_positions": [],
            "open_orders": [],
            "fetched_at": int(time.time()),
            "error": "missing_credential_id",
            "warnings": ["缺少 credential_id"],
        }
    exchange_config = resolve_exchange_config({"credential_id": cred}, user_id=int(user_id))
    exchange_id = str(exchange_config.get("exchange_id") or "").strip().lower()
    if not exchange_id:
        return {
            "swap_positions": [],
            "spot_positions": [],
            "open_orders": [],
            "fetched_at": int(time.time()),
            "error": "missing_exchange_id",
            "warnings": ["凭证未配置 exchange_id"],
        }

    swap_all: List[Dict[str, Any]] = []
    spot_all: List[Dict[str, Any]] = []
    orders_all: List[Dict[str, Any]] = []

    if exchange_id in ("okx", "okex"):
        try:
            client = create_client(exchange_config, market_type="swap")
            sp, st, od = _fetch_okx_snapshot(client, exchange_id, errors)
            swap_all.extend(sp)
            spot_all.extend(st)
            orders_all.extend(od)
        except Exception as e:
            _append_snapshot_error(errors, e, context="OKX 账户连接")
    elif exchange_id in ("binance", "binanceusdm", "binancefutures"):
        for market_type in ("swap", "spot"):
            try:
                client = create_client(exchange_config, market_type=market_type)
            except Exception as e:
                _append_snapshot_error(errors, e, context=f"Binance {market_type} 连接")
                continue
            sp, st, od = _fetch_binance_snapshot(client, market_type, errors)
            swap_all.extend(sp)
            spot_all.extend(st)
            orders_all.extend(od)
    elif exchange_id in (
        "bitget",
        "bybit",
        "gate",
        "gateio",
        "htx",
        "huobi",
        "kraken",
        "coinbase",
        "coinbaseexchange",
    ):
        sp, st, od = _fetch_multi_crypto_snapshot(exchange_config, exchange_id, errors)
        swap_all.extend(sp)
        spot_all.extend(st)
        orders_all.extend(od)
    else:
        try:
            client = create_client(exchange_config, market_type="swap")
            from app.services.live_trading.binance import BinanceFuturesClient

            if isinstance(client, BinanceFuturesClient):
                sp, st, od = _fetch_binance_snapshot(client, "swap", errors)
                swap_all.extend(sp)
                spot_all.extend(st)
                orders_all.extend(od)
            else:
                sp, st, od = _fetch_multi_crypto_snapshot(exchange_config, exchange_id, errors)
                swap_all.extend(sp)
                spot_all.extend(st)
                orders_all.extend(od)
        except Exception as e:
            _append_snapshot_error(errors, e, context=f"{exchange_id} 账户连接")

    # De-dupe orders by exchange_order_id
    seen: set = set()
    deduped_orders: List[Dict[str, Any]] = []
    for o in orders_all:
        oid = str(o.get("exchange_order_id") or "")
        key = oid or f"{o.get('symbol')}-{o.get('side')}-{o.get('price')}"
        if key in seen:
            continue
        seen.add(key)
        deduped_orders.append(o)

    has_data = bool(swap_all or spot_all or deduped_orders)
    uniq_errors: List[str] = []
    seen_fp: set = set()
    for e in errors:
        fp = _error_fingerprint(e)
        if fp in seen_fp:
            continue
        seen_fp.add(fp)
        uniq_errors.append(e)

    out: Dict[str, Any] = {
        "swap_positions": swap_all,
        "spot_positions": spot_all,
        "open_orders": deduped_orders,
        "fetched_at": int(time.time()),
        "exchange_id": exchange_id,
        "warnings": uniq_errors,
        "partial": bool(uniq_errors) and has_data,
    }
    if uniq_errors and not has_data:
        out["error"] = uniq_errors[0]
    elif uniq_errors:
        out["error"] = ""
    return out
