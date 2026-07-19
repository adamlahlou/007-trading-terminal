from __future__ import annotations
import logging
import os
from datetime import datetime, timezone
from . import db, oanda_client
from .renko import RenkoState, process_candle

logger = logging.getLogger("007-terminal")

BOX_SIZE = float(os.environ.get("BOX_SIZE", "0.0022"))


def _parse_time(t: str) -> datetime:
    # OANDA times look like "2026-07-19T14:30:00.000000000Z"
    return datetime.strptime(t[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def run_scan() -> dict:
    saved = db.load_state(BOX_SIZE)
    state = RenkoState(
        box_size=BOX_SIZE,
        anchor=saved["anchor"],
        last_close=saved["last_close"],
        direction=saved["direction"],
    )
    last_candle_time = saved["last_candle_time"]

    since_dt = _parse_time(last_candle_time) if last_candle_time else None
    candles = oanda_client.fetch_candles(since=since_dt, count=500, granularity="M30")

    # Guard against re-processing the boundary candle we already handled
    if last_candle_time:
        candles = [c for c in candles if c["time"] > last_candle_time]

    logger.info(f"Fetched {len(candles)} new candles")

    all_new_bricks = []
    for candle in candles:
        new_bricks = process_candle(state, candle)
        all_new_bricks.extend(new_bricks)
        last_candle_time = candle["time"]

    db.append_bricks([{"direction": b.direction, "open": b.open, "close": b.close, "formed_at": b.formed_at} for b in all_new_bricks])
    db.save_state(BOX_SIZE, state.anchor, state.last_close, state.direction, last_candle_time)

    logger.info(f"Scan complete: {len(all_new_bricks)} new bricks, {len(candles)} candles processed")
    return {
        "candles_processed": len(candles),
        "new_bricks": len(all_new_bricks),
        "total_bricks": db.get_brick_count(),
    }
