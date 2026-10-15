#!/usr/bin/env python3
"""Patch 15 -- /api/backtest-dump now also returns the raw gauge SCORES per day
(not just the -1/0/1 votes), so gauges can be rebuilt as CHANGE-based signals
(e.g. yield spread moving, COT shifting) and tested offline.
Needs patch 11 applied. Nothing is written unless every edit matches once."""
import pathlib, sys
edits = {"app/backtest.py": [
 ('''    votes, d = {}, start
    while d <= end:
        iso = d.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
        votes[d.date().isoformat()] = gh.badge_votes_as_of(iso)
        d += timedelta(days=1)
''', '''    votes, scores, d = {}, {}, start
    while d <= end:
        iso = d.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
        votes[d.date().isoformat()] = gh.badge_votes_as_of(iso)
        sc = {
            "yield_spread": gh.yield_score(iso), "cot": gh.cot_score(iso),
            "momentum": gh.momentum_score(iso), "news": gh.news_score(iso),
            "geo": gh.geopolitical_score(iso), "rate_tone": gh.rate_tone_blended_score(iso),
        }
        scores[d.date().isoformat()] = {k: (round(v, 4) if v is not None else None) for k, v in sc.items()}
        d += timedelta(days=1)
'''),
 ('''        "votes_by_day": votes,
''', '''        "votes_by_day": votes,
        "scores_by_day": scores,
'''),
]}
new = {}
for path, reps in edits.items():
    t = pathlib.Path(path).read_text()
    for old, rep in reps:
        n = t.count(old)
        if n != 1:
            print(f"FAILED on {path}: expected 1 match, found {n}, for:\n{old[:140]}..."); sys.exit(1)
        t = t.replace(old, rep)
    new[path] = t
for p, t in new.items():
    pathlib.Path(p).write_text(t); print("patched", p)
print("\nPatch 15 applied cleanly.")
