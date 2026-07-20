"""
Sends an email when a new Renko brick forms, via Resend (resend.com).
Just needs a free API key -- no SMTP config, no app passwords.
"""
from __future__ import annotations
import os
import logging
import requests

logger = logging.getLogger("007-terminal")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
EMAIL_RECIPIENT = os.environ.get("EMAIL_RECIPIENT", "write2adaml@gmail.com")
# Resend's shared sandbox sender -- works immediately with no domain setup.
# If you later verify your own domain on Resend, swap this for your address.
EMAIL_SENDER = os.environ.get("EMAIL_SENDER", "007 Trading Terminal <onboarding@resend.dev>")


def send_brick_notification(bricks: list[dict]):
    """bricks: list of {direction, open, close, formed_at}. Silently skips
    if email isn't configured, and never raises -- a failed email should
    never break a scan."""
    if not RESEND_API_KEY:
        return
    if not bricks:
        return

    try:
        lines = []
        for b in bricks:
            arrow = "UP" if b["direction"] == 1 else "DOWN"
            lines.append(f"{arrow}  {b['open']:.5f} -> {b['close']:.5f}  ({b['formed_at']})")
        body = "New GBP/USD Renko brick(s) formed:\n\n" + "\n".join(lines)
        subject = f"007 Terminal: {len(bricks)} new brick(s)" if len(bricks) > 1 else "007 Terminal: new brick"

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
