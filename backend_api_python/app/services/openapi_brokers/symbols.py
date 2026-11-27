"""Equity identity helpers; never apply these to futures contracts or crypto pairs."""

import re


def equity_symbol(symbol, market=""):
    value = str(symbol or "").strip().upper()
    hint = str(market or "").strip().upper()
    for prefix, category in (("HKSTOCK:", "HKSTOCK"), ("USSTOCK:", "USSTOCK")):
        if value.startswith(prefix):
            if hint and hint != category:
                return ""
            hint, value = category, value[len(prefix):]
            break
    hk = re.fullmatch(r"(?:HK\.)?(\d{1,5})(?:\.HK)?", value)
    if hk and hint != "USSTOCK":
        return hk[1].zfill(5) + ".HK"
    if hint == "USSTOCK":
        value = value.removeprefix("US.").removesuffix(".US")
    return value


def equity_symbols_match(left, right, market=""):
    key = equity_symbol(left, market)
    return bool(key) and key == equity_symbol(right, market)
