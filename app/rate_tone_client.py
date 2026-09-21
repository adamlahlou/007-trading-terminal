"""
Fetches the actual text of FOMC and BoE rate decision statements and has
Claude judge whether the tone is hawkish or dovish -- this is the one gauge
that genuinely can't be done any other way. A keyword search or generic
sentiment score can't tell "we expect to raise rates in due course" apart
from "we expect to raise rates soon", but that distinction is exactly what
moves markets.

Also fetches FOMC Minutes -- a genuinely separate, later-released document
(3 weeks after the decision) that goes into far more depth than the brief
statement, including actual dissent counts and vote splits.

Fed and BoE decisions are tracked and looked up SEPARATELY (see
find_most_recent_fed_decision / find_most_recent_boe_decision below) --
they used to share one "most recent of either" lookup, but BoE meets just
1 day after Fed in 5 of 8 months this year, so BoE always won that
comparison and Fed's decision was never actually processed.

URL patterns verified directly against the Fed's and BoE's own sites:
  Fed statement: https://www.federalreserve.gov/newsevents/pressreleases/monetary{YYYYMMDD}a.htm
  Fed minutes:   https://www.federalreserve.gov/monetarypolicy/fomcminutes{YYYYMMDD}.htm (meeting end date)
  BoE:           https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/{year}/{month-name}-{year}
"""
from __future__ import annotations
import os
import re
import json
import requests
from datetime import date

from .calendar_schedule import FOMC_DATES_2026, BOE_MPC_DATES_2026, get_fomc_minutes_dates, get_fomc_minutes_release_datetimes

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


def _fomc_minutes_url(meeting_end_date: date) -> str:
    return f"https://www.federalreserve.gov/monetarypolicy/fomcminutes{meeting_end_date.strftime('%Y%m%d')}.htm"


def _boe_url(d: date) -> str:
    month_name = _MONTH_NAMES[d.month - 1]
    return f"https://www.bankofengland.co.uk/monetary-policy-summary-and-minutes/{d.year}/{month_name}-{d.year}"


def _find_most_recent(dates: list[date], today: date, lookback_days: int) -> date | None:
    candidates = [d for d in dates if 0 <= (today - d).days <= lookback_days]
    if not candidates:
        return None
    return max(candidates)


def find_most_recent_fed_decision(today: date, lookback_days: int = 3) -> date | None:
    """Returns the Fed meeting date if one was decided within the last
    `lookback_days`, or None -- checks Fed's OWN calendar only, independent
    of BoE, so a nearby BoE meeting can never shadow it."""
    return _find_most_recent([d for d, _ in FOMC_DATES_2026], today, lookback_days)


def find_most_recent_boe_decision(today: date, lookback_days: int = 3) -> date | None:
    """Same as find_most_recent_fed_decision but for BoE's own calendar."""
    return _find_most_recent([d for d, _ in BOE_MPC_DATES_2026], today, lookback_days)


def find_most_recent_minutes(today: date, lookback_days: int = 3) -> date | None:
    """Returns the meeting_end_date whose Minutes were released within the
    last `lookback_days`, or None if nothing recent. Same lookback pattern
    as the decision lookups, just checking the computed RELEASE date
    (meeting + 3 weeks), not the meeting date itself. Fed-only (Minutes
    have no BoE equivalent), so no cross-bank shadowing risk here."""
    candidates = []
    for meeting_date, release_dt in get_fomc_minutes_release_datetimes():
        release_date = release_dt.date()
        if 0 <= (today - release_date).days <= lookback_days:
            candidates.append((meeting_date, release_date))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[1], reverse=True)
    return candidates[0][0]


def fetch_statement_text(bank: str, d: date) -> str:
    url = _fomc_url(d) if bank == "Fed" else _boe_url(d)
    resp = requests.get(url, headers={"User-Agent": "one-trading-terminal/1.0"}, timeout=20)
    resp.raise_for_status()
    text = _strip_html(resp.text)

    marker_idx = text.lower().find("for release at")
    if marker_idx != -1:
        text = text[marker_idx:]
    else:
        text = text[-6000:]

    return text[:6000]


def fetch_minutes_text(meeting_end_date: date) -> str:
    """Minutes are MUCH longer than the brief statement, and critically,
    the most analytically important content (Participants' Views, the
    actual vote count, named dissents) sits well past the opening --
    confirmed directly against a real Minutes page. A 6000-char cap (fine
    for the short statement) would cut off before ever reaching the vote/
    dissent section, so this uses a far more generous limit."""
    url = _fomc_minutes_url(meeting_end_date)
    resp = requests.get(url, headers={"User-Agent": "one-trading-terminal/1.0"}, timeout=20)
    resp.raise_for_status()
    text = _strip_html(resp.text)

    marker_idx = text.lower().find("a joint meeting of the federal open market committee")
    if marker_idx != -1:
        text = text[marker_idx:]
    else:
        text = text[-28000:]

    return text[:28000]


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


def interpret_minutes_text(minutes_text: str) -> dict:
    """Same hawkish/dovish scoring as interpret_rate_statement, but tuned
    for Minutes specifically -- explicitly told about the Fed's own
    quantifier convention ('a couple'/'a few' < 'some' < 'several' <
    'many' < 'most' < 'almost all', and 'participants' vs 'members') since
    that's exactly the kind of nuance real analysts read Minutes for, and
    told to weight the actual vote count/named dissents heavily -- that's
    concrete, not just tone."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    prompt = f"""You are reading the official FOMC Minutes (the detailed account of a Federal Reserve meeting, released 3 weeks after the decision) to judge its tone for currency trading purposes, specifically for USD.

Judge whether the Minutes are HAWKISH (leaning toward higher rates / tighter policy -- bullish for USD) or DOVISH (leaning toward lower rates / looser policy -- bearish for USD).

Important context for reading FOMC Minutes specifically:
- The Fed uses a deliberate quantifier ladder to convey how widely a view was held, from least to most participants: "a couple" / "a few" < "some" < "several" < "many" < "most" < "almost all". Weight views described with stronger quantifiers more heavily.
- "Participants" means everyone at the table (including non-voting regional presidents); "members" means only the twelve who actually vote -- a view held by "members" is more directly actionable than one merely held by "participants".
- The ACTUAL vote count and any NAMED DISSENTS (e.g. "Voting against this action: X, Y, Z") are concrete, high-weight signals -- weight these more heavily than general tone language elsewhere in the document.
- Minutes are backward-looking (describing a meeting that already happened) -- if the actual policy decision was already known, focus on what's genuinely NEW here: how close the vote was, what the internal debate revealed about the committee's likely NEXT move, not just restating the known decision.

Minutes text:
{minutes_text}

Respond with ONLY a JSON object, no other text:
{{"score": <float -1.0 to 1.0, negative = dovish, positive = hawkish>, "reason": "<one short plain-English sentence on the tone, the vote/dissent detail if notable, and what it implies for the next meeting>"}}"""

    resp = requests.post(
        API_URL,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={"model": MODEL, "max_tokens": 300, "messages": [{"role": "user", "content": prompt}]},
        timeout=40,
    )
    resp.raise_for_status()
    data = resp.json()
    text = data["content"][0]["text"].strip()

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise RuntimeError(f"Could not parse JSON from Claude's response: {text[:200]}")
    parsed = json.loads(match.group(0))

    score = max(-1.0, min(1.0, float(parsed["score"])))
    reason = str(parsed.get("reason", "")).strip()[:400]
    return {"score": round(score, 3), "reason": reason}
