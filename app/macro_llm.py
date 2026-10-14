"""
LLM macro verdict: one small, fast model reads EVERY gauge together (scores
AND the reasons behind them), the recent economic calendar with
forecast-vs-actual, and what price has actually been doing, then gives a
single call -- bullish / bearish / neutral for GBPUSD -- with a confidence
and a short reason.

Why: the plain gauge count can be outvoted by slow, lagging gauges (COT,
yield level) while price trends the other way, and it has no concept of
surprise (hot CPI vs forecast). This reads them in context instead.

Cadence (a cheap check runs every 5 minutes, an LLM call only happens when
needed):
  * a gauge verdict flipped, or a calendar release landed -> call (debounced
    to at most one call per 5 minutes)
  * otherwise at most one call every 30 minutes

Every call is logged to macro_llm_log with its full inputs, so after a few
weeks the backtester can replay the model's REAL verdicts by timestamp --
no lookahead, no re-asking about the past.

GATE_SOURCE env var ("count" default, or "llm") decides what the live entry
gate uses. Until switched, the LLM verdict is shown beside the badge and
logged, but does not gate anything.
"""
from __future__ import annotations
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import requests

from . import db, llm_client
from .gauge_history import macro_verdict

logger = logging.getLogger("007-terminal")

REFRESH_MINUTES = 30      # max gap between calls
DEBOUNCE_MINUTES = 5      # min gap between calls, even if something changed
MAX_AGE_MINUTES = int(os.environ.get("MACRO_LLM_MAX_AGE_MIN", "90"))      # older than this = ignored by the gate
MIN_CONFIDENCE = float(os.environ.get("MACRO_LLM_MIN_CONFIDENCE", "0.55"))  # below this = treated as neutral by the gate


def gate_source() -> str:
    return os.environ.get("GATE_SOURCE", "count").strip().lower()


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

def _vote(score, threshold):
    if score is None:
        return None
    return 1 if score > threshold else (-1 if score < -threshold else 0)


def _headline_titles(raw, limit=5):
    out = []
    for h in (raw or [])[:limit]:
        if isinstance(h, dict):
            t = h.get("title")
        else:
            t = str(h)
        if t:
            out.append(t[:160])
    return out


def _gauge_snapshot() -> list[dict]:
    """The same 6 slots, same thresholds as the badge (yield/cot 0.1,
    everything else 0.15; momentum standalone, rate tone one blended Fed+BoE
    reading -- exactly the panels), plus the
    reasons so the model can read the substance, not just the numbers."""
    gauges = []

    y = db.get_yield_state()
    if y:
        norm = y["spread"] / 1.5
        gauges.append({
            "name": "yield", "score": round(norm, 3), "vote": _vote(norm, 0.1),
            "detail": f"US 10y {y.get('us_yield')}% vs UK 10y {y.get('uk_yield')}%, spread {y.get('spread')} (positive = favours GBP)",
        })

    n = db.get_news_state()
    if n:
        gauges.append({
            "name": "news", "score": round(n["score"], 3), "vote": _vote(n["score"], 0.15),
            "detail": f"GBP tone {n.get('gbp_score')}, USD tone {n.get('usd_score')}",
            "headlines": _headline_titles(n.get("headlines"), 12),
        })

    c = db.get_cot_state()
    if c:
        gauges.append({
            "name": "cot", "score": round(c["gauge_score"], 3), "vote": _vote(c["gauge_score"], 0.1),
            "detail": f"weekly leveraged-fund net {c.get('lev_net')} (prior {c.get('prior_net')}), report {c.get('report_date')} -- weekly, slow-moving positioning",
        })

    m = db.get_momentum_state()
    if m:
        gauges.append({
            "name": "momentum", "score": round(m["gauge_score"], 3), "vote": _vote(m["gauge_score"], 0.15),
            "detail": f"US data trend (levels, not surprises): CPI YoY {m.get('cpi_yoy')} ({m.get('cpi_date')}), NFP change {m.get('nfp_change')} ({m.get('nfp_date')})",
            "reason": (m.get("reason") or "")[:300],
        })

    g = db.get_geo_state()
    if g:
        gauges.append({
            "name": "geo", "score": round(g["gauge_score"], 3), "vote": _vote(g["gauge_score"], 0.15),
            "detail": f"{g.get('article_count')} articles",
            "reason": (g.get("reason") or "")[:300],
            "headlines": _headline_titles(g.get("headlines"), 6),
        })

    fed = db.get_rate_tone_state()
    boe = db.get_boe_rate_tone_state()
    banks = [(name, st) for name, st in (("Fed", fed), ("BoE", boe)) if st]
    if banks:
        score = sum(st["gauge_score"] for _, st in banks) / len(banks)
        gauges.append({
            "name": "rate_tone", "score": round(score, 3), "vote": _vote(score, 0.15),
            "detail": "one blended reading: " + "; ".join(
                f"{name} {round(st['gauge_score'], 3)} (meeting {st.get('meeting_date')})" for name, st in banks),
            "reason": " | ".join(f"{name}: {(st.get('reason') or '')[:220]}" for name, st in banks),
        })
    return gauges


def _parse_time(s):
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


_COUNTRIES = {"us", "usa", "united states", "gb", "uk", "gbr", "united kingdom"}


def _calendar_context(now: datetime) -> list[dict]:
    """The calendar feed is only a schedule of the big dates (Fed, BoE, NFP,
    FOMC minutes) -- no CPI, no forecasts, actual/estimate normally empty. So
    this is UPCOMING EVENT RISK (plus any release that does carry an actual),
    not a surprise source; surprises come from the headlines."""
    events = []
    for e in db.get_calendar_events():
        t = _parse_time(e["time"])
        if t is None or str(e.get("country", "")).strip().lower() not in _COUNTRIES:
            continue
        if not (now - timedelta(hours=10) <= t <= now + timedelta(days=30)):
            continue
        hours = round((t - now).total_seconds() / 3600, 1)
        events.append({
            "event": e["event"], "country": e["country"], "impact": e["impact"],
            "when_utc": t.strftime("%a %d %b %H:%M"), "hours_from_now": hours,
            "actual": e["actual"], "forecast": e["estimate"], "previous": e["prev"], "_t": t,
        })
    events.sort(key=lambda x: x["_t"])
    events = events[:6]
    for ev in events:
        ev.pop("_t")
    return events


def _price_context() -> dict:
    bricks = db.get_recent_bricks(limit=24)
    if not bricks:
        return {}
    dirs = "".join("U" if b["direction"] > 0 else "D" for b in bricks)
    first_open, last_close = bricks[0]["open"], bricks[-1]["close"]
    return {
        "last_close": last_close,
        "recent_brick_directions_oldest_to_newest": dirs,
        "net_move_pips_over_these_bricks": round((last_close - first_open) / 0.0001, 1),
        "brick_size_pips": 22,
    }


def build_inputs() -> dict:
    now = datetime.now(timezone.utc)
    gauges = _gauge_snapshot()
    votes = {g["name"]: g["vote"] for g in gauges if g["vote"] is not None}
    return {
        "now_utc": now.strftime("%a %Y-%m-%d %H:%M"),
        "gauges": gauges,
        "plain_count_verdict": {1: "bullish", -1: "bearish", 0: "mixed"}[macro_verdict(votes)] if votes else "none",
        "calendar": _calendar_context(now),
        "price": _price_context(),
    }


def _fingerprint(inputs: dict) -> str:
    """Changes when a gauge's verdict flips, a calendar release lands, or the
    broader price direction turns -- NOT on every small score wiggle."""
    price = inputs.get("price", {}).get("net_move_pips_over_these_bricks")
    key = {
        "votes": [(g["name"], g["vote"]) for g in inputs["gauges"]],
        "released": sorted(f'{e["event"]}={e["actual"]}' for e in inputs["calendar"] if e["actual"] not in (None, "None", "")),
        "price_dir": (1 if price and price > 0 else -1 if price and price < 0 else 0),
    }
    return hashlib.sha1(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# LLM call
# --------------------------------------------------------------------------

PROMPT = """You are a macro analyst giving ONE call on GBPUSD (British pound vs US dollar) for an automated Renko trading system. It will only take LONG trades if you say bullish, only SHORT trades if you say bearish, and nothing at all if you say neutral.

Below are the system's gauges (positive score = bullish GBPUSD, negative = bearish GBPUSD), the upcoming scheduled events, and what price has actually been doing.

Guidance:
- SURPRISES matter more than levels. You are NOT given economic forecasts, so read the news headlines for reported surprises and market reactions (e.g. "US CPI hotter than expected", "pound falls after..."). Hotter-than-expected US inflation or jobs is typically USD-positive (bearish GBPUSD); a miss is the opposite. Mirror this for UK data. A gauge that scores a data LEVEL (e.g. momentum) can disagree with how the market actually reacted -- when the headlines say how the market reacted, trust that over the level score.
- The scheduled-events list is upcoming event risk (e.g. a Fed decision within hours = be cautious), not a source of surprises.
- COT is weekly positioning and YIELD is a slow level -- they lag. Do not let them outvote fresh, high-impact information.
- Price is evidence, not a vote: if the fundamentals look one way but price has been trending hard the other way, either the gauges are lagging or the move is not yet priced in -- say neutral unless the fresh information clearly explains the gap.
- If the evidence is genuinely mixed or there is no clear edge, say neutral. Neutral is a perfectly good answer.

INPUT:
{inputs}

Respond with ONLY a JSON object, no other text:
{{"verdict": "bullish" | "bearish" | "neutral", "confidence": <float 0.0-1.0>, "reason": "<one or two plain-English sentences>", "key_drivers": ["<short>", "<short>", "<short>"]}}"""


def _call_llm(inputs: dict) -> dict:
    if not llm_client.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    resp = requests.post(
        llm_client.API_URL,
        headers={
            "x-api-key": llm_client.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": llm_client.MODEL,
            "max_tokens": 700,
            "messages": [{"role": "user", "content": PROMPT.format(inputs=json.dumps(inputs, indent=1, default=str))}],
        },
        timeout=30,
    )
    resp.raise_for_status()
    text = resp.json()["content"][0]["text"].strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise RuntimeError(f"Could not parse JSON from model response: {text[:200]}")
    parsed = json.loads(match.group(0))
    verdict = str(parsed.get("verdict", "neutral")).strip().lower()
    if verdict not in ("bullish", "bearish", "neutral"):
        verdict = "neutral"
    conf = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
    drivers = [str(d)[:200] for d in (parsed.get("key_drivers") or [])][:4]
    return {
        "verdict": verdict, "confidence": round(conf, 2),
        "reason": str(parsed.get("reason", "")).strip()[:700], "key_drivers": drivers,
    }


# --------------------------------------------------------------------------
# Refresh (called by the scheduler every 5 minutes, or forced)
# --------------------------------------------------------------------------

def run_macro_llm_refresh(force: bool = False) -> dict:
    inputs = build_inputs()
    if not inputs["gauges"]:
        return {"skipped": "no gauge data yet"}
    fp = _fingerprint(inputs)
    now = datetime.now(timezone.utc)

    last = db.get_latest_macro_llm()
    trigger = "forced" if force else "interval"
    if last and not force:
        last_t = _parse_time(last["created_at"])
        age_min = (now - last_t).total_seconds() / 60 if last_t else 9999
        changed = fp != last["fingerprint"]
        if age_min < DEBOUNCE_MINUTES:
            return {"skipped": "debounce"}
        if not changed and age_min < REFRESH_MINUTES:
            return {"skipped": "unchanged"}
        trigger = "change" if changed else "interval"

    result = _call_llm(inputs)
    db.add_macro_llm_log(
        created_at=now.isoformat(), verdict=result["verdict"], confidence=result["confidence"],
        reason=result["reason"], key_drivers=result["key_drivers"], inputs=inputs,
        fingerprint=fp, count_verdict=inputs["plain_count_verdict"], trigger=trigger,
    )
    logger.info(f"Macro LLM ({trigger}): {result['verdict']} @ {result['confidence']} -- {result['reason'][:100]}")
    return {**result, "trigger": trigger}


# --------------------------------------------------------------------------
# What the live gate asks
# --------------------------------------------------------------------------

def current_verdict_num() -> int | None:
    """+1 / -1 / 0 from the latest LLM call, or None if there's no usable one
    (nothing logged yet, or older than MAX_AGE_MINUTES) -- the caller then
    falls back to the plain gauge count rather than trading blind or freezing."""
    last = db.get_latest_macro_llm()
    if not last:
        return None
    t = _parse_time(last["created_at"])
    if t is None or (datetime.now(timezone.utc) - t) > timedelta(minutes=MAX_AGE_MINUTES):
        return None
    if last["confidence"] < MIN_CONFIDENCE:
        return 0
    return {"bullish": 1, "bearish": -1}.get(last["verdict"], 0)
