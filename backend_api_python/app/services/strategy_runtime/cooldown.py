"""Recognize temporary exchange throttling without weakening fatal-stop policy."""

import math
import re

from app.data_sources.errors import MarketDataUnavailableError
from app.utils.exchange_request_budget import ExchangeRateLimitError


def exchange_cooldown_seconds(error) -> float:
    if isinstance(error, ExchangeRateLimitError):
        seconds = float(error.retry_after)
        return max(1.0, seconds) if math.isfinite(seconds) else 60.0
    if isinstance(error, MarketDataUnavailableError):
        if error.failure.code != "rate_limited":
            return 0.0
        detail = error.failure.technical_detail
    else:
        detail = str(error or "")
        if not re.search(r"marketData\.rate_limited|rate limit|ratelimit|HTTP[ :]+(?:418|429)\b|too many requests", detail, re.I):
            return 0.0
    match = re.search(r"retry after\s+(\d+(?:\.\d+)?)\s*s", detail, re.I)
    return max(1.0, float(match.group(1))) if match else 60.0
