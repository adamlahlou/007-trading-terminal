#!/usr/bin/env python3
"""Removes the data-release lookahead from the backtest. Run from repo root.

Verified in fred_client.py / cot_client.py: the series are dated by the
period they COVER, not by when they were published (September CPI is dated
2026-09-01 but came out mid-October; the Tuesday COT report is public on
Friday). The backtest treated each number as known from its own date.
This shifts every series forward by an approximate, deliberately
conservative publication lag:
    US 10y yield +1 day, UK 10y yield (monthly OECD) +60 days,
    CPI +45 days, NFP +38 days, COT +4 days.
Results WILL change (data arrives later in the backtest, as it did in life)."""
import pathlib, sys

p = pathlib.Path("app/gauge_history.py")
t = p.read_text()

def once(old, new):
    global t
    n = t.count(old)
    if n != 1:
        print(f"FAILED on gauge_history.py: expected 1 match, found {n}, for:\n{old[:160]}...")
        sys.exit(1)
    t = t.replace(old, new)

once('''def _value_as_of(series''', '''# Approximate PUBLICATION lags (days from the period a number covers to when it
# was actually public). Deliberately on the conservative (late) side. Without
# these, the backtest "knew" e.g. September CPI on 1 September.
US_YIELD_LAG_DAYS = 1
UK_YIELD_LAG_DAYS = 60   # monthly OECD series on FRED, published with a long delay
CPI_LAG_DAYS = 45        # month-start date; released ~mid next month
NFP_LAG_DAYS = 38        # month-start date; released first Friday of next month
COT_LAG_DAYS = 4         # as-of Tuesday; public Friday afternoon


def _shift_series(series: list[tuple[float, str]], days: int) -> list[tuple[float, str]]:
    out = []
    for value, d in series:
        shifted = (datetime.strptime(d[:10], "%Y-%m-%d").date() + timedelta(days=days)).isoformat()
        out.append((value, shifted))
    return out


def _shift_cot(rows: list[dict], days: int) -> list[dict]:
    out = []
    for r in rows:
        r2 = dict(r)
        r2["report_date"] = (datetime.strptime(r["report_date"][:10], "%Y-%m-%d").date() + timedelta(days=days)).isoformat()
        out.append(r2)
    return out


def _value_as_of(series''')

once('''        self.us_yield_series = yields["us"]
        self.uk_yield_series = yields["uk"]
''', '''        self.us_yield_series = _shift_series(yields["us"], US_YIELD_LAG_DAYS)
        self.uk_yield_series = _shift_series(yields["uk"], UK_YIELD_LAG_DAYS)
''')
once('''        self.cpi_series = momentum["cpi"]
        self.nfp_series = momentum["nfp"]
''', '''        self.cpi_series = _shift_series(momentum["cpi"], CPI_LAG_DAYS)
        self.nfp_series = _shift_series(momentum["nfp"], NFP_LAG_DAYS)
''')
once('''        self.cot_series = cot_client.fetch_cot_history(cot_start, end)
''', '''        self.cot_series = _shift_cot(cot_client.fetch_cot_history(cot_start, end), COT_LAG_DAYS)
''')
p.write_text(t)
print("patched app/gauge_history.py")

b = pathlib.Path("app/backtest.py")
bt = b.read_text()
OLD = '''        "UNVERIFIED: CPI / NFP / UK-yield / COT series may be dated by observation period rather than release date, so the backtest may see them days to weeks early (lookahead). Not yet corrected.",
'''
NEW = '''        "Release lags are applied to the FRED/CFTC series (US yield +1d, UK yield +60d, CPI +45d, NFP +38d, COT +4d) -- approximate and deliberately conservative, not exact release dates.",
'''
if bt.count(OLD) != 1:
    print(f"FAILED on backtest.py: expected 1 match, found {bt.count(OLD)}")
    sys.exit(1)
b.write_text(bt.replace(OLD, NEW))
print("patched app/backtest.py")
print("\nPatch applied cleanly.")
