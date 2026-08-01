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
TRAIL_BOXES = 1.0                 # once trailing starts, trail this tight (the "tight" mode)

# "breakeven_then_wide" mode: at exactly 2 favorable bricks, move the stop
# to breakeven (not just start trailing) -- then trail 2 boxes (44 pips)
# behind from there on, matching the fact that a genuine Renko reversal
# itself requires a 2-box (44 pip) adverse move. Trailing tighter than that
# (1 box/22 pips) doesn't meaningfully protect against anything a real
# reversal wouldn't already trigger on its own.
BREAKEVEN_AFTER_BRICKS = 2
WIDE_TRAIL_BOXES = 2.0

# "gradual_lock" mode: same as breakeven_then_wide, but adds an earlier
# stage -- after just 1 favorable brick (not yet the full 2 needed for
# breakeven), lock in a small amount of real profit instead of leaving the
# trade completely unprotected until the 2-brick threshold. The stop only
# ever ratchets forward (via max/min), so this early lock is never undone
# once the 2-brick breakeven/wide-trail stage takes over -- it just gets
# superseded once that's actually better.
EARLY_LOCK_AFTER_BRICKS = 1
EARLY_LOCK_PIPS = 5.0

# "simple_22_33" mode: trail 1 box (22 pips) from the very start, no hold
# period -- then widen to 1.5 boxes (33 pips) once the position has reached
# 2 boxes (44 pips) of profit, giving a winning trade a bit more room once
# it's proven itself, without the "hold completely still" period the other
# modes use.
SIMPLE_TRAIL_BOXES = 1.0
SIMPLE_WIDEN_AFTER_PIPS = 44.0
SIMPLE_WIDENED_TRAIL_BOXES = 1.5


def _get_votes(gauge_hist, at_time: str, gauge_set: str) -> dict:
    """gauge_set='yield_cot_momentum' (default, the originally validated
    set), 'momentum_rate_tone' (momentum + real historical rate-tone
    instead of yield/COT), or 'news_geo_rate_tone' -- the 3 fast,
    event-driven gauges (real weekly-bucketed news/geo reconstruction +
    real rate-tone), dropping yield/COT/momentum entirely."""
    if gauge_set == "momentum_rate_tone":
        votes = gauge_hist.votes_as_of(at_time, include_rate_tone=True)
        return {k: v for k, v in votes.items() if k in ("momentum", "rate_tone")}
    if gauge_set == "news_geo_rate_tone":
        votes = gauge_hist.votes_as_of(at_time, include_rate_tone=True, include_news_geo=True)
        return {k: v for k, v in votes.items() if k in ("news", "geo", "rate_tone")}
    return gauge_hist.votes_as_of(at_time)


def _continuation_allowed(votes: dict, direction: int, mode: str, threshold: int = 2) -> bool:
    """votes: {gauge_name: -1/0/1}. direction: 1 (long) or -1 (short) --
    the direction we'd be continuing in. threshold: how many gauges need
    to agree (1 or 2, out of the 3 -- yield/COT/momentum, never
    geopolitical, which was never part of this validated set). Returns
    whether the gauges support allowing a same-direction re-entry instead
    of requiring a genuine reversal."""
    if not votes:
        return False
    if mode == "majority":
        matching = sum(1 for v in votes.values() if v == direction)
        return matching >= threshold
    if mode == "momentum_weighted":
        score = 0
        for name, v in votes.items():
            weight = 2 if name == "momentum" else 1
            if v == direction:
                score += weight
            elif v == -direction:
                score -= weight
        return score >= threshold
    return False


def run_backtest(
    days: int = 45,
    box_size: float = 0.0022,
    require_reversal_to_reenter: bool = False,
    continuation_override: str | None = None,
    gate_all_entries: bool = False,
    trailing_mode: str = "tight",
    gate_threshold: int = 2,
    start_date: str | None = None,
    end_date: str | None = None,
    debug_gauges: bool = False,
    gauge_set: str = "yield_cot_momentum",
) -> dict:
    """
    start_date/end_date (YYYY-MM-DD): test a SPECIFIC historical window
    instead of the default rolling "last `days` days from now" -- e.g. a
    particular month, so results from different periods can be compared
    or added together rather than always looking at the same recent stretch.
    When both are given, they override `days` entirely.

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

    trailing_mode: "tight" (default) holds the stop unmoved for 2 bricks,
    then trails 1 box behind from there. "breakeven_then_wide" holds the
    stop unmoved for 2 bricks, then moves it EXACTLY to breakeven at that
    point, then trails 2 boxes (44 pips) behind from the 3rd favorable
    brick onward -- deliberately matching the 44 pip move a genuine Renko
    reversal itself requires, on the reasoning that trailing any tighter
    than that isn't meaningfully protective against anything a real
    reversal wouldn't already catch.
    """
    if start_date and end_date:
        start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        now = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        days = (now - start).days  # for the results summary, so it reflects the real span
    else:
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)

    candles = oanda_client.fetch_candles(since=start, until=now, granularity="M15")
    if not candles:
        raise RuntimeError("No candles returned for the requested backtest window")

    gauge_hist = None
    if (require_reversal_to_reenter and continuation_override) or gate_all_entries:
        gauge_hist = GaugeHistory(start.date(), now.date())

    gauge_samples = None
    if debug_gauges and gauge_hist is not None:
        # Sample every 3 days across the window so we can SEE what the
        # gauges actually said throughout, rather than assuming either
        # "genuine divergence" or "a bug" -- lets that be checked directly.
        gauge_samples = []
        sample_date = start
        while sample_date <= now:
            iso = sample_date.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
            votes = _get_votes(gauge_hist, iso, gauge_set)
            raw_scores = {
                "yield": gauge_hist.yield_score(iso),
                "cot": gauge_hist.cot_score(iso),
                "momentum": gauge_hist.momentum_score(iso),
                "rate_tone": gauge_hist.rate_tone_score(iso),
            }
            if gauge_set == "news_geo_rate_tone":
                raw_scores["geo"] = gauge_hist.geopolitical_score(iso)
                raw_scores["news"] = gauge_hist.news_score(iso)
                raw_scores["geo_article_count"] = gauge_hist.geo_article_count(iso)
                raw_scores["news_article_counts"] = gauge_hist.news_article_counts(iso)
            gauge_samples.append({"date": sample_date.date().isoformat(), "votes": votes, "raw_scores": raw_scores})
            sample_date += timedelta(days=3)

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
        votes = _get_votes(gauge_hist, at_time, gauge_set)
        return _continuation_allowed(votes, direction, "majority", gate_threshold)

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
                    votes = _get_votes(gauge_hist, candle["time"], gauge_set)
                    if _continuation_allowed(votes, b.direction, continuation_override, gate_threshold):
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
                if trailing_mode == "breakeven_then_wide":
                    if favorable_bricks == BREAKEVEN_AFTER_BRICKS:
                        stop_price = entry_price  # exactly breakeven, not just "start trailing"
                    elif favorable_bricks > BREAKEVEN_AFTER_BRICKS:
                        trail_dist = WIDE_TRAIL_BOXES * box_size
                        candidate_stop = b.close - trail_dist if position == 1 else b.close + trail_dist
                        if position == 1:
                            stop_price = max(stop_price, candidate_stop)
                        else:
                            stop_price = min(stop_price, candidate_stop)
                    # else (favorable_bricks < 2): stop stays at its initial level, unmoved
                elif trailing_mode == "gradual_lock":
                    if favorable_bricks >= BREAKEVEN_AFTER_BRICKS:
                        trail_dist = WIDE_TRAIL_BOXES * box_size
                        candidate_stop = b.close - trail_dist if position == 1 else b.close + trail_dist
                    else:  # exactly at the early-lock stage (1 favorable brick)
                        lock_dist = EARLY_LOCK_PIPS * PIP
                        candidate_stop = entry_price + lock_dist if position == 1 else entry_price - lock_dist
                    # stop only ever ratchets forward -- never regresses
                    if position == 1:
                        stop_price = max(stop_price, candidate_stop)
                    else:
                        stop_price = min(stop_price, candidate_stop)
                elif trailing_mode == "simple_22_33":
                    # No hold period -- trail from the very first favorable
                    # brick. Widen the trail distance once genuinely in profit.
                    profit_pips = ((b.close - entry_price) if position == 1 else (entry_price - b.close)) / PIP
                    trail_boxes = SIMPLE_WIDENED_TRAIL_BOXES if profit_pips >= SIMPLE_WIDEN_AFTER_PIPS else SIMPLE_TRAIL_BOXES
                    trail_dist = trail_boxes * box_size
                    candidate_stop = b.close - trail_dist if position == 1 else b.close + trail_dist
                    if position == 1:
                        stop_price = max(stop_price, candidate_stop)
                    else:
                        stop_price = min(stop_price, candidate_stop)
                else:  # "tight" mode (default)
                    if favorable_bricks >= HOLD_BRICKS_BEFORE_TRAILING:
                        trail_dist = TRAIL_BOXES * box_size
                        candidate_stop = b.close - trail_dist if position == 1 else b.close + trail_dist
                        if position == 1:
                            stop_price = max(stop_price, candidate_stop)
                        else:
                            stop_price = min(stop_price, candidate_stop)
                    # else: still within the hold period -- stop stays put at its initial level

                # Re-check the stop against this SAME candle's range right after
                # updating it -- without this, a single volatile candle that both
                # tightens the stop (via a favorable brick forming) AND moves far
                # enough to breach that new level would "leapfrog" past it
                # undetected, since the next stop-check wouldn't run until the
                # following candle. This is what let some trades show a deeper
                # loss than the trailing distance being tested actually allowed.
                if position == 1 and candle["low"] <= stop_price:
                    close_trade(stop_price, b.formed_at, "stop")
                elif position == -1 and candle["high"] >= stop_price:
                    close_trade(stop_price, b.formed_at, "stop")
            else:
                # Reversal brick against an open position -- shouldn't
                # normally happen since the stop-check above should have
                # already caught it, but handle defensively. Use the
                # reversal brick's CLOSE (not its open) as the reference --
                # a genuine Renko reversal requires a full 2-box move to
                # even trigger, so the brick's open (only 1 box back) was
                # understating how far price actually had to move against
                # the position. Exit at whichever price is more protective
                # between the tracked stop level (which may already be at
                # breakeven or the wide trail) and that full 2-box point --
                # price would realistically have hit the tighter of the two first.
                exit_price = max(stop_price, b.close) if position == 1 else min(stop_price, b.close)
                close_trade(exit_price, b.formed_at, "reversal_slip")
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

    result = _summarize(trades, days, require_reversal_to_reenter, continuation_override, gate_all_entries, trailing_mode, gate_threshold)
    result["window"] = f"{start.date().isoformat()} to {now.date().isoformat()}"
    result["gauge_set"] = gauge_set
    if gauge_samples is not None:
        result["gauge_samples"] = gauge_samples
    return result


def _summarize(trades: list[dict], days: int, require_reversal_to_reenter: bool, continuation_override: str | None, gate_all_entries: bool = False, trailing_mode: str = "tight", gate_threshold: int = 2) -> dict:
    if gate_all_entries:
        mode = f"gate_all_entries_{gate_threshold}of3"
    elif require_reversal_to_reenter and continuation_override:
        mode = f"reversal_only_with_{continuation_override}_override_{gate_threshold}of3"
    elif require_reversal_to_reenter:
        mode = "reversal_only_reentry"
    else:
        mode = "any_brick_reentry"
    mode = f"{mode}__trail_{trailing_mode}"

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
