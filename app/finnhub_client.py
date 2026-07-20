"""
Fetches upcoming economic calendar events from Finnhub.
"""
from __future__ import annotations
import os
import requests
from datetime import datetime, timedelta, timezone

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY")
BASE_URL = "https://finnhub.io/api/v1"

# Only these countries matter for GBPUSD -- keeps the panel focused
RELEVANT_COUNTRIES = {"US", "GB", "UK"}


def fetch_calendar(days_ahead: int = 10, days_behind: int = 1) -> list[dict]:
    """
    Returns upcoming (and a little recent) economic events relevant to GBPUSD,
    sorted by time ascending. Each item:
    {time, country, event, impact, actual, estimate, prev}
    """
    if not FINNHUB_API_KEY:
        raise RuntimeError("FINNHUB_API_KEY is not set")

    today = datetime.now(timezone.utc).date()
    frm = (today - timedelta(days=days_behind)).isoformat()
    to = (today + timedelta(days=days_ahead)).isoformat()

    resp = requests.get(
        f"{BASE_URL}/calendar/economic",
        params={"token": FINNHUB_API_KEY, "from": frm, "to": to},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()

    raw_events = data.get("economicCalendar") or data.get("calendar") or []
    events = []
    for e in raw_events:
        country = (e.get("country") or "").upper()
        if country not in RELEVANT_COUNTRIES:
            continue
        events.append(
            {
                "time": e.get("time"),
                "country": country,
                "event": e.get("event"),
                "impact": (e.get("impact") or "").lower() or "low",
                "actual": e.get("actual"),
                "estimate": e.get("estimate"),
                "prev": e.get("prev"),
            }
        )

    events.sort(key=lambda x: x["time"] or "")
    return events
