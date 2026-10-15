#!/usr/bin/env python3
"""Swaps the stale monthly UK 10y (OECD via FRED, ~2.5 months old) for the Bank
of England's DAILY 10y gilt yield, in both the live yield gauge and the
backtest, keeping the FRED series as an automatic fallback.

Needs apply_patch9 already applied. Run from the repo root with boe_client.py
sitting next to this script. Nothing is written unless every edit matches once."""
import pathlib, shutil, sys

if not pathlib.Path("boe_client.py").exists():
    print("FAILED: boe_client.py not found next to apply_patch10.py"); sys.exit(1)
if pathlib.Path("app/boe_client.py").exists():
    print("FAILED: app/boe_client.py already exists -- looks already applied"); sys.exit(1)

edits = {
    "app/fred_client.py": [
        ("import os\nimport requests\n", "import os\nimport requests\nfrom . import boe_client\n"),
        ('''    us = _fetch_latest(US_10Y_SERIES)
    uk = _fetch_latest(UK_10Y_SERIES)
''', '''    us = _fetch_latest(US_10Y_SERIES)
    # Daily Bank of England gilt yield first; the monthly OECD series on FRED
    # (months stale) only as a fallback if the BoE fetch fails.
    try:
        uk = boe_client.fetch_uk_10y_latest()
    except Exception:
        uk = _fetch_latest(UK_10Y_SERIES)
'''),
        ('''    return {
        "us": _fetch_range(US_10Y_SERIES, start_date, end_date),
        "uk": _fetch_range(UK_10Y_SERIES, start_date, end_date),
    }
''', '''    us = _fetch_range(US_10Y_SERIES, start_date, end_date)
    try:
        uk = boe_client.fetch_uk_10y_history(start_date, end_date)
        uk_source = "boe"
    except Exception:
        uk = _fetch_range(UK_10Y_SERIES, start_date, end_date)
        uk_source = "fred"
    return {"us": us, "uk": uk, "uk_source": uk_source}
'''),
    ],
    "app/gauge_history.py": [
        ('        self.uk_yield_series = _shift_series(yields["uk"], UK_YIELD_LAG_DAYS)\n',
         '        # BoE daily data is public next working day; the FRED/OECD monthly fallback lags ~60 days.\n'
         '        uk_lag = UK_YIELD_LAG_DAYS if yields.get("uk_source", "fred") == "fred" else US_YIELD_LAG_DAYS\n'
         '        self.uk_yield_series = _shift_series(yields["uk"], uk_lag)\n'),
    ],
}

new_texts = {}
for path, reps in edits.items():
    text = pathlib.Path(path).read_text()
    for old, new in reps:
        n = text.count(old)
        if n != 1:
            print(f"FAILED on {path}: expected 1 match, found {n}, for:\n{old[:160]}...")
            sys.exit(1)
        text = text.replace(old, new)
    new_texts[path] = text

for path, text in new_texts.items():
    pathlib.Path(path).write_text(text)
    print("patched", path)
shutil.move("boe_client.py", "app/boe_client.py")
print("added app/boe_client.py")
print("\nPatch applied cleanly.")
