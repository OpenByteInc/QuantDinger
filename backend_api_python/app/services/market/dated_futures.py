"""Exact dated futures reference feeds; never substitute continuous contracts."""

import json
import os
import re


_YAHOO_EXCHANGES = {
    "CME": {"MES", "MNQ", "M2K", "ES", "NQ", "RTY", "6A", "6B", "6C", "6E", "6J", "6N", "6S", "GF", "HE", "LE", "MBT"},
    "CBT": {"YM", "MYM", "ZC", "ZW", "ZS", "ZL", "ZM", "ZB", "ZN", "ZF", "ZT"},
    "CMX": {"GC", "MGC", "SI", "SIL", "HG", "QC"},
    "NYM": {"CL", "MCL", "QM", "NG", "QG", "HO", "RB", "PL", "PA"},
}


def default_dated_symbol(symbol: str):
    if not isinstance(symbol, str) or not 4 <= len(symbol) <= 11:
        return None
    match = re.fullmatch(r"([A-Z0-9]{1,8})[FGHJKMNQUVXZ][0-9]{2}", symbol)
    if match:
        for exchange, roots in _YAHOO_EXCHANGES.items():
            if match[1] in roots:
                return symbol + "." + exchange
    return None


def dated_future_feed(symbol: str, *, required=False, allow_default=False):
    try:
        specs = json.loads(os.getenv("FORTHRIGHT_FUTURES_CONTRACT_SPECS", "{}"))
        spec = specs.get(symbol, {})
        provider, feed_symbol = spec.get("data_provider"), spec.get("data_symbol", "")
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("brokerAccounts.forthright.contractMetadataRequired") from exc
    if not provider and not feed_symbol:
        default_symbol = default_dated_symbol(symbol) if allow_default else None
        if default_symbol:
            return "yfinance", default_symbol
        if not required:
            return None
    if (provider not in {"twelvedata", "yfinance"}
            or not isinstance(feed_symbol, str)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", feed_symbol)
            or not re.search(r"\d", feed_symbol)):
        raise ValueError("brokerAccounts.forthright.contractMetadataRequired")
    return provider, feed_symbol


def is_dated_future(symbol: str) -> bool:
    return bool(re.fullmatch(r"[A-Z0-9]{1,8}(?:[FGHJKMNQUVXZ]\d{1,4}|\d{4,8})", symbol))


def future_display_metadata(symbol: str):
    try:
        feed = dated_future_feed(symbol, required=True, allow_default=True)
    except ValueError:
        feed = None
    micro_index = bool(re.fullmatch(r"(?:MES|MNQ)[HMUZ]\d{2}", symbol))
    return {"data_provider": feed[0] if feed else None, "data_symbol": feed[1] if feed else None,
            "quote_currency": "USD" if micro_index else None,
            "price_tick": 0.25 if micro_index else None,
            "quote_unit": "index_points" if micro_index else None}


def contract_catalog_rows(contracts):
    """Build public contract identities, without account or trade permissions."""
    from datetime import datetime, timezone
    from app.services.symbol_master_sync import SymbolMasterRow
    today = datetime.now(timezone.utc).strftime('%Y%m%d')
    rows = []
    for contract in contracts:
        symbol = str(contract.get('prodCode') or '')
        expiry = str(contract.get('expiryYmd') or '')
        last_trade = str(contract.get('lastTradeDate') or '').replace('-', '')
        if not is_dated_future(symbol) or not re.fullmatch(r'\d{8}', expiry) or expiry < today or (last_trade and last_trade < today):
            continue
        metadata = future_display_metadata(symbol)
        rows.append(SymbolMasterRow(
            market='Futures', symbol=symbol, name=str(contract.get('prodName') or symbol),
            exchange='forthright', currency=metadata['quote_currency'] or '',
            instrument_id=symbol, asset_class='futures', product_type='futures',
            api_family='futures', product_meta={
                'quantity_step': 1, **metadata,
                'exchange': str(contract.get('marketCode') or ''),
                'expiry': expiry, 'last_trade_date': last_trade,
                **({'root': str(contract['instmntCode'])} if contract.get('instmntCode') else {}),
            },
        ))
    return rows


def register_contract_catalog(contracts):
    from app.services.symbol_master_sync import upsert_symbol_master
    return upsert_symbol_master(contract_catalog_rows(contracts))


def preset_contracts():
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / 'data' / 'forthright_futures_catalog.json'
    return json.loads(path.read_text(encoding='utf-8'))['contracts']


def futures_products():
    products = {}
    for row in preset_contracts():
        root = row.get('instmntCode')
        if root:
            products[root] = {'symbol': root, 'name': re.sub(r'\s+\d{4}-\d{2}$', '', row.get('prodName') or root), 'exchange': row.get('marketCode')}
    return sorted(products.values(), key=lambda item: item['symbol'])
