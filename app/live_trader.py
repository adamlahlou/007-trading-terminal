"""
Runs the gate-all-entries trading rule LIVE, on real incoming bricks --
reuses the exact same constants and decision logic from backtest.py (not a
reimplementation) so there's zero drift between what was validated and what
actually runs. Persists position state across scans/restarts, and records
entry/exit events tagged to specific brick sequence numbers so the chart
can show a dot at exactly the right spot.

Entry rule: NO entry is taken -- fresh from flat, a same-direction
continuation, or even a genuine reversal brick -- unless 2+ of 3 gauges
(yield, COT, momentum) agree with that direction.

Trailing rule: breakeven-then-wide -- hold the stop unmoved for 2 bricks,
then move it to EXACT breakeven, then trail a full 2 boxes (44 pips) from
there on. Switched from a tighter 1-box trail after it was closing real
trades on ordinary 1-box pullbacks that weren't genuine reversals.
"""
from __future__ import annotations
from datetime import datetime, timezone
from . import db, notifier
from .backtest import INITIAL_STOP_PIPS, BREAKEVEN_AFTER_BRICKS, WIDE_TRAIL_BOXES, PIP, _continuation_allowed


def _current_gauge_votes() -> dict:
    """Live equivalent of GaugeHistory's point-in-time votes_as_of -- just
    reads today's actual current gauge state instead of a historical
    reconstruction, using the same yield/COT/momentum trio and thresholds."""
    votes = {}
    yield_state = db.get_yield_state()
    if yield_state:
        s = yield_state["spread"]
        votes["yield"] = 1 if s > 0.1 else (-1 if s < -0.1 else 0)
    cot_state = db.get_cot_state()
    if cot_state:
        s = cot_state["gauge_score"]
        votes["cot"] = 1 if s > 0.1 else (-1 if s < -0.1 else 0)
    momentum_state = db.get_momentum_state()
    if momentum_state:
        s = momentum_state["gauge_score"]
        votes["momentum"] = 1 if s > 0.15 else (-1 if s < -0.15 else 0)
    return votes


def _gauges_support(direction: int) -> bool:
    votes = _current_gauge_votes()
    return _continuation_allowed(votes, direction, "majority")


def process_scan(candle_brick_groups: list[tuple[dict, list[tuple[object, int]]]], box_size: float) -> list[dict]:
    """
    candle_brick_groups: [(candle, [(brick, seq), ...]), ...] in chronological
    order for this scan. box_size: the live Renko box size (kept in sync
    with scanner.BOX_SIZE, not hardcoded). Returns the list of event dicts recorded.
    """
    saved = db.get_live_trade_state()
    position = saved["position"] if saved else 0
    entry_price = saved["entry_price"] if saved else None
    stop_price = saved["stop_price"] if saved else None
    favorable_bricks = saved["favorable_bricks"] if saved else 0
    last_closed_direction = saved["last_closed_direction"] if saved else None

    initial_stop_dist = INITIAL_STOP_PIPS * PIP
    events = []
    now_iso = datetime.now(timezone.utc).isoformat()

    def record(event_type, direction, price, event_time, brick_seq, reason):
        events.append({
            "event_type": event_type, "direction": direction, "price": price,
            "event_time": event_time, "brick_seq": brick_seq, "reason": reason,
        })

    def close(exit_price, exit_time, brick_seq, reason):
        nonlocal position, entry_price, stop_price, favorable_bricks, last_closed_direction
        record("exit", position, exit_price, exit_time, brick_seq, reason)
        last_closed_direction = position
        position = 0
        entry_price = None
        stop_price = None
        favorable_bricks = 0

    def open_position(direction, price, event_time, brick_seq, reason):
        nonlocal position, entry_price, stop_price, favorable_bricks
        position = direction
        entry_price = price
        stop_price = price - initial_stop_dist if direction == 1 else price + initial_stop_dist
        favorable_bricks = 0
        record("entry", direction, price, event_time, brick_seq, reason)

    for candle, bricks_with_seq in candle_brick_groups:
        # Stop-check against this candle's real price range, before this
        # candle's new bricks are processed -- same order as the backtest.
        if position == 1 and candle["low"] <= stop_price:
            close(stop_price, candle["time"], None, "stop")
        elif position == -1 and candle["high"] >= stop_price:
            close(stop_price, candle["time"], None, "stop")

        for b, seq in bricks_with_seq:
            if position == 0:
                if not _gauges_support(b.direction):
                    continue  # gauges don't back this direction -- no trade, stay flat
                open_position(b.direction, b.close, b.formed_at, seq, "entry")
            elif b.direction == position:
                favorable_bricks += 1
                # breakeven-then-wide: hold still for 2 bricks, then move to
                # EXACT breakeven, then trail a full 2 boxes (44 pips) from
                # there -- avoids closing out on an ordinary 1-box pullback
                # that isn't a genuine reversal.
                if favorable_bricks == BREAKEVEN_AFTER_BRICKS:
                    stop_price = entry_price
                elif favorable_bricks > BREAKEVEN_AFTER_BRICKS:
                    trail_dist = WIDE_TRAIL_BOXES * box_size
                    candidate_stop = b.close - trail_dist if position == 1 else b.close + trail_dist
                    stop_price = max(stop_price, candidate_stop) if position == 1 else min(stop_price, candidate_stop)
            else:
                # Genuine reversal brick -- close the old position regardless
                # (exiting an invalidated position is never gated). Exit at
                # whichever price is more protective between the tracked
                # stop (which may already be at breakeven or the wide trail)
                # and the reversal brick's CLOSE (the true 2-box/44-pip
                # point a genuine reversal requires) -- using the brick's
                # raw open understated the real adverse move.
                exit_price = max(stop_price, b.close) if position == 1 else min(stop_price, b.close)
                close(exit_price, b.formed_at, seq, "reversal")
                if _gauges_support(b.direction):
                    open_position(b.direction, b.close, b.formed_at, seq, "reversal_entry")

    db.save_live_trade_state(position, entry_price, stop_price, favorable_bricks, last_closed_direction, now_iso)
    for e in events:
        db.add_live_trade_event(
            e["event_type"], e["direction"], e["price"], e["event_time"],
            e["brick_seq"], e["reason"], now_iso,
        )
        try:
            notifier.send_trade_signal_notification(e)
        except Exception:
            pass  # a failed signal email should never break the scan
    return events
