"""
A free, self-maintained economic calendar covering the highest-impact
recurring events for GBPUSD -- built from official published schedules
rather than a paid data vendor (Finnhub, FMP, Trading Economics, FXStreet
all gate this specific data type behind paid plans).

Covers:
  - FOMC interest rate decisions (Federal Reserve, published ~1yr ahead)
  - BoE MPC interest rate decisions (Bank of England, published ~1yr ahead)
  - US Non-Farm Payrolls (computed -- always the first Friday of the month)
  - FOMC Minutes (computed -- always 3 weeks after the meeting, per the
    Fed's own published schedule)

Does NOT include forecast/consensus or actual/prior figures -- that's the
part vendors charge for. This gives you reliable event timing + impact only.

To extend: add more entries to FOMC_DATES_2026 / BOE_MPC_DATES_2026 for
future years once published, or add a new recurring-event generator
following the same pattern as `_nfp_occurrences`.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone

# ---- Official published dates, sourced directly from federalreserve.gov
# and bankofengland.co.uk. Update these once the next year's dates are
# published (usually ~1 year in advance). ----

# (date, UK local decision time as "HH:MM")
FOMC_DATES_2026 = [
    (date(2026, 1, 28), "19:00"),
    (date(2026, 3, 18), "19:00"),
    (date(2026, 4, 29), "19:00"),
    (date(2026, 6, 17), "19:00"),
    (date(2026, 7, 29), "19:00"),
    (date(2026, 9, 16), "19:00"),
    (date(2026, 10, 28), "19:00"),
    (date(2026, 12, 9), "19:00"),
]

BOE_MPC_DATES_2026 = [
    (date(2026, 2, 5), "12:00"),
    (date(2026, 3, 19), "12:00"),
    (date(2026, 4, 30), "12:00"),
    (date(2026, 6, 18), "12:00"),
    (date(2026, 7, 30), "12:00"),
    (date(2026, 9, 17), "12:00"),
    (date(2026, 11, 5), "12:00"),
    (date(2026, 12, 17), "12:00"),
]


def _is_bst(d: date) -> bool:
    """UK civil time: BST runs from the last Sunday of March to the last
    Sunday of October. Good enough precision for calendar display purposes."""
    def last_sunday(year: int, month: int) -> date:
        if month == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month + 1, 1)
        d2 = next_month - timedelta(days=1)
        while d2.weekday() != 6:  # Sunday
            d2 -= timedelta(days=1)
        return d2

    bst_start = last_sunday(d.year, 3)
    bst_end = last_sunday(d.year, 10)
    return bst_start <= d < bst_end


def _uk_local_to_utc_iso(d: date, hhmm: str) -> str:
    hour, minute = map(int, hhmm.split(":"))
    offset_hours = 1 if _is_bst(d) else 0
    local_dt = datetime(d.year, d.month, d.day, hour, minute)
    utc_dt = local_dt - timedelta(hours=offset_hours)
    return utc_dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _first_friday(d: date) -> date:
    first = date(d.year, d.month, 1)
    while first.weekday() != 4:  # Friday
        first += timedelta(days=1)
    return first


def _nfp_occurrences(start: date, end: date) -> list[dict]:
    """US Non-Farm Payrolls: always the first Friday of the month, released
    8:30am ET, which is 13:30 UK time for most of the year."""
    events = []
    d = date(start.year, start.month, 1)
    while d <= end:
        first = _first_friday(d)
        if start <= first <= end:
            events.append(
                {
                    "time": _uk_local_to_utc_iso(first, "13:30"),
                    "country": "US",
                    "event": "US Non-Farm Payrolls",
                    "impact": "high",
                    "actual": None,
                    "estimate": None,
                    "prev": None,
                }
            )
        if d.month == 12:
            d = date(d.year + 1, 1, 1)
        else:
            d = date(d.year, d.month + 1, 1)
    return events


def get_rate_decision_datetimes() -> list[tuple[str, datetime]]:
    """Returns [(bank, utc_datetime)] for every known FOMC/BoE decision --
    used to schedule an exact check shortly after each release instead of
    polling blindly, since these times are publicly known in advance."""
    results = []
    for d, hhmm in FOMC_DATES_2026:
        iso = _uk_local_to_utc_iso(d, hhmm)
        results.append(("Fed", datetime.strptime(iso, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)))
    for d, hhmm in BOE_MPC_DATES_2026:
        iso = _uk_local_to_utc_iso(d, hhmm)
        results.append(("BoE", datetime.strptime(iso, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)))
    return results


def get_nfp_datetimes(start: date, end: date) -> list[datetime]:
    """Returns actual UTC datetimes for every NFP release in [start, end]."""
    results = []
    d = date(start.year, start.month, 1)
    while d <= end:
        first = _first_friday(d)
        if start <= first <= end:
            iso = _uk_local_to_utc_iso(first, "13:30")
            results.append(datetime.strptime(iso, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc))
        if d.month == 12:
            d = date(d.year + 1, 1, 1)
        else:
            d = date(d.year, d.month + 1, 1)
    return results


def get_fomc_minutes_dates() -> list[date]:
    """Returns [meeting_end_date] for every FOMC meeting -- the Minutes URL
    uses the MEETING's end date, not the release date, even though the
    Minutes are actually published 3 weeks later. Confirmed directly from
    the Fed's own site: minutes of the regularly scheduled meetings are
    released exactly 3 weeks after the day of the policy decision."""
    return [d for d, _ in FOMC_DATES_2026]


def get_fomc_minutes_release_datetimes() -> list[tuple[date, datetime]]:
    """Returns [(meeting_end_date, release_utc_datetime)] -- the ACTUAL
    release moment (meeting date + 21 days, 2:00pm ET), used for precise
    scheduling. NOTE: 2pm ET = 18:00 UTC assumes EDT (summer, UTC-4); during
    EST (winter, UTC-5) this would need to shift to 19:00 UTC -- not
    auto-adjusted for DST, same caveat as the existing COT Friday scheduling."""
    results = []
    for meeting_date in get_fomc_minutes_dates():
        release_date = meeting_date + timedelta(days=21)
        release_dt = datetime(release_date.year, release_date.month, release_date.day, 18, 0, tzinfo=timezone.utc)
        results.append((meeting_date, release_dt))
    return results


def fetch_calendar(days_ahead: int = 30, days_behind: int = 1) -> list[dict]:
    """Same interface as the vendor clients it replaces:
    returns [{time, country, event, impact, actual, estimate, prev}, ...]
    sorted by time ascending."""
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days_behind)
    end = today + timedelta(days=days_ahead)

    events = []

    for d, hhmm in FOMC_DATES_2026:
        if start <= d <= end:
            events.append(
                {
                    "time": _uk_local_to_utc_iso(d, hhmm),
                    "country": "US",
                    "event": "Fed Interest Rate Decision (FOMC)",
                    "impact": "high",
                    "actual": None,
                    "estimate": None,
                    "prev": None,
                }
            )

    for d, hhmm in BOE_MPC_DATES_2026:
        if start <= d <= end:
            events.append(
                {
                    "time": _uk_local_to_utc_iso(d, hhmm),
                    "country": "GB",
                    "event": "BoE Interest Rate Decision (MPC)",
                    "impact": "high",
                    "actual": None,
                    "estimate": None,
                    "prev": None,
                }
            )

    for meeting_date, release_dt in get_fomc_minutes_release_datetimes():
        release_date = release_dt.date()
        if start <= release_date <= end:
            events.append(
                {
                    "time": release_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "country": "US",
                    "event": "FOMC Minutes",
                    "impact": "high",
                    "actual": None,
                    "estimate": None,
                    "prev": None,
                }
            )

    events.extend(_nfp_occurrences(start, end))

    events.sort(key=lambda x: x["time"])
    return events
