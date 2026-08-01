"""
Fetches genuine world/geopolitical AND finance news via FreeNewsApi.io --
verified before building: 5,000 free requests/day, 71 countries, confirmed
category list includes finance/business/economy/personal-finance.

Used for BOTH the geopolitical gauge (topic=world) and the GBP/USD news
gauge (topic=finance, GBP/USD classification done in our own code below
rather than trusting the vendor's search-string parsing -- Marketaux's
unclear OR-boolean handling caused a real production bug where the exact
query syntax we assumed didn't behave as expected. Classifying headlines
ourselves in plain Python removes that entire class of risk.

No sentiment scores from this API (unlike Marketaux) -- tone judgment
relies entirely on the LLM interpretation step in llm_client.py.
"""
from __future__ import annotations
import os
import requests
from datetime import datetime, timezone, timedelta

FREENEWS_API_KEY = os.environ.get("FREENEWS_API_KEY")
BASE_URL = "https://api.freenewsapi.io/v1/news"

GBP_KEYWORDS = ["gbp", "pound", "sterling", "bank of england", " boe ", "uk economy", "britain", "british"]
USD_KEYWORDS = ["usd", "dollar", "federal reserve", " fed ", "non-farm", "nonfarm", "nfp", "fomc", "us economy", "us inflation"]
OTHER_DOLLAR_CURRENCIES = ["canadian dollar", "australian dollar", "new zealand dollar", "nz dollar", "singapore dollar", "hong kong dollar", "taiwan dollar"]


def _fetch_headlines_range(topic: str, limit: int, published_after: str, published_before: str | None = None) -> list[dict]:
    """Low-level fetch with explicit published_after/published_before (both
    ISO strings) -- used for both live (computed from lookback_hours) and
    historical (explicit date range) fetching, so both paths share one
    tested code path rather than diverging."""
    if not FREENEWS_API_KEY:
        raise RuntimeError("FREENEWS_API_KEY is not set")

    params = {"language": "en", "topic": topic, "published_after": published_after}
    if published_before:
        params["published_before"] = published_before

    resp = requests.get(
        BASE_URL,
        headers={"x-api-key": FREENEWS_API_KEY},
        params=params,
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    articles = data.get("data", [])[:limit]

    return [
        {"title": a.get("title"), "published_at": a.get("published_at"), "publisher": a.get("publisher")}
        for a in articles
        if a.get("title")
    ]


def _fetch_headlines(topic: str, limit: int, lookback_hours: int) -> list[dict]:
    published_after = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return _fetch_headlines_range(topic, limit, published_after)


def _classify_gbp_usd(articles: list[dict]) -> dict:
    """Shared classification logic (used by both live and historical
    fetching) -- our own keyword matching, not a vendor search-string
    query, per the Marketaux lesson."""
    seen_titles = set()
    unique_articles = []
    for a in articles:
        if a["title"] not in seen_titles:
            seen_titles.add(a["title"])
            unique_articles.append(a)

    gbp_headlines, usd_headlines = [], []
    for a in unique_articles:
        title_lower = f" {a['title'].lower()} "
        mentions_other_dollar = any(od in title_lower for od in OTHER_DOLLAR_CURRENCIES)
        if any(kw in title_lower for kw in GBP_KEYWORDS):
            gbp_headlines.append(a)
        if any(kw in title_lower for kw in USD_KEYWORDS) and not mentions_other_dollar:
            usd_headlines.append(a)

    return {"gbp": gbp_headlines, "usd": usd_headlines}


def fetch_geopolitical_headlines(limit: int = 15, lookback_hours: int = 24) -> list[dict]:
    """Returns [{title, published_at, publisher}] for recent world/politics
    news. Filters by topic rather than keyword-matching -- more reliable
    than guessing which keywords catch genuine hard-news coverage."""
    return _fetch_headlines("world", limit, lookback_hours)


def fetch_gbp_usd_headlines(lookback_hours: int = 48) -> dict:
    """Returns {gbp: [headlines], usd: [headlines]} -- fetches recent
    finance/business/economy articles, then classifies each headline
    ourselves via simple keyword matching (not a vendor search-string
    query) so there's no ambiguity about how the classification works."""
    all_articles = []
    for topic in ("finance", "business", "economy"):
        all_articles.extend(_fetch_headlines(topic, limit=25, lookback_hours=lookback_hours))
    return _classify_gbp_usd(all_articles)


def fetch_geopolitical_headlines_range(published_after: str, published_before: str, limit: int = 15) -> list[dict]:
    """Historical version -- same topic filter, explicit date range instead
    of a lookback from now. Used for backtesting."""
    return _fetch_headlines_range("world", limit, published_after, published_before)


def fetch_gbp_usd_headlines_range(published_after: str, published_before: str) -> dict:
    """Historical version of fetch_gbp_usd_headlines -- explicit date range."""
    all_articles = []
    for topic in ("finance", "business", "economy"):
        all_articles.extend(_fetch_headlines_range(topic, limit=25, published_after=published_after, published_before=published_before))
    return _classify_gbp_usd(all_articles)
