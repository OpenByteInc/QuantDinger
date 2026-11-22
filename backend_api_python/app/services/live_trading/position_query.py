"""Resolve close/reduce quantities without crossing strategy ownership."""

from __future__ import annotations

import math
import os
import random
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

import requests

from app.services.pending_orders.error_classification import classify_exchange_order_error
from app.services.live_trading.records import (
    fetch_allocated_position_size,
    fetch_position_size_for_side,
    normalize_strategy_symbol,
)
from app.services.live_trading.symbols import (
    to_bitget_um_symbol,
    to_gate_currency_pair,
    to_okx_swap_inst_id,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

_SnapshotT = TypeVar("_SnapshotT")


class PositionSnapshotUnavailable(RuntimeError):
    """A position snapshot could not be read or validated safely."""

    def __init__(
        self,
        *,
        exchange_id: str,
        symbol: str,
        side: str,
        attempts: int,
        cause: BaseException,
    ) -> None:
        venue = str(exchange_id or "exchange").strip().lower() or "exchange"
        instrument = str(symbol or "unknown").strip() or "unknown"
        leg = str(side or "unknown").strip().lower() or "unknown"
        cause_name = type(cause).__name__
        super().__init__(
            f"Position snapshot temporarily unavailable after {int(attempts)} attempt(s): "
            f"{venue} {instrument} {leg} ({cause_name})"
        )
        self.exchange_id = venue
        self.symbol = instrument
        self.side = leg
        self.attempts = int(attempts)
        self.cause_name = cause_name


def _bounded_int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _bounded_float_env(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    if not math.isfinite(value):
        value = default
    return max(minimum, min(maximum, value))


def _is_transient_position_snapshot_error(exc: BaseException) -> bool:
    if isinstance(exc, (requests.exceptions.SSLError, requests.exceptions.InvalidURL)):
        return False
    if isinstance(
        exc,
        (
            TimeoutError,
            ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.ChunkedEncodingError,
        ),
    ):
        return True
    classified = classify_exchange_order_error(exc)
    return bool(
        classified.get("retryable")
        and classified.get("category")
        in {"transport", "rate_limit", "market_unavailable"}
    )


def _read_position_snapshot(
    fetch: Callable[[], _SnapshotT],
    *,
    client: Any,
    exchange_config: Optional[Dict[str, Any]],
    symbol: str,
    side: str,
) -> _SnapshotT:
    """Retry only transient, read-only position snapshots; never retry an order."""
    cfg = exchange_config if isinstance(exchange_config, dict) else {}
    exchange_id = str(cfg.get("exchange_id") or cfg.get("exchange") or "").strip().lower()
    if not exchange_id:
        exchange_id = type(client).__name__.removesuffix("Client").lower()

    retries = _bounded_int_env(
        "LIVE_TRADING_POSITION_SNAPSHOT_RETRIES",
        1,
        minimum=0,
        maximum=3,
    )
    backoff = _bounded_float_env(
        "LIVE_TRADING_POSITION_SNAPSHOT_RETRY_BACKOFF_SEC",
        0.5,
        minimum=0.0,
        maximum=5.0,
    )
    jitter = _bounded_float_env(
        "LIVE_TRADING_POSITION_SNAPSHOT_RETRY_JITTER_RATIO",
        0.2,
        minimum=0.0,
        maximum=1.0,
    )
    attempts = retries + 1
    for attempt in range(1, attempts + 1):
        try:
            return fetch()
        except Exception as exc:
            if not _is_transient_position_snapshot_error(exc):
                raise
            if attempt >= attempts:
                raise PositionSnapshotUnavailable(
                    exchange_id=exchange_id,
                    symbol=symbol,
                    side=side,
                    attempts=attempt,
                    cause=exc,
                ) from exc
            base_delay = backoff * (2 ** (attempt - 1))
            delay = base_delay * random.uniform(
                max(0.0, 1.0 - jitter),
                1.0 + jitter,
            )
            logger.warning(
                "Position snapshot transport failure exchange=%s symbol=%s side=%s "
                "attempt=%s/%s error=%s retry_in_sec=%.3f",
                exchange_id,
                symbol,
                side,
                attempt,
                attempts,
                type(exc).__name__,
                delay,
            )
            if delay > 0:
                time.sleep(delay)


def symbols_equivalent(a: str, b: str) -> bool:
    na = normalize_strategy_symbol(a)
    nb = normalize_strategy_symbol(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    return na.replace("/", "") == nb.replace("/", "")


from app.services.live_trading.position_row_parse import (
    infer_position_side_from_row,
    position_base_qty_for_side,
)


def query_exchange_position_size(
    *,
    client: Any,
    symbol: str,
    pos_side: str,
    market_type: str,
    exchange_config: Optional[Dict[str, Any]] = None,
    strict: bool = False,
) -> float:
    """
    Best-effort base-asset position size on the connected exchange for ``pos_side``.
    Returns 0.0 when unknown or flat.
    """
    if client is None:
        if strict:
            raise PositionSnapshotUnavailable(
                exchange_id=str((exchange_config or {}).get("exchange_id") or "exchange"),
                symbol=symbol, side=pos_side, attempts=0,
                cause=ValueError("Position client is unavailable"),
            )
        return 0.0
    side = str(pos_side or "").strip().lower()
    if side not in ("long", "short"):
        return 0.0
    mt = str(market_type or "swap").strip().lower()
    cfg = exchange_config if isinstance(exchange_config, dict) else {}
    sym = str(symbol or "").strip()

    def position_rows(value: Any, quantity_keys: tuple[str, ...]) -> List[dict]:
        valid = isinstance(value, list) and all(isinstance(row, dict) for row in value)
        if valid and strict:
            for row in value:
                try:
                    quantity = next(row[key] for key in quantity_keys if key in row)
                    valid = math.isfinite(float(quantity))
                except (StopIteration, TypeError, ValueError):
                    valid = False
                if not valid:
                    break
        if not valid:
            if strict:
                raise PositionSnapshotUnavailable(
                    exchange_id=str(cfg.get("exchange_id") or type(client).__name__),
                    symbol=sym, side=side, attempts=1,
                    cause=ValueError("Invalid position snapshot payload"),
                )
            return []
        return value

    # Spot ownership is the complete base inventory.  Sell sizing has a
    # separate free/available clamp because locked balances are still owned.
    if mt == "spot":
        if side != "long":
            return 0.0
        try:
            from app.services.live_trading.spot_sizing import get_spot_total_base_balance

            return max(
                0.0,
                float(
                    _read_position_snapshot(
                        lambda: get_spot_total_base_balance(
                            client,
                            symbol=sym,
                            strict=strict,
                        ),
                        client=client,
                        exchange_config=cfg,
                        symbol=sym,
                        side=side,
                    )
                    or 0.0
                ),
            )
        except Exception as e:
            if strict:
                raise
            logger.debug("spot total balance query failed symbol=%s: %s", sym, e)
            return 0.0

    try:
        from app.services.live_trading.binance import BinanceFuturesClient
        from app.services.live_trading.okx import OkxClient
        from app.services.live_trading.bybit import BybitClient
        from app.services.live_trading.bitget import BitgetMixClient
        from app.services.live_trading.gate import GateUsdtFuturesClient
        from app.services.live_trading.htx import HtxClient
    except Exception:
        if strict:
            raise
        return 0.0

    if isinstance(client, OkxClient):
        inst_id = to_okx_swap_inst_id(sym)
        pos_resp = _read_position_snapshot(
            lambda: client.get_positions(inst_id=inst_id),
            client=client,
            exchange_config=cfg,
            symbol=sym,
            side=side,
        ) or {}
        pos_data = position_rows(pos_resp.get("data") if isinstance(pos_resp, dict) else None, ("pos",))
        for pos in pos_data:
            if not isinstance(pos, dict):
                continue
            if str(pos.get("instId") or "").strip() != inst_id:
                continue
            if infer_position_side_from_row(pos) != side:
                continue
            try:
                contracts = abs(float(pos.get("pos") or 0.0))
                contract_value = float(pos.get("ctVal") or 0.0)
            except (TypeError, ValueError):
                contracts = 0.0
                contract_value = 0.0
            if contracts <= 0:
                continue
            if contract_value <= 0:
                from app.services.live_trading.fill_accounting import contract_multiplier

                contract_value = _read_position_snapshot(
                    lambda: contract_multiplier(client, "okx", sym),
                    client=client,
                    exchange_config=cfg,
                    symbol=sym,
                    side=side,
                )
            qty = contracts * max(0.0, float(contract_value or 0.0))
            if qty > 0:
                return qty
        return 0.0

    if isinstance(client, BinanceFuturesClient):
        norm_sym = sym.replace("/", "").replace("-", "").upper()
        pos_list = _read_position_snapshot(
            client.get_positions,
            client=client,
            exchange_config=cfg,
            symbol=sym,
            side=side,
        )
        if isinstance(pos_list, dict) and "raw" in pos_list:
            pos_list = pos_list["raw"]
        pos_list = position_rows(pos_list, ("positionAmt",))
        for pos in pos_list:
            if not isinstance(pos, dict):
                continue
            if str(pos.get("symbol") or "").upper() != norm_sym:
                continue
            qty = position_base_qty_for_side(pos, side)
            if qty > 0:
                return qty
        return 0.0

    if isinstance(client, BybitClient):
        pos_resp = _read_position_snapshot(
            lambda: client.get_positions(symbol=sym),
            client=client,
            exchange_config=cfg,
            symbol=sym,
            side=side,
        ) or {}
        result = pos_resp.get("result") if isinstance(pos_resp, dict) else None
        pos_list = position_rows(result.get("list") if isinstance(result, dict) else None, ("size",))
        want = sym.replace("/", "").replace("-", "").upper()
        for pos in pos_list:
            if not isinstance(pos, dict):
                continue
            if str(pos.get("symbol") or "").strip().upper() != want:
                continue
            qty = position_base_qty_for_side(pos, side)
            if qty > 0:
                return qty
        return 0.0

    if isinstance(client, BitgetMixClient):
        product_type = str(cfg.get("product_type") or cfg.get("productType") or "USDT-FUTURES")
        pos_resp = _read_position_snapshot(
            lambda: client.get_positions(product_type=product_type, symbol=sym),
            client=client,
            exchange_config=cfg,
            symbol=sym,
            side=side,
        ) or {}
        pos_list = position_rows(pos_resp.get("data") if isinstance(pos_resp, dict) else None, ("total", "size"))
        want = to_bitget_um_symbol(sym).upper()
        for pos in pos_list:
            if not isinstance(pos, dict):
                continue
            if to_bitget_um_symbol(str(pos.get("symbol") or "")).upper() != want:
                continue
            qty = position_base_qty_for_side(pos, side)
            if qty > 0:
                return qty
        return 0.0

    if isinstance(client, GateUsdtFuturesClient):
        resp = _read_position_snapshot(
            client.get_positions,
            client=client,
            exchange_config=cfg,
            symbol=sym,
            side=side,
        )
        items = position_rows(resp, ("size",))
        want_contract = to_gate_currency_pair(sym)
        for p in items:
            if not isinstance(p, dict):
                continue
            contract = str(p.get("contract") or "").strip()
            if contract != want_contract and not symbols_equivalent(contract.replace("_", "/"), sym):
                continue
            from app.services.live_trading.fill_accounting import contract_multiplier
            qm = _read_position_snapshot(
                lambda: contract_multiplier(client, "gate", contract.replace("_", "/")),
                client=client,
                exchange_config=cfg,
                symbol=sym,
                side=side,
            )
            qty_base = position_base_qty_for_side(p, side, contracts_to_base=qm)
            if qty_base > 0:
                return float(qty_base)
        return 0.0

    if isinstance(client, HtxClient):
        resp = _read_position_snapshot(
            lambda: client.get_positions(symbol=sym),
            client=client,
            exchange_config=cfg,
            symbol=sym,
            side=side,
        ) or {}
        data = position_rows(resp.get("data") if isinstance(resp, dict) else None, ("volume",))
        if not data:
            return 0.0
        contract_size = 1.0
        if getattr(client, "market_type", "swap") != "spot":
            from app.services.live_trading.fill_accounting import contract_multiplier
            contract_size = _read_position_snapshot(
                lambda: contract_multiplier(client, "htx", sym),
                client=client,
                exchange_config=cfg,
                symbol=sym,
                side=side,
            )
        for p in data:
            if not isinstance(p, dict):
                continue
            contract = str(p.get("contract_code") or p.get("symbol") or "")
            if sym and contract and not symbols_equivalent(contract.replace("-", "/"), sym):
                continue
            mult = contract_size if getattr(client, "market_type", "swap") != "spot" else 1.0
            qty = position_base_qty_for_side(p, side, contracts_to_base=mult)
            if qty > 0:
                return qty
        return 0.0

    # IBKR / Alpaca broker clients.
    try:
        def fetch_broker_positions():
            method = getattr(client, "get_positions", None)
            if not callable(method):
                if strict:
                    raise RuntimeError(f"position_query_not_supported:{type(client).__name__}")
                return []
            if not strict:
                return method()
            try:
                return method(raise_on_error=True)
            except TypeError as exc:
                if "raise_on_error" not in str(exc):
                    raise
                return method()

        positions = _read_position_snapshot(
            fetch_broker_positions,
            client=client,
            exchange_config=cfg,
            symbol=sym,
            side=side,
        )
    except Exception:
        if strict:
            raise
        positions = []
    if isinstance(positions, list):
        positions = position_rows(positions, ("quantity", "qty", "position", "size", "positionAmt"))
        for p in positions:
            if not isinstance(p, dict):
                continue
            p_sym = str(
                p.get("symbol")
                or p.get("ib_symbol")
                or p.get("contract")
                or ""
            ).strip()
            if sym and p_sym and not symbols_equivalent(p_sym, sym):
                continue
            qty = position_base_qty_for_side(p, side)
            if qty > 0:
                return qty
        return 0.0
    if strict:
        raise RuntimeError(f"position_query_not_supported:{type(client).__name__}")
    return 0.0


def _extract_position_rows(resp: Any) -> List[Any]:
    if isinstance(resp, list):
        return resp
    if not isinstance(resp, dict):
        return []
    for key in ("data", "list", "positions", "result"):
        chunk = resp.get(key)
        if isinstance(chunk, list):
            return chunk
        if isinstance(chunk, dict):
            nested = chunk.get("list") or chunk.get("data")
            if isinstance(nested, list):
                return nested
    return []


def resolve_reduce_only_quantity(
    *,
    strategy_id: int,
    symbol: str,
    pos_side: str,
    requested_amount: float,
    client: Any,
    market_type: str,
    exchange_config: Optional[Dict[str, Any]] = None,
    allow_exchange_fallback: bool = False,
    user_id: int = 0,
    credential_id: int = 0,
) -> Tuple[float, Dict[str, Any]]:
    """
    Choose a safe close/reduce base quantity.

    Strategy orders are capped by the strategy-owned L3 position ledger.  The
    exchange account position/balance is only a second safety cap and must not
    become the source quantity, because it can include a user's manual holdings
    or positions allocated to another strategy.

    ``allow_exchange_fallback`` is an explicit recovery-only escape hatch for
    callers that intentionally operate on the whole exchange position.
    """
    meta: Dict[str, Any] = {}
    amount = max(0.0, float(requested_amount or 0.0))
    meta["requested"] = amount

    db_size = fetch_position_size_for_side(int(strategy_id), symbol, pos_side)
    meta["db_size"] = db_size
    if db_size > 0:
        if amount <= 0:
            amount = db_size
            meta["filled_from"] = "db"
        elif amount > db_size:
            meta["capped_by"] = "db"
            amount = db_size
    else:
        meta["db_missing"] = True
        if not allow_exchange_fallback:
            amount = 0.0
            meta["blocked_by"] = "strategy_position_missing"

    exch_size = query_exchange_position_size(
        client=client,
        symbol=symbol,
        pos_side=pos_side,
        market_type=market_type,
        exchange_config=exchange_config,
    )
    meta["exchange_size"] = exch_size

    if exch_size > 0:
        if amount <= 0 and allow_exchange_fallback:
            amount = exch_size
            meta["filled_from"] = "exchange"
            logger.info(
                "[RiskControl] Close %s %s: no DB size; using exchange position=%s",
                symbol,
                pos_side,
                exch_size,
            )
        elif amount > exch_size:
            logger.info(
                "[RiskControl] Close %s %s: capping amount %s -> exchange position %s",
                symbol,
                pos_side,
                amount,
                exch_size,
            )
            meta["capped_by"] = "exchange"
            amount = exch_size
    elif amount <= 0:
        logger.warning(
            "[RiskControl] Close %s %s: no position in DB or on exchange (amount stays 0)",
            symbol,
            pos_side,
        )
        meta["filled_from"] = "none"

    # Reserve the allocations of other strategies sharing this account leg.
    # Account surplus belongs to the user automatically; this strategy may
    # still close its own ledger quantity without registering a manual floor.
    allocated = db_size
    if int(credential_id or 0) > 0:
        allocated = fetch_allocated_position_size(
            strategy_id=int(strategy_id),
            credential_id=int(credential_id),
            market_type=market_type,
            symbol=symbol,
            side=pos_side,
        )
    other_allocated = max(0.0, float(allocated or 0.0) - float(db_size or 0.0))
    available = max(0.0, float(exch_size or 0.0) - other_allocated)
    meta["account_allocated_size"] = allocated
    meta["other_strategy_allocated_size"] = other_allocated
    meta["exchange_strategy_available"] = available
    if amount > available:
        amount = available
        meta["capped_by"] = "account_allocation"

    meta["resolved"] = amount
    return amount, meta
