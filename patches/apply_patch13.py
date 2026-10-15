#!/usr/bin/env python3
"""Patch 13 -- backtest can now simulate how Adam actually trades.

New backtest options (defaults unchanged, so nothing existing moves):
  exit_mode=ride   fixed stop only: no breakeven, no trailing, no exit on a
                   reversal brick -- the trade just rides until its stop is hit
  stop_pips=52     initial stop distance (try 40, 52, 70 ...)
Example:
  /api/backtest?start_date=2026-05-01&end_date=2026-10-14&exit_mode=ride
Needs patches 11 and 12 applied. Nothing is written unless every edit matches once."""
import pathlib, sys

edits = {
 "app/backtest.py": [
  ("    spread_pips: float = 1.0,\n    warmup_days: int = 14,\n) -> dict:\n",
   "    spread_pips: float = 1.0,\n    warmup_days: int = 14,\n    exit_mode: str = \"system\",\n    stop_pips: float = INITIAL_STOP_PIPS,\n) -> dict:\n"),
  ("    state = RenkoState(box_size=box_size, anchor=live_anchor)\n    initial_stop_dist = INITIAL_STOP_PIPS * PIP\n", "    state = RenkoState(box_size=box_size, anchor=live_anchor)\n    initial_stop_dist = stop_pips * PIP\n"),
  ("            elif b.direction == position:\n                favorable_bricks += 1\n",
   "            elif b.direction == position:\n                favorable_bricks += 1\n                if exit_mode == \"ride\":\n                    continue  # ride: the stop never moves\n"),
  ("                exit_price = max(stop_price, b.close) if position == 1 else min(stop_price, b.close)\n                close_trade(exit_price, b.formed_at, \"reversal_slip\")",
   "                if exit_mode == \"ride\":\n                    continue  # ride: a reversal brick does not close the trade, only the stop does\n                exit_price = max(stop_price, b.close) if position == 1 else min(stop_price, b.close)\n                close_trade(exit_price, b.formed_at, \"reversal_slip\")"),
  ("    result[\"spread_pips\"] = spread_pips\n",
   "    result[\"spread_pips\"] = spread_pips\n    result[\"exit_mode\"] = exit_mode\n    result[\"stop_pips\"] = stop_pips\n"),
 ],
 "app/main.py": [
  ("spread_pips: float = 1.0, warmup_days: int = 14):\n    try:\n        result = await asyncio.to_thread(backtest.run_backtest,",
   "spread_pips: float = 1.0, warmup_days: int = 14, exit_mode: str = \"system\", stop_pips: float = 52.0):\n    try:\n        result = await asyncio.to_thread(backtest.run_backtest,"),
  ("gauge_set, macro_exit, spread_pips, warmup_days)", "gauge_set, macro_exit, spread_pips, warmup_days, exit_mode, stop_pips)"),
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
print("\nPatch 13 applied cleanly.")
