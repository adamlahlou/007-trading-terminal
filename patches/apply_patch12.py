#!/usr/bin/env python3
"""Patch 12 -- fix real-order exits (every exit since August failed).

Root cause: close_position() sent {"longUnits":"ALL","shortUnits":"ALL"}. OANDA
rejects that (400/404) whenever one side has no units -- i.e. always. So the demo
account never closed on a signal; it only left via the static 52-pip stop, or got
netted by the next opposite entry.

Fixes:
 * close_position(side) sends ONLY the side that actually has units, and does
   nothing (no error) if already flat.
 * An exit closes only the direction being exited, so a new entry that has
   already filled can never be closed by a late exit.
 * An entry first closes any opposite real position, and does not stack on top
   of an existing same-direction position.
 * Pricing call retries once (one 401 on 1 Oct was a transient failure).
Run from the repo root. Nothing is written unless every edit matches once."""
import pathlib, sys

edits = {
 "app/oanda_execution.py": [
  ('''def close_position() -> dict:
    """Closes any open GBP/USD position entirely. Used for both a normal
    signal-driven exit and a manual emergency flatten."""''',
   '''def close_position(direction: int | None = None) -> dict:
    """Closes the open GBP/USD position. direction=1 closes only a LONG,
    -1 only a SHORT, None closes whatever is open (emergency flatten).
    Only sides that really hold units are sent -- OANDA rejects "ALL" for an
    empty side. Already flat = no-op, not an error."""'''),
  ('''    body = {"longUnits": "ALL", "shortUnits": "ALL"}
    resp = requests.put(url, headers=headers, json=body, timeout=15)
    resp.raise_for_status()
    return resp.json()
''',
   '''    pos = get_open_position()
    body = {}
    if pos and pos["long_units"] > 0 and direction in (None, 1):
        body["longUnits"] = "ALL"
    if pos and pos["short_units"] != 0 and direction in (None, -1):
        body["shortUnits"] = "ALL"
    if not body:
        return {"closed": False, "reason": "no matching real position", "real_position": pos}
    resp = requests.put(url, headers=headers, json=body, timeout=15)
    resp.raise_for_status()
    out = resp.json()
    out["closed"] = True
    return out
'''),
 ],
 "app/live_execution.py": [
  ('''        current_price = oanda_client.fetch_current_price()
''',
   '''        try:
            current_price = oanda_client.fetch_current_price()
        except Exception:
            import time as _t
            _t.sleep(2)
            current_price = oanda_client.fetch_current_price()
'''),
  ('''        if event["event_type"] in ("entry", "reversal_entry"):
            account = oanda_execution.get_account_summary()''',
   '''        note = None
        if event["event_type"] in ("entry", "reversal_entry"):
            real_pos = oanda_execution.get_open_position()
            if real_pos:
                d = event["direction"]
                if (d == 1 and real_pos["long_units"] > 0) or (d == -1 and real_pos["short_units"] != 0):
                    note = "skipped: real position already open in this direction (no stacking)"
                    logger.warning(note)
                    db.add_live_execution_event(event["event_type"], d, signal_price, real_price, slippage_pips, now_iso, error=note)
                    return
                oanda_execution.close_position(-d)  # flatten the opposite side first
            account = oanda_execution.get_account_summary()'''),
  ('''            oanda_execution.close_position()
            logger.info(
                f"REAL EXIT executed''',
   '''            res = oanda_execution.close_position(event["direction"])
            if not res.get("closed"):
                note = "exit: nothing to close on that side (already flat or already reversed)"
            logger.info(
                f"REAL EXIT executed'''),
  ('''            slippage_pips, now_iso, error=None,
        )
    except Exception as e:''',
   '''            slippage_pips, now_iso, error=note,
        )
    except Exception as e:'''),
 ],
}
new = {}
for path, reps in edits.items():
    t = pathlib.Path(path).read_text()
    for old, rep in reps:
        n = t.count(old)
        if n != 1:
            print(f"FAILED on {path}: expected 1 match, found {n}, for:\n{old[:140]}..."); sys.exit(1)
        t = t.replace(old, rep)
    new[path] = t
for p, t in new.items():
    pathlib.Path(p).write_text(t); print("patched", p)
print("\nPatch 12 applied cleanly.")
