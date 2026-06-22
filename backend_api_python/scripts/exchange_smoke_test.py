#!/usr/bin/env python3
"""
Exchange adapter smoke test runner.

Default mode is read-only. Order placement is disabled unless BOTH are set:
  - command line: --allow-orders
  - environment: EXCHANGE_SMOKE_ALLOW_ORDERS=1

Examples:
  python scripts/exchange_smoke_test.py --config smoke.exchanges.json
  python scripts/exchange_smoke_test.py --exchange bitget --market spot --symbol BTC/USDT --demo
  EXCHANGE_SMOKE_ALLOW_ORDERS=1 python scripts/exchange_smoke_test.py --config smoke.json --allow-orders
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.grid.exchange_orders import (  # noqa: E402
    cancel_grid_order,
    place_grid_limit_order,
    query_grid_order_fill,
)
from app.services.live_trading.base import LiveTradingError  # noqa: E402
from app.services.live_trading.capabilities import canonical_exchange_id, supported_crypto_exchange_ids  # noqa: E402
from app.services.live_trading.factory import create_client  # noqa: E402
from app.services.live_trading.position_query import query_exchange_position_size  # noqa: E402
from app.services.live_trading.spot_wallet_snapshot import list_spot_wallet_positions  # noqa: E402


SUPPORTED_EXCHANGES = tuple(sorted(supported_crypto_exchange_ids()))


@dataclass
class StepResult:
    name: str
    status: str
    detail: str = ""
    elapsed_ms: int = 0


@dataclass
class SmokeCase:
    exchange: str
    market_type: str
    symbol: str
    exchange_config: Dict[str, Any]
    name: str = ""
    limit_price: float = 0.0
    limit_quantity: float = 0.0
    leverage: float = 1.0
    margin_mode: str = "cross"
    pos_side: str = "long"
    max_notional_usdt: float = 5.0

    def label(self) -> str:
        return self.name or f"{self.exchange}:{self.market_type}:{self.symbol}"


@dataclass
class CaseResult:
    case: SmokeCase
    steps: List[StepResult] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(s.status == "FAIL" for s in self.steps):
            return "FAIL"
        if any(s.status == "WARN" for s in self.steps):
            return "WARN"
        return "PASS"


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return int(v) == 1
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _norm_market_type(v: Any) -> str:
    mt = str(v or "swap").strip().lower()
    if mt in ("futures", "future", "perp", "perpetual"):
        return "swap"
    if mt not in ("spot", "swap"):
        return "swap"
    return mt


def _first_str(*values: Any) -> str:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def _mask(v: Any) -> str:
    s = str(v or "")
    if not s:
        return ""
    if len(s) <= 8:
        return s[:2] + "***"
    return s[:4] + "..." + s[-4:]


def _safe_cfg_for_output(cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(cfg or {})
    for key in ("api_key", "apiKey", "secret_key", "secret", "passphrase", "password"):
        if out.get(key):
            out[key] = _mask(out.get(key))
    return out


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, val = s.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _case_from_mapping(raw: Dict[str, Any], defaults: Optional[Dict[str, Any]] = None) -> SmokeCase:
    defaults = defaults or {}
    cfg = dict(defaults.get("exchange_config") or {})
    nested = raw.get("exchange_config")
    if isinstance(nested, dict):
        cfg.update(nested)
    for key in (
        "exchange_id",
        "exchangeId",
        "api_key",
        "apiKey",
        "secret_key",
        "secret",
        "passphrase",
        "password",
        "base_url",
        "baseUrl",
        "futures_base_url",
        "futuresBaseUrl",
        "enable_demo_trading",
        "simulated_trading",
        "use_testnet",
        "is_testnet",
        "environment",
        "network",
        "product_type",
        "productType",
        "margin_coin",
        "marginCoin",
    ):
        if raw.get(key) is not None:
            cfg[key] = raw[key]

    exchange = canonical_exchange_id(_first_str(raw.get("exchange"), raw.get("exchange_id"), raw.get("exchangeId"), cfg.get("exchange_id"), cfg.get("exchangeId")))
    if not exchange:
        raise ValueError("case missing exchange/exchange_id")
    cfg.setdefault("exchange_id", exchange)

    mt = _norm_market_type(raw.get("market_type") or raw.get("market") or defaults.get("market_type"))
    symbol = _first_str(raw.get("symbol"), defaults.get("symbol"), "BTC/USDT")
    return SmokeCase(
        exchange=exchange,
        market_type=mt,
        symbol=symbol,
        exchange_config=cfg,
        name=str(raw.get("name") or ""),
        limit_price=float(raw.get("limit_price") or raw.get("price") or 0.0),
        limit_quantity=float(raw.get("limit_quantity") or raw.get("quantity") or raw.get("qty") or 0.0),
        leverage=float(raw.get("leverage") or defaults.get("leverage") or 1.0),
        margin_mode=str(raw.get("margin_mode") or raw.get("marginMode") or defaults.get("margin_mode") or "cross"),
        pos_side=str(raw.get("pos_side") or raw.get("posSide") or defaults.get("pos_side") or "long").lower(),
        max_notional_usdt=float(raw.get("max_notional_usdt") or defaults.get("max_notional_usdt") or 5.0),
    )


def load_cases(config_path: Optional[Path], args: argparse.Namespace) -> List[SmokeCase]:
    _load_dotenv(ROOT / ".env")
    _load_dotenv(ROOT / ".env.testnet.local")

    if config_path:
        obj = _read_json(config_path)
        defaults = obj.get("defaults") if isinstance(obj.get("defaults"), dict) else {}
        rows = obj.get("exchanges") or obj.get("cases") or []
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            raise ValueError("config exchanges/cases must be a list")
        return [_case_from_mapping(r, defaults) for r in rows if isinstance(r, dict)]

    exchange = str(args.exchange or os.getenv("EXCHANGE_ID") or "").strip().lower()
    if not exchange:
        raise ValueError("--exchange is required when --config is not provided")
    cfg = {
        "exchange_id": exchange,
        "api_key": os.getenv("EXCHANGE_API_KEY") or os.getenv(f"{exchange.upper()}_API_KEY") or "",
        "secret_key": os.getenv("EXCHANGE_SECRET_KEY") or os.getenv(f"{exchange.upper()}_SECRET_KEY") or "",
        "passphrase": os.getenv("EXCHANGE_PASSPHRASE") or os.getenv(f"{exchange.upper()}_PASSPHRASE") or "",
    }
    if args.demo:
        cfg["environment"] = "testnet"
        cfg["simulated_trading"] = True
    raw = {
        "exchange": exchange,
        "market_type": args.market,
        "symbol": args.symbol,
        "exchange_config": cfg,
        "limit_price": args.limit_price,
        "limit_quantity": args.limit_quantity,
        "max_notional_usdt": args.max_usdt,
    }
    return [_case_from_mapping(raw)]


def _run_step(result: CaseResult, name: str, fn: Callable[[], Tuple[str, str]]) -> None:
    start = time.time()
    try:
        status, detail = fn()
    except Exception as e:
        status, detail = "FAIL", f"{type(e).__name__}: {e}"
    elapsed_ms = int((time.time() - start) * 1000)
    result.steps.append(StepResult(name=name, status=status, detail=detail, elapsed_ms=elapsed_ms))


def _metadata_step(client: Any, case: SmokeCase) -> Tuple[str, str]:
    calls: List[Tuple[str, Callable[[], Any]]] = []
    if hasattr(client, "get_symbol_meta"):
        calls.append(("get_symbol_meta", lambda: client.get_symbol_meta(symbol=case.symbol)))
    if hasattr(client, "get_contract"):
        pt = str(case.exchange_config.get("product_type") or case.exchange_config.get("productType") or "USDT-FUTURES")
        calls.append(("get_contract", lambda: client.get_contract(symbol=case.symbol, product_type=pt)))
    if hasattr(client, "get_instrument"):
        calls.append(("get_instrument", lambda: client.get_instrument(inst_id=case.symbol)))
    if not calls:
        return "WARN", "no known metadata method"
    errors = []
    for name, fn in calls:
        try:
            raw = fn()
            if raw:
                return "PASS", name
        except TypeError:
            errors.append(f"{name}: signature mismatch")
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}: {e}")
    return "WARN", "; ".join(errors) if errors else "metadata empty"


def _ticker_step(client: Any, case: SmokeCase) -> Tuple[str, str]:
    if not hasattr(client, "get_ticker"):
        return "WARN", "no get_ticker method"
    try:
        raw = client.get_ticker(symbol=case.symbol)
    except TypeError:
        raw = client.get_ticker(symbol=case.symbol, product_type=case.exchange_config.get("product_type") or "USDT-FUTURES")
    if isinstance(raw, dict) and raw:
        return "PASS", "ticker ok"
    return "WARN", "ticker empty"


def _account_step(client: Any, case: SmokeCase) -> Tuple[str, str]:
    if case.market_type == "spot":
        rows = list_spot_wallet_positions(client)
        return "PASS", f"spot_wallet_rows={len(rows)}"
    long_sz = query_exchange_position_size(
        client=client,
        symbol=case.symbol,
        pos_side="long",
        market_type=case.market_type,
        exchange_config=case.exchange_config,
    )
    short_sz = query_exchange_position_size(
        client=client,
        symbol=case.symbol,
        pos_side="short",
        market_type=case.market_type,
        exchange_config=case.exchange_config,
    )
    return "PASS", f"long={long_sz} short={short_sz}"


def _orders_enabled(args: argparse.Namespace) -> bool:
    return bool(args.allow_orders and _truthy(os.getenv("EXCHANGE_SMOKE_ALLOW_ORDERS")))


def _order_safety(case: SmokeCase) -> Tuple[bool, str]:
    qty = float(case.limit_quantity or 0.0)
    px = float(case.limit_price or 0.0)
    if qty <= 0 or px <= 0:
        return False, "limit_price and limit_quantity are required"
    notional = qty * px
    if notional > float(case.max_notional_usdt or 5.0):
        return False, f"notional {notional:.8f} > max_notional_usdt {case.max_notional_usdt:.8f}"
    return True, f"notional={notional:.8f}"


def _limit_order_roundtrip(client: Any, case: SmokeCase) -> Tuple[str, str]:
    ok, reason = _order_safety(case)
    if not ok:
        return "WARN", f"order test skipped: {reason}"
    coid = f"smoke{int(time.time()) % 100000000}"
    order_id = ""
    try:
        res = place_grid_limit_order(
            client,
            symbol=case.symbol,
            side="buy",
            quantity=case.limit_quantity,
            price=case.limit_price,
            market_type=case.market_type,
            exchange_config=case.exchange_config,
            pos_side=case.pos_side,
            reduce_only=False,
            client_order_id=coid,
            leverage=case.leverage,
            margin_mode=case.margin_mode,
            post_only=True,
        )
        order_id = str(getattr(res, "exchange_order_id", "") or "")
        filled, avg, status = query_grid_order_fill(
            client,
            symbol=case.symbol,
            market_type=case.market_type,
            exchange_order_id=order_id,
            client_order_id=coid,
            exchange_config=case.exchange_config,
        )
        cancel_grid_order(
            client,
            symbol=case.symbol,
            market_type=case.market_type,
            exchange_order_id=order_id,
            client_order_id=coid,
            exchange_config=case.exchange_config,
        )
        return "PASS", f"placed={order_id or coid} status={status} filled={filled} avg={avg}"
    finally:
        # Best-effort cleanup if an exception happened after placement.
        if order_id:
            try:
                cancel_grid_order(
                    client,
                    symbol=case.symbol,
                    market_type=case.market_type,
                    exchange_order_id=order_id,
                    client_order_id=coid,
                    exchange_config=case.exchange_config,
                )
            except Exception:
                pass


def run_case(case: SmokeCase, args: argparse.Namespace) -> CaseResult:
    result = CaseResult(case=case)
    client_holder: Dict[str, Any] = {}

    def create() -> Tuple[str, str]:
        if case.exchange not in SUPPORTED_EXCHANGES:
            return "WARN", f"exchange not in default crypto set: {case.exchange}"
        client_holder["client"] = create_client(case.exchange_config, market_type=case.market_type)
        return "PASS", f"{client_holder['client'].__class__.__name__} {_safe_cfg_for_output(case.exchange_config)}"

    _run_step(result, "create_client", create)
    client = client_holder.get("client")
    if client is None:
        return result

    _run_step(result, "ticker", lambda: _ticker_step(client, case))
    _run_step(result, "metadata", lambda: _metadata_step(client, case))
    _run_step(result, "account_or_position", lambda: _account_step(client, case))

    if _orders_enabled(args):
        _run_step(result, "limit_place_query_cancel", lambda: _limit_order_roundtrip(client, case))
    else:
        result.steps.append(
            StepResult(
                name="limit_place_query_cancel",
                status="SKIP",
                detail="set --allow-orders and EXCHANGE_SMOKE_ALLOW_ORDERS=1 to enable",
            )
        )
    return result


def print_table(results: Iterable[CaseResult]) -> None:
    rows = []
    for r in results:
        by_name = {s.name: s for s in r.steps}
        rows.append(
            [
                r.case.label(),
                r.status,
                by_name.get("create_client", StepResult("", "")).status,
                by_name.get("ticker", StepResult("", "")).status,
                by_name.get("metadata", StepResult("", "")).status,
                by_name.get("account_or_position", StepResult("", "")).status,
                by_name.get("limit_place_query_cancel", StepResult("", "")).status,
            ]
        )
    headers = ["case", "status", "client", "ticker", "metadata", "account", "order"]
    widths = [max(len(str(x)) for x in [h] + [row[i] for row in rows]) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * widths[i] for i, _ in enumerate(headers)))
    for row in rows:
        print("  ".join(str(x).ljust(widths[i]) for i, x in enumerate(row)))


def results_to_json(results: Iterable[CaseResult]) -> Dict[str, Any]:
    out = []
    for r in results:
        out.append(
            {
                "case": r.case.label(),
                "exchange": r.case.exchange,
                "market_type": r.case.market_type,
                "symbol": r.case.symbol,
                "status": r.status,
                "steps": [s.__dict__ for s in r.steps],
            }
        )
    return {"results": out}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run exchange adapter smoke checks")
    parser.add_argument(
        "--offline-contracts",
        action="store_true",
        help="Run no-key offline exchange contract tests and exit",
    )
    parser.add_argument("--config", help="JSON config file with exchanges/cases")
    parser.add_argument("--exchange", help="Exchange id when no config is provided")
    parser.add_argument("--market", default="swap", choices=["spot", "swap", "futures", "future", "perp", "perpetual"])
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--demo", action="store_true", help="Enable demo/testnet/simulated flags")
    parser.add_argument("--allow-orders", action="store_true", help="Allow post-only limit place/query/cancel")
    parser.add_argument("--limit-price", type=float, default=0.0)
    parser.add_argument("--limit-quantity", type=float, default=0.0)
    parser.add_argument("--max-usdt", type=float, default=5.0)
    parser.add_argument("--json", action="store_true", help="Print JSON instead of table")
    args = parser.parse_args(argv)

    if args.offline_contracts:
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "tests/test_grid_exchange_fill_contracts.py",
            "tests/test_grid_bitget_fills.py",
            "tests/test_grid_fill_units.py",
            "tests/test_exchange_offline_contract_fixtures.py",
            "tests/test_exchange_order_param_contracts.py",
            "tests/test_exchange_position_contract_fixtures.py",
            "-v",
        ]
        print("+", " ".join(cmd), flush=True)
        return subprocess.call(cmd, cwd=str(ROOT))

    try:
        cases = load_cases(Path(args.config).resolve() if args.config else None, args)
    except Exception as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2

    results: List[CaseResult] = []
    for case in cases:
        try:
            results.append(run_case(case, args))
        except LiveTradingError as e:
            cr = CaseResult(case=case)
            cr.steps.append(StepResult("create_client", "FAIL", str(e)))
            results.append(cr)

    if args.json:
        print(json.dumps(results_to_json(results), ensure_ascii=False, indent=2))
    else:
        print_table(results)
        for r in results:
            print(f"\n[{r.case.label()}]")
            for s in r.steps:
                print(f"  {s.status:4} {s.name:24} {s.elapsed_ms:5}ms  {s.detail}")
    return 1 if any(r.status == "FAIL" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
