"""
Fetches the actual text of FOMC and BoE rate decision statements and has
Claude judge whether the tone is hawkish or dovish -- this is the one gauge
that genuinely can't be done any other way. A keyword search or generic
sentiment score can't tell "we expect to raise rates in due course" apart
from "we expect to raise rates soon", but that distinction is exactly what
moves markets.

URL patterns verified directly against the Fed's and BoE's own sites:
  Fed:  https://www.federalreserve.gov/newsevents/pressreleases/monetary{YYYYMMDD}a.htm
  BoE:  https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/{year}/{month-name}-{year}
"""
from __future__ import annotations
import os
import re
import json
import requests
from datetime import date

from .calendar_schedule import FOMC_DATES_2026, BOE_MPC_DATES_2026

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"

_MONTH_NAMES = ["january", "february", "march", "april", "may", "june",
                "july", "august", "september", "october", "november", "december"]


def _strip_html(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&#\d+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _fomc_url(d: date) -> str:
    return f"https://www.federalreserve.gov/newsevents/pressreleases/monetary{d.strftime('%Y%m%d')}a.htm"


def _boe_url(d: date) -> str:
    month_name = _MONTH_NAMES[d.month - 1]
    return f"https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/{d.year}/{month_name}-{d.year}"


def find_most_recent_decision(today: date, lookback_days: int = 3) -> tuple[str, date] | None:
    """Returns (bank, date) for the most recent FOMC/BoE decision within the
    last `lookback_days`, or None if nothing recent. Only looks backward --
    we want the statement AFTER it's been published, not before."""
    candidates = []
    for d, _ in FOMC_DATES_2026:
        if 0 <= (today - d).days <= lookback_days:
            candidates.append(("Fed", d))
    for d, _ in BOE_MPC_DATES_2026:
        if 0 <= (today - d).days <= lookback_days:
            candidates.append(("BoE", d))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[1], reverse=True)
    return candidates[0]


def fetch_statement_text(bank: str, d: date) -> str:
    url = _fomc_url(d) if bank == "Fed" else _boe_url(d)
    resp = requests.get(url, headers={"User-Agent": "one-trading-terminal/1.0"}, timeout=20)
    resp.raise_for_status()
    text = _strip_html(resp.text)
    # Statements are long (BoE minutes especially) -- the tone lives in the
    # opening summary, not deep in procedural detail, so cap it generously
    # but don't send the whole multi-thousand-word minutes document.
    return text[:6000]


def interpret_rate_statement(bank: str, statement_text: str) -> dict:
    """Returns {score, reason}. score: -1 (very dovish, bearish for that
    currency) to +1 (very hawkish, bullish for that currency)."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    currency = "USD" if bank == "Fed" else "GBP"
    prompt = f"""You are reading an official {bank} monetary policy statement to judge its tone for currency trading purposes.

Judge whether the language is HAWKISH (leaning toward higher rates / tighter policy -- bullish for {currency}) or DOVISH (leaning toward lower rates / looser policy -- bearish for {currency}). Focus on subtle language choices ("in due course" vs "soon", "monitoring" vs "prepared to act", unanimous vs split votes, forward guidance changes) -- these nuances are exactly what a simple keyword search would miss.

Statement text:
{statement_text}

Respond with ONLY a JSON object, no other text:
{{"score": <float -1.0 to 1.0, negative = dovish, positive = hawkish>, "reason": "<one short plain-English sentence on the tone and what changed, if anything>"}}"""

    resp = requests.post(
        API_URL,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={"model": MODEL, "max_tokens": 250, "messages": [{"role": "user", "content": prompt}]},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    text = data["content"][0]["text"].strip()

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise RuntimeError(f"Could not parse JSON from Claude's response: {text[:200]}")
    parsed = json.loads(match.group(0))

    score = max(-1.0, min(1.0, float(parsed["score"])))
    reason = str(parsed.get("reason", "")).strip()[:300]
    return {"score": round(score, 3), "reason": reason}
