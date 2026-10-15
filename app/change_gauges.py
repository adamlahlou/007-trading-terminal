"""
CHANGE-based macro gauges.

The level-based gauges (yield spread, COT, rate tone) sat on one side for
months -- they described where things ARE, so they could not flip when the
market turned. These ask what has CHANGED:

  COT        positioning moved more than 0.05 over the last 14 days
  rate tone  blended Fed+BoE tone moved more than 0.03 since 30 days ago
  news, geo  unchanged (level, +-0.15)
  yield      left out -- its change-version made results worse in testing
  momentum   left out -- the live one is an AI reading that cannot be backtested

Same majority rule as the badge: more bullish votes -> longs only, more
bearish -> shorts only, tie -> no entry. Backtested over Nov 2025 - Oct 2026
(see gauge_set=change_gauges). Shown on the page beside the old badge and
logged; it only gates entries when GATE_SOURCE=change.
"""
from __future__ import annotations
import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from . import db
from .gauge_history import GaugeHistory, macro_verdict, change_votes, COT_CHANGE_DAYS, RATE_CHANGE_DAYS

logger = logging.getLogger("007-terminal")
CACHE_TTL_MIN = 30
MAX_AGE_MIN = 120          # older than this = not used by the gate (falls back to the count)
LOG_EVERY_MIN = 30

_lock = threading.Lock()
_cache: dict = {"t": None, "data": None}


def _r(x):
    return None if x is None else round(x, 4)


def compute_now() -> dict:
    now = datetime.now(timezone.utc)
    today = now.date()
    gh = GaugeHistory(today - timedelta(days=75), today, light=True)
    iso = today.isoformat()
    prev = lambda n: (today - timedelta(days=n)).isoformat()
    cot_now, cot_prev = gh.cot_score(iso), gh.cot_score(prev(COT_CHANGE_DAYS))
    rate_now, rate_prev = gh.rate_tone_blended_score(iso), gh.rate_tone_blended_score(prev(RATE_CHANGE_DAYS))
    news_state, geo_state = db.get_news_state(), db.get_geo_state()
    news = news_state["score"] if news_state else None
    geo = geo_state["gauge_score"] if geo_state else None
    votes = change_votes(cot_now, cot_prev, rate_now, rate_prev, news, geo)
    verdict = macro_verdict(votes) if votes else 0
    bull = sum(1 for v in votes.values() if v > 0)
    bear = sum(1 for v in votes.values() if v < 0)
    return {
        "verdict_num": verdict,
        "verdict": {1: "bullish", -1: "bearish", 0: "mixed"}[verdict],
        "bull": bull, "bear": bear, "votes": votes,
        "detail": {
            "cot": {"now": _r(cot_now), "prev": _r(cot_prev), "days": COT_CHANGE_DAYS},
            "rate_tone": {"now": _r(rate_now), "prev": _r(rate_prev), "days": RATE_CHANGE_DAYS},
            "news": _r(news), "geo": _r(geo),
        },
        "computed_at": now.isoformat(),
    }


def get_state(force: bool = False) -> dict | None:
    with _lock:
        now = datetime.now(timezone.utc)
        t = _cache["t"]
        if not force and t and (now - t) < timedelta(minutes=CACHE_TTL_MIN) and _cache["data"]:
            return _cache["data"]
        try:
            data = compute_now()
            _cache["t"], _cache["data"] = now, data
            return data
        except Exception as e:
            logger.error(f"Change gauges failed: {e}")
            if _cache["data"]:
                return {**_cache["data"], "error": str(e)}
            return None


def current_verdict_num() -> int | None:
    """+1/-1/0, or None if unavailable/too old (caller falls back to the plain count)."""
    with _lock:
        t, data = _cache["t"], _cache["data"]
    if data is None or t is None or (datetime.now(timezone.utc) - t) > timedelta(minutes=MAX_AGE_MIN):
        data = get_state()
        if data is None or "error" in data:
            return None
    return data["verdict_num"]


# ------------------------------------------------------------------ forward log
def _ensure_table(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS gauge_verdict_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            count_verdict INTEGER, change_verdict INTEGER, change_votes_json TEXT,
            ai_verdict TEXT, ai_confidence REAL, price REAL)"""
    )


def refresh_and_log() -> dict:
    """Recompute and append one row (at most one per LOG_EVERY_MIN) so the old
    count, the change-based verdict and the AI verdict can be compared with
    what price did afterwards."""
    state = get_state(force=True)
    if state is None:
        return {"skipped": "no state"}
    from . import live_trader  # lazy: avoids a circular import
    try:
        count_v = macro_verdict(live_trader._current_gauge_votes())
    except Exception:
        count_v = None
    ai = db.get_latest_macro_llm() or {}
    bricks = db.get_recent_bricks(limit=1)
    price = bricks[-1]["close"] if bricks else None
    now = datetime.now(timezone.utc)
    conn = db.get_conn()
    try:
        _ensure_table(conn)
        last = conn.execute("SELECT created_at FROM gauge_verdict_log ORDER BY id DESC LIMIT 1").fetchone()
        if last:
            try:
                lt = datetime.fromisoformat(last["created_at"])
                if (now - lt) < timedelta(minutes=LOG_EVERY_MIN):
                    return {"logged": False, "reason": "logged recently", "change_verdict": state["verdict"]}
            except Exception:
                pass
        conn.execute(
            "INSERT INTO gauge_verdict_log (created_at, count_verdict, change_verdict, change_votes_json, ai_verdict, ai_confidence, price) VALUES (?,?,?,?,?,?,?)",
            (now.isoformat(), count_v, state["verdict_num"], json.dumps(state["votes"]), ai.get("verdict"), ai.get("confidence"), price),
        )
        conn.commit()
    finally:
        conn.close()
    return {"logged": True, "change_verdict": state["verdict"], "count_verdict": count_v}


def get_log(limit: int = 200) -> list[dict]:
    conn = db.get_conn()
    try:
        _ensure_table(conn)
        rows = conn.execute("SELECT * FROM gauge_verdict_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in reversed(rows)]
