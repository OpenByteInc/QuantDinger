from decimal import Decimal
from unittest.mock import Mock

import pytest
from flask import g

from app.services import community_service
from app.routes import community as community_route
from app.routes import ibkr as ibkr_route
from app.services.community_service import (
    CommunityService,
    _credit_marketplace_seller,
    _debit_marketplace_buyer,
    _lock_marketplace_accounts,
)
from app.services.ibkr_trading import client as ibkr_client_module
from app.services.ibkr_trading.client import IBKRClient
from app.services.live_trading import factory
from app.utils import local_brokers


def _marketplace_detail_row(**overrides):
    row = {
        "id": 41,
        "name": "Pending indicator",
        "description": "Review me",
        "pricing_type": "free",
        "price": 0,
        "vip_free": False,
        "preview_image": "",
        "purchase_count": 0,
        "avg_rating": 0,
        "rating_count": 0,
        "view_count": 0,
        "publish_to_community": True,
        "created_at": None,
        "updated_at": None,
        "user_id": 7,
        "review_status": "pending",
        "is_encrypted": 0,
        "asset_type": "indicator",
        "source_script_source_id": None,
        "source_strategy_id": None,
        "source_language": "en-US",
        "name_i18n": None,
        "description_i18n": None,
        "marketplace_contract": None,
        "marketplace_binding_mode": "",
        "marketplace_strategy_type": "",
        "marketplace_direction_mode": "",
        "marketplace_execution_mode": "bar",
        "marketplace_execution_frequency": "",
        "marketplace_confirmation_frequencies": "",
        "source_description": "",
        "author_id": 7,
        "author_username": "author",
        "author_nickname": "Author",
        "author_avatar": "",
    }
    row.update(overrides)
    return row


class _Cursor:
    def __init__(self, rows=None, row=None):
        self.rows = list(rows or [])
        self.row = row
        self.calls = []

    def execute(self, query, params=None):
        self.calls.append((" ".join(query.split()), params))

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.row

    def close(self):
        return None


def test_pending_marketplace_detail_is_visible_to_admin_only(monkeypatch):
    detail_row = _marketplace_detail_row()

    class Cursor:
        def __init__(self):
            self.row = None

        def execute(self, query, params=None):
            sql = " ".join(query.split())
            if "FROM qd_indicator_codes i" in sql:
                self.row = dict(detail_row)
            else:
                self.row = None

        def fetchone(self):
            return self.row

        def close(self):
            return None

    class Database:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

        def commit(self):
            return None

    monkeypatch.setattr(community_service, "get_db_connection", lambda: Database())
    service = CommunityService.__new__(CommunityService)

    assert service.get_indicator_detail(41, user_id=99, is_admin=False) is None
    detail = service.get_indicator_detail(41, user_id=99, is_admin=True)

    assert detail["id"] == 41
    assert detail["name"] == "Pending indicator"
    assert detail["is_own"] is False


def test_marketplace_detail_route_passes_verified_admin_role(app, monkeypatch):
    service = Mock()
    service.get_indicator_detail.return_value = {"id": 41}
    monkeypatch.setattr(community_route, "get_community_service", lambda: service)
    view = community_route.get_indicator_detail
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__

    with app.test_request_context("/api/community/indicators/41"):
        g.user_id = 99
        g.user_role = "admin"
        response = view(41)

    assert response.get_json()["data"] == {"id": 41}
    service.get_indicator_detail.assert_called_once_with(
        41,
        user_id=99,
        accept_language="en-US",
        is_admin=True,
    )


def test_marketplace_accounts_are_locked_in_stable_order():
    cur = _Cursor(rows=[{"id": 2, "credits": "5.00"}, {"id": 9, "credits": "100.00"}])

    balances = _lock_marketplace_accounts(cur, buyer_id=9, seller_id=2)

    query, params = cur.calls[0]
    assert params == (2, 9)
    assert "ORDER BY id FOR UPDATE" in query
    assert balances == {2: Decimal("5.00"), 9: Decimal("100.00")}


def test_marketplace_debit_is_relative_guarded_and_returns_actual_balance():
    cur = _Cursor(row={"credits": "70.00"})

    balance = _debit_marketplace_buyer(cur, buyer_id=9, amount=Decimal("30.00"))

    query, params = cur.calls[0]
    assert "credits = credits - ?" in query
    assert "credits >= ?" in query
    assert "RETURNING credits" in query
    assert params == (Decimal("30.00"), 9, Decimal("30.00"))
    assert balance == Decimal("70.00")


def test_marketplace_debit_reports_racing_insufficient_balance():
    cur = _Cursor(row=None)

    assert _debit_marketplace_buyer(cur, buyer_id=9, amount=Decimal("30.00")) is None


def test_marketplace_seller_credit_is_relative_and_returns_actual_balance():
    cur = _Cursor(row={"credits": "35.00"})

    balance = _credit_marketplace_seller(cur, seller_id=2, amount=Decimal("30.00"))

    query, params = cur.calls[0]
    assert "credits = credits + ?" in query
    assert "RETURNING credits" in query
    assert params == (Decimal("30.00"), 2)
    assert balance == Decimal("35.00")


def test_marketplace_purchase_uses_shared_transaction(monkeypatch):
    used = {"transaction": 0}

    class Transaction:
        def __enter__(self):
            used["transaction"] += 1
            raise RuntimeError("transaction sentinel")

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(community_service, "get_db_transaction", lambda: Transaction())
    service = CommunityService.__new__(CommunityService)

    success, message, data = service.purchase_indicator(9, 4)

    assert success is False
    assert message == "error: transaction sentinel"
    assert data == {}
    assert used["transaction"] == 1


def test_ibkr_client_policy_blocks_before_loading_sdk(monkeypatch):
    monkeypatch.setattr("app.utils.local_brokers.is_trading_venue_enabled", lambda _venue: False)
    ensure_sdk = Mock()
    monkeypatch.setattr(ibkr_client_module, "_ensure_ib_insync", ensure_sdk)

    assert IBKRClient().connect() is False
    ensure_sdk.assert_not_called()


def test_ibkr_policy_follows_supported_market_selection(monkeypatch):
    monkeypatch.setattr(
        "app.services.settings.env_file.read_env_file",
        lambda: {"ENABLED_TRADING_VENUES": "alpaca,binance,okx"},
    )

    assert local_brokers.local_desktop_brokers_allowed() is False

    monkeypatch.setattr(
        "app.services.settings.env_file.read_env_file",
        lambda: {"ENABLED_TRADING_VENUES": "alpaca,ibkr,binance"},
    )

    assert local_brokers.local_desktop_brokers_allowed() is True


def test_ibkr_existing_client_operations_obey_disabled_policy(monkeypatch):
    monkeypatch.setattr("app.utils.local_brokers.is_trading_venue_enabled", lambda _venue: False)
    client = IBKRClient()

    with pytest.raises(PermissionError):
        client._ensure_connected()


def test_ibkr_factory_policy_blocks_before_client_creation(monkeypatch):
    monkeypatch.setattr("app.utils.local_brokers.is_trading_venue_enabled", lambda _venue: False)
    client_class = Mock()
    monkeypatch.setattr(factory, "IBKRClient", client_class)

    with pytest.raises(PermissionError):
        factory.create_ibkr_client({"ibkr_host": "127.0.0.1"})

    client_class.assert_not_called()


def test_ibkr_connect_route_returns_forbidden_before_client_creation(app, monkeypatch):
    monkeypatch.setattr("app.utils.local_brokers.is_trading_venue_enabled", lambda _venue: False)
    client_class = Mock()
    monkeypatch.setattr(ibkr_route, "IBKRClient", client_class)
    view = ibkr_route.connect
    while hasattr(view, "__wrapped__"):
        view = view.__wrapped__

    with app.test_request_context("/api/ibkr/connect", method="POST", json={"host": "10.0.0.8"}):
        result = view()

    if isinstance(result, tuple):
        response, status = result
    else:
        response, status = result, result.status_code

    assert status == 403
    assert response.get_json()["success"] is False
    client_class.assert_not_called()
