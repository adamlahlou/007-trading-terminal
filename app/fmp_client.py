"""
Fetches upcoming economic calendar events from Financial Modeling Prep (FMP).
Finnhub's equivalent endpoint turned out to require a paid plan (403 on the
free tier) -- FMP has a genuine free tier that includes this endpoint.
"""
from __future__ import annotations
import os
import requests
from datetime import datetime, timedelta, timezone

FMP_API_KEY = os.environ.get("FMP_API_KEY")
BASE_URL = "https://financialmodelingprep.com/api/v3"

# Country strings FMP uses for the US/UK -- matched loosely since their
# exact format has varied between "US"/"USA" and "GB"/"UK" over time.
US_ALIASES = {"US", "USA", "UNITED STATES"}
GB_ALIASES = {"GB", "UK", "UNITED KINGDOM"}


def _normalize_country(raw: str) -> str | None:
    c = (raw or "").strip().upper()
    if c in US_ALIASES:
        return "US"
    if c in GB_ALIASES:
        return "GB"
    return None


def _normalize_impact(raw: str) -> str:
    c = (raw or "").strip().lower()
    if c in ("high", "medium", "low"):
        return c
    return "low"


def fetch_calendar(days_ahead: int = 10, days_behind: int = 1) -> list[dict]:
    """
    Returns upcoming (and a little recent) economic events relevant to GBPUSD,
    sorted by time ascending. Each item:
    {time, country, event, impact, actual, estimate, prev}
    """
    if not FMP_API_KEY:
        raise RuntimeError("FMP_API_KEY is not set")

    today = datetime.now(timezone.utc).date()
    frm = (today - timedelta(days=days_behind)).isoformat()
    to = (today + timedelta(days=days_ahead)).isoformat()

    resp = requests.get(
        f"{BASE_URL}/economic_calendar",
        params={"from": frm, "to": to, "apikey": FMP_API_KEY},
        timeout=20,
    )
    resp.raise_for_status()
    raw_events = resp.json()
    if not isinstance(raw_events, list):
        raise RuntimeError(f"Unexpected response shape from FMP: {raw_events}")

    events = []
    for e in raw_events:
        country = _normalize_country(e.get("country"))
        if country is None:
            continue
        events.append(
            {
                "time": e.get("date"),
                "country": country,
                "event": e.get("event"),
                "impact": _normalize_impact(e.get("impact")),
                "actual": e.get("actual"),
                "estimate": e.get("estimate"),
                "prev": e.get("previous"),
            }
        )

    events.sort(key=lambda x: x["time"] or "")
    return events
