"""Official macro time-series providers for research agents."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Iterable, List, Optional

import requests

from app.config.data_sources import BEAConfig, BLSConfig, FredConfig
from app.utils.logger import get_logger

logger = get_logger(__name__)


class MacroSeriesProvider:
    """Small typed client for stable US macro sources."""

    def source_status(self) -> List[Dict[str, Any]]:
        return [
            {
                "provider": "FRED",
                "configured": FredConfig.CONFIGURED,
                "available": FredConfig.CONFIGURED,
                "purpose": "US macro time series: rates, inflation, labor, financial conditions.",
            },
            {
                "provider": "BLS",
                "configured": bool(BLSConfig.API_KEY),
                "available": True,
                "purpose": "Official CPI, employment, wages, and labor market series.",
                "note": "A registration key is optional but recommended for higher limits.",
            },
            {
                "provider": "BEA",
                "configured": BEAConfig.CONFIGURED,
                "available": BEAConfig.CONFIGURED,
                "purpose": "Official GDP, income, consumption, and national accounts data.",
            },
        ]

    def fetch_fred_series(
        self,
        series_id: str,
        start: Optional[date | str] = None,
        end: Optional[date | str] = None,
        limit: int = 120,
    ) -> Dict[str, Any]:
        if not FredConfig.API_KEY:
            raise ValueError("FRED_API_KEY is not configured")

        params: Dict[str, Any] = {
            "series_id": series_id,
            "api_key": FredConfig.API_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": max(1, min(int(limit), 1000)),
        }
        if start:
            params["observation_start"] = str(start)
        if end:
            params["observation_end"] = str(end)

        response = requests.get(
            f"{FredConfig.BASE_URL}/series/observations",
            params=params,
            timeout=FredConfig.TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        return {
            "provider": "FRED",
            "series_id": series_id,
            "observations": data.get("observations", []),
        }

    def fetch_bls_series(
        self,
        series_ids: Iterable[str],
        start_year: int,
        end_year: int,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "seriesid": list(series_ids),
            "startyear": str(start_year),
            "endyear": str(end_year),
        }
        if BLSConfig.API_KEY:
            payload["registrationkey"] = BLSConfig.API_KEY

        response = requests.post(
            f"{BLSConfig.BASE_URL}/timeseries/data/",
            json=payload,
            timeout=BLSConfig.TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        return {
            "provider": "BLS",
            "series": data.get("Results", {}).get("series", []),
            "status": data.get("status"),
            "messages": data.get("message", []),
        }

    def fetch_bea_data(self, dataset: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not BEAConfig.API_KEY:
            raise ValueError("BEA_API_KEY is not configured")

        request_params: Dict[str, Any] = {
            "UserID": BEAConfig.API_KEY,
            "method": "GetData",
            "DataSetName": dataset,
            "ResultFormat": "JSON",
        }
        if params:
            request_params.update(params)

        response = requests.get(BEAConfig.BASE_URL, params=request_params, timeout=BEAConfig.TIMEOUT)
        response.raise_for_status()
        return {
            "provider": "BEA",
            "dataset": dataset,
            "data": response.json(),
        }


_macro_series_provider: Optional[MacroSeriesProvider] = None


def get_macro_series_provider() -> MacroSeriesProvider:
    global _macro_series_provider
    if _macro_series_provider is None:
        _macro_series_provider = MacroSeriesProvider()
    return _macro_series_provider
