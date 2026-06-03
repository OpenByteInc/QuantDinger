from app.data_sources.forex import normalize_forex_pair_symbol


def test_xau_alias_normalizes_to_gold_pair():
    assert normalize_forex_pair_symbol("XAU") == "XAUUSD"
    assert normalize_forex_pair_symbol("XAU/USD") == "XAUUSD"
