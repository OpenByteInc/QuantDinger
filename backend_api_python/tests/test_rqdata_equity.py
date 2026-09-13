from app.data_sources.rqdata_equity import (
    is_cn_index_code,
    to_rqdata_cn_equity_id,
    to_rqdata_hk_equity_id,
)


def test_cn_stock_ids():
    assert to_rqdata_cn_equity_id("600519") == "600519.XSHG"
    assert to_rqdata_cn_equity_id("000001") == "000001.XSHE"
    assert to_rqdata_cn_equity_id("000001.SH") == "000001.XSHG"
    assert to_rqdata_cn_equity_id("300750") == "300750.XSHE"
    assert to_rqdata_cn_equity_id("000300.SH") == "000300.XSHG"
    assert to_rqdata_cn_equity_id("399006.SZ") == "399006.XSHE"
    assert to_rqdata_cn_equity_id("600519.XSHG") == "600519.XSHG"


def test_index_adjust_flag():
    assert is_cn_index_code("SH000001")
    assert is_cn_index_code("SH000300")
    assert is_cn_index_code("SZ399006")
    assert not is_cn_index_code("SZ000001")
    assert not is_cn_index_code("SH600519")


def test_hk_ids():
    assert to_rqdata_hk_equity_id("0700.HK") == "00700.XHKG"
    assert to_rqdata_hk_equity_id("700") == "00700.XHKG"
    assert to_rqdata_hk_equity_id("00700.XHKG") == "00700.XHKG"
