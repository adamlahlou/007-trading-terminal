"""
Fetches recent GBP/USD-relevant news from Marketaux and aggregates the
per-entity sentiment scores it provides into a single gauge value, while
keeping the actual headlines so the number is never a mystery.
"""
from __future__ import annotations
import os
import requests

MARKETAUX_API_KEY = os.environ.get("MARKETAUX_API_KEY")
BASE_URL = "https://api.marketaux.com/v1/news/all"

# Kept broad on purpose -- GBP-specific plus USD/macro-relevant terms, since
# this is one combined gauge for now (see notes on splitting into two later).
DEFAULT_QUERY = "GBP OR sterling OR pound OR Bank of England OR Federal Reserve OR US dollar OR interest rates"


def fetch_news_sentiment(query: str = DEFAULT_QUERY, limit: int = 15) -> dict:
    """
    Returns {score, article_count, headlines: [{title, url, published_at, sentiment}]}.
    score is the average entity sentiment across all fetched articles, -1..1.
    """
    if not MARKETAUX_API_KEY:
        raise RuntimeError("MARKETAUX_API_KEY is not set")

    resp = requests.get(
        BASE_URL,
        params={
            "api_token": MARKETAUX_API_KEY,
            "search": query,
            "language": "en",
            "limit": limit,
            "sort": "published_desc",
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    articles = data.get("data", [])

    all_scores = []
    headlines = []
    for a in articles:
        entities = a.get("entities") or []
        scores = [e["sentiment_score"] for e in entities if e.get("sentiment_score") is not None]
        article_avg = sum(scores) / len(scores) if scores else None
        if article_avg is not None:
            all_scores.append(article_avg)
        headlines.append(
            {
                "title": a.get("title"),
                "url": a.get("url"),
                "published_at": a.get("published_at"),
                "sentiment": round(article_avg, 3) if article_avg is not None else None,
            }
        )

    overall_score = round(sum(all_scores) / len(all_scores), 3) if all_scores else 0.0
    return {
        "score": overall_score,
        "article_count": len(articles),
        "headlines": headlines[:8],  # keep the payload small
    }
