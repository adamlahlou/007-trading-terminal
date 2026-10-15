"""
Daily UK 10-year gilt yield from the Bank of England's Statistical Interactive
Database (series IUDMNPY, 10-year nominal par yield) -- free, official, and
DAILY, unlike the OECD monthly series on FRED, which was ~2.5 months stale
and made the UK leg of the yield spread meaningless day to day.

Used by fred_client.py for both the live yield gauge and the backtest, with
the FRED series kept as a fallback if this ever fails.
"""
from __future__ import annotations
import re
from datetime import datetime, date, timedelta
import requests

BASE_URL = "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp"
SERIES = "IUDMNPY"
_ROW = re.compile(r"^\s*(\d{1,2} [A-Za-z]{3} \d{4})\s*,\s*([0-9.]+)\s*$")


def _fmt(d: date) -> str:
    return d.strftime("%d/%b/%Y")  # BoE wants e.g. 01/Oct/2026


def _fetch(start: date, end: date | None) -> list[tuple[float, str]]:
    """[(yield_pct, 'YYYY-MM-DD')] ascending. Raises if nothing usable came back
    (HTML block page, empty body, etc.) so callers fall back cleanly."""
    resp = requests.get(
        BASE_URL,
        params={
            "csv.x": "yes", "Datefrom": _fmt(start), "Dateto": _fmt(end) if end else "now",
            "SeriesCodes": SERIES, "CSVF": "TN", "UsingCodes": "Y", "VPD": "Y", "VFD": "N",
        },
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    resp.raise_for_status()
    out = []
    for line in resp.text.splitlines():
        m = _ROW.match(line)
        if m:
            d = datetime.strptime(m.group(1), "%d %b %Y").date().isoformat()
            out.append((float(m.group(2)), d))
    if not out:
        raise RuntimeError("Bank of England returned no usable yield rows")
    out.sort(key=lambda x: x[1])
    return out


def fetch_uk_10y_latest() -> tuple[float, str]:
    """(yield_pct, date) of the most recent daily observation."""
    rows = _fetch(date.today() - timedelta(days=20), None)
    return rows[-1]


def fetch_uk_10y_history(start_date: str, end_date: str) -> list[tuple[float, str]]:
    s = datetime.strptime(start_date[:10], "%Y-%m-%d").date()
    e = datetime.strptime(end_date[:10], "%Y-%m-%d").date()
    return _fetch(s, e)
