"""
Fetches genuine world/geopolitical news via FreeNewsApi.io -- verified
before building: 5,000 free requests/day, 71 countries, real topic tagging
(confirmed a real "Iran war" article tagged ["politics", "world"] in their
own docs example). Chosen specifically because Marketaux (used for the
GBP/USD news gauge) is a finance-entity-focused API that's a poor fit for
pure war/conflict coverage -- it doesn't have a "financial entity" to hook
a war story onto, so real hard-news coverage was getting lost there.

No sentiment scores from this API (unlike Marketaux) -- severity judgment
here relies entirely on the LLM interpretation step in llm_client.py.
"""
from __future__ import annotations
import os
import requests
from datetime import datetime, timezone, timedelta

FREENEWS_API_KEY = os.environ.get("FREENEWS_API_KEY")
BASE_URL = "https://api.freenewsapi.io/v1/news"


def fetch_geopolitical_headlines(limit: int = 15, lookback_hours: int = 24) -> list[dict]:
    """Returns [{title, published_at, publisher}] for recent world/politics
    news. Filters by topic rather than keyword-matching -- more reliable
    than guessing which keywords catch genuine hard-news coverage."""
    if not FREENEWS_API_KEY:
        raise RuntimeError("FREENEWS_API_KEY is not set")

    published_after = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

    resp = requests.get(
        BASE_URL,
        headers={"x-api-key": FREENEWS_API_KEY},
        params={
            "language": "en",
            "topic": "world",
            "published_after": published_after,
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    articles = data.get("data", [])[:limit]

    return [
        {
            "title": a.get("title"),
            "published_at": a.get("published_at"),
            "publisher": a.get("publisher"),
        }
        for a in articles
        if a.get("title")
    ]
