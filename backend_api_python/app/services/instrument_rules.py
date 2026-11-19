"""Exchange-native instrument rules shared by live trading and backtests.

K-line rows deliberately do not carry these values.  Live trading reads the
current public exchange metadata while backtests consume an immutable,
content-addressed snapshot.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from app.services.live_trading.base import BaseRestClient, LiveTradingError
from app.services.live_trading.symbols import (
    to_binance_futures_symbol,
    to_bitget_um_symbol,
    to_bybit_symbol,
    to_gate_currency_pair,
    to_htx_contract_code,
    to_htx_spot_symbol,
    to_okx_spot_inst_id,
    to_okx_swap_inst_id,
)
from app.services.market_context import default_crypto_exchange_id, normalize_exchange_id
from app.utils.numeric_precision import floor_decimal_to_step


RULES_SCHEMA_VERSION = 1
SUPPORTED_RULE_EXCHANGES = frozenset({"binance", "bitget", "bybit", "okx", "gate", "htx"})
_default_provider: "InstrumentRulesProvider | None" = None
_default_provider_lock = threading.Lock()


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")
    return result if result.is_finite() else Decimal("0")


def _positive(value: Any) -> Decimal:
    result = _decimal(value)
    return result if result > 0 else Decimal("0")


def _precision_step(value: Any) -> Decimal:
    try:
        places = int(value)
    except (TypeError, ValueError):
        return Decimal("0")
    return Decimal("1").scaleb(-places) if 0 <= places <= 18 else Decimal("0")


def _first_positive(*values: Any) -> Decimal:
    for value in values:
        result = _positive(value)
        if result > 0:
            return result
    return Decimal("0")


def _iso_utc(timestamp: float | None = None) -> str:
    return datetime.fromtimestamp(timestamp or time.time(), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def instrument_rules_key(
    symbol: str,
    *,
    exchange_id: str = "",
    market_type: str = "spot",
) -> str:
    exchange = normalize_exchange_id(exchange_id) or default_crypto_exchange_id()
    kind = "swap" if str(market_type or "").strip().lower() in {"swap", "future", "futures", "perp", "perpetual"} else "spot"
    normalized_symbol = str(symbol or "").strip().upper().replace("-", "/").replace("_", "/")
    if normalized_symbol.startswith("CRYPTO:"):
        normalized_symbol = normalized_symbol.split(":", 1)[1]
    if "@" in normalized_symbol:
        normalized_symbol = normalized_symbol.split("@", 1)[0]
    return f"Crypto:{normalized_symbol}@{exchange}:{kind}"


@dataclass(frozen=True)
class InstrumentRules:
    """Normalized quantities are always expressed in base-asset units."""

    key: str
    exchange_id: str
    market_type: str
    symbol: str
    amount_step: float = 0.0
    min_amount: float = 0.0
    min_notional: float = 0.0
    price_tick: float = 0.0
    contract_size: float = 1.0
    source: str = "exchange_public_api"
    captured_at: str = ""
    schema_version: int = RULES_SCHEMA_VERSION

    def metadata(self) -> dict[str, Any]:
        return asdict(self)

    def normalize_amount(self, amount: float, *, enforce_minimum: bool = True) -> float:
        requested = _positive(amount)
        step = _positive(self.amount_step)
        if step > 0:
            requested = floor_decimal_to_step(requested, step)
        if enforce_minimum and requested < _positive(self.min_amount):
            return 0.0
        return float(requested)

    def normalize_price(self, price: float) -> float:
        requested = _positive(price)
        tick = _positive(self.price_tick)
        if tick > 0:
            requested = floor_decimal_to_step(requested, tick)
        return float(requested)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "InstrumentRules":
        fields = cls.__dataclass_fields__
        return cls(**{name: value[name] for name in fields if name in value})


@dataclass(frozen=True)
class InstrumentRulesSnapshot:
    snapshot_id: str
    captured_at: str
    rules: Mapping[str, InstrumentRules]
    schema_version: int = RULES_SCHEMA_VERSION

    def get(self, key: str) -> InstrumentRules | None:
        direct = self.rules.get(str(key))
        if direct is not None:
            return direct
        try:
            parsed = _parse_strategy_crypto_key(key)
        except ValueError:
            return None
        if not parsed[1]:
            symbol = parsed[0].strip().upper().replace("-", "/").replace("_", "/")
            market_type = parsed[2].strip().lower()
            matches = [
                rule
                for rule in self.rules.values()
                if rule.symbol.strip().upper().replace("-", "/").replace("_", "/") == symbol
                and rule.market_type.strip().lower() == market_type
            ]
            return matches[0] if len(matches) == 1 else None
        canonical = instrument_rules_key(
            parsed[0], exchange_id=parsed[1], market_type=parsed[2]
        )
        return self.rules.get(canonical)

    def metadata(self) -> dict[str, Any]:
        return {
            "snapshotId": self.snapshot_id,
            "snapshotFormat": "instrument-rules-json-gzip-v1",
            "schemaVersion": self.schema_version,
            "capturedAt": self.captured_at,
            "rules": [self.rules[key].metadata() for key in sorted(self.rules)],
        }

    @classmethod
    def build(
        cls,
        rules: Iterable[InstrumentRules],
        *,
        captured_at: str | None = None,
    ) -> "InstrumentRulesSnapshot":
        indexed = {item.key: item for item in rules}
        timestamp = captured_at or max(
            (item.captured_at for item in indexed.values() if item.captured_at),
            default=_iso_utc(),
        )
        payload = _snapshot_payload(indexed, timestamp)
        return cls(
            snapshot_id=hashlib.sha256(payload).hexdigest(),
            captured_at=timestamp,
            rules=indexed,
        )

    @classmethod
    def from_metadata(cls, value: Mapping[str, Any]) -> "InstrumentRulesSnapshot":
        rows = value.get("rules") or []
        snapshot = cls.build(
            [InstrumentRules.from_mapping(row) for row in rows if isinstance(row, Mapping)],
            captured_at=str(value.get("capturedAt") or "") or None,
        )
        expected = str(value.get("snapshotId") or "")
        if expected and expected != snapshot.snapshot_id:
            raise ValueError("strategyV2.instrumentRulesSnapshotHashMismatch")
        return snapshot


class InstrumentRulesSnapshotStore:
    def __init__(self, root: Path | str | None = None) -> None:
        configured = root or os.getenv("INSTRUMENT_RULES_SNAPSHOT_DIR") or "data/instrument_rule_snapshots"
        self.root = Path(configured)

    def save(self, snapshot: InstrumentRulesSnapshot) -> dict[str, Any]:
        payload = _snapshot_payload(snapshot.rules, snapshot.captured_at)
        snapshot_id = hashlib.sha256(payload).hexdigest()
        if snapshot_id != snapshot.snapshot_id:
            raise ValueError("strategyV2.instrumentRulesSnapshotHashMismatch")
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{snapshot_id}.json.gz"
        if not target.exists():
            temporary = target.with_suffix(".tmp")
            with gzip.open(temporary, "wb", compresslevel=6) as handle:
                handle.write(payload)
            os.replace(temporary, target)
        return snapshot.metadata()

    def load(self, snapshot_id: str) -> InstrumentRulesSnapshot:
        normalized = str(snapshot_id or "").strip().lower()
        if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("strategyV2.instrumentRulesSnapshotIdInvalid")
        with gzip.open(self.root / f"{normalized}.json.gz", "rb") as handle:
            payload = handle.read()
        if hashlib.sha256(payload).hexdigest() != normalized:
            raise ValueError("strategyV2.instrumentRulesSnapshotHashMismatch")
        document = json.loads(payload.decode("utf-8"))
        return InstrumentRulesSnapshot.from_metadata({**document, "snapshotId": normalized})

    def find_as_of(
        self,
        instruments: Iterable[Mapping[str, Any]],
        *,
        as_of: datetime,
    ) -> InstrumentRulesSnapshot | None:
        """Find the newest stored snapshot at or before a historical boundary."""
        required = {
            instrument_rules_key(
                str(item.get("symbol") or ""),
                exchange_id=str(item.get("exchange_id") or ""),
                market_type=str(item.get("market_type") or "spot"),
            )
            for item in instruments
            if str(item.get("market") or "") == "Crypto"
        }
        if not required or not self.root.exists():
            return None
        boundary = as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=timezone.utc)
        best: tuple[int, datetime, InstrumentRulesSnapshot] | None = None
        for path in self.root.glob("*.json.gz"):
            try:
                snapshot = self.load(path.name.removesuffix(".json.gz"))
                captured = datetime.fromisoformat(snapshot.captured_at.replace("Z", "+00:00"))
                if captured.tzinfo is None:
                    captured = captured.replace(tzinfo=timezone.utc)
                if captured <= boundary and required.issubset(snapshot.rules):
                    authoritative = int(all(
                        snapshot.rules[key].source != "historical_fallback_no_snapshot"
                        for key in required
                    ))
                    rank = (authoritative, captured)
                    if best is None or rank > best[:2]:
                        best = authoritative, captured, snapshot
            except Exception:
                continue
        return best[2] if best is not None else None


class _PublicExchangeClient(BaseRestClient):
    def get_json(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        status, data, text = self._request(
            "GET", path, params=dict(params or {}), headers=dict(headers or {})
        )
        if status >= 400:
            raise LiveTradingError(f"Instrument rules HTTP {status}: {text[:500]}")
        return data if isinstance(data, dict) else {"data": data}


RawFetcher = Callable[[str, str, str, Mapping[str, Any], Mapping[str, str]], Mapping[str, Any]]


class InstrumentRulesProvider:
    """TTL-cached provider backed only by native public exchange endpoints."""

    _BASE_URLS = {
        "binance": "https://api.binance.com",
        "binance_data": "https://data-api.binance.vision",
        "binance_swap": "https://fapi.binance.com",
        "bitget": "https://api.bitget.com",
        "bybit": "https://api.bybit.com",
        "okx": "https://www.okx.com",
        "gate": "https://api.gateio.ws",
        "htx": "https://api.htx.com",
        "htx_swap": "https://api.hbdm.com",
    }

    def __init__(
        self,
        *,
        ttl_sec: float = 300.0,
        timeout_sec: float = 8.0,
        raw_fetcher: RawFetcher | None = None,
        snapshot_store: InstrumentRulesSnapshotStore | None = None,
    ) -> None:
        self.ttl_sec = max(0.0, float(ttl_sec))
        self.timeout_sec = max(0.5, float(timeout_sec))
        self.raw_fetcher = raw_fetcher
        self.snapshot_store = snapshot_store or InstrumentRulesSnapshotStore()
        self._cache: dict[str, tuple[float, InstrumentRules]] = {}
        self._lock = threading.RLock()

    def get_rules(
        self,
        symbol: str,
        *,
        exchange_id: str = "",
        market_type: str = "spot",
        client: Any = None,
        refresh: bool = False,
    ) -> InstrumentRules:
        exchange = normalize_exchange_id(exchange_id) or default_crypto_exchange_id()
        kind = "swap" if str(market_type or "").lower() in {"swap", "future", "futures", "perp", "perpetual"} else "spot"
        if exchange not in SUPPORTED_RULE_EXCHANGES:
            raise ValueError(f"instrumentRules.exchangeUnsupported:{exchange}")
        key = instrument_rules_key(symbol, exchange_id=exchange, market_type=kind)
        now = time.time()
        with self._lock:
            cached = self._cache.get(key)
            if not refresh and cached and now - cached[0] <= self.ttl_sec:
                return cached[1]

        raw = self._from_live_client(client, exchange, kind, symbol)
        if client is not None and not raw and not self._client_supports_rules(
            client, exchange, kind
        ):
            raise ValueError(f"instrumentRules.clientMetadataUnavailable:{exchange}:{kind}")
        if not raw:
            raw = self._fetch_native(exchange, kind, symbol)
        if not raw:
            raise ValueError(f"instrumentRules.symbolMetadataUnavailable:{exchange}:{kind}:{symbol}")
        rules = self.normalize(exchange, kind, symbol, raw, captured_at=_iso_utc(now))
        with self._lock:
            self._cache[key] = (now, rules)
        return rules

    def snapshot(
        self,
        instruments: Iterable[Mapping[str, Any]],
        *,
        persist: bool = True,
    ) -> InstrumentRulesSnapshot:
        rows: list[InstrumentRules] = []
        for item in instruments:
            if str(item.get("market") or "") != "Crypto":
                continue
            rows.append(self.get_rules(
                str(item.get("symbol") or ""),
                exchange_id=str(item.get("exchange_id") or ""),
                market_type=str(item.get("market_type") or "spot"),
            ))
        snapshot = InstrumentRulesSnapshot.build(rows)
        if persist:
            self.snapshot_store.save(snapshot)
        return snapshot

    def historical_snapshot(
        self,
        instruments: Iterable[Mapping[str, Any]],
        *,
        snapshot_id: str = "",
        as_of: datetime | None = None,
        persist: bool = True,
    ) -> InstrumentRulesSnapshot:
        instrument_list = list(instruments)
        if snapshot_id:
            snapshot = self.snapshot_store.load(snapshot_id)
            missing = [
                instrument_rules_key(
                    str(item.get("symbol") or ""),
                    exchange_id=str(item.get("exchange_id") or ""),
                    market_type=str(item.get("market_type") or "spot"),
                )
                for item in instrument_list
                if str(item.get("market") or "") == "Crypto"
                and instrument_rules_key(
                    str(item.get("symbol") or ""),
                    exchange_id=str(item.get("exchange_id") or ""),
                    market_type=str(item.get("market_type") or "spot"),
                ) not in snapshot.rules
            ]
            if missing:
                raise ValueError(
                    "strategyV2.instrumentRulesSnapshotMissing:" + ",".join(sorted(missing))
                )
            return snapshot
        now = datetime.now(timezone.utc)
        boundary = as_of or now
        if boundary.tzinfo is None:
            boundary = boundary.replace(tzinfo=timezone.utc)
        is_historical = boundary.astimezone(timezone.utc) < now - timedelta(seconds=5)
        if not is_historical:
            return self.snapshot(instrument_list, persist=persist)

        cached = self.snapshot_store.find_as_of(instrument_list, as_of=boundary)
        if cached is not None:
            return cached

        # Native exchanges expose current rules, not a complete historical rule
        # ledger.  Never silently apply today's metadata to an old simulation.
        captured = boundary.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        fallbacks = []
        for item in instrument_list:
            if str(item.get("market") or "") != "Crypto":
                continue
            exchange = normalize_exchange_id(item.get("exchange_id")) or default_crypto_exchange_id()
            market_type = str(item.get("market_type") or "spot")
            symbol = str(item.get("symbol") or "")
            fallbacks.append(InstrumentRules(
                key=instrument_rules_key(symbol, exchange_id=exchange, market_type=market_type),
                exchange_id=exchange,
                market_type=market_type,
                symbol=symbol,
                amount_step=1e-8,
                source="historical_fallback_no_snapshot",
                captured_at=captured,
            ))
        snapshot = InstrumentRulesSnapshot.build(fallbacks, captured_at=captured)
        if persist:
            self.snapshot_store.save(snapshot)
        return snapshot

    @staticmethod
    def normalize(
        exchange_id: str,
        market_type: str,
        symbol: str,
        raw: Mapping[str, Any],
        *,
        captured_at: str = "",
    ) -> InstrumentRules:
        exchange = normalize_exchange_id(exchange_id)
        kind = "swap" if str(market_type or "").lower() == "swap" else "spot"
        parsers = {
            "binance": _parse_binance,
            "bybit": _parse_bybit,
            "okx": _parse_okx,
            "bitget": _parse_bitget,
            "gate": _parse_gate,
            "htx": _parse_htx,
        }
        values = parsers[exchange](kind, raw)
        return InstrumentRules(
            key=instrument_rules_key(symbol, exchange_id=exchange, market_type=kind),
            exchange_id=exchange,
            market_type=kind,
            symbol=str(symbol or "").strip().upper(),
            amount_step=float(values["amount_step"]),
            min_amount=float(values["min_amount"]),
            min_notional=float(values["min_notional"]),
            price_tick=float(values["price_tick"]),
            contract_size=float(values["contract_size"] or Decimal("1")),
            captured_at=captured_at or _iso_utc(),
        )

    def _request(
        self,
        exchange: str,
        market_type: str,
        path: str,
        params: Mapping[str, Any],
        headers: Mapping[str, str] | None = None,
        base_key: str = "",
    ) -> Mapping[str, Any]:
        resolved_base_key = base_key or (
            f"{exchange}_swap"
            if market_type == "swap" and f"{exchange}_swap" in self._BASE_URLS
            else exchange
        )
        base_url = self._BASE_URLS[resolved_base_key]
        if self.raw_fetcher is not None:
            return self.raw_fetcher(exchange, market_type, path, params, headers or {})
        return _PublicExchangeClient(base_url, timeout_sec=self.timeout_sec).get_json(
            path, params=params, headers=headers
        )

    def _fetch_native(self, exchange: str, market_type: str, symbol: str) -> Mapping[str, Any]:
        if exchange == "binance":
            path = "/fapi/v1/exchangeInfo" if market_type == "swap" else "/api/v3/exchangeInfo"
            params = {"symbol": to_binance_futures_symbol(symbol)}
            try:
                document = self._request(exchange, market_type, path, params)
            except LiveTradingError:
                if market_type != "spot" or self.raw_fetcher is not None:
                    raise
                # Binance provides an official market-data-only host which is
                # usable in regions where the trading API host returns 451.
                document = self._request(
                    exchange, market_type, path, params, base_key="binance_data"
                )
            return _matching_row(document.get("symbols"), "symbol", to_binance_futures_symbol(symbol))
        if exchange == "bybit":
            category = "linear" if market_type == "swap" else "spot"
            document = self._request(exchange, market_type, "/v5/market/instruments-info", {
                "category": category, "symbol": to_bybit_symbol(symbol)
            })
            return _first_mapping((document.get("result") or {}).get("list"))
        if exchange == "okx":
            inst_id = to_okx_swap_inst_id(symbol) if market_type == "swap" else to_okx_spot_inst_id(symbol)
            document = self._request(exchange, market_type, "/api/v5/public/instruments", {
                "instType": "SWAP" if market_type == "swap" else "SPOT", "instId": inst_id
            })
            return _first_mapping(document.get("data"))
        if exchange == "bitget":
            document = self._request(
                exchange,
                market_type,
                "/api/v3/market/instruments",
                {
                    "category": "USDT-FUTURES" if market_type == "swap" else "SPOT",
                    "symbol": to_bitget_um_symbol(symbol),
                },
            )
            return _matching_row(document.get("data"), "symbol", to_bitget_um_symbol(symbol))
        if exchange == "gate":
            pair = to_gate_currency_pair(symbol)
            if market_type == "swap":
                return self._request(exchange, market_type, f"/api/v4/futures/usdt/contracts/{pair}", {}, {
                    "X-Gate-Size-Decimal": "1"
                })
            return self._request(exchange, market_type, f"/api/v4/spot/currency_pairs/{pair}", {})
        if exchange == "htx":
            if market_type == "swap":
                document = self._request(exchange, market_type, "/linear-swap-api/v1/swap_contract_info", {
                    "contract_code": to_htx_contract_code(symbol)
                })
                return _first_mapping(document.get("data"))
            document = self._request(exchange, market_type, "/v1/common/symbols", {})
            native = to_htx_spot_symbol(symbol)
            return _matching_row(document.get("data"), "symbol", native)
        raise ValueError(f"instrumentRules.exchangeUnsupported:{exchange}")

    @staticmethod
    def _from_live_client(client: Any, exchange: str, market_type: str, symbol: str) -> Mapping[str, Any]:
        if client is None:
            return {}
        try:
            if exchange == "binance" and hasattr(client, "get_symbol_filters"):
                return client.get_symbol_filters(symbol=symbol) or {}
            if exchange == "bybit" and hasattr(client, "get_instrument_info"):
                return client.get_instrument_info(
                    category="linear" if market_type == "swap" else "spot", symbol=symbol
                ) or {}
            if exchange == "okx" and hasattr(client, "get_instrument"):
                native = to_okx_swap_inst_id(symbol) if market_type == "swap" else to_okx_spot_inst_id(symbol)
                return client.get_instrument(
                    inst_type="SWAP" if market_type == "swap" else "SPOT", inst_id=native
                ) or {}
            if exchange == "bitget":
                if market_type == "swap" and hasattr(client, "get_contract"):
                    return client.get_contract(symbol=symbol, product_type="USDT-FUTURES") or {}
                if market_type == "spot" and hasattr(client, "get_symbol_meta"):
                    return client.get_symbol_meta(symbol=symbol) or {}
            if exchange == "gate" and market_type == "swap" and hasattr(client, "get_contract"):
                return client.get_contract(contract=to_gate_currency_pair(symbol)) or {}
            if exchange == "gate" and market_type == "spot" and hasattr(client, "get_currency_pair"):
                return client.get_currency_pair(symbol=symbol) or {}
            if exchange == "htx" and market_type == "swap" and hasattr(client, "get_contract_info"):
                return client.get_contract_info(symbol=symbol) or {}
            if exchange == "htx" and market_type == "spot" and hasattr(client, "get_spot_symbol_info"):
                return client.get_spot_symbol_info(symbol=symbol) or {}
        except Exception:
            return {}
        return {}

    @staticmethod
    def _client_supports_rules(client: Any, exchange: str, market_type: str) -> bool:
        method = {
            ("binance", "spot"): "get_symbol_filters",
            ("binance", "swap"): "get_symbol_filters",
            ("bybit", "spot"): "get_instrument_info",
            ("bybit", "swap"): "get_instrument_info",
            ("okx", "spot"): "get_instrument",
            ("okx", "swap"): "get_instrument",
            ("bitget", "spot"): "get_symbol_meta",
            ("bitget", "swap"): "get_contract",
            ("gate", "spot"): "get_currency_pair",
            ("gate", "swap"): "get_contract",
            ("htx", "spot"): "get_spot_symbol_info",
            ("htx", "swap"): "get_contract_info",
        }.get((exchange, market_type), "")
        return bool(method and callable(getattr(type(client), method, None)))


def _empty_values() -> dict[str, Decimal]:
    return {
        "amount_step": Decimal("0"),
        "min_amount": Decimal("0"),
        "min_notional": Decimal("0"),
        "price_tick": Decimal("0"),
        "contract_size": Decimal("1"),
    }


def _parse_binance(_market_type: str, raw: Mapping[str, Any]) -> dict[str, Decimal]:
    values = _empty_values()
    filters = raw.get("filters")
    if isinstance(filters, list):
        filters = {str(item.get("filterType") or ""): item for item in filters if isinstance(item, Mapping)}
    filters = filters if isinstance(filters, Mapping) else raw
    lot = filters.get("LOT_SIZE") or filters.get("MARKET_LOT_SIZE") or {}
    price = filters.get("PRICE_FILTER") or {}
    notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL") or {}
    values.update({
        "amount_step": _positive(lot.get("stepSize")),
        "min_amount": _positive(lot.get("minQty")),
        "min_notional": _first_positive(notional.get("minNotional"), notional.get("notional")),
        "price_tick": _positive(price.get("tickSize")),
    })
    return values


def _parse_bybit(market_type: str, raw: Mapping[str, Any]) -> dict[str, Decimal]:
    values = _empty_values()
    lot = raw.get("lotSizeFilter") or {}
    price = raw.get("priceFilter") or {}
    values.update({
        "amount_step": _first_positive(lot.get("qtyStep"), lot.get("basePrecision")),
        "min_amount": _first_positive(lot.get("minOrderQty"), lot.get("minTradingQty")),
        "min_notional": _first_positive(
            lot.get("minNotionalValue"),
            lot.get("minOrderAmt") if market_type == "spot" else None,
        ),
        "price_tick": _positive(price.get("tickSize")),
    })
    return values


def _parse_okx(market_type: str, raw: Mapping[str, Any]) -> dict[str, Decimal]:
    values = _empty_values()
    contract = _positive(raw.get("ctVal")) if market_type == "swap" else Decimal("1")
    if contract <= 0:
        contract = Decimal("1")
    values.update({
        "amount_step": _positive(raw.get("lotSz")) * contract,
        "min_amount": _positive(raw.get("minSz")) * contract,
        "min_notional": _first_positive(raw.get("minNotional"), raw.get("minNotionalValue")),
        "price_tick": _positive(raw.get("tickSz")),
        "contract_size": contract,
    })
    return values


def _parse_bitget(market_type: str, raw: Mapping[str, Any]) -> dict[str, Decimal]:
    values = _empty_values()
    contract = Decimal("1")
    if market_type == "swap":
        contract = _first_positive(raw.get("contractSize"), raw.get("contractSz"), raw.get("ctVal")) or Decimal("1")
        amount_step = _first_positive(
            raw.get("quantityMultiplier"),
            raw.get("sizeMultiplier"),
            raw.get("sizeStep"),
            raw.get("lotSize"),
        )
        if amount_step <= 0:
            amount_step = _precision_step(raw.get("quantityPrecision") or raw.get("sizePlace"))
        price_tick = _first_positive(
            raw.get("priceMultiplier"),
            raw.get("priceStep"),
            raw.get("tickSize"),
        )
        if price_tick <= 0:
            price_tick = _positive(raw.get("priceEndStep")) * _precision_step(
                raw.get("pricePrecision") or raw.get("pricePlace")
            )
        values.update({
            "amount_step": amount_step * contract,
            "min_amount": _first_positive(
                raw.get("minOrderQty"),
                raw.get("minTradeNum"),
                raw.get("minQty"),
                raw.get("minSize"),
            ) * contract,
            "min_notional": _first_positive(
                raw.get("minOrderAmount"),
                raw.get("minTradeUSDT"),
                raw.get("minNotional"),
            ),
            "price_tick": price_tick,
            "contract_size": contract,
        })
        return values
    amount_step = _first_positive(
        raw.get("quantityMultiplier"),
        raw.get("quantityStep"),
        raw.get("sizeStep"),
        raw.get("minTradeIncrement"),
    )
    if amount_step <= 0:
        amount_step = _precision_step(raw.get("quantityPrecision") or raw.get("quantityPlace"))
    price_tick = _first_positive(
        raw.get("priceMultiplier"),
        raw.get("priceStep"),
        raw.get("tickSize"),
        raw.get("priceTick"),
    )
    if price_tick <= 0:
        price_tick = _precision_step(raw.get("pricePrecision") or raw.get("pricePlace"))
    values.update({
        "amount_step": amount_step,
        "min_amount": _first_positive(
            raw.get("minOrderQty"),
            raw.get("minTradeAmount"),
            raw.get("minTradeNum"),
            raw.get("minQty"),
        ),
        "min_notional": _first_positive(
            raw.get("minOrderAmount"),
            raw.get("minTradeUSDT"),
            raw.get("minNotional"),
        ),
        "price_tick": price_tick,
    })
    return values


def _parse_gate(market_type: str, raw: Mapping[str, Any]) -> dict[str, Decimal]:
    values = _empty_values()
    if market_type == "swap":
        contract = _first_positive(raw.get("quanto_multiplier"), raw.get("contract_size")) or Decimal("1")
        order_min = _positive(raw.get("order_size_min")) or Decimal("1")
        exponent = order_min.normalize().as_tuple().exponent
        contract_step = Decimal("1").scaleb(exponent) if exponent < 0 else Decimal("1")
        values.update({
            "amount_step": contract_step * contract,
            "min_amount": order_min * contract,
            "min_notional": _first_positive(raw.get("order_value_min"), raw.get("min_notional")),
            "price_tick": _positive(raw.get("order_price_round")),
            "contract_size": contract,
        })
        return values
    values.update({
        "amount_step": _precision_step(raw.get("amount_precision")),
        "min_amount": _positive(raw.get("min_base_amount")),
        "min_notional": _positive(raw.get("min_quote_amount")),
        "price_tick": _precision_step(raw.get("precision")),
    })
    return values


def _parse_htx(market_type: str, raw: Mapping[str, Any]) -> dict[str, Decimal]:
    values = _empty_values()
    if market_type == "swap":
        contract = _positive(raw.get("contract_size")) or Decimal("1")
        values.update({
            "amount_step": contract,
            "min_amount": _first_positive(raw.get("min_volume"), raw.get("min_order_volume"), 1) * contract,
            "min_notional": _first_positive(raw.get("min_notional"), raw.get("min_order_value")),
            "price_tick": _positive(raw.get("price_tick")),
            "contract_size": contract,
        })
        return values
    values.update({
        "amount_step": _precision_step(raw.get("ap") or raw.get("amount-precision") or raw.get("amount_precision")),
        "min_amount": _first_positive(
            raw.get("minoa"), raw.get("min-order-amt"), raw.get("limit-order-min-order-amt")
        ),
        "min_notional": _first_positive(raw.get("minov"), raw.get("min-order-value")),
        "price_tick": _precision_step(raw.get("pp") or raw.get("price-precision") or raw.get("price_precision")),
    })
    return values


def _first_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, Mapping)), {})
    return value if isinstance(value, Mapping) else {}


def _matching_row(value: Any, field: str | tuple[str, ...], expected: str) -> Mapping[str, Any]:
    fields = (field,) if isinstance(field, str) else field
    target = str(expected or "").replace("-", "").replace("_", "").replace("/", "").upper()
    items = value if isinstance(value, list) else [value] if isinstance(value, Mapping) else []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        for name in fields:
            candidate = str(item.get(name) or "").replace("-", "").replace("_", "").replace("/", "").upper()
            if candidate == target:
                return item
    return {}


def _parse_strategy_crypto_key(value: str) -> tuple[str, str, str]:
    text = str(value or "").strip()
    if not text.lower().startswith("crypto:"):
        raise ValueError("not crypto")
    body = text.split(":", 1)[1]
    symbol, _, venue = body.partition("@")
    exchange = ""
    market_type = "spot"
    if venue:
        if ":" in venue:
            exchange, market_type = venue.split(":", 1)
        elif venue.lower() in {"spot", "swap"}:
            market_type = venue
        else:
            exchange = venue
    return symbol, exchange, market_type


def _snapshot_payload(rules: Mapping[str, InstrumentRules], captured_at: str) -> bytes:
    return json.dumps(
        {
            "schemaVersion": RULES_SCHEMA_VERSION,
            "capturedAt": captured_at,
            "rules": [rules[key].metadata() for key in sorted(rules)],
        },
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def default_rules_for_symbol(symbol: str) -> InstrumentRules:
    """Non-network fallback used by low-level runner tests and non-crypto assets."""
    is_crypto = str(symbol or "").startswith("Crypto:")
    return InstrumentRules(
        key=str(symbol),
        exchange_id="",
        market_type="",
        symbol=str(symbol),
        amount_step=1e-8 if is_crypto else 1.0,
        source="engine_default",
        captured_at="",
    )


def get_instrument_rules_provider() -> InstrumentRulesProvider:
    global _default_provider
    if _default_provider is None:
        with _default_provider_lock:
            if _default_provider is None:
                _default_provider = InstrumentRulesProvider()
    return _default_provider
