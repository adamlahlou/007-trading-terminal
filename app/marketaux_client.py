"""
Fetches recent GBP-relevant and USD-relevant news from Marketaux separately,
then combines them into one properly GBPUSD-directional score -- blending
them into a single average (as this used to do) is actually wrong, since
positive USD news should point OPPOSITE to positive GBP news for the pair.
"""
from __future__ import annotations
import os
import requests

MARKETAUX_API_KEY = os.environ.get("MARKETAUX_API_KEY")
BASE_URL = "https://api.marketaux.com/v1/news/all"

GBP_QUERY = "GBP OR sterling OR pound OR Bank of England OR UK economy"
USD_QUERY = "Federal Reserve OR US dollar OR Non-Farm Payrolls OR US inflation OR US economy"


def _fetch_raw_sentiment(query: str, limit: int = 10) -> dict:
    """Returns {score, article_count, headlines}. score is the average
    entity sentiment across fetched articles, -1..1, NOT yet GBPUSD-directional
    on its own -- see fetch_combined_sentiment for that."""
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
        "headlines": headlines[:5],
    }


def fetch_combined_sentiment() -> dict:
    """
    Returns {gauge_score, gbp_score, usd_score, article_count, headlines}.
    gauge_score = (gbp_score - usd_score) / 2 -- positive GBP news pushes it
    bullish for GBPUSD, positive USD news pushes it bearish, matching how
    the pair actually trades. This is what makes the gauge directional
    instead of just "positive/negative sentiment" with no interpretation.
    """
    gbp = _fetch_raw_sentiment(GBP_QUERY)
    usd = _fetch_raw_sentiment(USD_QUERY)

    gauge_score = round((gbp["score"] - usd["score"]) / 2, 4)

    merged_headlines = (
        [{**h, "side": "GBP"} for h in gbp["headlines"][:4]]
        + [{**h, "side": "USD"} for h in usd["headlines"][:4]]
    )

    return {
        "gauge_score": gauge_score,
        "gbp_score": gbp["score"],
        "usd_score": usd["score"],
        "article_count": gbp["article_count"] + usd["article_count"],
        "headlines": merged_headlines,
    }
