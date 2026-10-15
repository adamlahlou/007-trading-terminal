#!/usr/bin/env python3
"""Makes /api/backtest accept and forward macro_exit. Run from repo root."""
import pathlib, sys
p = pathlib.Path("app/main.py")
t = p.read_text()
for old, new in [
    ('gauge_set: str = "yield_cot_momentum"):', 'gauge_set: str = "yield_cot_momentum", macro_exit: bool = False):'),
    ('debug_gauges, gauge_set)', 'debug_gauges, gauge_set, macro_exit)'),
]:
    n = t.count(old)
    if n != 1:
        print(f"FAILED: expected 1 match, found {n} for: {old}")
        sys.exit(1)
    t = t.replace(old, new)
p.write_text(t)
print("patched app/main.py")
