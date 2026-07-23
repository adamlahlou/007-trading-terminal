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

US_CPI_SERIES = "CPIAUCSL"           # US CPI, All Urban Consumers, monthly index
US_NFP_SERIES = "PAYEMS"             # US Total Nonfarm Payrolls, thousands of persons, monthly


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


def _fetch_recent(series_id: str, limit: int = 14) -> list[tuple[float, str]]:
    """Returns up to `limit` most recent (value, date) pairs, most recent first."""
    if not FRED_API_KEY:
        raise RuntimeError("FRED_API_KEY is not set")

    resp = requests.get(
        BASE_URL,
        params={
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    out = []
    for obs in data.get("observations", []):
        if obs.get("value") not in (None, ".", ""):
            out.append((float(obs["value"]), obs["date"]))
    return out


def _fetch_range(series_id: str, start_date: str, end_date: str) -> list[tuple[float, str]]:
    """Returns [(value, date)] ascending, for the given date range (YYYY-MM-DD).
    Used for backtesting -- reconstructing what the series actually said at
    each historical point, not just the latest value."""
    if not FRED_API_KEY:
        raise RuntimeError("FRED_API_KEY is not set")

    resp = requests.get(
        BASE_URL,
        params={
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "sort_order": "asc",
            "observation_start": start_date,
            "observation_end": end_date,
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    out = []
    for obs in data.get("observations", []):
        if obs.get("value") not in (None, ".", ""):
            out.append((float(obs["value"]), obs["date"]))
    return out


def fetch_yield_history(start_date: str, end_date: str) -> dict:
    """Returns {us: [(value,date)], uk: [(value,date)]} for reconstructing
    the yield spread at any point in the range."""
    return {
        "us": _fetch_range(US_10Y_SERIES, start_date, end_date),
        "uk": _fetch_range(UK_10Y_SERIES, start_date, end_date),
    }


def fetch_momentum_history(start_date: str, end_date: str) -> dict:
    """Returns {cpi: [(value,date)], nfp: [(value,date)]} -- callers compute
    YoY CPI / month-over-month NFP change themselves per point in time."""
    return {
        "cpi": _fetch_range(US_CPI_SERIES, start_date, end_date),
        "nfp": _fetch_range(US_NFP_SERIES, start_date, end_date),
    }


def fetch_data_momentum() -> dict:
    """
    Returns {cpi_yoy, cpi_date, nfp_change, nfp_date, gauge_score} -- US
    inflation trend (year-over-year % change) and the latest month's
    payrolls change (in thousands). No forecast/consensus available for
    free, so this reflects trend direction/level, not "beat vs expected".

    gauge_score follows the same convention as the other gauges: positive
    = bullish for GBPUSD, negative = bearish. Since this is a US-side (not
    GBP-side) indicator, HOT US data (strong jobs, above-target inflation)
    is USD-supportive, which is GBPUSD-*bearish* -- so hot data produces a
    negative gauge_score here, the opposite of a naive "big number = good".
    """
    cpi_obs = _fetch_recent(US_CPI_SERIES, limit=14)  # need ~13 months for YoY
    nfp_obs = _fetch_recent(US_NFP_SERIES, limit=2)

    if len(cpi_obs) < 13:
        raise RuntimeError("Not enough CPI history returned from FRED to compute YoY")
    if len(nfp_obs) < 2:
        raise RuntimeError("Not enough NFP history returned from FRED to compute change")

    latest_cpi, cpi_date = cpi_obs[0]
    year_ago_cpi, _ = cpi_obs[12]
    cpi_yoy = round((latest_cpi - year_ago_cpi) / year_ago_cpi * 100, 2)

    latest_nfp, nfp_date = nfp_obs[0]
    prev_nfp, _ = nfp_obs[1]
    nfp_change = round(latest_nfp - prev_nfp, 1)  # thousands of jobs

    # Simple, transparent heuristic normalization (documented, not a black box):
    # NFP: +-300k/month spans the typical range -> -1..1
    # CPI: deviation from the Fed's ~2% target, +-3pp spans typical range -> -1..1
    norm_nfp = max(-1.0, min(1.0, nfp_change / 300.0))
    norm_cpi = max(-1.0, min(1.0, (cpi_yoy - 2.0) / 3.0))
    hot_data_score = round((norm_nfp + norm_cpi) / 2, 4)
    gauge_score = round(-hot_data_score, 4)  # flip: hot US data -> GBPUSD-bearish

    return {
        "cpi_yoy": cpi_yoy,
        "cpi_date": cpi_date,
        "nfp_change": nfp_change,
        "nfp_date": nfp_date,
        "gauge_score": gauge_score,
    }
