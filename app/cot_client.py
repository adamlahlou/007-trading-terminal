"""
Fetches the latest CFTC Commitment of Traders data for British Pound
futures (Traders in Financial Futures, Futures Only report) -- genuine
free government data via Socrata, no vendor gating like the calendar saga.

Focuses on the "Leveraged Funds" category (hedge funds / CTAs / money
managers) since that's the classic "large speculator positioning" read
traders actually watch -- not the Dealer or Asset Manager categories.
"""
from __future__ import annotations
import os
import requests

SOCRATA_APP_TOKEN = os.environ.get("SOCRATA_APP_TOKEN")  # optional, not required for our low volume
BASE_URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"
GBP_CONTRACT_CODE = "096742"  # British Pound futures, CME


def _find_field(row: dict, *must_contain: str) -> str:
    """Finds the actual key in the row whose name contains all the given
    substrings (case-insensitive). Socrata's real API field names don't
    always match a naive lowercase of the published CSV headers, so we
    search rather than assume one exact spelling."""
    for key in row.keys():
        low = key.lower()
        if all(term in low for term in must_contain):
            return key
    raise KeyError(f"No field found containing {must_contain}. Available fields: {list(row.keys())}")


def fetch_cot_history(start_date: str, end_date: str) -> list[dict]:
    """Returns [{report_date, lev_long, lev_short, gauge_score}] ascending
    by date, for reconstructing what COT positioning actually said at any
    point in a backtest window (weekly data, so expect one row per week)."""
    headers = {"User-Agent": "one-trading-terminal/1.0"}
    if SOCRATA_APP_TOKEN:
        headers["X-App-Token"] = SOCRATA_APP_TOKEN
    params = {
        "$where": (
            f"cftc_contract_market_code='{GBP_CONTRACT_CODE}' AND "
            f"report_date_as_yyyy_mm_dd between '{start_date}T00:00:00' and '{end_date}T23:59:59'"
        ),
        "$order": "report_date_as_yyyy_mm_dd ASC",
        "$limit": 200,
    }
    resp = requests.get(BASE_URL, headers=headers, params=params, timeout=20)
    resp.raise_for_status()
    rows = resp.json()

    out = []
    for row in rows:
        long_field = _find_field(row, "lev", "long")
        short_field = _find_field(row, "lev", "short")
        date_field = _find_field(row, "report", "date")
        lev_long = float(row[long_field])
        lev_short = float(row[short_field])
        total = lev_long + lev_short
        gauge_score = round((lev_long - lev_short) / total, 4) if total else 0.0
        out.append({
            "report_date": str(row[date_field])[:10],
            "lev_long": lev_long,
            "lev_short": lev_short,
            "gauge_score": gauge_score,
        })
    return out


def fetch_cot_data() -> dict:
    """
    Returns {report_date, lev_long, lev_short, lev_net, prior_net, gauge_score}.
    gauge_score is net leveraged-fund position as a fraction of long+short
    (-1..1), a simple long/short skew read -- not a historical percentile,
    just "are speculators net long or short right now, and by how much
    relative to their own total position."
    """
    headers = {"User-Agent": "one-trading-terminal/1.0"}
    if SOCRATA_APP_TOKEN:
        headers["X-App-Token"] = SOCRATA_APP_TOKEN
    params = {
        "$where": f"cftc_contract_market_code='{GBP_CONTRACT_CODE}'",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": 2,
    }
    resp = requests.get(BASE_URL, headers=headers, params=params, timeout=20)
    resp.raise_for_status()
    rows = resp.json()
    if not rows:
        raise RuntimeError("CFTC returned no rows for GBP futures")

    latest = rows[0]
    long_field = _find_field(latest, "lev", "long")
    short_field = _find_field(latest, "lev", "short")
    date_field = _find_field(latest, "report", "date")

    lev_long = float(latest[long_field])
    lev_short = float(latest[short_field])
    lev_net = lev_long - lev_short
    total = lev_long + lev_short
    gauge_score = round(lev_net / total, 4) if total else 0.0

    prior_net = None
    if len(rows) > 1:
        prior = rows[1]
        prior_net = float(prior[long_field]) - float(prior[short_field])

    return {
        "report_date": str(latest[date_field])[:10],
        "lev_long": lev_long,
        "lev_short": lev_short,
        "lev_net": lev_net,
        "prior_net": prior_net,
        "gauge_score": gauge_score,
    }
