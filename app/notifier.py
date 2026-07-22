"""
Sends an email when a new Renko brick forms, via Resend (resend.com).
Just needs a free API key -- no SMTP config, no app passwords.
"""
from __future__ import annotations
import os
import logging
import requests
from datetime import datetime, timezone

logger = logging.getLogger("007-terminal")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
EMAIL_RECIPIENT = os.environ.get("EMAIL_RECIPIENT", "write2adaml@gmail.com")
# Resend's shared sandbox sender -- works immediately with no domain setup.
# If you later verify your own domain on Resend, swap this for your address.
EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "One Trading Terminal <onboarding@resend.dev>")


def _fmt_time(iso_str: str) -> str:
    try:
        t = datetime.strptime(iso_str[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        return t.strftime("%d %b, %H:%M UTC")
    except Exception:
        return iso_str


def send_brick_notification(bricks: list[dict]):
    """bricks: list of {direction, open, close, formed_at, confluence}.
    Kept deliberately minimal -- one line per brick, no raw history dump.
    Silently skips if email isn't configured, and never raises -- a failed
    email should never break a scan."""
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
                f"{verdict} block detected\n"
                f"Price: {b['close']:.5f}\n"
                f"Time: {_fmt_time(b['formed_at'])}\n"
                f"Gauge confluence: {confluence}"
            )
        body = "\n\n---\n\n".join(lines)
        subject = f"{('BULLISH' if bricks[0]['direction'] == 1 else 'BEARISH')} block — {bricks[0]['close']:.5f}"
        if len(bricks) > 1:
            subject = f"{len(bricks)} new blocks"

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
