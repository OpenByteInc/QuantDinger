"""Numeric validation at broker order entry and submission boundaries."""

import math


class BrokerOrderValidationError(ValueError):
    def __init__(self):
        super().__init__("brokerAccounts.invalidOrder")


def order_number(value, *, allow_zero=False):
    """Reject invalid sizing inputs instead of forwarding them to a broker."""
    if isinstance(value, bool):
        raise BrokerOrderValidationError()
    if allow_zero and (value is None or value == ""):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BrokerOrderValidationError() from exc
    if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero):
        raise BrokerOrderValidationError()
    return number
