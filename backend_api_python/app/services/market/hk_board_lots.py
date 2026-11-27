"""Durable HKEX board-lot metadata shared by whole-lot stock routes."""

from datetime import datetime, timedelta
from decimal import Decimal
from io import BytesIO
import re
from zoneinfo import ZoneInfo

import openpyxl
import requests
from app.services.openapi_brokers.symbols import equity_symbol

SOURCE = "https://www.hkex.com.hk/eng/services/trading/securities/securitieslists/ListOfSecurities.xlsx"


def _today():
    return datetime.now(ZoneInfo("Asia/Hong_Kong")).date()


def _parse(content):
    workbook = openpyxl.load_workbook(BytesIO(content), read_only=True, data_only=True)
    try:
        sheet = workbook.active
        # HKEX's declared worksheet dimensions can cover only the first eight rows.
        sheet.reset_dimensions()
        rows = iter(sheet.values)
        next(rows)
        stamp = str(next(rows)[0])
        match = re.search(r"(\d{2}/\d{2}/\d{4})", stamp)
        as_of = datetime.strptime(match[1], "%d/%m/%Y").date() if match else None
        if as_of is None or not _today() - timedelta(days=7) <= as_of <= _today():
            raise ValueError("brokerAccounts.forthright.lotUnavailable")
        headers = list(next(rows))
        indexes = {key: headers.index(key) for key in ("Stock Code", "Category", "Board Lot", "Trading Currency")}
        securities = {}
        names = {}
        records = []
        for row in rows:
            if len(row) <= max(indexes.values()):
                continue
            get = lambda key: row[indexes[key]]
            if get("Category") not in {"Equity", "Exchange Traded Products", "Real Estate Investment Trusts"}:
                continue
            code = str(get("Stock Code") or "").zfill(5)
            if not re.fullmatch(r"\d{5}", code):
                continue
            lot = Decimal(str(get("Board Lot")).replace(",", ""))
            if not lot.is_finite() or lot <= 0 or lot != lot.to_integral_value():
                raise ValueError("brokerAccounts.forthright.lotUnavailable")
            if code in securities and securities[code] != int(lot):
                raise ValueError("brokerAccounts.forthright.lotUnavailable")
            name = str(row[headers.index("Name of Securities")] or code) if "Name of Securities" in headers else code
            records.append({"symbol": code, "name": name, "currency": get("Trading Currency"),
                            "board_lot": int(lot), "asset_class": "etf" if get("Category") == "Exchange Traded Products" else "equity"})
            if get("Trading Currency") == "HKD":
                securities[code] = int(lot)
                names[code] = name
        if not securities:
            raise ValueError("brokerAccounts.forthright.lotUnavailable")
        return {"as_of": as_of.isoformat(), "securities": securities, "names": names, "records": records}
    finally:
        workbook.close()


def _download():
    with requests.get(SOURCE, timeout=15, stream=True) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 5_000_000:
                raise ValueError("brokerAccounts.forthright.lotUnavailable")
            chunks.append(chunk)
    return _parse(b"".join(chunks))


def board_lot_rule(symbol):
    from app.utils.db import get_db_connection
    code = equity_symbol(symbol, "HKStock").removesuffix(".HK")
    if not re.fullmatch(r"\d{1,5}", code):
        raise ValueError("brokerAccounts.forthright.lotUnavailable")
    code = code.zfill(5)
    try:
        with get_db_connection() as db:
            cur = db.cursor()
            cur.execute("""SELECT board_lot, rules_as_of, rules_source FROM qd_market_symbols
                WHERE market = 'HKStock' AND symbol = ? AND exchange = 'HKEX'
                  AND market_type = 'spot' AND instrument_id = '' AND currency = 'HKD' AND is_active = 1""", (code,))
            row = cur.fetchone()
            cur.close()
        lot = int((row or {}).get("board_lot") or 0)
        as_of = datetime.strptime(str((row or {}).get("rules_as_of")), "%Y-%m-%d").date()
        if lot <= 0 or not _today() - timedelta(days=7) <= as_of <= _today():
            raise ValueError("Invalid board-lot metadata")
    except Exception as exc:
        raise ValueError("brokerAccounts.forthright.lotUnavailable") from exc
    return {"symbol": code + ".HK", "market": "HKStock", "currency": "HKD",
            "lot_size": lot, "quantity_step": lot, "minimum_quantity": lot,
            "odd_lots": False, "source": row["rules_source"], "as_of": as_of.isoformat()}


def validate_board_lot(symbol, quantity):
    lot = board_lot_rule(symbol)["lot_size"]
    value = Decimal(str(quantity))
    if not value.is_finite() or value <= 0 or value % lot:
        raise ValueError("brokerAccounts.forthright.wholeBoardLot")
