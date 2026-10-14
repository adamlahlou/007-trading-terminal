#!/usr/bin/env python3
"""Fixes the AI macro verdict inputs: the calendar feed holds no forecasts or
CPI, so (1) feed it far more news headlines (where reported surprises live),
(2) show upcoming scheduled event risk instead, (3) tell it plainly that
there are no forecasts, (4) stop cutting its reasoning off. Run from repo root."""
import pathlib, re, sys

p = pathlib.Path("app/macro_llm.py")
t = p.read_text()

def once(old, new):
    global t
    n = t.count(old)
    if n != 1:
        print(f"FAILED: expected 1 match, found {n}, for:\n{old[:150]}...")
        sys.exit(1)
    t = t.replace(old, new)

once('"headlines": _headline_titles(n.get("headlines")),', '"headlines": _headline_titles(n.get("headlines"), 12),')
once('"headlines": _headline_titles(g.get("headlines"), 4),', '"headlines": _headline_titles(g.get("headlines"), 6),')

# Replace the whole calendar function.
start = t.index("def _calendar_context(")
end = t.index("def _price_context(")
assert t.count("def _calendar_context(") == 1 and start < end
NEW_CAL = '''def _calendar_context(now: datetime) -> list[dict]:
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


'''
t = t[:start] + NEW_CAL + t[end:]

once("the recent/upcoming economic calendar with forecast vs actual, and what price has actually been doing.",
     "the upcoming scheduled events, and what price has actually been doing.")
once("""- SURPRISES matter more than levels. Actual vs forecast is what moves markets: hotter-than-expected US inflation or jobs is typically USD-positive (bearish GBPUSD); a miss is the opposite. Mirror this for UK data. A gauge that scores a data LEVEL can disagree with how the market actually reacted -- trust the surprise and the reaction.""",
     """- SURPRISES matter more than levels. You are NOT given economic forecasts, so read the news headlines for reported surprises and market reactions (e.g. "US CPI hotter than expected", "pound falls after..."). Hotter-than-expected US inflation or jobs is typically USD-positive (bearish GBPUSD); a miss is the opposite. Mirror this for UK data. A gauge that scores a data LEVEL (e.g. momentum) can disagree with how the market actually reacted -- when the headlines say how the market reacted, trust that over the level score.
- The scheduled-events list is upcoming event risk (e.g. a Fed decision within hours = be cautious), not a source of surprises.""")
once('"reason": str(parsed.get("reason", "")).strip()[:400],', '"reason": str(parsed.get("reason", "")).strip()[:700],')
once("[str(d)[:120] for d in", "[str(d)[:200] for d in")
once('"max_tokens": 400,', '"max_tokens": 700,')

p.write_text(t)
print("patched app/macro_llm.py")
