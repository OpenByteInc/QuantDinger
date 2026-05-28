"""Admin system-strategies exchange column resolution."""

from app.routes.user import _strategy_exchange_display_name


def test_inline_exchange_id():
    assert _strategy_exchange_display_name(
        {'exchange_id': 'binance'},
        credential_map={},
    ) == 'binance'


def test_credential_id_uses_credential_map():
    assert _strategy_exchange_display_name(
        {'credential_id': 42},
        credential_map={42: {'id': 42, 'exchange_id': 'okx', 'name': 'Main OKX'}},
    ) == 'okx'


def test_credential_id_without_map_returns_empty():
    assert _strategy_exchange_display_name(
        {'credential_id': 99},
        credential_map={},
        user_id=1,
    ) == ''


def test_legacy_exchange_key():
    assert _strategy_exchange_display_name(
        {'exchange': 'gate'},
        credential_map={},
    ) == 'gate'
