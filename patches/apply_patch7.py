#!/usr/bin/env python3
"""Runs the AI macro refresh from /api/cron/scan too (the endpoint the outside
pinger hits), because the in-app 5-minute timer doesn't fire while Render has
the app asleep. The refresh itself is debounced/deduped, so the extra trigger
costs nothing when nothing is due. Run from the repo root."""
import pathlib, sys

p = pathlib.Path("app/main.py")
t = p.read_text()
OLD = '''        result = await asyncio.to_thread(run_scan)
        return JSONResponse({"ok": True, **result})
'''
NEW = '''        result = await asyncio.to_thread(run_scan)
        # Piggyback the AI macro refresh on the pinged scan: the in-app timer
        # doesn't fire while the instance sleeps, this does. Debounced inside.
        try:
            macro = await asyncio.to_thread(macro_llm.run_macro_llm_refresh)
        except Exception as e:
            logger.error(f"Macro LLM refresh (cron) failed: {e}")
            macro = {"error": str(e)}
        return JSONResponse({"ok": True, **result, "macro_llm": macro})
'''
if t.count(OLD) != 1:
    print(f"FAILED: expected 1 match, found {t.count(OLD)}")
    sys.exit(1)
p.write_text(t.replace(OLD, NEW))
print("patched app/main.py")
