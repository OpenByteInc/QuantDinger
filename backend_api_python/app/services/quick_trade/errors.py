"""Quick trade exchange error normalization."""

from __future__ import annotations

import re
from typing import Any, Dict, List


FRIENDLY_ERROR_PATTERNS = [
    (
        re.compile(
            r"HTTP\s*451|\b451\b|restricted location|service unavailable from a restricted location",
            re.IGNORECASE,
        ),
        "quickTrade.errorHints.regionRestricted",
    ),
    (
        re.compile(
            r"OKX_SWAP_ACCOUNT_MODE_REQUIRED|51010|current account mode|unsupported under current account mode",
            re.IGNORECASE,
        ),
        "quickTrade.errorHints.okxAccountMode",
    ),
    (
        re.compile(
            r"INSUFFICIENT[_ ]?AVAILABLE|insufficient.{0,20}(balance|margin|fund)|margin.{0,30}while available|not enough|资金不足",
            re.IGNORECASE,
        ),
        "quickTrade.errorHints.insufficientBalance",
    ),
    (
        re.compile(
            r"invalid.{0,10}size|invalid.{0,10}(qty|quantity|amount|volume)|Order size.{0,20}(too small|below|minimum)|MIN_NOTIONAL|too many decimals|170137",
            re.IGNORECASE,
        ),
        "quickTrade.errorHints.invalidSize",
    ),
    (
        re.compile(
            r"invalid.{0,10}price|price.{0,20}(deviate|deviation|exceed|out of range)",
            re.IGNORECASE,
        ),
        "quickTrade.errorHints.invalidPrice",
    ),
    (
        re.compile(
            r"(?<!\d)-4411(?!\d)|Please\s+sign\s+TradFi[- ]Perps?\s+agreement",
            re.IGNORECASE,
        ),
        "quickTrade.errorHints.binanceTradFiAgreement",
    ),
    (
        re.compile(
            r"too many requests?|HTTP\s*429|(?<!\d)(?:-1003|-1015|429)(?!\d)|REQUEST_FREQUENCY|rate[ _-]limit(?:ed|\s+(?:exceeded|reached))?\b",
            re.IGNORECASE,
        ),
        "quickTrade.errorHints.rateLimit",
    ),
    (
        re.compile(
            r"(invalid|wrong|expired).{0,10}(api.?key|key|signature|sign)|NOT_LOGIN|UNAUTHORIZED|permission.{0,10}denied|IP.{0,20}(not|whitelist|restrict)",
            re.IGNORECASE,
        ),
        "quickTrade.errorHints.authError",
    ),
    (
        re.compile(
            r"reduce.?only|position.{0,20}(not exist|not found|side)|POSITION_NOT_EXIST",
            re.IGNORECASE,
        ),
        "quickTrade.errorHints.positionConflict",
    ),
    (
        re.compile(
            r"timeout|timed? ?out|connect|ECONNREFUSED|SSL|ConnectionError|RemoteDisconnected",
            re.IGNORECASE,
        ),
        "quickTrade.errorHints.networkError",
    ),
    (
        re.compile(r"maintenance|unavailable|system.{0,10}(busy|error|upgrade)|suspend|暂停", re.IGNORECASE),
        "quickTrade.errorHints.exchangeMaintenance",
    ),
]


def parse_trade_error_hint(error_str: str) -> str:
    """Return an i18n key hint for common exchange trading errors."""
    text = str(error_str or "")
    for pattern, hint_key in FRIENDLY_ERROR_PATTERNS:
        if pattern.search(text):
            return hint_key
    return ""


def extract_request_ip_from_exchange_error(err: str) -> str:
    """Extract an exchange-reported request IP from an error string."""
    match = re.search(r"Current request IP\s+([0-9a-fA-F.:]+)", str(err or ""), re.IGNORECASE)
    return (match.group(1) or "").strip() if match else ""


def exchange_error_user_message(*, exchange_id: str, err: str) -> Dict[str, str]:
    """Map a raw exchange error to UI-friendly text and an optional i18n key."""
    text = str(err or "").strip()
    low = text.lower()
    exchange = (exchange_id or "").strip().lower()
    if not text:
        return {"message": "", "hint_key": ""}

    if "OKX_SWAP_ACCOUNT_MODE_REQUIRED" in text or "51010" in text:
        return {
            "message": "OKX_SWAP_ACCOUNT_MODE_REQUIRED",
            "hint_key": "quickTrade.errorHints.okxAccountMode",
        }

    if "BITGET_UTA_HYBRID_BALANCE_UNAVAILABLE" in text:
        return {
            "message": "BITGET_UTA_HYBRID_BALANCE_UNAVAILABLE",
            "hint_key": "quickTrade.errorHints.bitgetUnifiedAccount",
        }

    if "40018" in text or "invalid ip" in low:
        request_ip = extract_request_ip_from_exchange_error(text)
        if exchange == "bitget":
            message = "Bitget rejected the API request because the current egress IP is not whitelisted."
        else:
            message = "The exchange rejected the API request because the current egress IP is not whitelisted."
        if request_ip:
            message = f"{message} Current request IP: {request_ip}."
        return {
            "message": message,
            "hint_key": "quickTrade.errorHints.ipWhitelist",
            "request_ip": request_ip,
        }

    if "balance_not_enough" in low or "not enough balance" in low:
        return {
            "message": "Insufficient balance. Check the spot and derivatives wallets, then retry with a smaller order or transfer funds.",
            "hint_key": "quickTrade.errorHints.insufficientBalance",
        }

    if "account-frozen-balance-insufficient" in low or "balance is not enough, left" in low:
        return {
            "message": "Available spot balance is insufficient. Some funds may be frozen by open orders.",
            "hint_key": "quickTrade.errorHints.insufficientBalance",
        }

    if "insufficient margin" in low:
        return {
            "message": "Insufficient margin. Reduce order size, adjust leverage, or transfer funds to the derivatives wallet.",
            "hint_key": "quickTrade.errorHints.insufficientBalance",
        }

    return {"message": text[:500], "hint_key": parse_trade_error_hint(text)}


def merge_balance_leg_errors(
    swap_bal: Dict[str, Any],
    spot_bal: Dict[str, Any],
    *,
    exchange_id: str = "",
) -> Dict[str, str]:
    """Collect swap and spot leg errors into top-level API response fields."""
    parts: List[str] = []
    hint_keys: List[str] = []
    request_ip = ""
    for leg in (swap_bal, spot_bal):
        if not isinstance(leg, dict):
            continue
        raw_err = str(leg.get("error") or "").strip()
        if not raw_err:
            continue
        meta = exchange_error_user_message(exchange_id=exchange_id, err=raw_err)
        if meta.get("message"):
            parts.append(str(meta["message"]))
        if meta.get("hint_key"):
            hint_keys.append(str(meta["hint_key"]))
        if meta.get("request_ip"):
            request_ip = str(meta["request_ip"])

    if not parts:
        return {}
    return {
        "error": parts[0],
        "errors": parts,
        "error_hint_key": hint_keys[0] if hint_keys else "",
        "request_ip": request_ip,
    }
