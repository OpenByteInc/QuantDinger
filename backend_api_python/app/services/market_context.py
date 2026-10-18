"""Canonical market-data identity shared by research, backtest, and live flows."""

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from app.config.data_sources import CryptoPublicConfig


CRYPTO_MARKET_TYPES = frozenset({"spot", "swap"})
SUPPORTED_CRYPTO_EXCHANGE_IDS = frozenset({"binance", "bitget", "bybit", "okx", "gate", "htx"})


def normalize_exchange_id(value: Any) -> str:
    exchange_id = str(value or "").strip().lower()
    aliases = {
        "okex": "okx",
        "gateio": "gate",
        "huobi": "htx",
        "binanceusdm": "binance",
    }
    return aliases.get(exchange_id, exchange_id)


def default_crypto_exchange_id() -> str:
    exchange_id = normalize_exchange_id(CryptoPublicConfig.DEFAULT_EXCHANGE or "binance") or "binance"
    return exchange_id if exchange_id in SUPPORTED_CRYPTO_EXCHANGE_IDS else "binance"


def normalize_market_type(value: Any, *, market: str = "") -> str:
    market_type = str(value or "").strip().lower()
    if market_type in {"future", "futures", "perp", "perpetual"}:
        market_type = "swap"
    if str(market or "").strip() != "Crypto":
        return "spot"
    return market_type if market_type in CRYPTO_MARKET_TYPES else "spot"


def parse_crypto_identifier(value: Any) -> tuple[str, str | None, str | None]:
    """Parse a canonical crypto identifier into symbol, product type, and venue.

    QuantDinger identifiers use ``@swap`` for a perpetual and may qualify the
    venue as ``@binance:swap``. A CCXT-style settlement suffix such as
    ``:USDT`` remains part of the pair until it is validated and removed.
    """
    identifier = str(value or "").strip().upper()
    if identifier.startswith("CRYPTO:"):
        identifier = identifier[len("CRYPTO:"):]

    suffix_market_type = None
    suffix_exchange_id = None
    if "@" in identifier:
        identifier, suffix = identifier.rsplit("@", 1)
        if "@" in identifier or not identifier or not suffix:
            raise ValueError(f"Invalid crypto market identifier: {value}")
        if ":" in suffix:
            suffix_exchange_id, suffix = suffix.rsplit(":", 1)
            if not suffix_exchange_id:
                raise ValueError(f"Invalid crypto exchange in market identifier: {value}")
            suffix_exchange_id = normalize_exchange_id(suffix_exchange_id)
        suffix_aliases = {
            "SPOT": "spot",
            "SWAP": "swap",
            "PERP": "swap",
            "PERPETUAL": "swap",
            "FUTURE": "swap",
            "FUTURES": "swap",
        }
        suffix_market_type = suffix_aliases.get(suffix)
        if suffix_market_type is None:
            raise ValueError(f"Unsupported crypto product suffix: @{suffix}")

    identifier = identifier.strip()
    if ":" in identifier:
        pair, settle = identifier.rsplit(":", 1)
        if "/" in pair:
            quote = pair.rsplit("/", 1)[1]
            if settle != quote:
                raise ValueError(
                    f"Crypto settlement suffix :{settle} does not match quote {quote}"
                )
            identifier = pair
        else:
            identifier = pair

    return identifier.strip().upper(), suffix_market_type, suffix_exchange_id


def canonical_crypto_symbol(value: Any) -> str:
    return parse_crypto_identifier(value)[0]


@dataclass(frozen=True)
class MarketContext:
    market: str
    symbol: str
    exchange_id: str = ""
    market_type: str = "spot"
    instrument_id: str = ""
    settle_currency: str = ""
    timeframe: str = ""

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, Any] | None,
        *,
        apply_crypto_defaults: bool = True,
    ) -> "MarketContext":
        data = payload or {}
        market = str(
            data.get("market")
            or data.get("market_category")
            or data.get("marketCategory")
            or ""
        ).strip()
        raw_symbol = data.get("symbol")
        if market == "Crypto":
            symbol, suffix_market_type, suffix_exchange_id = parse_crypto_identifier(raw_symbol)
        else:
            symbol = str(raw_symbol or "").strip().upper()
            suffix_market_type = None
            suffix_exchange_id = None

        explicit_exchange_id = normalize_exchange_id(
            data.get("exchange_id")
            or data.get("exchangeId")
            or data.get("exchange")
        )
        if suffix_exchange_id and explicit_exchange_id and suffix_exchange_id != explicit_exchange_id:
            raise ValueError(
                f"Market identifier exchange {suffix_exchange_id} conflicts with "
                f"requested exchange {explicit_exchange_id}"
            )
        exchange_id = explicit_exchange_id or suffix_exchange_id or ""

        explicit_market_type = data.get("market_type") or data.get("marketType")
        normalized_market_type = normalize_market_type(explicit_market_type, market=market)
        if (
            suffix_market_type
            and explicit_market_type
            and normalized_market_type != suffix_market_type
        ):
            raise ValueError(
                f"Market identifier product {suffix_market_type} conflicts with "
                f"requested market_type {normalized_market_type}"
            )
        market_type = suffix_market_type or normalized_market_type
        if market == "Crypto" and apply_crypto_defaults and not exchange_id:
            # The unqualified BTC/USDT perpetual identifier is the public
            # Binance USDⓈ-M instrument used by research and chart requests.
            if suffix_market_type == "swap" and symbol == "BTC/USDT":
                exchange_id = "binance"
            else:
                exchange_id = default_crypto_exchange_id()
        if market != "Crypto":
            exchange_id = ""
        return cls(
            market=market,
            symbol=symbol,
            exchange_id=exchange_id,
            market_type=market_type,
            instrument_id=str(data.get("instrument_id") or data.get("instrumentId") or "").strip(),
            settle_currency=str(data.get("settle_currency") or data.get("settleCurrency") or "").strip().upper(),
            timeframe=str(data.get("timeframe") or "").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def identity_key(self) -> tuple[str, str, str, str, str]:
        return (
            self.market,
            self.symbol,
            self.exchange_id,
            self.market_type,
            self.instrument_id,
        )
