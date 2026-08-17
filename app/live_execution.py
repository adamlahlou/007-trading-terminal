"""
Bridges the (theoretical) live trade signal from live_trader.py to REAL
order placement on OANDA -- with a realistic 3-minute delay before actually
executing, modeling how long it would genuinely take a person to notice a
signal and act on it, rather than assuming an instant, unrealistic fill at
the exact signal price. His own explicit choice, based on how he'd
actually trade this himself.

Gated by LIVE_EXECUTION_ENABLED (see oanda_execution.py) -- defaults OFF.
Every real order attempt and outcome is logged and stored SEPARATELY from
the theoretical live_trade_events, so the two can be directly compared --
this comparison itself is valuable, since it shows real slippage from the
delay against the idealized instant-fill assumption the backtest/tracker uses.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from . import db, oanda_client, oanda_execution, scheduler_registry

logger = logging.getLogger("007-terminal")

EXECUTION_DELAY = timedelta(minutes=3)
INITIAL_STOP_PIPS = 52  # matches backtest.py / live_trader.py exactly


def schedule_delayed_execution(events: list[dict]):
    """Called right after live_trader.process_scan() returns its events --
    for each real entry/exit, schedules the ACTUAL order 3 minutes later
    instead of executing instantly at the theoretical signal price."""
    if not events:
        return
    scheduler = scheduler_registry.get_scheduler()
    if scheduler is None:
        logger.warning("No scheduler registered -- cannot schedule delayed execution")
        return

    for event in events:
        run_date = datetime.now(timezone.utc) + EXECUTION_DELAY
        job_id = f"delayed_exec_{event['event_type']}_{event.get('brick_seq')}_{int(run_date.timestamp())}"
        scheduler.add_job(
            lambda e=event: _execute_delayed(e),
            "date",
            run_date=run_date,
            id=job_id,
        )
        logger.info(f"Scheduled delayed execution: {event['event_type']} ({event['direction']}) for {run_date.isoformat()}")


def _execute_delayed(event: dict):
    """Runs ~3 minutes after the signal -- fetches the CURRENT live price
    (not the original signal price) and acts on it, modeling realistic
    execution instead of an idealized instant fill."""
    if not oanda_execution.LIVE_EXECUTION_ENABLED:
        logger.info(f"Live execution disabled -- skipping real order for {event['event_type']}")
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    signal_price = event["price"]

    try:
        current_price = oanda_client.fetch_current_price()
        # Buying pays the ask, selling hits the bid -- real spread cost,
        # not the idealized mid-price the theoretical tracker uses.
        real_price = current_price["ask"] if event["direction"] == 1 else current_price["bid"]
        slippage_pips = round((real_price - signal_price) / 0.0001 * event["direction"], 1)

        if event["event_type"] in ("entry", "reversal_entry"):
            account = oanda_execution.get_account_summary()
            units = oanda_execution.calculate_position_size(INITIAL_STOP_PIPS, account["nav"])
            stop_price = (
                real_price - INITIAL_STOP_PIPS * 0.0001 if event["direction"] == 1
                else real_price + INITIAL_STOP_PIPS * 0.0001
            )
            oanda_execution.place_market_order(event["direction"], units, stop_price)
            logger.info(
                f"REAL ENTRY executed: {units} units @ {real_price:.5f} "
                f"(signal was {signal_price:.5f}, {slippage_pips:+.1f} pips slippage)"
            )
        else:
            oanda_execution.close_position()
            logger.info(
                f"REAL EXIT executed @ {real_price:.5f} "
                f"(signal was {signal_price:.5f}, {slippage_pips:+.1f} pips slippage)"
            )

        db.add_live_execution_event(
            event["event_type"], event["direction"], signal_price, real_price,
            slippage_pips, now_iso, error=None,
        )
    except Exception as e:
        logger.error(f"REAL order execution FAILED for {event['event_type']}: {e}")
        db.add_live_execution_event(
            event["event_type"], event["direction"], signal_price, None,
            None, now_iso, error=str(e),
        )
