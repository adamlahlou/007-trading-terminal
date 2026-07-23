"""
Sends an email when a new Renko brick forms, via Resend (resend.com).
Just needs a free API key -- no SMTP config, no app passwords.
"""
from __future__ import annotations
import os
import logging
import requests
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("007-terminal")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
EMAIL_RECIPIENT = os.environ.get("EMAIL_RECIPIENT", "write2adaml@gmail.com")
# Resend's shared sandbox sender -- works immediately with no domain setup.
# If you later verify your own domain on Resend, swap this for your address.
EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "One Trading Terminal <onboarding@resend.dev>")


def _fmt_time(iso_str: str) -> str:
    """OANDA labels a candle by when it OPENED, not when it closed, so a
    candle timestamped 14:45 actually only completes at 15:00. Also converts
    to UK local time (currently BST, UTC+1) since email can't auto-detect
    the reader's timezone the way a browser can.

    NOTE: the +1h here is hardcoded for BST (British Summer Time). If
    reading this outside BST (e.g. UK winter time, or while in a different
    timezone), this offset would need adjusting -- it's not auto-detected."""
    try:
        t = datetime.strptime(iso_str[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        t = t + timedelta(minutes=15, hours=1)
        return t.strftime("%d %b, %H:%M")
    except Exception:
        return iso_str


def send_brick_notification(bricks: list[dict]):
    """bricks: list of {direction, open, close, formed_at, confluence}.
    This is the ROUTINE per-brick update -- fires for every new brick
    regardless of confluence, and is deliberately worded/formatted to read
    as an FYI, not a trade signal (see send_trade_signal_notification for
    the actual actionable one). Silently skips if email isn't configured,
    and never raises -- a failed email should never break a scan."""
    if not RESEND_API_KEY:
        return
    if not bricks:
        return

    try:
        lines = []
        for b in bricks:
            verdict = "BULLISH" if b["direction"] == 1 else "BEARISH"
            confluence = b.get("confluence", "?/?")
            lines.append(
                f"{verdict} block formed (routine update, not a trade signal)\n"
                f"Price: {b['close']:.5f}\n"
                f"Time: {_fmt_time(b['formed_at'])}\n"
                f"Gauge confluence: {confluence}"
            )
        body = "\n\n---\n\n".join(lines)
        subject = f"Block update: {('bullish' if bricks[0]['direction'] == 1 else 'bearish')} — {bricks[0]['close']:.5f}"
        if len(bricks) > 1:
            subject = f"Block update: {len(bricks)} new blocks"

        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={
                "from": EMAIL_SENDER,
                "to": [EMAIL_RECIPIENT],
                "subject": subject,
                "text": body,
            },
            timeout=10,
        )
        resp.raise_for_status()
        logger.info(f"Sent brick notification email for {len(bricks)} brick(s)")
    except Exception as e:
        logger.error(f"Failed to send brick notification email: {e}")


def send_trade_signal_notification(event: dict):
    """event: {event_type, direction, price, event_time, reason}. This is
    the ACTUAL actionable notification -- only fires when the live trade
    tracker really enters or exits a position, clearly distinguished from
    the routine per-brick email (different subject prefix, different
    wording, no confusing it for a routine update)."""
    if not RESEND_API_KEY:
        return

    try:
        verdict = "LONG" if event["direction"] == 1 else "SHORT"
        action = "ENTERED" if event["event_type"] in ("entry", "reversal_entry") else "EXITED"
        subject = f"TRADE SIGNAL: {action} {verdict} @ {event['price']:.5f}"
        body = (
            f"Live trade signal -- {action} {verdict}\n"
            f"Price: {event['price']:.5f}\n"
            f"Time: {_fmt_time(event['event_time'])}\n"
            f"Reason: {event['reason']}"
        )

        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={
                "from": EMAIL_SENDER,
                "to": [EMAIL_RECIPIENT],
                "subject": subject,
                "text": body,
            },
            timeout=10,
        )
        resp.raise_for_status()
        logger.info(f"Sent trade signal email: {action} {verdict} @ {event['price']}")
    except Exception as e:
        logger.error(f"Failed to send trade signal email: {e}")
