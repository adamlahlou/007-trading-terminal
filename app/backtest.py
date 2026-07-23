"""
Backtests the Renko trading rules (no gauges) against real historical
GBP/USD price -- reuses the exact same renko.py engine that runs live, so
"what would have happened" is built from the same brick-construction logic
you're actually trading, not a separate approximation.

Trade rules being tested:
  - Enter flat -> take every new brick's direction as an entry
  - Initial stop: 52 pips (fixed, per his stated preference for a bit more breathing room)
  - Trailing stop: the stop stays completely unmoved until the trade has
    captured 2 favorable bricks; from that point on, it trails tightly at
    1 brick behind the latest favorable brick close
  - No fixed take-profit -- only the trailing stop closes a winning trade

Honest limitations (worth knowing before trusting the numbers):
  - We only have 15-min OHLC, not tick data, so a stop-hit is detected by
    checking each candle's high/low against the stop level -- this is a
    reasonable approximation, not perfect intra-candle precision
  - When multiple bricks form from a single candle, they're processed in
    the same path-order the live engine already uses (open->low->high->close
    or open->high->low->close), not literally simultaneously
  - 52 pips is used as a single fixed number for a clean test
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from . import oanda_client
from .renko import RenkoState, process_candle
from .gauge_history import GaugeHistory

PIP = 0.0001
INITIAL_STOP_PIPS = 52
HOLD_BRICKS_BEFORE_TRAILING = 2  # don't move the stop at all until this many favorable bricks
TRAIL_BOXES = 1.0                 # once trailing starts, trail this tight


def _continuation_allowed(votes: dict, direction: int, mode: str) -> bool:
    """votes: {gauge_name: -1/0/1}. direction: 1 (long) or -1 (short) --
    the direction we'd be continuing in. Returns whether the gauges
    support allowing a same-direction re-entry instead of requiring a
    genuine reversal."""
    if not votes:
        return False
    if mode == "majority":
        matching = sum(1 for v in votes.values() if v == direction)
        return matching >= 2
    if mode == "momentum_weighted":
        score = 0
        for name, v in votes.items():
            weight = 2 if name == "momentum" else 1
            if v == direction:
                score += weight
            elif v == -direction:
                score -= weight
        return score >= 2
    return False


def run_backtest(
    days: int = 45,
    box_size: float = 0.0022,
    require_reversal_to_reenter: bool = False,
    continuation_override: str | None = None,
    gate_all_entries: bool = False,
) -> dict:
    """
    require_reversal_to_reenter=False (default): enter on any new brick
    while flat, same direction or not -- matches "enter on every brick".

    require_reversal_to_reenter=True: after a position closes (stop or
    otherwise), don't re-enter on a brick continuing the SAME direction you
    just exited -- only re-enter once a genuine opposite-direction (reversal)
    brick appears, UNLESS continuation_override says otherwise (see below).

    continuation_override: only meaningful when require_reversal_to_reenter
    is True. "majority" allows a same-direction re-entry anyway if 2+ of the
    3 historically-reconstructed gauges (yield, COT, momentum) agree with
    that direction. "momentum_weighted" does the same but counts momentum's
    vote double. None disables the override entirely.

    gate_all_entries=True: a stricter, independent filter -- NO entry is
    taken at all (whether a brand new flat-start entry, a same-direction
    continuation, or even a genuine reversal brick) unless 2+ of the 3
    gauges agree with that direction. This applies universally, on top of
    whatever the require_reversal_to_reenter/continuation_override settings
    are doing -- it's the strictest of the three knobs.
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)

    candles = oanda_client.fetch_candles(since=start, until=now, granularity="M15")
    if not candles:
        raise RuntimeError("No candles returned for the requested backtest window")

    gauge_hist = None
    if (require_reversal_to_reenter and continuation_override) or gate_all_entries:
        gauge_hist = GaugeHistory(start.date(), now.date())

    state = RenkoState(box_size=box_size)
    initial_stop_dist = INITIAL_STOP_PIPS * PIP

    position = None  # None, 1 (long), or -1 (short)
    entry_price = None
    stop_price = None
    favorable_bricks = 0
    last_closed_direction = None
    trades = []

    def close_trade(exit_price, exit_time, reason):
        nonlocal position, entry_price, stop_price, favorable_bricks, last_closed_direction
        pips = (exit_price - entry_price) / PIP if position == 1 else (entry_price - exit_price) / PIP
        trades.append({
            "direction": "long" if position == 1 else "short",
            "entry_price": round(entry_price, 5),
            "exit_price": round(exit_price, 5),
            "exit_time": exit_time,
            "pips": round(pips, 1),
            "reason": reason,
        })
        last_closed_direction = position
        position = None
        entry_price = None
        stop_price = None
        favorable_bricks = 0

    def gauges_support(direction, at_time):
        if gauge_hist is None:
            return True
        votes = gauge_hist.votes_as_of(at_time)
        return _continuation_allowed(votes, direction, "majority")

    for candle in candles:
        # 1) Check if the currently open position would have been stopped
        # out during this candle, BEFORE processing any new bricks from it.
        if position == 1 and candle["low"] <= stop_price:
            close_trade(stop_price, candle["time"], "stop")
        elif position == -1 and candle["high"] >= stop_price:
            close_trade(stop_price, candle["time"], "stop")

        # 2) Feed the candle through the same live brick engine
        new_bricks = process_candle(state, candle)

        # 3) Walk each newly formed brick (already in correct path order)
        for b in new_bricks:
            if position is None:
                blocked = (
                    require_reversal_to_reenter
                    and last_closed_direction is not None
                    and b.direction == last_closed_direction
                )
                if blocked and gauge_hist is not None and not gate_all_entries:
                    votes = gauge_hist.votes_as_of(candle["time"])
                    if _continuation_allowed(votes, b.direction, continuation_override):
                        blocked = False  # gauges support the continuation -- allow it anyway
                if blocked:
                    continue  # same-direction continuation after a close -- wait for a genuine reversal instead
                if gate_all_entries and not gauges_support(b.direction, candle["time"]):
                    continue  # gauges don't back this direction -- no trade, stay flat
                position = b.direction
                entry_price = b.close
                stop_price = entry_price - initial_stop_dist if position == 1 else entry_price + initial_stop_dist
                favorable_bricks = 0
            elif b.direction == position:
                favorable_bricks += 1
                if favorable_bricks >= HOLD_BRICKS_BEFORE_TRAILING:
                    trail_dist = TRAIL_BOXES * box_size
                    candidate_stop = b.close - trail_dist if position == 1 else b.close + trail_dist
                    if position == 1:
                        stop_price = max(stop_price, candidate_stop)
                    else:
                        stop_price = min(stop_price, candidate_stop)
                # else: still within the hold period -- stop stays put at its initial level
            else:
                # Reversal brick against an open position -- shouldn't
                # normally happen since the stop-check above should have
                # already caught it, but handle defensively: close at the
                # brick's open (conservative) regardless of gauges (exiting
                # the old, invalidated position is not gated), then only
                # open the new direction if gate_all_entries permits it.
                close_trade(b.open, b.formed_at, "reversal_slip")
                if gate_all_entries and not gauges_support(b.direction, b.formed_at):
                    continue  # exit taken, but don't flip into the new direction -- stay flat
                position = b.direction
                entry_price = b.close
                stop_price = entry_price - initial_stop_dist if position == 1 else entry_price + initial_stop_dist
                favorable_bricks = 0

    # Mark-to-market anything still open at the end of the window
    if position is not None:
        last_close = candles[-1]["close"]
        close_trade(last_close, candles[-1]["time"], "end_of_window")

    return _summarize(trades, days, require_reversal_to_reenter, continuation_override, gate_all_entries)


def _summarize(trades: list[dict], days: int, require_reversal_to_reenter: bool, continuation_override: str | None, gate_all_entries: bool = False) -> dict:
    if gate_all_entries:
        mode = "gate_all_entries_2of3"
    elif require_reversal_to_reenter and continuation_override:
        mode = f"reversal_only_with_{continuation_override}_override"
    elif require_reversal_to_reenter:
        mode = "reversal_only_reentry"
    else:
        mode = "any_brick_reentry"

    if not trades:
        return {"days": days, "mode": mode, "total_trades": 0, "message": "No trades triggered in this window"}

    wins = [t for t in trades if t["pips"] > 0]
    losses = [t for t in trades if t["pips"] <= 0]
    total_pips = sum(t["pips"] for t in trades)
    win_rate = round(len(wins) / len(trades) * 100, 1)
    avg_win = round(sum(t["pips"] for t in wins) / len(wins), 1) if wins else 0
    avg_loss = round(sum(t["pips"] for t in losses) / len(losses), 1) if losses else 0
    gross_win = sum(t["pips"] for t in wins)
    gross_loss = abs(sum(t["pips"] for t in losses))
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else None

    # running max drawdown in pips
    running = 0
    peak = 0
    max_dd = 0
    for t in trades:
        running += t["pips"]
        peak = max(peak, running)
        max_dd = min(max_dd, running - peak)

    return {
        "days": days,
        "mode": mode,
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": win_rate,
        "total_pips": round(total_pips, 1),
        "avg_win_pips": avg_win,
        "avg_loss_pips": avg_loss,
        "profit_factor": profit_factor,
        "max_drawdown_pips": round(max_dd, 1),
        "trades": trades,
    }
