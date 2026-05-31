from uuid import UUID
from unittest.mock import MagicMock, patch

from app.services.alpaca_trading.client import AlpacaClient, AlpacaConfig, _as_str_id, _id_log_prefix


def test_as_str_id_from_uuid():
    uid = UUID("12345678-1234-5678-1234-567812345678")
    assert _as_str_id(uid) == "12345678-1234-5678-1234-567812345678"
    assert _id_log_prefix(uid) == "12345678-123"


@patch("app.services.alpaca_trading.client._ensure_alpaca")
def test_alpaca_connect_stores_string_account_id(mock_ensure):
    mock_modules = {
        "TradingClient": MagicMock(),
        "StockHistoricalDataClient": MagicMock(),
        "CryptoHistoricalDataClient": MagicMock(),
    }
    mock_ensure.return_value = mock_modules

    account = MagicMock()
    account.id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    account.status = "ACTIVE"

    trading = MagicMock()
    trading.get_account.return_value = account
    mock_modules["TradingClient"].return_value = trading

    client = AlpacaClient(
        AlpacaConfig(api_key="PKtest", secret_key="secret", paper=True)
    )
    assert client.connect() is True
    assert client._account_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert isinstance(client._account_id, str)
