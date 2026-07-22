"""
Fetches GBP/USD candles from OANDA's v20 API (practice/demo environment).
"""
from __future__ import annotations
import os
import requests
from datetime import datetime, timezone

OANDA_API_TOKEN = os.environ.get("OANDA_API_TOKEN")
OANDA_ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID")
OANDA_ENV = os.environ.get("OANDA_ENV", "practice")  # "practice" or "live"

BASE_URL = (
    "https://api-fxpractice.oanda.com"
    if OANDA_ENV == "practice"
    else "https://api-fxtrade.oanda.com"
)
INSTRUMENT = "GBP_USD"


def fetch_current_price() -> dict:
    """
    Returns {bid, ask, mid, time} for GBP/USD right now. Cheap, single
    lightweight request -- safe to poll every few seconds without coming
    anywhere near OANDA's rate limits (which are per-second, not a tiny
    daily cap).
    """
    if not OANDA_API_TOKEN or not OANDA_ACCOUNT_ID:
        raise RuntimeError("OANDA_API_TOKEN / OANDA_ACCOUNT_ID not set")

    headers = {"Authorization": f"Bearer {OANDA_API_TOKEN}"}
    url = f"{BASE_URL}/v3/accounts/{OANDA_ACCOUNT_ID}/pricing"
    resp = requests.get(url, headers=headers, params={"instruments": INSTRUMENT}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    price = data["prices"][0]
    bid = float(price["bids"][0]["price"])
    ask = float(price["asks"][0]["price"])
    return {"bid": bid, "ask": ask, "mid": round((bid + ask) / 2, 5), "time": price["time"]}


def fetch_candles(since: datetime | None = None, count: int = 500, granularity: str = "M15") -> list[dict]:
    """
    Returns a list of {time, open, high, low, close, complete} dicts, oldest first.
    If `since` is given, fetches candles from that point forward (used to avoid
    gaps/re-fetching everything on every scan). Otherwise fetches the most
    recent `count` candles.
    """
    if not OANDA_API_TOKEN:
        raise RuntimeError("OANDA_API_TOKEN is not set")

    headers = {"Authorization": f"Bearer {OANDA_API_TOKEN}"}
    params = {"granularity": granularity, "price": "M"}
    if since is not None:
        # OANDA wants RFC3339; add a tiny buffer so we don't refetch the same last candle
        params["from"] = since.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
    else:
        params["count"] = count

    url = f"{BASE_URL}/v3/instruments/{INSTRUMENT}/candles"
    resp = requests.get(url, headers=headers, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    out = []
    for c in data.get("candles", []):
        if not c.get("complete"):
            continue  # skip the in-progress candle
        mid = c["mid"]
        out.append(
            {
                "time": c["time"],
                "open": float(mid["o"]),
                "high": float(mid["h"]),
                "low": float(mid["l"]),
                "close": float(mid["c"]),
            }
        )
    return out
