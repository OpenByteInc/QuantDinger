import pytest
from pathlib import Path

from app.services import user_service
from app.services.market import assets, symbol_search, watchlist


class _Cursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.executed = []
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def close(self):
        pass


class _Connection:
    def __init__(self, rows=None):
        self.cursor_obj = _Cursor(rows)
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True


def test_find_market_symbol_requires_exact_external_match(monkeypatch):
    monkeypatch.setattr(symbol_search, "seed_search_symbols", lambda **kwargs: [])
    monkeypatch.setattr(
        symbol_search,
        "_search_external_symbols",
        lambda market, keyword, limit, existing: [
            {"market": "USStock", "symbol": "AAP", "name": "Advance Auto Parts"}
        ],
    )

    assert symbol_search.find_market_symbol("USStock", "AAPL") is None


def test_find_market_symbol_accepts_exact_external_match(monkeypatch):
    monkeypatch.setattr(symbol_search, "seed_search_symbols", lambda **kwargs: [])
    monkeypatch.setattr(
        symbol_search,
        "_search_external_symbols",
        lambda market, keyword, limit, existing: [
            {"market": "USStock", "symbol": "AAPL", "name": "Apple Inc."}
        ],
    )

    assert symbol_search.find_market_symbol("USStock", "AAPL") == {
        "market": "USStock",
        "symbol": "AAPL",
        "name": "Apple Inc.",
    }


def test_watchlist_persists_only_a_logical_asset(monkeypatch):
    conn = _Connection()
    monkeypatch.setattr(
        watchlist,
        "get_market_asset",
        lambda **kwargs: {
            "asset_id": 41,
            "market": "Crypto",
            "symbol": "BTC/USDT",
            "product_type": "crypto",
        },
    )
    monkeypatch.setattr(watchlist, "get_db_connection", lambda: conn)

    ok, message = watchlist.add_watchlist_item(7, asset_id=41)

    assert ok is True
    assert message == "success"
    assert conn.committed is True
    sql, params = conn.cursor_obj.executed[0]
    assert "INSERT INTO qd_watchlist (user_id, asset_id" in " ".join(sql.split())
    assert "exchange_id" not in sql
    assert "market_type" not in sql
    assert params == (7, 41)


def test_watchlist_requires_a_catalog_asset_id():
    ok, message = watchlist.add_watchlist_item(7)

    assert ok is False
    assert message == "market.assetIdRequired"


def test_default_watchlist_seed_resolves_catalog_assets(monkeypatch):
    conn = _Connection()
    monkeypatch.setattr(
        user_service,
        "_DEFAULT_WATCHLIST",
        [("Crypto", "BTC/USDT", "Bitcoin")],
    )

    user_service._seed_default_watchlist(conn, 9)

    assert conn.committed is True
    sql, params = conn.cursor_obj.executed[0]
    assert "FROM qd_market_assets" in sql
    assert "ON CONFLICT (user_id, asset_id) DO NOTHING" in " ".join(sql.split())
    assert params == (9, "Crypto", "BTC/USDT")


def test_runtime_resolution_uses_account_exchange_and_requested_product(monkeypatch):
    conn = _Connection(rows=[{
        "asset_id": 41,
        "market": "Crypto",
        "symbol": "BTC/USDT",
        "exchange_id": "OKX",
        "market_type": "swap",
        "instrument_id": "BTC-USDT-SWAP",
        "settle_currency": "USDT",
        "product_type": "crypto",
        "api_family": "swap",
        "product_meta": {},
    }])
    monkeypatch.setattr(
        assets,
        "get_market_asset",
        lambda **kwargs: {"asset_id": 41, "market": "Crypto", "symbol": "BTC/USDT"},
    )
    monkeypatch.setattr(assets, "get_db_connection", lambda: conn)

    product = assets.resolve_runtime_instrument(
        asset_id=41,
        exchange_id="okx",
        market_type="swap",
    )

    assert product["exchange_id"] == "okx"
    assert product["market_type"] == "swap"
    assert product["instrument_id"] == "BTC-USDT-SWAP"
    _, params = conn.cursor_obj.executed[0]
    assert params == (41, "okx", "swap")


def test_runtime_resolution_fails_closed_when_account_has_no_exact_product(monkeypatch):
    monkeypatch.setattr(
        assets,
        "get_market_asset",
        lambda **kwargs: {"asset_id": 12, "market": "Crypto", "symbol": "QQQ/USDT"},
    )
    monkeypatch.setattr(assets, "get_db_connection", lambda: _Connection())

    with pytest.raises(assets.AssetResolutionError, match="instrumentUnavailableForAccount"):
        assets.resolve_runtime_instrument(
            asset_id=12,
            exchange_id="binance",
            market_type="swap",
        )


def test_quick_trade_contract_uses_account_venue_not_chart_product_identity():
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "quick_trade"
        / "products.py"
    ).read_text(encoding="utf-8")

    assert 'account_overrides.pop("exchange_id", None)' in source
    assert 'exchange_id = str(config.get("exchange_id")' in source
    assert 'for key in ("instrument_id", "product_type", "api_family")' not in source


def test_strategy_deployment_resolves_manifest_against_selected_account():
    source = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "strategy_v2"
        / "deployment.py"
    ).read_text(encoding="utf-8")
    method = source[source.index("    def _validate_manifest_products("):]
    method = method[:method.index("    def _resolve_live_instruments(")]

    assert "exchange_id=exchange_id" in method
    assert '"exchange_id": exchange_id' in method
    assert method.count('item.get("exchange_id")') == 1
