"""Bounded read-only venue queries for reported order P&L; no trade placement."""
from collections import defaultdict
import json
import time
from datetime import datetime

from app.services.execution_streams.reported_pnl import fetch_order_report, json_object, number, same_quantity, websocket_report
from app.utils.db import get_db_connection
from app.utils.logger import get_logger

logger = get_logger(__name__)
_CRYPTO_VENUES = {"binance", "okx", "bybit", "bitget", "gate", "htx"}
_DERIVATIVE_MARKETS = {"swap", "future", "futures", "perp", "perpetual"}


def supports_reported_order_pnl(exchange_id, market_type):
    exchange = str(exchange_id or "").strip().lower()
    market = str(market_type or "").strip().lower()
    if exchange == "ibkr":
        return market == "usstock"
    if exchange == "tiger":
        return market in {"usstock", "hkstock"}
    return exchange in _CRYPTO_VENUES and market in _DERIVATIVE_MARKETS


def order_key(row):
    return (int(row.get("credential_id") or 0), str(row.get("exchange_id") or "").lower(),
            str(row.get("market_type") or "").lower(), str(row.get("symbol") or ""), str(row.get("exchange_order_id") or ""))


def _claim(key, expected):
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("reported-pnl:" + str(key[0]),))
        cur.execute("""SELECT COUNT(*) AS n FROM qd_exchange_order_pnl
            WHERE credential_id=%s AND checked_at > NOW() - INTERVAL '60 seconds'""", (key[0],))
        if int((cur.fetchone() or {}).get("n") or 0) >= 4:
            db.commit()
            return False
        cur.execute("""INSERT INTO qd_exchange_order_pnl
            (credential_id, exchange_id, market_type, symbol, exchange_order_id, expected_quantity)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (credential_id,exchange_id,market_type,symbol,exchange_order_id)
            DO UPDATE SET checked_at=NOW(), expected_quantity=EXCLUDED.expected_quantity
            WHERE qd_exchange_order_pnl.checked_at < NOW() - INTERVAL '60 seconds'
            RETURNING credential_id""", (*key, expected))
        claimed = bool(cur.fetchone())
        db.commit()
        cur.close()
        return claimed


def _save(key, report):
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("""UPDATE qd_exchange_order_pnl SET report=%s::jsonb
            WHERE credential_id=%s AND exchange_id=%s AND market_type=%s AND symbol=%s AND exchange_order_id=%s""",
                    (json.dumps(report), *key))
        db.commit()
        cur.close()


def _load(keys):
    if not keys:
        return {}, {}
    credentials, orders = list({k[0] for k in keys}), list({k[4] for k in keys})
    with get_db_connection() as db:
        cur = db.cursor()
        cur.execute("SELECT * FROM qd_exchange_order_pnl WHERE credential_id=ANY(%s) AND exchange_order_id=ANY(%s)",
                    (credentials, orders))
        reports = {order_key(row): dict(json_object(row.get("report")), checked_at=row["checked_at"].timestamp())
                   for row in cur.fetchall() or []}
        cur.execute("""SELECT credential_id,exchange_id,market_type,symbol,exchange_order_id,exchange_fill_id,
            quantity,realized_pnl,raw_json FROM qd_execution_events
            WHERE credential_id=ANY(%s) AND exchange_order_id=ANY(%s) AND (realized_pnl IS NOT NULL OR quantity > 0)
            ORDER BY id""", (credentials, orders))
        events = defaultdict(list)
        for row in cur.fetchall() or []:
            events[order_key(row)].append(dict(row))
        cur.close()
    return reports, events


def _client(key, user_id, trading_config):
    from app.services.exchange_execution import resolve_exchange_config
    from app.services.live_trading.factory import create_client
    from app.services.pending_orders.live_order_support import bind_instrument_product_contract
    cfg = resolve_exchange_config({"credential_id": key[0]}, user_id=user_id)
    if str(cfg.get("exchange_id") or "").lower() != key[1]:
        raise ValueError("Exchange credential mismatch")
    cfg = bind_instrument_product_contract(cfg, trading_config, symbol=key[3], exchange_id=key[1], market_type=key[2])
    return create_client(cfg, market_type=key[2]), cfg


def enrich_reported_order_pnl(rows, *, user_id, trading_config):
    """Attach each order total once; never allocate reported P&L by local quantity."""
    from app.utils.trade_close_reason import is_exit_trade_type
    groups = defaultdict(list)
    for row in rows:
        row["exchange_pnl"] = {"status": "not_applicable"}
        if row.get("exchange_order_id"):
            groups[order_key(row)].append(row)
    eligible = {k: v for k, v in groups.items() if k[0] > 0 and supports_reported_order_pnl(k[1], k[2])
                and all(is_exit_trade_type(str(r.get("type") or "")) for r in v)}
    for key, members in groups.items():
        if any(is_exit_trade_type(str(r.get("type") or "")) for r in members):
            for row in members:
                if key[0] <= 0:
                    row["exchange_pnl"] = {"status": "unavailable"}
                elif key[2] == "spot" and key[1] in _CRYPTO_VENUES:
                    row["exchange_pnl"] = {"status": "not_applicable_spot"}
                elif not supports_reported_order_pnl(key[1], key[2]):
                    row["exchange_pnl"] = {"status": "unsupported"}
                else:
                    row["exchange_pnl"] = {"status": "pending" if key in eligible else "unavailable"}
    if not eligible:
        return rows
    reports, events = _load(eligible)
    attempts = 0
    for key, members in sorted(eligible.items(), key=lambda item: (
            (reports.get(item[0]) or {}).get("status") == "reported",
            (reports.get(item[0]) or {}).get("checked_at", 0), -max(int(r["id"]) for r in item[1]))):
        quantities = [number(r.get("amount")) for r in members]
        if any(q is None or q <= 0 for q in quantities):
            continue
        expected = sum(quantities)
        report = reports.get(key) or {}
        if (report.get("status") != "reported" or not same_quantity(report.get("quantity"), expected)
                or time.time() - float(report.get("checked_at") or 0) > 300):
            report = {}
            quote = key[3].rsplit("/", 1)[-1].upper() if "/" in key[3] else ""
            if (key[1] in {"binance", "bybit"} and quote in {"USDT", "USDC"}) or key[1] == "ibkr":
                report = websocket_report(events.get(key, []), expected=expected, exchange=key[1], currency=quote) or {}
            if not report and key[1] != "ibkr" and attempts < 2 and _claim(key, expected):
                attempts += 1
                try:
                    client, cfg = _client(key, user_id, trading_config)
                    from app.services.live_trading.fill_accounting import contract_multiplier
                    multiplier = contract_multiplier(client, key[1], key[3]) if key[1] in {"gate", "okx", "htx"} else 1
                    report = websocket_report(events.get(key, []), expected=expected, exchange=key[1],
                                              currency=quote, multiplier=multiplier) or {}
                    if not report:
                        stamp = max(r.get("created_at") or 0 for r in members)
                        if isinstance(stamp, datetime):
                            stamp = stamp.timestamp()
                        report = fetch_order_report(client, exchange=key[1], symbol=key[3], order_id=key[4],
                                                    expected=expected, timestamp=float(stamp), config=cfg) or {}
                    if report:
                        _save(key, report)
                except Exception:
                    logger.warning("Reported order P&L reconciliation failed credential=%s exchange=%s order=%s", key[0], key[1], key[4])
        newest = max(members, key=lambda r: int(r["id"]))
        if report:
            newest["exchange_pnl"] = dict(report, order_id=key[4])
            for row in members:
                if row is not newest:
                    row["exchange_pnl"] = dict(status="order_total_elsewhere", order_id=key[4], row_id=newest["id"])
    return rows
