#!/usr/bin/env python3
"""Makes the badge, the live gate, the backtest and the AI all count EXACTLY
what the panels on the page show.

What was wrong: the page serves static/app.js, but earlier badge fixes went
into app/app.js (a different file), so the live badge still re-thresholded raw
scores. And the panels themselves show momentum STANDALONE and rate tone as ONE
blended Fed+BoE reading -- while the live gate / backtest / AI assumed momentum
blended with Fed-only rate tone. This aligns all of them to the panels.

Run from the repo root. Nothing is written unless every edit matches once."""
import pathlib, sys

def build(edits_by_file):
    out = {}
    for path, edits in edits_by_file.items():
        text = pathlib.Path(path).read_text()
        for old, new in edits:
            n = text.count(old)
            if n != 1:
                print(f"FAILED on {path}: expected 1 match, found {n}, for:\n{old[:200]}...")
                sys.exit(1)
            text = text.replace(old, new)
        out[path] = text
    return out

# ---------------------------------------------------------------- static/app.js
JS_VERDICT_FN = '''// Converts a gauge's own already-thresholded verdict back to -1/0/1, so the
// macro badge counts EXACTLY what each panel displays -- not a re-threshold
// of the raw score against some other flat number.
function verdictToNum(verdict) {
  if (verdict.text === 'BULLISH GBPUSD') return 1;
  if (verdict.text === 'BEARISH GBPUSD') return -1;
  return 0;
}

function tick() {
'''

JS_AI = '''// AI macro verdict, shown beside the count badge (see macro_llm.py). Pure
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

JS_REPOLL = '''// Re-poll the gauges so a tab left open can't drift away from the server's
// view (they used to load once, at page open, and never again).
setInterval(() => {
  loadYields(); loadNewsGauge(); loadCotGauge(); loadMomentumGauge(); loadGeoGauge(); loadRateToneGauge();
}, 300000);

// ---- Single global refresh button ----
'''

# ---------------------------------------------------------------- live_trader.py
LT_MOM_OLD = '''    momentum_state = db.get_momentum_state()
    rate_tone_state = db.get_rate_tone_state()  # Fed only -- the only one actually shown live
    if momentum_state:
        m = momentum_state["gauge_score"]
        blended = (m + rate_tone_state["gauge_score"]) / 2 if rate_tone_state else m
        votes["momentum"] = 1 if blended > 0.15 else (-1 if blended < -0.15 else 0)
'''
LT_MOM_NEW = '''    momentum_state = db.get_momentum_state()
    if momentum_state:
        m = momentum_state["gauge_score"]  # standalone, exactly like the US Data Trend panel
        votes["momentum"] = 1 if m > 0.15 else (-1 if m < -0.15 else 0)
'''
LT_RT_OLD = '''    if rate_tone_state:
        s = rate_tone_state["gauge_score"]
        votes["rate_tone"] = 1 if s > 0.15 else (-1 if s < -0.15 else 0)
'''
LT_RT_NEW = '''    rt_scores = [st["gauge_score"] for st in (db.get_rate_tone_state(), db.get_boe_rate_tone_state()) if st]
    if rt_scores:
        s = sum(rt_scores) / len(rt_scores)  # ONE blended Fed+BoE reading, exactly like the Rate Tone panel
        votes["rate_tone"] = 1 if s > 0.15 else (-1 if s < -0.15 else 0)
'''

# ---------------------------------------------------------------- gauge_history.py
GH_METHODS = '''    def _decision_score(self, bank: str, meeting_date) -> float | None:
        key = (bank, meeting_date)
        if key in self._rate_tone_cache:
            return self._rate_tone_cache[key]
        try:
            statement_text = rate_tone_client.fetch_statement_text(bank, meeting_date)
            result = rate_tone_client.interpret_rate_statement(bank, statement_text)
            # Fed hawkish -> GBPUSD bearish (inverted); BoE hawkish -> bullish (direct).
            score = -result["score"] if bank == "Fed" else result["score"]
        except Exception:
            score = None
        self._rate_tone_cache[key] = score
        return score

    def rate_tone_blended_score(self, target_date: str) -> float | None:
        """Mirror of the Rate Tone panel: the average of the latest Fed and
        latest BoE decision on or before the date (whichever exist)."""
        target = datetime.strptime(target_date[:10], "%Y-%m-%d").date()
        latest = {}
        for bank, meeting_date in self._all_decisions:
            if meeting_date <= target and (bank not in latest or meeting_date > latest[bank]):
                latest[bank] = meeting_date
        scores = [s for s in (self._decision_score(b, d) for b, d in latest.items()) if s is not None]
        return sum(scores) / len(scores) if scores else None

    def _week_bucket(self, target_date: str) -> tuple[date, date]:
'''
GH_OLD = '''        m = self.momentum_score(target_date)
        rt = self.rate_tone_score(target_date)
        if m is not None:
            blended = (m + rt) / 2 if rt is not None else m
            votes["momentum"] = 1 if blended > 0.15 else (-1 if blended < -0.15 else 0)
'''
GH_NEW = '''        m = self.momentum_score(target_date)
        rt = self.rate_tone_blended_score(target_date)  # one blended Fed+BoE reading, like the panel
        if m is not None:
            votes["momentum"] = 1 if m > 0.15 else (-1 if m < -0.15 else 0)  # standalone, like the panel
'''

# ---------------------------------------------------------------- macro_llm.py
ML_MOM_OLD = '''    m = db.get_momentum_state()
    rt = db.get_rate_tone_state()
    if m:
        blended = (m["gauge_score"] + rt["gauge_score"]) / 2 if rt else m["gauge_score"]
        gauges.append({
            "name": "momentum", "score": round(blended, 3), "vote": _vote(blended, 0.15),
            "detail": f"US CPI YoY {m.get('cpi_yoy')} ({m.get('cpi_date')}), NFP change {m.get('nfp_change')} ({m.get('nfp_date')}); raw momentum score {round(m['gauge_score'], 3)} blended with Fed tone",
'''
ML_MOM_NEW = '''    m = db.get_momentum_state()
    if m:
        gauges.append({
            "name": "momentum", "score": round(m["gauge_score"], 3), "vote": _vote(m["gauge_score"], 0.15),
            "detail": f"US data trend (levels, not surprises): CPI YoY {m.get('cpi_yoy')} ({m.get('cpi_date')}), NFP change {m.get('nfp_change')} ({m.get('nfp_date')})",
'''
ML_RT_START = '''    if rt:
        gauges.append({
            "name": "rate_tone",'''
ML_DOC_OLD = "everything else 0.15, momentum blended with Fed rate tone), plus the"
ML_DOC_NEW = "everything else 0.15; momentum standalone, rate tone one blended Fed+BoE\n    reading -- exactly the panels), plus the"

edits = {
    "static/app.js": [
        ("function tick() {\n", JS_VERDICT_FN),
        ("verdicts.filter(v => v > 0.15).length", "verdicts.filter(v => v > 0).length"),
        ("verdicts.filter(v => v < -0.15).length", "verdicts.filter(v => v < 0).length"),
        ("state.gaugeVerdicts.yield = clamped / GAUGE_CLAMP;", "state.gaugeVerdicts.yield = verdictToNum(verdict);"),
        ("state.gaugeVerdicts.news = d.score;", "state.gaugeVerdicts.news = verdictToNum(verdict);"),
        ("state.gaugeVerdicts.cot = d.gauge_score;", "state.gaugeVerdicts.cot = verdictToNum(verdict);"),
        ("state.gaugeVerdicts.momentum = d.gauge_score;", "state.gaugeVerdicts.momentum = verdictToNum(verdict);"),
        ("state.gaugeVerdicts.geo = d.gauge_score;", "state.gaugeVerdicts.geo = verdictToNum(verdict);"),
        ("state.gaugeVerdicts.rateTone = blendedScore;", "state.gaugeVerdicts.rateTone = verdictToNum(verdict);"),
        ("function drawBricks() {\n", JS_AI),
        ("// ---- Single global refresh button ----\n", JS_REPOLL),
    ],
    "app/live_trader.py": [
        (LT_MOM_OLD, LT_MOM_NEW),
        (LT_RT_OLD, LT_RT_NEW),
        ('rate_tone at 0.15, momentum blended with rate tone exactly like the\n    "Past Macro News" panel).',
         'rate_tone at 0.15; momentum standalone and rate tone one blended\n    Fed+BoE reading, exactly like the panels).'),
        ("blended with rate tone, geo, rate tone standalone)", "standalone, geo, blended Fed+BoE rate tone)"),
    ],
    "app/gauge_history.py": [
        ("    def _week_bucket(self, target_date: str) -> tuple[date, date]:\n", GH_METHODS),
        (GH_OLD, GH_NEW),
    ],
    "app/macro_llm.py": [
        (ML_MOM_OLD, ML_MOM_NEW),
        (ML_DOC_OLD, ML_DOC_NEW),
    ],
}

# macro_llm rate_tone/boe tail: replace from the `if rt:` block through `return gauges`.
ml = pathlib.Path("app/macro_llm.py").read_text()
if ml.count(ML_RT_START) != 1 or ml.count("    return gauges\n") != 1:
    print("FAILED on app/macro_llm.py: rate_tone block not found exactly once")
    sys.exit(1)
ML_TAIL_NEW = '''    fed = db.get_rate_tone_state()
    boe = db.get_boe_rate_tone_state()
    banks = [(name, st) for name, st in (("Fed", fed), ("BoE", boe)) if st]
    if banks:
        score = sum(st["gauge_score"] for _, st in banks) / len(banks)
        gauges.append({
            "name": "rate_tone", "score": round(score, 3), "vote": _vote(score, 0.15),
            "detail": "one blended reading: " + "; ".join(
                f"{name} {round(st['gauge_score'], 3)} (meeting {st.get('meeting_date')})" for name, st in banks),
            "reason": " | ".join(f"{name}: {(st.get('reason') or '')[:220]}" for name, st in banks),
        })
    return gauges
'''

results = build(edits)
i = results["app/macro_llm.py"].index(ML_RT_START)
j = results["app/macro_llm.py"].index("    return gauges\n") + len("    return gauges\n")
results["app/macro_llm.py"] = results["app/macro_llm.py"][:i] + ML_TAIL_NEW + results["app/macro_llm.py"][j:]

for path, text in results.items():
    pathlib.Path(path).write_text(text)
    print("patched", path)
print("\nPatch applied cleanly.")
