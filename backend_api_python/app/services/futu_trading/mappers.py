"""Map Futu enums / status strings onto QuantDinger canonical values."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


# Futu OrderStatus → QuantDinger pending_orders / worker status
_ORDER_STATUS_MAP = {
    "NONE": "submitted",
    "UNSUBMITTED": "submitted",
    "WAITING_SUBMIT": "submitted",
    "SUBMITTING": "submitted",
    "SUBMITTED": "submitted",
    "FILLED_PART": "partially_filled",
    "FILLED_ALL": "filled",
    "CANCELLED_PART": "cancelled",
    "CANCELLED_ALL": "cancelled",
    "FAILED": "rejected",
    "DISABLED": "rejected",
    "DELETED": "cancelled",
    # Lowercase / alternate spellings seen in DataFrames / str(enum)
    "filled_part": "partially_filled",
    "filled_all": "filled",
    "cancelled_part": "cancelled",
    "cancelled_all": "cancelled",
    "canceled_part": "cancelled",
    "canceled_all": "cancelled",
    "partially_filled": "partially_filled",
    "filled": "filled",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "rejected": "rejected",
    "failed": "rejected",
    "submitted": "submitted",
    "open": "submitted",
    "new": "submitted",
}


def normalize_order_status(value: Any) -> str:
    if value is None:
        return "submitted"
    if hasattr(value, "name"):
        key = str(value.name)
    else:
        key = str(value).strip()
        # "OrderStatus.SUBMITTED" → SUBMITTED
        if "." in key:
            key = key.split(".")[-1]
    mapped = _ORDER_STATUS_MAP.get(key) or _ORDER_STATUS_MAP.get(key.upper()) or _ORDER_STATUS_MAP.get(key.lower())
    return mapped or "submitted"


def is_terminal_status(status: str) -> bool:
    return normalize_order_status(status) in ("filled", "cancelled", "rejected")


def is_final_fill_status(status: str) -> bool:
    return normalize_order_status(status) == "filled"


def side_to_futu(side: str) -> str:
    s = str(side or "").strip().lower()
    if s in ("buy", "long", "open_long", "add_long"):
        return "BUY"
    if s in ("sell", "short", "close_long", "reduce_long"):
        return "SELL"
    return s.upper() or "BUY"


def side_from_futu(side: Any) -> str:
    if hasattr(side, "name"):
        raw = str(side.name).upper()
    else:
        raw = str(side or "").strip().upper()
        if "." in raw:
            raw = raw.split(".")[-1]
    if raw in ("BUY", "BUY_BACK"):
        return "buy"
    if raw in ("SELL", "SELL_SHORT"):
        return "sell"
    return raw.lower()


def order_type_to_futu(order_type: str) -> str:
    ot = str(order_type or "market").strip().lower()
    if ot in ("limit", "normal", "lmt"):
        return "NORMAL"
    if ot in ("market", "mkt"):
        return "MARKET"
    return "MARKET"


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def row_get(row: Any, *keys: str, default: Any = None) -> Any:
    """Read a value from a pandas Series, dict, or object."""
    if row is None:
        return default
    for key in keys:
        if isinstance(row, dict) and key in row:
            return row.get(key)
        try:
            # pandas Series
            if hasattr(row, "get"):
                val = row.get(key)
                if val is not None:
                    return val
        except Exception:
            pass
        try:
            if hasattr(row, "__getitem__"):
                val = row[key]
                if val is not None:
                    return val
        except Exception:
            pass
        if hasattr(row, key):
            return getattr(row, key)
    return default


def order_row_to_raw(row: Any) -> Dict[str, Any]:
    """Normalize one Futu order_list_query / place_order row into a dict."""
    if row is None:
        return {}
    if isinstance(row, dict):
        data = dict(row)
    else:
        try:
            data = dict(row)
        except Exception:
            data = {}
            for key in (
                "order_id", "orderid", "code", "stock_name", "trd_side", "order_type",
                "order_status", "qty", "price", "dealt_qty", "dealt_avg_price",
                "currency", "remark", "create_time", "updated_time", "aux_price",
                "dealt_avg_price", "last_err_msg", "acc_id",
            ):
                val = row_get(row, key)
                if val is not None:
                    data[key] = val
    order_id = str(row_get(data, "order_id", "orderid", default="") or "")
    status = normalize_order_status(row_get(data, "order_status", "status"))
    filled = safe_float(row_get(data, "dealt_qty", "filled", "filled_qty"))
    avg_price = safe_float(row_get(data, "dealt_avg_price", "avg_price", "avgFillPrice"))
    qty = safe_float(row_get(data, "qty", "quantity", "total_qty"))
    price = safe_float(row_get(data, "price", "limit_price"))
    code = str(row_get(data, "code", "symbol", default="") or "")
    remark = str(row_get(data, "remark", "client_order_id", default="") or "")
    commission = safe_float(row_get(data, "commission", "fee", "charge"))
    commission_ccy = str(row_get(data, "currency", "commission_ccy", default="") or "")
    return {
        "order_id": order_id,
        "orderId": order_id,
        "code": code,
        "symbol": code,
        "status": status,
        "order_status": status,
        "filled": filled,
        "dealt_qty": filled,
        "avg_price": avg_price,
        "dealt_avg_price": avg_price,
        "qty": qty,
        "price": price,
        "side": side_from_futu(row_get(data, "trd_side", "side")),
        "remark": remark,
        "client_order_id": remark,
        "commission": commission,
        "commission_ccy": commission_ccy,
        "message": str(row_get(data, "last_err_msg", "message", default="") or ""),
        "raw": data,
    }


def position_row_to_dict(row: Any) -> Dict[str, Any]:
    from app.services.futu_trading.symbols import from_futu_code

    code = str(row_get(row, "code", "symbol", default="") or "")
    display, market = from_futu_code(code)
    qty = safe_float(row_get(row, "qty", "quantity", "can_sell_qty"))
    avg = safe_float(row_get(row, "cost_price", "average_cost", "avgCost", "avg_cost"))
    market_val = safe_float(row_get(row, "market_val", "market_value", "marketValue"))
    pl = safe_float(row_get(row, "pl_val", "unrealized_pl", "pl"))
    currency = str(row_get(row, "currency", default="") or "")
    side = "long" if qty >= 0 else "short"
    return {
        "symbol": display or code,
        "futu_code": code,
        "market_category": market,
        "quantity": abs(qty),
        "qty": abs(qty),
        "avgCost": avg,
        "avg_cost": avg,
        "marketValue": market_val,
        "unrealized_pl": pl,
        "currency": currency,
        "side": side,
    }


def account_row_to_dict(row: Any) -> Dict[str, Any]:
    return {
        "power": safe_float(row_get(row, "power", "buying_power")),
        "total_assets": safe_float(row_get(row, "total_assets", "totalAssets")),
        "cash": safe_float(row_get(row, "cash", "avl_withdrawal_cash")),
        "market_val": safe_float(row_get(row, "market_val", "marketValue")),
        "currency": str(row_get(row, "currency", default="") or ""),
        "max_power_short": safe_float(row_get(row, "max_power_short")),
        "net_cash_power": safe_float(row_get(row, "net_cash_power")),
        "avl_withdrawal_cash": safe_float(row_get(row, "avl_withdrawal_cash")),
    }


def classify_futu_error(message: Any) -> Tuple[str, str]:
    """Return (error_code, human_message)."""
    msg = str(message or "").strip()
    low = msg.lower()
    if "not connected" in low or "connect" in low and "fail" in low:
        return "FUTU_OPEND_UNREACHABLE", msg or "Cannot reach FutuOpenD"
    if "no right" in low or "no authority" in low or "permission" in low or "行情权限" in msg:
        return "FUTU_QUOTE_PERMISSION_DENIED", msg
    if "unlock" in low or "交易密码" in msg or "password" in low:
        return "FUTU_TRADE_LOCKED", msg
    if "lot" in low or "手数" in msg or "qty" in low and "invalid" in low:
        return "FUTU_INVALID_LOT_SIZE", msg
    if "quota" in low or "额度" in msg or "limit" in low and "kline" in low:
        return "FUTU_QUOTE_QUOTA_EXCEEDED", msg
    if "subscribe" in low and ("max" in low or "limit" in low or "超额" in msg):
        return "FUTU_SUBSCRIBE_LIMIT", msg
    return "FUTU_API_ERROR", msg
