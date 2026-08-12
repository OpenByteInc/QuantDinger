"""Optional integration tests against a local FutuOpenD simulate account.

Skipped unless FUTU_INTEGRATION=1 and OpenD is reachable.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration


def _enabled() -> bool:
    return str(os.getenv("FUTU_INTEGRATION") or "").strip().lower() in ("1", "true", "yes", "on")


@pytest.mark.skipif(not _enabled(), reason="Set FUTU_INTEGRATION=1 with a running FutuOpenD")
def test_futu_opend_probe_and_quote():
    from app.services.futu_trading import FutuClient, FutuConfig

    client = FutuClient(
        FutuConfig(
            host=os.getenv("FUTU_OPEND_HOST", "127.0.0.1"),
            port=int(os.getenv("FUTU_OPEND_PORT", "11111")),
            trade_env="demo",
            trade_market="HK",
        )
    )
    assert client.connect(), "FutuOpenD connect failed"
    try:
        status = client.get_connection_status()
        assert status.get("connected") is True
        probe = client.probe_permissions()
        assert probe.get("quote_ok") or probe.get("trade_ok")
        quote = client.get_quote("00700.HK", "HKStock")
        assert quote.get("success") is True
        assert float(quote.get("last") or 0) > 0
    finally:
        client.disconnect()
