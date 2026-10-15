#!/usr/bin/env python3
"""Patch 11 -- backtest price-level reliability.

1. Renko grid parity: the backtest used to anchor its brick grid on the first
   candle of each window, so bricks (and every entry/stop price) sat on a
   different grid from the live terminal. Now it seeds the grid with the LIVE
   anchor and replays a warm-up period before the window so direction/phase
   settle -- then only trades inside the window.
2. Duplicate candles at 25-day fetch-chunk boundaries are removed.
3. Spread cost: spread_pips (default 1.0) is charged on every trade.
4. /api/backtest-brickcheck : compares backtest bricks to the bricks the live
   terminal actually stored -- the direct test of price-level accuracy.
5. /api/backtest-dump : candles + per-day gauge votes as JSON, for offline tuning.

Run from the repo root. Nothing is written unless every edit matches once."""
import pathlib, sys

edits = {
 "app/backtest.py": [
  ("    macro_exit: bool = False,\n) -> dict:\n",
   "    macro_exit: bool = False,\n    spread_pips: float = 1.0,\n    warmup_days: int = 14,\n) -> dict:\n"),
  ('''    candles = oanda_client.fetch_candles(since=start, until=now, granularity="M15")
    if not candles:
        raise RuntimeError("No candles returned for the requested backtest window")

    gauge_hist = None
    if (require''',
   '''    candles = _load_candles(start - timedelta(days=warmup_days), now)
    if not candles:
        raise RuntimeError("No candles returned for the requested backtest window")
    start_iso = start.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
    live_anchor = _live_anchor(box_size)

    gauge_hist = None
    if (require'''),
  ("    state = RenkoState(box_size=box_size)\n    initial_stop_dist",
   "    state = RenkoState(box_size=box_size, anchor=live_anchor)\n    initial_stop_dist"),
  ('''        pips = (exit_price - entry_price) / PIP if position == 1 else (entry_price - exit_price) / PIP
        trades.append({''',
   '''        pips = (exit_price - entry_price) / PIP if position == 1 else (entry_price - exit_price) / PIP
        pips -= spread_pips  # round-trip spread cost
        trades.append({'''),
  ('''    for candle in candles:
        # 1) Check if the currently open''',
   '''    for candle in candles:
        if candle["time"] < start_iso:
            # warm-up: build the Renko state only, never trade
            process_candle(state, candle)
            continue
        # 1) Check if the currently open'''),
  ('''    result["macro_exit"] = macro_exit
''',
   '''    result["macro_exit"] = macro_exit
    result["spread_pips"] = spread_pips
    result["renko_grid"] = {"anchor": live_anchor, "source": "live engine_state" if live_anchor is not None else "first candle (no live anchor found)", "warmup_days": warmup_days}
'''),
  ('''        "Small samples: a handful of trades says little. Compare windows, not single numbers.",
    ]''',
   '''        "Small samples: a handful of trades says little. Compare windows, not single numbers.",
        "Fills: stops fill at the exact stop price (no slippage); a flat spread_pips is charged per trade; prices are OANDA mid.",
        "Intra-candle order is assumed (O-L-H-C or O-H-L-C) from M15 bars, same as the live engine.",
    ]'''),  # OPTIONAL
 ],
 "app/main.py": [
  ('gauge_set: str = "yield_cot_momentum", macro_exit: bool = False):\n    try:\n        result = await asyncio.to_thread(backtest.run_backtest, days, 0.0022, reversal_only, continuation_override, gate_all_entries, trailing_mode, gate_threshold, start_date, end_date, debug_gauges, gauge_set, macro_exit)',
   'gauge_set: str = "yield_cot_momentum", macro_exit: bool = False, spread_pips: float = 1.0, warmup_days: int = 14):\n    try:\n        result = await asyncio.to_thread(backtest.run_backtest, days, 0.0022, reversal_only, continuation_override, gate_all_entries, trailing_mode, gate_threshold, start_date, end_date, debug_gauges, gauge_set, macro_exit, spread_pips, warmup_days)'),
  ('''@app.get("/api/live-trades")''',
   '''@app.get("/api/backtest-brickcheck")
async def api_backtest_brickcheck(days: int = 30):
    try:
        return JSONResponse(await asyncio.to_thread(backtest.brick_parity, days))
    except Exception as e:
        logger.error(f"Brickcheck failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/backtest-dump")
async def api_backtest_dump(start_date: str, end_date: str, warmup_days: int = 14):
    try:
        return JSONResponse(await asyncio.to_thread(backtest.research_dump, start_date, end_date, warmup_days))
    except Exception as e:
        logger.error(f"Dump failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/live-trades")'''),
 ],
}

APPEND = '''

# ---------------------------------------------------------------- reliability helpers
def _load_candles(since, until) -> list[dict]:
    """M15 candles, oldest first, with duplicates (25-day chunk boundaries) removed."""
    raw = oanda_client.fetch_candles(since=since, until=until, granularity="M15")
    seen, out = set(), []
    for c in sorted(raw, key=lambda c: c["time"]):
        if c["time"] in seen:
            continue
        seen.add(c["time"])
        out.append(c)
    return out


def _live_anchor(box_size: float):
    """The live terminal's Renko grid anchor (bricks sit at anchor + k*box)."""
    try:
        from . import db
        return db.load_state(box_size).get("anchor")
    except Exception:
        return None


def brick_parity(days: int = 30, box_size: float = 0.0022, warmup_days: int = 14) -> dict:
    """Do the backtest's bricks equal the bricks the live terminal stored?"""
    from . import db
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    start_iso = start.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
    candles = _load_candles(start - timedelta(days=warmup_days), now)
    if not candles:
        raise RuntimeError("No candles returned")
    last_t = candles[-1]["time"]
    anchor = _live_anchor(box_size)
    state = RenkoState(box_size=box_size, anchor=anchor)
    bt = []
    for c in candles:
        for b in process_candle(state, c):
            if start_iso <= b.formed_at <= last_t:
                bt.append((b.formed_at, b.direction, round(b.close, 5)))
    live = [(b["formed_at"], b["direction"], round(b["close"], 5))
            for b in db.get_recent_bricks(5000) if start_iso <= b["formed_at"] <= last_t]
    exact = set(bt) & set(live)
    levels_bt = {(d, c) for _, d, c in bt}
    levels_live = {(d, c) for _, d, c in live}
    return {
        "window": f"{start.date()} to {now.date()}",
        "anchor_used": anchor,
        "live_bricks": len(live),
        "backtest_bricks": len(bt),
        "exact_matches_time_dir_price": len(exact),
        "same_price_levels_and_direction": len(levels_bt & levels_live),
        "match_pct_of_live": round(len(exact) / len(live) * 100, 1) if live else None,
        "live_only_first5": sorted(set(live) - set(bt))[:5],
        "backtest_only_first5": sorted(set(bt) - set(live))[:5],
        "verdict": "PASS" if live and len(exact) / len(live) >= 0.95 and abs(len(live) - len(bt)) <= 2 else "CHECK",
    }


def research_dump(start_date: str, end_date: str, warmup_days: int = 14) -> dict:
    """Everything the backtest sees, as data: warm-up + window candles and the
    exact per-day gauge votes the all_gauges_badge gate would use."""
    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    candles = _load_candles(start - timedelta(days=warmup_days), end)
    gh = GaugeHistory(start.date(), end.date())
    votes, d = {}, start
    while d <= end:
        iso = d.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
        votes[d.date().isoformat()] = gh.badge_votes_as_of(iso)
        d += timedelta(days=1)
    return {
        "start": start_date, "end": end_date, "warmup_days": warmup_days,
        "anchor": _live_anchor(0.0022),
        "candles": [[c["time"], c["open"], c["high"], c["low"], c["close"]] for c in candles],
        "votes_by_day": votes,
        "gauge_coverage": gh.gauge_coverage(start.date(), end.date()),
    }
'''

new = {}
for path, reps in edits.items():
    t = pathlib.Path(path).read_text()
    for old, rep in reps:
        n = t.count(old)
        if n != 1 and "Small samples" in old:
            print("skipped optional limitations edit"); continue
        if n != 1:
            print(f"FAILED on {path}: expected 1 match, found {n}, for:\n{old[:140]}..."); sys.exit(1)
        t = t.replace(old, rep)
    new[path] = t
new["app/backtest.py"] += APPEND
for p, t in new.items():
    pathlib.Path(p).write_text(t); print("patched", p)
print("\nPatch 11 applied cleanly.")
