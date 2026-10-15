"""
AI macro verdict REPLAY: asks the same model that runs live to give one
bullish/bearish/neutral call per historical day, using only what was knowable
at 07:00 UTC that day, then caches the answers so backtests are repeatable.

What it can and cannot reproduce (be honest about this when reading results):
  + gauge scores/votes as of that day (with the release lags the backtest uses)
  + price action: the last 24 Renko bricks before 07:00 UTC
  - NO headlines and NO calendar (not stored historically), so this measures the
    AI's judgement over gauge scores + price, not the full live AI.
A call made at 07:00 UTC on day D applies to entries from 07:00 D to 07:00 D+1.
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone
from . import db, macro_llm
from .renko import RenkoState, process_candle
from .gauge_history import GaugeHistory, macro_verdict

logger = logging.getLogger("007-terminal")
CACHE_PREFIX = "aireplay:v1:"
DECISION_HOUR_UTC = 7
MIN_CONFIDENCE = macro_llm.MIN_CONFIDENCE
_ISO = "%Y-%m-%dT%H:%M:%S.000000000Z"


def _key(day: str) -> str:
    return CACHE_PREFIX + day


def cached_vote(at_time: str) -> int | None:
    """-1/0/1 for the AI call valid at `at_time` (ISO), or None if not replayed."""
    t = datetime.strptime(at_time[:19], "%Y-%m-%dT%H:%M:%S") - timedelta(hours=DECISION_HOUR_UTC)
    row = db.get_backtest_cache(_key(t.date().isoformat()))
    if row is None:
        return None
    v = float(row["value"])
    if abs(v) < MIN_CONFIDENCE:
        return 0
    return 1 if v > 0 else -1


def _vote(score, thr):
    return 1 if score > thr else (-1 if score < -thr else 0)


def _inputs_for(day: datetime, gh, bricks) -> dict:
    iso = day.strftime(_ISO)
    gauges = []
    y = gh.yield_score(iso)
    if y is not None:
        n = y / 1.5
        gauges.append({"name": "yield", "score": round(n, 3), "vote": _vote(n, 0.1),
                       "detail": f"UK 10y minus US 10y spread {round(y, 3)} (positive = favours GBP); slow-moving level"})
    news = gh.news_score(iso)
    if news is not None:
        gauges.append({"name": "news", "score": round(news, 3), "vote": _vote(news, 0.15),
                       "detail": "GBP-vs-USD headline tone over the prior week"})
    c = gh.cot_score(iso)
    if c is not None:
        gauges.append({"name": "cot", "score": round(c, 3), "vote": _vote(c, 0.1),
                       "detail": "weekly leveraged-fund positioning; slow-moving"})
    m = gh.momentum_score(iso)
    if m is not None:
        gauges.append({"name": "momentum", "score": round(m, 3), "vote": _vote(m, 0.15),
                       "detail": "US CPI/NFP data trend (levels, not surprises)"})
    g = gh.geopolitical_score(iso)
    if g is not None:
        gauges.append({"name": "geo", "score": round(g, 3), "vote": _vote(g, 0.15),
                       "detail": "geopolitical headline tone over the prior week"})
    rt = gh.rate_tone_blended_score(iso)
    if rt is not None:
        gauges.append({"name": "rate_tone", "score": round(rt, 3), "vote": _vote(rt, 0.15),
                       "detail": "blended Fed + BoE policy-statement tone"})
    votes = {x["name"]: x["vote"] for x in gauges}
    cutoff = (day + timedelta(hours=DECISION_HOUR_UTC)).strftime(_ISO)
    prior = [b for b in bricks if b.formed_at < cutoff][-24:]
    price = {}
    if prior:
        price = {
            "last_close": prior[-1].close,
            "recent_brick_directions_oldest_to_newest": "".join("U" if b.direction > 0 else "D" for b in prior),
            "net_move_pips_over_these_bricks": round((prior[-1].close - prior[0].open) / 0.0001, 1),
            "brick_size_pips": 22,
        }
    return {
        "now_utc": (day + timedelta(hours=DECISION_HOUR_UTC)).strftime("%a %Y-%m-%d %H:%M"),
        "NOTE": "Historical replay: no headlines or calendar are available. Judge from the gauge scores and price only.",
        "gauges": gauges,
        "plain_count_verdict": {1: "bullish", -1: "bearish", 0: "mixed"}[macro_verdict(votes)] if votes else "none",
        "calendar": [],
        "price": price,
    }


def run_replay(start_date: str, end_date: str, max_new: int = 25) -> dict:
    from . import backtest  # for candle loading
    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    days, d = [], start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    todo = [x for x in days if db.get_backtest_cache(_key(x.date().isoformat())) is None]
    new_calls, errors = 0, []
    if todo:
        candles = backtest._load_candles(start - timedelta(days=14), end + timedelta(days=1))
        anchor = backtest._live_anchor(0.0022)
        state = RenkoState(box_size=0.0022, anchor=anchor)
        bricks = []
        for cndl in candles:
            bricks.extend(process_candle(state, cndl))
        gh = GaugeHistory(start.date(), end.date())
        for day in todo[:max_new]:
            try:
                inputs = _inputs_for(day, gh, bricks)
                out = macro_llm._call_llm(inputs)
                signed = 0.0 if out["verdict"] == "neutral" else (out["confidence"] if out["verdict"] == "bullish" else -out["confidence"])
                db.set_backtest_cache(_key(day.date().isoformat()), signed)
                new_calls += 1
            except Exception as e:
                errors.append(f"{day.date()}: {e}")
                if len(errors) >= 3:
                    break
    verdicts = {}
    for x in days:
        row = db.get_backtest_cache(_key(x.date().isoformat()))
        if row is not None:
            verdicts[x.date().isoformat()] = row["value"]
    return {
        "start": start_date, "end": end_date, "total_days": len(days),
        "replayed": len(verdicts), "new_calls_this_request": new_calls,
        "remaining": len(days) - len(verdicts), "errors": errors,
        "approx_cost_usd_this_request": round(new_calls * 0.004, 3),
        "verdicts": verdicts,
    }
