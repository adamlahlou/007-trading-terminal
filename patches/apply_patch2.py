#!/usr/bin/env python3
"""Adds an optional macro_exit mode to run_backtest: force-closes an open
position outright the moment the macro verdict flips against it, instead
of waiting for a stop-loss or a genuine price reversal. Run from the repo
root (007-terminal-git), same as apply_patch.py."""
import pathlib
import sys

ROOT = pathlib.Path("app")


def patch(path: str, replacements: list[tuple[str, str]]):
    p = ROOT / path
    text = p.read_text()
    for old, new in replacements:
        count = text.count(old)
        if count != 1:
            print(f"FAILED on {path}: expected 1 match, found {count}, for:\n{old[:200]}...")
            sys.exit(1)
        text = text.replace(old, new)
    p.write_text(text)
    print(f"patched {path}")


patch("backtest.py", [
    (
'''    debug_gauges: bool = False,
    gauge_set: str = "yield_cot_momentum",
) -> dict:''',
'''    debug_gauges: bool = False,
    gauge_set: str = "yield_cot_momentum",
    macro_exit: bool = False,
) -> dict:''',
    ),
    (
'''    trailing_mode: "tight" (default) holds the stop unmoved for 2 bricks,
    then trails 1 box behind from there. "breakeven_then_wide" holds the
    stop unmoved for 2 bricks, then moves it EXACTLY to breakeven at that
    point, then trails 2 boxes (44 pips) behind from the 3rd favorable
    brick onward -- deliberately matching the 44 pip move a genuine Renko
    reversal itself requires, on the reasoning that trailing any tighter
    than that isn't meaningfully protective against anything a real
    reversal wouldn't already catch.
    """''',
'''    trailing_mode: "tight" (default) holds the stop unmoved for 2 bricks,
    then trails 1 box behind from there. "breakeven_then_wide" holds the
    stop unmoved for 2 bricks, then moves it EXACTLY to breakeven at that
    point, then trails 2 boxes (44 pips) behind from the 3rd favorable
    brick onward -- deliberately matching the 44 pip move a genuine Renko
    reversal itself requires, on the reasoning that trailing any tighter
    than that isn't meaningfully protective against anything a real
    reversal wouldn't already catch.

    macro_exit=True: only meaningful with gauge_set="all_gauges_badge" and
    gauge_hist actually built (i.e. gate_all_entries=True too, or
    require_reversal_to_reenter+continuation_override). While a position is
    open, checks the SAME macro verdict used for gating on every candle; if
    it has flipped to the opposite direction (not just gone neutral/mixed),
    the position is force-closed outright at that candle's close, tagged
    "macro_flip" -- independent of the stop-loss and of waiting for a
    genuine price reversal brick. The idea: get out ahead of either, once
    the fundamentals themselves have turned against the trade.
    """''',
    ),
    (
'''                position = b.direction
                entry_price = b.close
                stop_price = entry_price - initial_stop_dist if position == 1 else entry_price + initial_stop_dist
                favorable_bricks = 0

    # Mark-to-market anything still open at the end of the window''',
'''                position = b.direction
                entry_price = b.close
                stop_price = entry_price - initial_stop_dist if position == 1 else entry_price + initial_stop_dist
                favorable_bricks = 0

        # 4) Optional: force-close an open position outright the moment
        # the macro verdict flips against it, rather than waiting for a
        # stop-loss hit or a genuine price reversal brick.
        if macro_exit and gauge_hist is not None and position is not None:
            votes = _get_votes(gauge_hist, candle["time"], gauge_set)
            verdict = macro_verdict(votes)
            if verdict != 0 and verdict != position:
                close_trade(candle["close"], candle["time"], "macro_flip")

    # Mark-to-market anything still open at the end of the window''',
    ),
    (
'''    result = _summarize(trades, days, require_reversal_to_reenter, continuation_override, gate_all_entries, trailing_mode, gate_threshold)
    result["window"] = f"{start.date().isoformat()} to {now.date().isoformat()}"
    result["gauge_set"] = gauge_set''',
'''    result = _summarize(trades, days, require_reversal_to_reenter, continuation_override, gate_all_entries, trailing_mode, gate_threshold)
    result["window"] = f"{start.date().isoformat()} to {now.date().isoformat()}"
    result["gauge_set"] = gauge_set
    result["macro_exit"] = macro_exit''',
    ),
])

print("\nPatch applied cleanly.")
