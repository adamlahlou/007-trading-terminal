"""
Fetches the latest US and UK 10-year government bond yields from FRED
(Federal Reserve Economic Data) -- a genuinely free, official government
data source, no paid tier gating like the calendar vendors we hit earlier.
"""
from __future__ import annotations
import os
import requests

FRED_API_KEY = os.environ.get("FRED_API_KEY")
BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

US_10Y_SERIES = "DGS10"              # US 10-Year Treasury, daily
UK_10Y_SERIES = "IRLTLT01GBM156N"    # UK 10-Year Gilt (OECD via FRED), monthly


def _fetch_latest(series_id: str) -> tuple[float, str] | None:
    """Returns (value, date) for the most recent non-missing observation,
    or None if the series has no usable recent data."""
    if not FRED_API_KEY:
        raise RuntimeError("FRED_API_KEY is not set")

    resp = requests.get(
        BASE_URL,
        params={
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 5,  # a few, in case the most recent is a "." (missing)
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    for obs in data.get("observations", []):
        if obs.get("value") not in (None, ".", ""):
            return float(obs["value"]), obs["date"]
    return None


def fetch_yield_differential() -> dict:
    """
    Returns {us_yield, us_date, uk_yield, uk_date, spread}.
    spread = uk_yield - us_yield (positive = UK yields higher, generally
    GBP-supportive; negative = US yields higher, generally GBP-negative).
    """
    us = _fetch_latest(US_10Y_SERIES)
    uk = _fetch_latest(UK_10Y_SERIES)
    if us is None or uk is None:
        raise RuntimeError("Could not fetch one or both yield series from FRED")

    us_yield, us_date = us
    uk_yield, uk_date = uk
    return {
        "us_yield": us_yield,
        "us_date": us_date,
        "uk_yield": uk_yield,
        "uk_date": uk_date,
        "spread": round(uk_yield - us_yield, 4),
    }
