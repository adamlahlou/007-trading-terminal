#!/usr/bin/env python3
"""Adds the LLM macro verdict. Run from the repo root (007-terminal-git) with
macro_llm.py sitting next to this script. Every edit is match-checked first;
nothing is written unless ALL of them match exactly once."""
import pathlib, shutil, sys

ROOT = pathlib.Path("app")

DB_TABLE_OLD = '''            recorded_at TEXT NOT NULL,
            detail TEXT
        )
        """
    )
    conn.commit()
    conn.close()
'''
DB_TABLE_NEW = '''            recorded_at TEXT NOT NULL,
            detail TEXT
        )
        """
    )
    # LLM macro verdict log -- INSERT-only, full inputs kept, so the model's
    # real verdicts can be replayed by timestamp in the backtester later.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS macro_llm_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            verdict TEXT NOT NULL,
            confidence REAL NOT NULL,
            reason TEXT,
            key_drivers_json TEXT,
            inputs_json TEXT,
            fingerprint TEXT,
            count_verdict TEXT,
            trigger_kind TEXT
        )
        """
    )
    conn.commit()
    conn.close()
'''

DB_FUNCS = '''


def add_macro_llm_log(created_at, verdict, confidence, reason, key_drivers, inputs, fingerprint, count_verdict, trigger):
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO macro_llm_log
        (created_at, verdict, confidence, reason, key_drivers_json, inputs_json, fingerprint, count_verdict, trigger_kind)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (created_at, verdict, confidence, reason, json.dumps(key_drivers), json.dumps(inputs, default=str),
         fingerprint, count_verdict, trigger),
    )
    conn.commit()
    conn.close()


def _macro_llm_row(r, include_inputs=True):
    d = {
        "id": r["id"], "created_at": r["created_at"], "verdict": r["verdict"],
        "confidence": r["confidence"], "reason": r["reason"],
        "key_drivers": json.loads(r["key_drivers_json"] or "[]"),
        "fingerprint": r["fingerprint"], "count_verdict": r["count_verdict"], "trigger": r["trigger_kind"],
    }
    if include_inputs:
        d["inputs"] = json.loads(r["inputs_json"] or "{}")
    return d


def get_latest_macro_llm() -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM macro_llm_log ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    return _macro_llm_row(row) if row else None


def get_macro_llm_log(limit: int = 100, include_inputs: bool = False) -> list[dict]:
    """Oldest-first, like get_gauge_history."""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM macro_llm_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [_macro_llm_row(r, include_inputs) for r in reversed(rows)]
'''

LT_IMPORT_OLD = "from .gauge_history import macro_verdict\n"
LT_IMPORT_NEW = "from .gauge_history import macro_verdict\nfrom . import macro_llm\n"

LT_GATE_OLD = '''    votes = _current_gauge_votes()
    return macro_verdict(votes) == direction
'''
LT_GATE_NEW = '''    if macro_llm.gate_source() == "llm":
        llm_verdict = macro_llm.current_verdict_num()
        if llm_verdict is not None:
            return llm_verdict == direction
        # No fresh LLM verdict (none logged yet, or too old) -- fall back to
        # the plain gauge count rather than freezing or trading blind.
    votes = _current_gauge_votes()
    return macro_verdict(votes) == direction
'''

MAIN_IMPORT_OLD = "from . import db, oanda_client, calendar_schedule, backtest, oanda_execution, scheduler_registry\n"
MAIN_IMPORT_NEW = "from . import db, oanda_client, calendar_schedule, backtest, oanda_execution, scheduler_registry, macro_llm\n"

MAIN_JOB_OLD = '''    scheduler.add_job(lambda: asyncio.to_thread(run_geo_refresh), "cron", hour="*", id="geo_refresh")
'''
MAIN_JOB_NEW = '''    scheduler.add_job(lambda: asyncio.to_thread(run_geo_refresh), "cron", hour="*", id="geo_refresh")
    # LLM macro verdict: a cheap check every 5 min; the model is only actually
    # called when a gauge verdict flipped / a release landed (debounced), or
    # every 30 min at most. See macro_llm.py.
    scheduler.add_job(lambda: asyncio.to_thread(macro_llm.run_macro_llm_refresh), "cron", minute="*/5", id="macro_llm_refresh")
'''

MAIN_STARTUP_OLD = '''    asyncio.create_task(_startup_fomc_minutes())
    yield
'''
MAIN_STARTUP_NEW = '''    asyncio.create_task(_startup_fomc_minutes())

    async def _startup_macro_llm():
        try:
            await asyncio.sleep(90)  # let the gauges above finish loading first
            await asyncio.to_thread(macro_llm.run_macro_llm_refresh)
        except Exception as e:
            logger.error(f"Startup macro LLM refresh failed: {e}")

    asyncio.create_task(_startup_macro_llm())
    yield
'''

MAIN_ENDPOINT_OLD = '''@app.get("/api/gauge-history")
'''
MAIN_ENDPOINT_NEW = '''@app.get("/api/macro-llm")
async def api_macro_llm():
    """Latest LLM macro verdict, plus what the live gate is actually using."""
    last = db.get_latest_macro_llm()
    gate = macro_llm.gate_source()
    effective = macro_llm.current_verdict_num()
    if not last:
        return JSONResponse({"gate_source": gate, "verdict": None})
    last.pop("inputs", None)
    created = macro_llm._parse_time(last["created_at"])
    age = (datetime.now(timezone.utc) - created).total_seconds() / 60 if created else None
    last["age_minutes"] = round(age, 1) if age is not None else None
    last["stale"] = age is None or age > macro_llm.MAX_AGE_MINUTES
    last["gate_source"] = gate
    last["gate_verdict_now"] = effective  # +1 / -1 / 0, or null = no usable verdict (gate falls back to the count)
    return JSONResponse(last)


@app.get("/api/macro-llm/log")
async def api_macro_llm_log(limit: int = 100, include_inputs: bool = False):
    return JSONResponse({"log": db.get_macro_llm_log(limit=limit, include_inputs=include_inputs)})


@app.post("/api/macro-llm-refresh-now")
async def api_macro_llm_refresh_now():
    try:
        result = await asyncio.to_thread(macro_llm.run_macro_llm_refresh, True)
        return JSONResponse({"ok": True, **result})
    except Exception as e:
        logger.error(f"Macro LLM refresh failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)


@app.get("/api/gauge-history")
'''

JS_OLD = "function drawBricks() {\n"
JS_NEW = '''// AI macro verdict, shown beside the count badge (see macro_llm.py). Pure
// display here -- whether it GATES entries is the GATE_SOURCE server setting.
async function loadMacroLlm() {
  try {
    const badge = document.getElementById('macro-badge');
    if (!badge) return;
    const r = await fetch('/api/macro-llm');
    const d = await r.json();
    let el = document.getElementById('macro-llm-badge');
    if (!el) {
      el = document.createElement('span');
      el.id = 'macro-llm-badge';
      el.style.marginLeft = '14px';
      el.style.fontSize = '0.8em';
      badge.insertAdjacentElement('afterend', el);
    }
    if (!d || !d.verdict) { el.textContent = ''; return; }
    const color = d.verdict === 'bullish' ? 'var(--white)' : d.verdict === 'bearish' ? 'var(--blue)' : 'var(--amber)';
    el.textContent = `AI: ${d.verdict.toUpperCase()} ${Math.round(d.confidence * 100)}%` + (d.stale ? ' (stale)' : '') + (d.gate_source === 'llm' ? ' · GATING' : '');
    el.style.color = color;
    el.title = `${d.reason}\\n\\nDrivers: ${(d.key_drivers || []).join('; ')}\\n\\nUpdated ${d.age_minutes} min ago`;
  } catch (e) { /* display only -- never break the page */ }
}
loadMacroLlm();
setInterval(loadMacroLlm, 60000);

function drawBricks() {
'''

edits = {
    "db.py": [(DB_TABLE_OLD, DB_TABLE_NEW)],
    "live_trader.py": [(LT_IMPORT_OLD, LT_IMPORT_NEW), (LT_GATE_OLD, LT_GATE_NEW)],
    "main.py": [(MAIN_IMPORT_OLD, MAIN_IMPORT_NEW), (MAIN_JOB_OLD, MAIN_JOB_NEW),
                (MAIN_STARTUP_OLD, MAIN_STARTUP_NEW), (MAIN_ENDPOINT_OLD, MAIN_ENDPOINT_NEW)],
    "app.js": [(JS_OLD, JS_NEW)],
}

src = pathlib.Path("macro_llm.py")
if not src.exists():
    print("FAILED: macro_llm.py not found next to apply_patch4.py")
    sys.exit(1)
if (ROOT / "macro_llm.py").exists():
    print("FAILED: app/macro_llm.py already exists -- patch looks already applied")
    sys.exit(1)

# Pass 1: compute everything in memory, bail before writing if anything's off.
new_texts = {}
for name, reps in edits.items():
    text = (ROOT / name).read_text()
    for old, new in reps:
        n = text.count(old)
        if n != 1:
            print(f"FAILED on {name}: expected 1 match, found {n}, for:\n{old[:160]}...")
            sys.exit(1)
        text = text.replace(old, new)
    new_texts[name] = text
new_texts["db.py"] = new_texts["db.py"].rstrip("\n") + "\n" + DB_FUNCS

# Pass 2: write.
for name, text in new_texts.items():
    (ROOT / name).write_text(text)
    print(f"patched app/{name}")
shutil.move(str(src), str(ROOT / "macro_llm.py"))
print("added app/macro_llm.py")
print("\nPatch applied cleanly. GATE_SOURCE defaults to 'count' -- the AI verdict is logged and shown but does not gate until you set GATE_SOURCE=llm on Render.")
