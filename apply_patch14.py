#!/usr/bin/env python3
"""Patch 14 -- AI-verdict replay + reversal-only entries in the backtest.

 * app/macro_replay.py (new): asks the live model for one call per historical day
   (07:00 UTC, using only gauge scores + price known by then), cached in the DB.
 * /api/ai-replay?start_date=&end_date=  : fills the cache 25 days per request.
 * backtest options: gauge_set=ai_verdict  (gate = the replayed AI call)
                     exit_mode=ride_trail (no pullback exit, but breakeven + wide trail kept: use with trailing_mode=breakeven_then_wide)
                     entry_type=reversal  (only enter on a brick that reverses direction)
Needs patches 11-13 and macro_replay.py next to this script. Nothing is written
unless every edit matches once."""
import pathlib, shutil, sys

if not pathlib.Path("macro_replay.py").exists():
    print("FAILED: macro_replay.py not found next to this script"); sys.exit(1)
if pathlib.Path("app/macro_replay.py").exists():
    print("FAILED: app/macro_replay.py already exists -- looks already applied"); sys.exit(1)

edits = {
 "app/backtest.py": [
  ("from .gauge_history import GaugeHistory, macro_verdict\n", "from .gauge_history import GaugeHistory, macro_verdict\nfrom . import macro_replay\n"),
  ("    if gauge_set == \"all_gauges_badge\":\n        # Built directly from Adam's explicit instruction",
   "    if gauge_set == \"ai_verdict\":\n        v = macro_replay.cached_vote(at_time)\n        return {} if v is None else {\"ai\": v}\n    if gauge_set == \"all_gauges_badge\":\n        # Built directly from Adam's explicit instruction"),
  ("    exit_mode: str = \"system\",\n    stop_pips: float = INITIAL_STOP_PIPS,\n) -> dict:\n",
   "    exit_mode: str = \"system\",\n    stop_pips: float = INITIAL_STOP_PIPS,\n    entry_type: str = \"any\",\n) -> dict:\n"),
  ("        if gauge_set == \"all_gauges_badge\":\n            # Macro-verdict matching",
   "        if gauge_set in (\"all_gauges_badge\", \"ai_verdict\"):\n            # Macro-verdict matching"),
  ("    last_closed_direction = None\n    trades = []\n",
   "    last_closed_direction = None\n    last_brick_dir = None  # direction of the previous brick (warm-up included)\n    trades = []\n"),
  ("            process_candle(state, candle)\n            continue\n",
   "            for _b in process_candle(state, candle):\n                last_brick_dir = _b.direction\n            continue\n"),
  ("        for b in new_bricks:\n            if position is None:\n                blocked = (",
   "        for b in new_bricks:\n            prev_dir, last_brick_dir = last_brick_dir, b.direction\n            if position is None:\n                if entry_type == \"reversal\" and not (prev_dir is not None and prev_dir != b.direction):\n                    continue  # only enter on a brick that reverses the previous direction\n                blocked = ("),
  ("    result[\"exit_mode\"] = exit_mode\n", "    result[\"exit_mode\"] = exit_mode\n    result[\"entry_type\"] = entry_type\n"),
  ("    if gauge_set == \"all_gauges_badge\":\n        result[\"mode\"] = result[\"mode\"].replace(", "    if gauge_set in (\"all_gauges_badge\", \"ai_verdict\"):\n        result[\"mode\"] = result[\"mode\"].replace("),
  ("                if exit_mode == \"ride\":\n                    continue  # ride: a reversal brick does not close",
   "                if exit_mode in (\"ride\", \"ride_trail\"):\n                    continue  # ride: a reversal brick does not close"),
 ],
 "app/main.py": [
  ("exit_mode: str = \"system\", stop_pips: float = 52.0):", "exit_mode: str = \"system\", stop_pips: float = 52.0, entry_type: str = \"any\"):"),
  ("macro_exit, spread_pips, warmup_days, exit_mode, stop_pips)", "macro_exit, spread_pips, warmup_days, exit_mode, stop_pips, entry_type)"),
  ("@app.get(\"/api/backtest-brickcheck\")",
   "@app.get(\"/api/ai-replay\")\nasync def api_ai_replay(start_date: str, end_date: str, max_new: int = 25):\n    try:\n        from . import macro_replay\n        return JSONResponse(await asyncio.to_thread(macro_replay.run_replay, start_date, end_date, max_new))\n    except Exception as e:\n        logger.error(f\"AI replay failed: {e}\")\n        return JSONResponse({\"error\": str(e)}, status_code=502)\n\n\n@app.get(\"/api/backtest-brickcheck\")"),
 ],
}
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
shutil.move("macro_replay.py", "app/macro_replay.py"); print("added app/macro_replay.py")
print("\nPatch 14 applied cleanly.")
