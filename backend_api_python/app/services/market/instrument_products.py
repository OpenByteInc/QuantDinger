"""Exchange instrument product classification and metadata extraction."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Collection, Mapping


PRODUCT_CRYPTO = "crypto"
PRODUCT_TOKENIZED_EQUITY = "tokenized_equity"
PRODUCT_STOCK_PERPETUAL = "stock_perpetual"
PRODUCT_DIRECT_EQUITY = "direct_equity"


@dataclass(frozen=True)
class InstrumentProductProfile:
    asset_class: str = "crypto"
    product_type: str = PRODUCT_CRYPTO
    api_family: str = "spot"
    underlying_market: str = ""
    underlying_symbol: str = ""
    product_meta: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["product_meta"] = dict(self.product_meta or {})
        return value


def classify_instrument_product(
    info: Mapping[str, Any] | None,
    *,
    exchange_id: str = "",
    market_type: str = "spot",
    symbol: str = "",
    instrument_id: str = "",
    known_equity_symbols: Collection[str] | None = None,
) -> InstrumentProductProfile:
    """Build a conservative product profile from exchange instrument metadata.

    The classifier only promotes a symbol to an equity product when the venue
    returns an explicit product flag. Bitget Reality also accepts its venue
    marker, while Binance bStocks require both the venue suffix and a matching
    reference equity symbol. Unknown instruments stay ordinary crypto and
    therefore remain outside equity-product live feature gates.
    """

    unified = dict(info or {})
    raw_value = unified.get("info")
    raw = dict(raw_value) if isinstance(raw_value, Mapping) else {}
    exchange = str(exchange_id or "").strip().lower()
    mt = str(market_type or "spot").strip().lower()
    base = str(unified.get("base") or raw.get("baseCoin") or raw.get("baseCcy") or "").strip().upper()
    native_id = str(instrument_id or unified.get("id") or raw.get("symbol") or raw.get("instId") or "").strip()

    explicit_equity = _explicit_equity_marker(raw)
    bitget_reality = exchange == "bitget" and mt == "spot" and (
        _truthy(raw.get("isReality"))
        or (
            str(raw.get("areaSymbol") or "").strip().lower() == "yes"
            and _looks_like_bitget_reality_base(base)
        )
    )
    known_equities = {
        str(value or "").strip().upper()
        for value in (known_equity_symbols or ())
        if str(value or "").strip()
    }
    binance_bstock = (
        exchange == "binance"
        and mt == "spot"
        and base.endswith("B")
        and base[:-1] in known_equities
    )
    is_equity_product = explicit_equity or bitget_reality or binance_bstock

    if is_equity_product and mt == "swap":
        product_type = PRODUCT_STOCK_PERPETUAL
        api_family = "swap"
    elif is_equity_product and mt == "spot":
        product_type = PRODUCT_TOKENIZED_EQUITY
        api_family = "reality" if bitget_reality else "spot"
    else:
        product_type = PRODUCT_CRYPTO
        api_family = "swap" if mt == "swap" else "spot"

    underlying = _extract_underlying(raw)
    if not underlying and product_type != PRODUCT_CRYPTO:
        underlying = _underlying_from_venue_symbol(exchange, base, product_type)
    underlying_market, underlying = _underlying_identity(
        raw,
        underlying=underlying,
        base=base,
        product_type=product_type,
    )

    asset_class = "equity" if product_type != PRODUCT_CRYPTO else "crypto"
    product_meta = _product_metadata(unified, raw, native_id)
    return InstrumentProductProfile(
        asset_class=asset_class,
        product_type=product_type,
        api_family=api_family,
        underlying_market=underlying_market,
        underlying_symbol=underlying,
        product_meta=product_meta,
    )


def _explicit_equity_marker(raw: Mapping[str, Any]) -> bool:
    inst_category = str(raw.get("instCategory") or "").strip()
    has_named_underlying = bool(
        str(raw.get("underlyingTicker") or raw.get("underlyingEquitySymbol") or "").strip()
        and str(raw.get("marketRegion") or "").strip()
    )
    fields = " ".join(
        str(raw.get(key) or "")
        for key in (
            "symbolType",
            "underlyingType",
            "underlyingSubType",
            "assetClass",
            "category",
            "contractType",
            "contract_type",
            "businessType",
            "business_type",
            "marketRegion",
            "tradfi_labels",
        )
    ).lower()
    return inst_category == "3" or has_named_underlying or any(
        token in fields for token in ("stock", "xstock", "equity", "etf")
    )


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _looks_like_bitget_reality_base(base: str) -> bool:
    if not base.startswith("R"):
        return False
    ticker = base[1:]
    return 1 <= len(ticker) <= 5 and ticker.isalpha()


def _extract_underlying(raw: Mapping[str, Any]) -> str:
    for key in (
        "underlyingTicker",
        "underlyingEquitySymbol",
        "underlyingSymbol",
        "underlying",
        "uly",
    ):
        value = str(raw.get(key) or "").strip().upper()
        if value:
            return value.split("-")[0].split("/")[0]
    return ""


def _underlying_from_venue_symbol(exchange: str, base: str, product_type: str) -> str:
    if product_type == PRODUCT_STOCK_PERPETUAL:
        return base
    if exchange == "bybit" and base.endswith("X"):
        return base[:-1]
    if exchange == "bitget" and base.startswith("R"):
        return base[1:]
    if exchange == "binance" and base.endswith("B"):
        return base[:-1]
    if exchange == "okx" and base.startswith("X"):
        return base[1:]
    if exchange == "gate" and base.endswith(("G", "X")):
        return base[:-1]
    return base


def _underlying_identity(
    raw: Mapping[str, Any],
    *,
    underlying: str,
    base: str,
    product_type: str,
) -> tuple[str, str]:
    if product_type == PRODUCT_CRYPTO or not underlying:
        return "", underlying

    region = " ".join(
        str(raw.get(key) or "")
        for key in (
            "marketRegion",
            "market_region",
            "underlyingMarket",
            "underlying_market",
            "underlyingExchange",
            "underlying_exchange",
            "primaryExchange",
            "primary_exchange",
        )
    ).strip().lower()
    candidate = str(underlying or base or "").strip().upper()
    compact_region = re.sub(r"[^a-z0-9]+", "", region)

    is_hong_kong = (
        any(token in region for token in ("hong kong", "hongkong", "hkex"))
        or compact_region in {"hk", "hkg", "xhkg"}
        or bool(re.fullmatch(r"HK\d{4,5}", candidate))
        or bool(re.fullmatch(r"\d{4,5}", candidate))
    )
    if is_hong_kong:
        ticker = candidate.removeprefix("HK")
        if ticker.isdigit():
            ticker = ticker.zfill(5)
        return "HKStock", ticker

    unsupported_region = any(
        token in region
        for token in ("korea", "korean", "krx", "kospi", "mainland china", "sse", "szse")
    ) or compact_region in {"kr", "kor", "xkrx", "cn", "chn", "xshg", "xshe"}
    if unsupported_region:
        return "", candidate

    return "USStock", candidate


def _product_metadata(
    unified: Mapping[str, Any],
    raw: Mapping[str, Any],
    native_id: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {"instrument_id": native_id}
    multiplier = _first_value(
        raw,
        "xstockMultiplier",
        "multiplier",
        "quanto_multiplier",
        "contract_size",
        "sizeMultiplier",
        "ctVal",
    )
    if multiplier not in (None, ""):
        result["multiplier"] = str(multiplier)
    for source, target in (
        ("status", "status"),
        ("symbolStatus", "status"),
        ("state", "status"),
        ("marketRegion", "market_region"),
        ("deliveryTime", "delivery_time"),
        ("delistingTime", "delisting_time"),
        ("fractionable", "fractionable"),
        ("overnightSupported", "overnight_supported"),
        ("extendedSession", "extended_session"),
    ):
        value = raw.get(source)
        if value not in (None, ""):
            result[target] = value
    if unified.get("contractSize") not in (None, ""):
        result["contract_size"] = unified.get("contractSize")
    if isinstance(unified.get("precision"), Mapping):
        result["precision"] = dict(unified["precision"])
    if isinstance(unified.get("limits"), Mapping):
        result["limits"] = dict(unified["limits"])
    return result


def _first_value(values: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = values.get(key)
        if value not in (None, ""):
            return value
    return None
