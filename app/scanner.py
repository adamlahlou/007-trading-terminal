from __future__ import annotations
import logging
import os
import threading
from datetime import datetime, timezone
from . import db, oanda_client, calendar_schedule, notifier, fred_client, marketaux_client, cot_client
from .renko import RenkoState, process_candle

logger = logging.getLogger("007-terminal")

BOX_SIZE = float(os.environ.get("BOX_SIZE", "0.0022"))

# Prevents overlapping scans -- e.g. a cold-start wake-up triggering both the
# startup task and the /api/cron/scan endpoint's own scan nearly
# simultaneously, which could otherwise double-process the same candles and
# send duplicate email alerts for the same brick.
_scan_lock = threading.Lock()


def _parse_time(t: str) -> datetime:
    # OANDA times look like "2026-07-19T14:30:00.000000000Z"
    return datetime.strptime(t[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def run_scan() -> dict:
    if not _scan_lock.acquire(blocking=False):
        logger.info("Scan already in progress, skipping this trigger")
        return {"skipped": True, "reason": "scan already in progress"}
    try:
        return _run_scan_locked()
    finally:
        _scan_lock.release()


def _gauge_verdict(score, threshold=0.15):
    """-1/0/1 for bearish/neutral/bullish, matching the same thresholds
    used on the dashboard so email confluence matches what you'd see there."""
    if score is None:
        return 0
    if score > threshold:
        return 1
    if score < -threshold:
        return -1
    return 0


def get_gauge_verdicts() -> list[tuple[str, int]]:
    """Returns [(gauge_name, verdict)] for every gauge that currently has
    data, verdict in {-1, 0, 1}. Only includes gauges with real data --
    a gauge that's never successfully fetched isn't counted either way."""
    verdicts = []

    yield_state = db.get_yield_state()
    if yield_state:
        verdicts.append(("Yield", _gauge_verdict(yield_state["spread"], 0.1)))

    news_state = db.get_news_state()
    if news_state:
        verdicts.append(("News", _gauge_verdict(news_state["score"], 0.15)))

    cot_state = db.get_cot_state()
    if cot_state:
        verdicts.append(("COT", _gauge_verdict(cot_state["gauge_score"], 0.1)))

    momentum_state = db.get_momentum_state()
    if momentum_state:
        verdicts.append(("Momentum", _gauge_verdict(momentum_state["gauge_score"], 0.15)))

    geo_state = db.get_geo_state()
    if geo_state:
        verdicts.append(("Geo", _gauge_verdict(geo_state["gauge_score"], 0.15)))

    return verdicts


def _run_scan_locked() -> dict:
    saved = db.load_state(BOX_SIZE)
    state = RenkoState(
        box_size=BOX_SIZE,
        anchor=saved["anchor"],
        last_close=saved["last_close"],
        direction=saved["direction"],
    )
    last_candle_time = saved["last_candle_time"]

    since_dt = _parse_time(last_candle_time) if last_candle_time else None
    candles = oanda_client.fetch_candles(since=since_dt, count=500, granularity="M15")

    # Guard against re-processing the boundary candle we already handled
    if last_candle_time:
        candles = [c for c in candles if c["time"] > last_candle_time]

    logger.info(f"Fetched {len(candles)} new candles")

    all_new_bricks = []
    for candle in candles:
        new_bricks = process_candle(state, candle)
        all_new_bricks.extend(new_bricks)
        last_candle_time = candle["time"]

    brick_dicts = [{"direction": b.direction, "open": b.open, "close": b.close, "formed_at": b.formed_at} for b in all_new_bricks]
    db.append_bricks(brick_dicts)
    db.save_state(BOX_SIZE, state.anchor, state.last_close, state.direction, last_candle_time)

    if brick_dicts:
        gauge_verdicts = get_gauge_verdicts()
        for b in brick_dicts:
            matching = sum(1 for _, v in gauge_verdicts if v == b["direction"])
            b["confluence"] = f"{matching}/{len(gauge_verdicts)}"
        notifier.send_brick_notification(brick_dicts)

    logger.info(f"Scan complete: {len(all_new_bricks)} new bricks, {len(candles)} candles processed")
    return {
        "candles_processed": len(candles),
        "new_bricks": len(all_new_bricks),
        "total_bricks": db.get_brick_count(),
    }


def run_calendar_refresh() -> dict:
    events = calendar_schedule.fetch_calendar(days_ahead=45, days_behind=1)
    db.replace_calendar_events(events)
    logger.info(f"Calendar refresh: {len(events)} events cached")
    return {"events_cached": len(events)}


def run_yield_refresh() -> dict:
    result = fred_client.fetch_yield_differential()
    now = datetime.now(timezone.utc).isoformat()
    db.save_yield_state(
        result["us_yield"], result["us_date"],
        result["uk_yield"], result["uk_date"],
        result["spread"], now,
    )
    logger.info(f"Yield refresh: US {result['us_yield']}%, UK {result['uk_yield']}%, spread {result['spread']}")
    return result


def run_news_refresh() -> dict:
    result = marketaux_client.fetch_combined_sentiment()
    now = datetime.now(timezone.utc).isoformat()
    db.save_news_state(
        result["gauge_score"], result["article_count"], result["headlines"], now,
        gbp_score=result["gbp_score"], usd_score=result["usd_score"],
    )
    logger.info(f"News refresh: gauge {result['gauge_score']} (GBP {result['gbp_score']}, USD {result['usd_score']}) across {result['article_count']} articles")
    return result


def run_cot_refresh() -> dict:
    result = cot_client.fetch_cot_data()
    now = datetime.now(timezone.utc).isoformat()
    db.save_cot_state(
        result["report_date"], result["lev_long"], result["lev_short"],
        result["lev_net"], result["prior_net"], result["gauge_score"], now,
    )
    logger.info(f"COT refresh: report {result['report_date']}, lev net {result['lev_net']}, gauge {result['gauge_score']}")
    return result


def run_momentum_refresh() -> dict:
    result = fred_client.fetch_data_momentum()
    now = datetime.now(timezone.utc).isoformat()
    db.save_momentum_state(
        result["cpi_yoy"], result["cpi_date"],
        result["nfp_change"], result["nfp_date"],
        result["gauge_score"], now,
    )
    logger.info(f"Momentum refresh: CPI YoY {result['cpi_yoy']}%, NFP change {result['nfp_change']}k, gauge {result['gauge_score']}")
    return result


def run_geo_refresh() -> dict:
    result = marketaux_client.fetch_geopolitical_sentiment()
    now = datetime.now(timezone.utc).isoformat()
    db.save_geo_state(result["gauge_score"], result["article_count"], result["headlines"], now)
    logger.info(f"Geopolitical refresh: gauge {result['gauge_score']} across {result['article_count']} articles")
    return result
