"""
Uses Claude to actually interpret geopolitical headlines -- judging severity
and escalation risk, not just averaging generic sentiment scores. Same
headlines already being pulled by marketaux_client, just read properly
instead of blindly averaged.
"""
from __future__ import annotations
import os
import json
import re
import requests

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
API_URL = "https://api.anthropic.com/v1/messages"
MODEL = "claude-haiku-4-5-20251001"  # cheap, fast, plenty for this task

PROMPT_TEMPLATE = """You are assessing geopolitical/global-risk news for its likely effect on GBPUSD (the British pound vs US dollar).

Judge the ACTUAL SEVERITY and ESCALATION RISK of these headlines, not just their tone. A minor sanctions headline and a major war escalation should NOT score the same just because both are "negative" -- weight genuine severity, escalation trajectory, and market-moving potential.

Escalating conflict, war, or crisis is typically risk-off: it drives flight-to-safety flows, which tends to strengthen the US dollar broadly, making it BEARISH for GBPUSD. Calm, de-escalation, or resolution is the opposite (bullish for GBPUSD, or neutral if nothing significant is happening).

Headlines (most recent first):
{headlines}

Respond with ONLY a JSON object, no other text, in this exact form:
{{"score": <float between -1.0 and 1.0, negative = bearish GBPUSD (risk-off), positive = bullish GBPUSD>, "reason": "<one short plain-English sentence explaining the read>"}}

If there is genuinely nothing significant in these headlines, return {{"score": 0.0, "reason": "No significant geopolitical developments detected."}}"""


def interpret_geopolitical_headlines(headlines: list[dict]) -> dict:
    """headlines: list of {title, url, published_at, sentiment}.
    Returns {score, reason}. Raises on failure -- caller should handle
    gracefully same as any other gauge refresh."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    if not headlines:
        return {"score": 0.0, "reason": "No significant geopolitical developments detected."}

    headline_lines = "\n".join(f"- {h['title']}" for h in headlines if h.get("title"))
    prompt = PROMPT_TEMPLATE.format(headlines=headline_lines)

    resp = requests.post(
        API_URL,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": 200,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    text = data["content"][0]["text"].strip()

    # Defensive parsing -- strip any accidental markdown fencing, extract the JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise RuntimeError(f"Could not parse JSON from Claude's response: {text[:200]}")
    parsed = json.loads(match.group(0))

    score = max(-1.0, min(1.0, float(parsed["score"])))
    reason = str(parsed.get("reason", "")).strip()[:300]
    return {"score": round(score, 3), "reason": reason}
