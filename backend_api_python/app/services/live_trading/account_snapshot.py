"""
Live account snapshot for a saved credential: swap/spot positions + open orders.
Used by broker-accounts UI (not strategy L3 ledger).
"""

from __future__ import annotations

import time
import math
from typing import Any, Dict, List, Tuple

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
                or item.get("avgPrice")
                or item.get("entry_price")
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
                from app.services.live_trading.fill_accounting import contract_multiplier
                qm = contract_multiplier(client, 'gate', contract.replace('_', '/'))
                for leg in _parse_swap_position_items([row], market_type="swap"):
                    leg["size"] = float(leg.get("size") or 0) * qm
                    parsed.append(leg)
            return parsed
        if isinstance(client, HtxClient):
            resp = client.get_positions() or {}
            data = (resp.get("data") or []) if isinstance(resp, dict) else []
            from app.services.live_trading.fill_accounting import contract_multiplier
            parsed = []
            for row in data if isinstance(data, list) else []:
                symbol = str(row.get('contract_code') or '').replace('-', '/')
                multiplier = contract_multiplier(client, 'htx', symbol)
                for leg in _parse_swap_position_items([row], market_type='swap'):
                    leg['size'] = float(leg.get('size') or 0) * multiplier
                    parsed.append(leg)
            return parsed
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

    if ex in ("gate", "gateio"):
        orders.extend(_fetch_gate_open_orders(swap_client, spot_client, errors))
    elif ex in ("bybit", "bitget"):
        from app.services.live_trading.open_orders import fetch_exchange_open_orders

        for mt, order_client in (("spot", spot_client), ("swap", swap_client)):
            try:
                if order_client is None:
                    raise ValueError("missing_exchange_client")
                orders.extend(fetch_exchange_open_orders(order_client, exchange_id=ex, market_type=mt))
            except Exception:
                logger.warning("%s %s open orders failed", ex, mt, exc_info=True)
                errors.append(
                    "brokerAccounts.snapshotSpotOrdersFailed"
                    if mt == "spot"
                    else "brokerAccounts.snapshotSwapOrdersFailed"
                )

    return swap_pos, spot_pos, orders


def _gate_symbol(raw: Any) -> str:
    native = str(raw or "").strip().upper()
    if not native:
        return ""
    symbol = native.replace("-", "/").replace("_", "/")
    return normalize_strategy_symbol(symbol) or symbol


def _parse_gate_spot_orders(payload: Any) -> List[Dict[str, Any]]:
    """Flatten Gate's account-wide spot open-order groups."""
    rows: List[Dict[str, Any]] = []
    raw_groups = payload if isinstance(payload, list) else []
    for group in raw_groups:
        if not isinstance(group, dict):
            continue
        grouped_orders = group.get("orders")
        if isinstance(grouped_orders, list):
            native_pair = str(group.get("currency_pair") or "")
            candidates = [
                (order, native_pair)
                for order in grouped_orders
                if isinstance(order, dict)
            ]
        else:
            candidates = [(group, str(group.get("currency_pair") or ""))]
        for order, fallback_pair in candidates:
            native_pair = str(order.get("currency_pair") or fallback_pair).strip()
            symbol = _gate_symbol(native_pair)
            if not symbol:
                continue
            try:
                amount = abs(float(order.get("amount") or 0.0))
                raw_left = order.get("left")
                remaining = abs(
                    float(amount if raw_left is None or raw_left == "" else raw_left)
                )
                price = float(order.get("price") or 0.0)
            except (TypeError, ValueError):
                amount, remaining, price = 0.0, 0.0, 0.0
            rows.append(
                {
                    "symbol": symbol,
                    "side": str(order.get("side") or "").strip().lower(),
                    "market_type": "spot",
                    "order_type": str(order.get("type") or "limit").strip().lower(),
                    "price": price,
                    "amount": amount,
                    "filled": max(0.0, amount - remaining),
                    "exchange_order_id": str(order.get("id") or order.get("id_string") or ""),
                    "status": str(order.get("status") or "open"),
                    "inst_id": native_pair,
                }
            )
    return rows


def _parse_gate_futures_orders(payload: Any, *, client: Any = None) -> List[Dict[str, Any]]:
    """Normalize Gate futures contracts to the account snapshot order schema."""
    rows: List[Dict[str, Any]] = []
    multiplier_cache: Dict[str, float] = {}
    for order in payload if isinstance(payload, list) else []:
        if not isinstance(order, dict):
            continue
        contract = str(order.get("contract") or "").strip()
        symbol = _gate_symbol(contract)
        if not symbol:
            continue
        try:
            raw_size = order.get("amount")
            if raw_size is None or raw_size == "":
                raw_size = order.get("size")
            signed_size = float(raw_size or 0.0)
            raw_left = order.get("left")
            signed_left = float(
                signed_size if raw_left is None or raw_left == "" else raw_left
            )
            price = float(order.get("price") or 0.0)
        except (TypeError, ValueError):
            signed_size, signed_left, price = 0.0, 0.0, 0.0

        multiplier = multiplier_cache.get(contract)
        if multiplier is None:
            from app.services.live_trading.fill_accounting import contract_multiplier
            multiplier = contract_multiplier(client, 'gate', contract.replace('_', '/'))
            multiplier_cache[contract] = multiplier
        amount = abs(signed_size) * multiplier
        remaining = abs(signed_left) * multiplier
        rows.append(
            {
                "symbol": symbol,
                "side": "buy" if signed_size > 0 else "sell" if signed_size < 0 else "",
                "market_type": "swap",
                "order_type": "market" if price <= 0 else "limit",
                "price": price,
                "amount": amount,
                "filled": max(0.0, amount - remaining),
                "exchange_order_id": str(order.get("id_string") or order.get("id") or ""),
                "status": str(order.get("status") or "open"),
                "inst_id": contract,
            }
        )
    return rows


def _fetch_gate_open_orders(
    swap_client: Any,
    spot_client: Any,
    errors: List[str],
) -> List[Dict[str, Any]]:
    from app.services.live_trading.gate import GateSpotClient, GateUsdtFuturesClient
    from app.services.live_trading.open_orders import fetch_exchange_open_orders

    orders: List[Dict[str, Any]] = []
    if isinstance(swap_client, GateUsdtFuturesClient):
        try:
            orders.extend(fetch_exchange_open_orders(swap_client, exchange_id="gate", market_type="swap"))
        except Exception as e:
            _append_snapshot_error(errors, e, context="GATE 合约挂单")
    if isinstance(spot_client, GateSpotClient):
        try:
            orders.extend(fetch_exchange_open_orders(spot_client, exchange_id="gate", market_type="spot"))
        except Exception as e:
            _append_snapshot_error(errors, e, context="GATE 现货挂单")
    return orders


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
    from app.services.live_trading.open_orders import fetch_exchange_open_orders

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
            orders.extend(fetch_exchange_open_orders(client, exchange_id="okx", market_type=mt))
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


def _fetch_alpaca_snapshot(exchange_config: Dict[str, Any], errors: List[str]) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    from app.services.alpaca_trading.symbols import parse_symbol

    positions: List[Dict[str, Any]] = []
    orders: List[Dict[str, Any]] = []
    try:
        client = create_client(exchange_config, market_type="spot")
    except Exception:
        logger.warning("Alpaca snapshot connection failed", exc_info=True)
        errors.append("brokerAccounts.snapshotConnectionFailed")
        return [], positions, orders

    def instrument(item):
        asset_class = str(item.get("asset_class") or "").lower()
        hint = "Crypto" if asset_class == "crypto" else "USStock" if asset_class == "us_equity" else None
        symbol, asset_class = parse_symbol(str(item.get("symbol") or ""), market_hint=hint)
        return {"symbol": symbol, "inst_id": str(item.get("symbol") or ""), "market_type": "spot",
                "market": "Crypto" if asset_class == "crypto" else "USStock", "asset_class": asset_class}

    try:
        for item in client.get_positions(raise_on_error=True):
            quantity = float(item.get("qty") or item.get("quantity") or 0)
            if not math.isfinite(quantity):
                raise ValueError("invalid_position_quantity")
            if not quantity:
                continue
            positions.append({
                **instrument(item),
                "side": "short" if quantity < 0 or str(item.get("side")).lower() == "short" else "long",
                "size": abs(quantity),
                "entry_price": float(item.get("avg_entry_price") or item.get("avgCost") or 0),
                "mark_price": float(item.get("current_price") or item.get("currentPrice") or 0),
                "market_value": float(item.get("market_value") or item.get("marketValue") or 0),
                "unrealized_pnl": float(item.get("unrealized_pnl") or item.get("unrealizedPnL") or 0),
            })
    except Exception:
        logger.warning("Alpaca snapshot positions failed", exc_info=True)
        errors.append("brokerAccounts.snapshotPositionsFailed")
    try:
        for item in client.get_orders(status="open", limit=500, raise_on_error=True):
            orders.append({
                **instrument(item),
                "exchange_order_id": str(item.get("id") or item.get("orderId") or ""),
                "side": str(item.get("side") or "").lower(),
                "order_type": str(item.get("order_type") or item.get("orderType") or "").lower(),
                "price": float(item.get("limit_price") or item.get("limitPrice") or 0),
                "amount": abs(float(item.get("qty") or item.get("quantity") or 0)),
                "filled": abs(float(item.get("filled_qty") or item.get("filled") or 0)),
                "notional": float(item.get("notional") or 0),
                "status": str(item.get("status") or ""),
            })
    except Exception:
        logger.warning("Alpaca snapshot orders failed", exc_info=True)
        errors.append("brokerAccounts.snapshotOrdersFailed")
    return [], positions, orders


def _fetch_equity_broker_snapshot(exchange_config, errors):
    from app.services.live_trading.broker_snapshot import broker_position_maps
    positions, orders = [], []
    try:
        client = create_client(exchange_config, market_type="spot")
        if getattr(client, "is_futures", False):
            raise ValueError("Futures require the dedicated account endpoint")
    except Exception:
        errors.append("brokerAccounts.snapshotConnectionFailed")
        return [], positions, orders
    try:
        sizes, prices, instruments = broker_position_maps(client.get_positions(raise_on_error=True))
        for symbol, sides in sizes.items():
            for side, size in sides.items():
                if size > 0:
                    positions.append({"symbol": symbol, "side": side, "size": size,
                                      "entry_price": prices[symbol][side], "inst_id": instruments[symbol][side],
                                      "market_type": "spot"})
    except Exception:
        errors.append("brokerAccounts.snapshotPositionsFailed")
    try:
        rows = (client.get_open_orders(raise_on_error=True) if exchange_config.get("exchange_id") == "ibkr"
                else client.get_orders(status="open", limit=500, raise_on_error=True))
        for item in rows:
            orders.append({
                "symbol": normalize_strategy_symbol(item.get("symbol")), "market_type": "spot",
                "exchange_order_id": str(item.get("id") or item.get("orderId") or ""),
                "side": str(item.get("side") or item.get("action") or "").lower(),
                "order_type": str(item.get("order_type") or item.get("orderType") or "").lower(),
                "price": float(item.get("limit_price") or item.get("limitPrice") or 0),
                "amount": abs(float(item.get("qty") or item.get("quantity") or 0)),
                "filled": abs(float(item.get("filled_qty") or item.get("filled") or 0)),
                "status": str(item.get("status") or ""),
            })
    except Exception:
        orders = []
        errors.append("brokerAccounts.snapshotOrdersFailed")
    return [], positions, orders


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

    if exchange_id == "alpaca":
        sp, st, od = _fetch_alpaca_snapshot(exchange_config, errors)
        swap_all.extend(sp)
        spot_all.extend(st)
        orders_all.extend(od)
    elif exchange_id in {"tiger", "webull", "forthright", "ibkr"}:
        sp, st, od = _fetch_equity_broker_snapshot(exchange_config, errors)
        swap_all.extend(sp)
        spot_all.extend(st)
        orders_all.extend(od)
    elif exchange_id in ("okx", "okex"):
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
        market_type = str(o.get("market_type") or "").strip().lower()
        key = (
            f"{market_type}:{oid}"
            if oid
            else f"{market_type}:{o.get('symbol')}-{o.get('side')}-{o.get('price')}"
        )
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
