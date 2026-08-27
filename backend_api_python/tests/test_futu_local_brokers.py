from app.utils.local_brokers import (
    LOCAL_DESKTOP_BROKERS,
    desktop_broker_cloud_reject_message,
    is_local_desktop_broker,
)


def test_futu_is_local_desktop_broker():
    assert "futu" in LOCAL_DESKTOP_BROKERS
    assert is_local_desktop_broker("futu")
    assert is_local_desktop_broker("FUTU")
    assert not is_local_desktop_broker("alpaca")


def test_futu_reject_message():
    msg = desktop_broker_cloud_reject_message("futu")
    assert "FutuOpenD" in msg
