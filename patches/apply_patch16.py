#!/usr/bin/env python3
"""Patch 16 -- change-based gauges ("what has changed", not "where is the level").

 * backtest: gauge_set=change_gauges
     /api/backtest?...&gauge_set=change_gauges&gate_all_entries=true&trailing_mode=breakeven_then_wide&exit_mode=ride_trail
 * live: app/change_gauges.py computes the same verdict from live data (cached 30 min),
   shown on the page beside the old badge as "CHANGE: BEARISH 2-1" (hover for the details).
 * /api/change-gauges (current) and /api/gauge-log (a forward log comparing count, change and AI).
 * GATE_SOURCE=change on Render makes the change verdict gate live entries (default stays "count").
Needs patches 11-15 and change_gauges.py next to this script.
Nothing is written unless every edit matches exactly once."""
import pathlib, shutil, sys

if not pathlib.Path("change_gauges.py").exists():
    print("FAILED: change_gauges.py not found next to this script"); sys.exit(1)
if pathlib.Path("app/change_gauges.py").exists():
    print("FAILED: app/change_gauges.py already exists -- looks already applied"); sys.exit(1)

edits = {
 "app/gauge_history.py": [
  ("    def __init__(self, start_date: date, end_date: date):\n", "    def __init__(self, start_date: date, end_date: date, light: bool = False):\n"),
  ("        yields = fred_client.fetch_yield_history(yield_start, end)\n",
   "        # light=True (live change-gauges): skip the yield/momentum history, which they don't use.\n        yields = {\"us\": [], \"uk\": [], \"uk_source\": \"boe\"} if light else fred_client.fetch_yield_history(yield_start, end)\n"),
  ("        momentum = fred_client.fetch_momentum_history(momentum_start, end)\n",
   "        momentum = {\"cpi\": [], \"nfp\": []} if light else fred_client.fetch_momentum_history(momentum_start, end)\n"),
  ("        return votes\n\n\ndef macro_verdict(votes: dict) -> int:",
   '''        return votes

    def change_votes_as_of(self, target_date: str) -> dict:
        """Votes from CHANGE-based gauges (see change_gauges.py)."""
        d = datetime.strptime(target_date[:10], "%Y-%m-%d").date()
        prev = lambda n: (d - timedelta(days=n)).isoformat()
        return change_votes(
            self.cot_score(target_date), self.cot_score(prev(COT_CHANGE_DAYS)),
            self.rate_tone_blended_score(target_date), self.rate_tone_blended_score(prev(RATE_CHANGE_DAYS)),
            self.news_score(target_date), self.geopolitical_score(target_date),
        )


COT_CHANGE_DAYS = 14
COT_CHANGE_THRESHOLD = 0.05
RATE_CHANGE_DAYS = 30
RATE_CHANGE_THRESHOLD = 0.03
LEVEL_THRESHOLD = 0.15   # news and geo


def _cvote(x, threshold):
    return 1 if x > threshold else (-1 if x < -threshold else 0)


def change_votes(cot_now, cot_prev, rate_now, rate_prev, news, geo) -> dict:
    """Pure function shared by the backtest and the live page, so they cannot differ.
    A gauge with missing data casts no vote."""
    votes = {}
    if cot_now is not None and cot_prev is not None:
        votes["cot"] = _cvote(round(cot_now, 4) - round(cot_prev, 4), COT_CHANGE_THRESHOLD)
    if rate_now is not None and rate_prev is not None:
        votes["rate_tone"] = _cvote(round(rate_now, 4) - round(rate_prev, 4), RATE_CHANGE_THRESHOLD)
    if news is not None:
        votes["news"] = _cvote(round(news, 4), LEVEL_THRESHOLD)
    if geo is not None:
        votes["geo"] = _cvote(round(geo, 4), LEVEL_THRESHOLD)
    return votes


def macro_verdict(votes: dict) -> int:'''),
 ],
 "app/backtest.py": [
  ("    if gauge_set == \"ai_verdict\":\n        v = macro_replay.cached_vote(at_time)",
   "    if gauge_set == \"change_gauges\":\n        return gauge_hist.change_votes_as_of(at_time)\n    if gauge_set == \"ai_verdict\":\n        v = macro_replay.cached_vote(at_time)"),
  ("        if gauge_set in (\"all_gauges_badge\", \"ai_verdict\"):\n            # Macro-verdict matching",
   "        if gauge_set in (\"all_gauges_badge\", \"ai_verdict\", \"change_gauges\"):\n            # Macro-verdict matching"),
  ("    if gauge_set in (\"all_gauges_badge\", \"ai_verdict\"):\n        result[\"mode\"] = result[\"mode\"].replace(",
   "    if gauge_set in (\"all_gauges_badge\", \"ai_verdict\", \"change_gauges\"):\n        result[\"mode\"] = result[\"mode\"].replace("),
  ("    result[\"spread_pips\"] = spread_pips\n",
   "    result[\"spread_pips\"] = spread_pips\n    if gauge_set == \"change_gauges\":\n        result[\"definition\"] = (\n            \"Majority vote of CHANGE-based gauges: COT change over 14d (+-0.05), blended Fed+BoE rate-tone \"\n            \"change over 30d (+-0.03), news and geo levels (+-0.15). Yield and momentum are not used. Tie = no entry.\"\n        )\n"),
 ],
 "app/live_trader.py": [
  ("    votes = _current_gauge_votes()\n    return macro_verdict(votes) == direction\n",
   "    if macro_llm.gate_source() == \"change\":\n        from . import change_gauges\n        cv = change_gauges.current_verdict_num()\n        if cv is not None:\n            return cv == direction\n        # change verdict unavailable -> fall back to the plain count below\n    votes = _current_gauge_votes()\n    return macro_verdict(votes) == direction\n"),
 ],
 "app/main.py": [
  ("from . import db, oanda_client, calendar_schedule, backtest, oanda_execution, scheduler_registry, macro_llm\n",
   "from . import db, oanda_client, calendar_schedule, backtest, oanda_execution, scheduler_registry, macro_llm, change_gauges\n"),
  ("    scheduler.add_job(lambda: asyncio.to_thread(macro_llm.run_macro_llm_refresh), \"cron\", minute=\"*/5\", id=\"macro_llm_refresh\")\n",
   "    scheduler.add_job(lambda: asyncio.to_thread(macro_llm.run_macro_llm_refresh), \"cron\", minute=\"*/5\", id=\"macro_llm_refresh\")\n    scheduler.add_job(lambda: asyncio.to_thread(change_gauges.refresh_and_log), \"cron\", minute=\"7,37\", id=\"change_gauges\")\n"),
  ("        return JSONResponse({\"ok\": True, **result, \"macro_llm\": macro})\n",
   "        try:\n            change = await asyncio.to_thread(change_gauges.refresh_and_log)\n        except Exception as e:\n            logger.error(f\"Change gauges (cron) failed: {e}\")\n            change = {\"error\": str(e)}\n        return JSONResponse({\"ok\": True, **result, \"macro_llm\": macro, \"change_gauges\": change})\n"),
  ("@app.get(\"/api/ai-replay\")",
   "@app.get(\"/api/change-gauges\")\nasync def api_change_gauges():\n    state = await asyncio.to_thread(change_gauges.get_state)\n    if state is None:\n        return JSONResponse({\"error\": \"unavailable\"}, status_code=503)\n    return JSONResponse({**state, \"gate_source\": macro_llm.gate_source()})\n\n\n@app.get(\"/api/gauge-log\")\nasync def api_gauge_log(limit: int = 200):\n    return JSONResponse({\"rows\": await asyncio.to_thread(change_gauges.get_log, limit)})\n\n\n@app.get(\"/api/ai-replay\")"),
 ],
 "static/app.js": [
  ("loadMacroLlm();\nsetInterval(loadMacroLlm, 60000);\n",
   '''loadMacroLlm();
setInterval(loadMacroLlm, 60000);

async function loadChangeGauges() {
  try {
    const badge = document.getElementById('macro-badge');
    if (!badge) return;
    const r = await fetch('/api/change-gauges');
    const d = await r.json();
    let el = document.getElementById('change-gauges-badge');
    if (!el) {
      el = document.createElement('span');
      el.id = 'change-gauges-badge';
      el.style.marginLeft = '14px';
      el.style.fontSize = '0.8em';
      const anchor = document.getElementById('macro-llm-badge') || badge;
      anchor.insertAdjacentElement('afterend', el);
    }
    if (!d || !d.verdict) { el.textContent = ''; return; }
    const color = d.verdict === 'bullish' ? 'var(--white)' : d.verdict === 'bearish' ? 'var(--blue)' : 'var(--amber)';
    el.textContent = `CHANGE: ${d.verdict.toUpperCase()} ${d.bull}-${d.bear}` + (d.gate_source === 'change' ? ' · GATING' : '');
    el.style.color = color;
    const v = d.votes || {}, x = d.detail || {};
    const arrow = (n) => n > 0 ? 'bullish' : n < 0 ? 'bearish' : 'no change';
    const fmt = (o) => (o && o.now != null && o.prev != null) ? `${o.prev} -> ${o.now}` : 'no data';
    el.title = `Change-based gauges (what has moved, not the level)\\n` +
      `COT (14d): ${v.cot != null ? arrow(v.cot) : 'n/a'}  [${fmt(x.cot)}]\\n` +
      `Rate tone (30d): ${v.rate_tone != null ? arrow(v.rate_tone) : 'n/a'}  [${fmt(x.rate_tone)}]\\n` +
      `News: ${v.news != null ? arrow(v.news) : 'n/a'}  [${x.news}]\\n` +
      `Geo: ${v.geo != null ? arrow(v.geo) : 'n/a'}  [${x.geo}]`;
  } catch (e) { /* display only -- never break the page */ }
}
loadChangeGauges();
setInterval(loadChangeGauges, 300000);
'''),
 ],
}
new = {}
for path, reps in edits.items():
    t = pathlib.Path(path).read_text()
    for old, rep in reps:
        n = t.count(old)
        if n != 1:
            print(f"FAILED on {path}: expected 1 match, found {n}, for:\n{old[:150]}..."); sys.exit(1)
        t = t.replace(old, rep)
    new[path] = t
for p, t in new.items():
    pathlib.Path(p).write_text(t); print("patched", p)
shutil.move("change_gauges.py", "app/change_gauges.py"); print("added app/change_gauges.py")
print("\nPatch 16 applied cleanly.")
