"""
Traditional Renko brick construction.

Rules (matching TradingView's "Renko: Traditional" method):
  - A new brick forms in the current trend direction once price moves a
    full box size beyond the last brick's close.
  - A reversal (flipping direction) requires price to move TWO full box
    sizes beyond the last brick's close -- this is what distinguishes
    "traditional" Renko from simpler close-only variants, and is why it
    filters out more noise than a naive box-based chart.

Since we only get OHLC per candle (not tick-by-tick price), each candle's
likely intra-bar path is approximated as open -> low -> high -> close (for
a bullish candle) or open -> high -> low -> close (for a bearish one), and
each of those points is fed through the brick state machine in order.
This is the standard approach for reconstructing Renko from OHLC bars.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class RenkoState:
    box_size: float
    anchor: float | None = None       # used only before the first brick exists
    last_close: float | None = None   # close of the most recently formed brick
    direction: int = 0                # 0 = none yet, 1 = up, -1 = down


@dataclass
class Brick:
    direction: int   # 1 = up, -1 = down
    open: float
    close: float
    formed_at: str   # ISO timestamp of the candle that completed this brick


def _feed_price(state: RenkoState, price: float, at: str) -> list[Brick]:
    box = state.box_size
    bricks: list[Brick] = []

    if state.direction == 0:
        if state.anchor is None:
            state.anchor = price
            return bricks
        diff = price - state.anchor
        if diff >= box:
            n = int(diff // box)
            o = state.anchor
            for _ in range(n):
                c = o + box
                bricks.append(Brick(1, o, c, at))
                o = c
            state.last_close = o
            state.direction = 1
        elif diff <= -box:
            n = int((-diff) // box)
            o = state.anchor
            for _ in range(n):
                c = o - box
                bricks.append(Brick(-1, o, c, at))
                o = c
            state.last_close = o
            state.direction = -1
        return bricks

    if state.direction == 1:
        while price >= state.last_close + box:
            o = state.last_close
            c = o + box
            bricks.append(Brick(1, o, c, at))
            state.last_close = c
        if price <= state.last_close - 2 * box:
            o = state.last_close - box
            c = o - box
            bricks.append(Brick(-1, o, c, at))
            state.last_close = c
            state.direction = -1
            while price <= state.last_close - box:
                o2 = state.last_close
                c2 = o2 - box
                bricks.append(Brick(-1, o2, c2, at))
                state.last_close = c2
        return bricks

    # state.direction == -1
    while price <= state.last_close - box:
        o = state.last_close
        c = o - box
        bricks.append(Brick(-1, o, c, at))
        state.last_close = c
    if price >= state.last_close + 2 * box:
        o = state.last_close + box
        c = o + box
        bricks.append(Brick(1, o, c, at))
        state.last_close = c
        state.direction = 1
        while price >= state.last_close + box:
            o2 = state.last_close
            c2 = o2 + box
            bricks.append(Brick(1, o2, c2, at))
            state.last_close = c2
    return bricks


def process_candle(state: RenkoState, candle: dict) -> list[Brick]:
    """candle: {time, open, high, low, close}. Returns any new bricks formed."""
    if candle["close"] >= candle["open"]:
        path = [candle["open"], candle["low"], candle["high"], candle["close"]]
    else:
        path = [candle["open"], candle["high"], candle["low"], candle["close"]]

    all_bricks: list[Brick] = []
    for price in path:
        all_bricks.extend(_feed_price(state, price, candle["time"]))
    return all_bricks
