"""
Places REAL orders against OANDA (demo initially -- exact same code path
for live later, only the env vars change). Genuinely higher-stakes than
anything else in this codebase: a bug here can cost real money, so every
function here fails loudly and explicitly rather than silently swallowing
an error.

Safety: gated by LIVE_EXECUTION_ENABLED (default "false") -- must be
explicitly set to "true" for any real order to actually be placed or any
real position to actually be closed. Never assume this is on.
"""
from __future__ import annotations
import os
import requests
from . import oanda_client

LIVE_EXECUTION_ENABLED = os.environ.get("LIVE_EXECUTION_ENABLED", "false").lower() == "true"
RISK_PCT_PER_TRADE = float(os.environ.get("RISK_PCT_PER_TRADE", "4.0"))  # percent of account NAV, per his explicit choice


def get_account_summary() -> dict:
    if not oanda_client.OANDA_API_TOKEN or not oanda_client.OANDA_ACCOUNT_ID:
        raise RuntimeError("OANDA_API_TOKEN / OANDA_ACCOUNT_ID not set")
    headers = {"Authorization": f"Bearer {oanda_client.OANDA_API_TOKEN}"}
    url = f"{oanda_client.BASE_URL}/v3/accounts/{oanda_client.OANDA_ACCOUNT_ID}/summary"
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    acc = resp.json()["account"]
    return {"nav": float(acc["NAV"]), "balance": float(acc["balance"]), "currency": acc["currency"]}


def calculate_position_size(stop_distance_pips: float, nav: float) -> int:
    """
    Sizes the position so that if the stop is hit, the loss equals exactly
    RISK_PCT_PER_TRADE% of NAV. Assumes a USD-denominated account (GBP/USD
    pip value = 0.0001 per unit directly in USD terms) -- a different
    account currency would need a conversion step this doesn't have.
    """
    if stop_distance_pips <= 0:
        raise ValueError("stop_distance_pips must be positive")
    risk_amount = nav * (RISK_PCT_PER_TRADE / 100.0)
    pip_value_per_unit = 0.0001  # GBP/USD, USD account currency
    stop_distance_value_per_unit = stop_distance_pips * pip_value_per_unit
    units = int(risk_amount / stop_distance_value_per_unit)
    return max(1, units)  # never size down to zero


def place_market_order(direction: int, units: int, stop_price: float) -> dict:
    """
    direction: 1 (long) or -1 (short). units: always positive; direction is
    encoded separately then combined into OANDA's signed-units convention
    (positive=buy, negative=sell). stop_price is a STATIC safety-net stop
    (the initial 52-pip level) -- it does not trail; real trailing exits
    are driven by our own already-tested monitoring closing the position
    via close_position() instead, not by modifying this order.
    Returns OANDA's raw response. Raises on any failure.
    """
    if not LIVE_EXECUTION_ENABLED:
        raise RuntimeError("LIVE_EXECUTION_ENABLED is not 'true' -- refusing to place a real order")
    if not oanda_client.OANDA_API_TOKEN or not oanda_client.OANDA_ACCOUNT_ID:
        raise RuntimeError("OANDA_API_TOKEN / OANDA_ACCOUNT_ID not set")

    signed_units = units if direction == 1 else -units
    headers = {
        "Authorization": f"Bearer {oanda_client.OANDA_API_TOKEN}",
        "Content-Type": "application/json",
    }
    url = f"{oanda_client.BASE_URL}/v3/accounts/{oanda_client.OANDA_ACCOUNT_ID}/orders"
    body = {
        "order": {
            "units": str(signed_units),
            "instrument": oanda_client.INSTRUMENT,
            "timeInForce": "FOK",
            "type": "MARKET",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {"price": f"{stop_price:.5f}"},
        }
    }
    resp = requests.post(url, headers=headers, json=body, timeout=15)
    resp.raise_for_status()
    return resp.json()


def close_position() -> dict:
    """Closes any open GBP/USD position entirely. Used for both a normal
    signal-driven exit and a manual emergency flatten."""
    if not LIVE_EXECUTION_ENABLED:
        raise RuntimeError("LIVE_EXECUTION_ENABLED is not 'true' -- refusing to close a real position")
    if not oanda_client.OANDA_API_TOKEN or not oanda_client.OANDA_ACCOUNT_ID:
        raise RuntimeError("OANDA_API_TOKEN / OANDA_ACCOUNT_ID not set")

    headers = {
        "Authorization": f"Bearer {oanda_client.OANDA_API_TOKEN}",
        "Content-Type": "application/json",
    }
    url = f"{oanda_client.BASE_URL}/v3/accounts/{oanda_client.OANDA_ACCOUNT_ID}/positions/{oanda_client.INSTRUMENT}/close"
    body = {"longUnits": "ALL", "shortUnits": "ALL"}
    resp = requests.put(url, headers=headers, json=body, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_open_position() -> dict | None:
    """Returns the current REAL open GBP/USD position straight from OANDA
    (not our own tracked state) -- the ground truth for what's genuinely
    open, useful for reconciling against what we think is open."""
    if not oanda_client.OANDA_API_TOKEN or not oanda_client.OANDA_ACCOUNT_ID:
        raise RuntimeError("OANDA_API_TOKEN / OANDA_ACCOUNT_ID not set")
    headers = {"Authorization": f"Bearer {oanda_client.OANDA_API_TOKEN}"}
    url = f"{oanda_client.BASE_URL}/v3/accounts/{oanda_client.OANDA_ACCOUNT_ID}/positions/{oanda_client.INSTRUMENT}"
    resp = requests.get(url, headers=headers, timeout=10)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    pos = resp.json()["position"]
    long_units = float(pos["long"]["units"])
    short_units = float(pos["short"]["units"])
    if long_units == 0 and short_units == 0:
        return None
    return {"long_units": long_units, "short_units": short_units}
