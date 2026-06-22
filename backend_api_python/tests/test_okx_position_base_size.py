"""OKX SWAP position size must be contracts * ctVal, not raw contracts."""

from unittest.mock import MagicMock

from app.services.grid.fill_units import okx_swap_position_base_size
from app.services.live_trading.account_snapshot import _parse_okx_positions


def test_okx_swap_position_base_size_uses_ct_val_from_row():
    pos = {"instId": "BNB-USDT-SWAP", "posSide": "net", "pos": "51", "ctVal": "0.01"}
    assert okx_swap_position_base_size(pos) == 0.51


def test_okx_swap_position_base_size_fetches_instrument():
    client = MagicMock()
    client.get_instrument.return_value = {"ctVal": "0.01"}
    pos = {"instId": "BNB-USDT-SWAP", "posSide": "net", "pos": "51"}
    assert okx_swap_position_base_size(pos, client=client) == 0.51


def test_parse_okx_positions_converts_contracts():
    client = MagicMock()
    client.get_instrument.return_value = {"ctVal": "0.01"}
    rows = _parse_okx_positions(
        [{"instId": "BNB-USDT-SWAP", "posSide": "long", "pos": "51", "avgPx": "685.4"}],
        market_type="swap",
        client=client,
    )
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BNB/USDT"
    assert rows[0]["size"] == 0.51
