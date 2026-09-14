"""Chinese domestic futures symbol helpers shared by AkShare and RQData adapters."""
from __future__ import annotations

import re
from typing import Optional

# SC2611, RB0, IF2609, IF88, IF888, IF2609.CFE
_CN_FUTURES_PATTERN = re.compile(
    r"^(?P<product>[A-Z]{1,3})(?P<contract>\d{1,4}|88A2)(?:\.(?P<ex>[A-Z]{2,4}))?$"
)

_CN_FINANCIAL_PRODUCTS = frozenset({"IF", "IH", "IC", "IM", "T", "TF", "TS", "TL"})

_CONTINUOUS_CONTRACTS = frozenset({"0", "00", "88", "888", "889", "99", "88A2"})

# Ricequant exchange suffixes. Unknown products are tried without a suffix.
_PRODUCT_EXCHANGE = {
    "IF": "CFE", "IH": "CFE", "IC": "CFE", "IM": "CFE",
    "T": "CFE", "TF": "CFE", "TS": "CFE", "TL": "CFE",
    "CU": "SHFE", "AL": "SHFE", "ZN": "SHFE", "PB": "SHFE",
    "NI": "SHFE", "SN": "SHFE", "AU": "SHFE", "AG": "SHFE",
    "RB": "SHFE", "HC": "SHFE", "SS": "SHFE", "BU": "SHFE",
    "RU": "SHFE", "FU": "SHFE", "SP": "SHFE", "WR": "SHFE",
    "AO": "SHFE", "BR": "SHFE", "AD": "SHFE",
    "SC": "INE", "NR": "INE", "LU": "INE", "BC": "INE", "EC": "INE",
    "SI": "GFEX", "LC": "GFEX", "PS": "GFEX",
    "C": "DCE", "CS": "DCE", "A": "DCE", "B": "DCE", "M": "DCE",
    "Y": "DCE", "P": "DCE", "FB": "DCE", "BB": "DCE", "JD": "DCE",
    "L": "DCE", "V": "DCE", "PP": "DCE", "EG": "DCE", "EB": "DCE",
    "PG": "DCE", "LH": "DCE", "LG": "DCE",
    "CF": "CZCE", "SR": "CZCE", "TA": "CZCE", "MA": "CZCE", "OI": "CZCE",
    "RI": "CZCE", "WH": "CZCE", "PM": "CZCE", "FG": "CZCE", "SF": "CZCE",
    "SM": "CZCE", "UR": "CZCE", "SA": "CZCE", "PF": "CZCE", "PK": "CZCE",
    "CY": "CZCE", "AP": "CZCE", "CJ": "CZCE", "RM": "CZCE", "RS": "CZCE",
    "JR": "CZCE", "LR": "CZCE", "ZC": "CZCE", "PX": "CZCE", "SH": "CZCE",
}


def parse_cn_futures_symbol(symbol: str) -> Optional[re.Match[str]]:
    return _CN_FUTURES_PATTERN.match((symbol or "").strip().upper())


def is_cn_futures_symbol(symbol: str) -> bool:
    """Return True when the symbol looks like a Chinese domestic futures contract."""
    return parse_cn_futures_symbol(symbol) is not None


def cn_futures_product(symbol: str) -> str:
    m = parse_cn_futures_symbol(symbol)
    return m.group("product") if m else ""


def cn_futures_sina_market(symbol: str) -> str:
    return "FF" if cn_futures_product(symbol) in _CN_FINANCIAL_PRODUCTS else "CF"


def _normalize_czce_contract(product: str, contract: str) -> str:
    """CZCE codes in RQData are year-padded (CF701 -> CF1701)."""
    if _PRODUCT_EXCHANGE.get(product) != "CZCE":
        return contract
    if contract in _CONTINUOUS_CONTRACTS or len(contract) >= 4:
        return contract
    if len(contract) == 3:
        # 609 -> 2609 for 2020s contracts
        return "2" + contract
    return contract


def suggest_cn_continuous_symbols(keyword: str) -> list[str]:
    """Map a search keyword onto Sina-style continuous codes (RB -> RB0)."""
    kw = (keyword or "").strip().upper()
    if not kw:
        return []
    if len(kw) == 1:
        return [f"{kw}0"] if kw in _PRODUCT_EXCHANGE else []
    out: list[str] = []
    seen = set()
    for product in sorted(_PRODUCT_EXCHANGE, key=lambda item: (-len(item), item)):
        continuous = f"{product}0"
        if kw in {product, continuous} or continuous.startswith(kw) or kw.startswith(product):
            if continuous not in seen:
                seen.add(continuous)
                out.append(continuous)
    return out


def to_rqdata_order_book_id(symbol: str) -> str:
    """Map QuantDinger/Sina-style codes to RQData order_book_id."""
    m = parse_cn_futures_symbol(symbol)
    if not m:
        return (symbol or "").strip().upper()
    product = m.group("product")
    contract = m.group("contract")
    if contract in {"0", "00"}:
        contract = "88"
    contract = _normalize_czce_contract(product, contract)
    # RQData futures IDs are bare (IF2609, SC2609, RB88), not IF2609.CFE.
    return f"{product}{contract}"
