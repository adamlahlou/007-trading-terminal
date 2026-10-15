#!/usr/bin/env python3
"""Backtest integrity patch. Run from the repo root.

Fixes what is verifiably wrong in the backtest code itself:
  1. News/geo used the SAME week's headlines for every day in that week
     (Monday "knew" Friday's news = lookahead) and the weeks were anchored to
     the backtest's start date, so results shifted with the start date.
     Now: Monday-aligned calendar weeks, scored from the PRIOR week's headlines.
  2. LLM-derived reconstructions (news, geo, Fed/BoE tone) were re-asked on
     every backtest call, so identical runs could differ. Now cached in the DB.
  3. The result label always said "gate_all_entries_2of3", even for the
     all-gauges rule. Now states the real rule, the gauge set and the commit.
  4. Adds a per-gauge coverage report (days with data, distinct values, % bull/
     bear, FLAT / NO DATA warnings) and a plain-language list of known
     limitations, so a number can't be over-trusted.
Nothing is written unless every edit matches exactly once."""
import pathlib, sys

# ------------------------------------------------------------------ db.py
DB_TABLE_OLD = '''            count_verdict TEXT,
            trigger_kind TEXT
        )
        """
    )
    conn.commit()
    conn.close()
'''
DB_TABLE_NEW = '''            count_verdict TEXT,
            trigger_kind TEXT
        )
        """
    )
    # Cache of LLM-derived backtest reconstructions (news / geo / rate tone),
    # so re-running the same window gives the same answer.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS backtest_cache (
            key TEXT PRIMARY KEY,
            value REAL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()
'''
DB_FUNCS = '''


def get_backtest_cache(key: str):
    """Returns {"value": float} if cached, else None."""
    conn = get_conn()
    row = conn.execute("SELECT value FROM backtest_cache WHERE key = ?", (key,)).fetchone()
    conn.close()
    return {"value": row["value"]} if row else None


def set_backtest_cache(key: str, value: float):
    from datetime import datetime, timezone
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO backtest_cache (key, value, created_at) VALUES (?, ?, ?)",
        (key, value, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
'''

# ------------------------------------------------------------------ gauge_history.py
GH_IMPORT_OLD = "from . import fred_client, cot_client, rate_tone_client, freenews_client, llm_client\n"
GH_IMPORT_NEW = "from . import db, fred_client, cot_client, rate_tone_client, freenews_client, llm_client\n"

GH_DECISION_OLD = '''        key = (bank, meeting_date)
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
'''
GH_DECISION_NEW = '''        key = (bank, meeting_date)
        if key in self._rate_tone_cache:
            return self._rate_tone_cache[key]
        cached = db.get_backtest_cache(f"rate:v1:{bank}:{meeting_date}")
        if cached is not None:
            self._rate_tone_cache[key] = cached["value"]
            return cached["value"]
        try:
            statement_text = rate_tone_client.fetch_statement_text(bank, meeting_date)
            result = rate_tone_client.interpret_rate_statement(bank, statement_text)
            # Fed hawkish -> GBPUSD bearish (inverted); BoE hawkish -> bullish (direct).
            score = -result["score"] if bank == "Fed" else result["score"]
        except Exception:
            score = None
        self._rate_tone_cache[key] = score
        if score is not None:
            db.set_backtest_cache(f"rate:v1:{bank}:{meeting_date}", score)
        return score
'''

GH_BUCKET_OLD = '''        target = datetime.strptime(target_date[:10], "%Y-%m-%d").date()
        days_since_start = max(0, (target - self._history_start).days)
        bucket_index = days_since_start // 7
        bucket_start = self._history_start + timedelta(days=bucket_index * 7)
        bucket_end = bucket_start + timedelta(days=7)
        return bucket_start, bucket_end
'''
GH_BUCKET_NEW = '''        """Calendar weeks (Monday-aligned), NOT anchored to the backtest's
        start date -- so a given date always lands in the same bucket (and
        the same cached score) whatever window is being tested."""
        target = datetime.strptime(target_date[:10], "%Y-%m-%d").date()
        bucket_start = target - timedelta(days=target.weekday())
        return bucket_start, bucket_start + timedelta(days=7)
'''

GH_GEO_CACHE_OLD = '''        if bucket_start in self._geo_cache:
            return self._geo_cache[bucket_start]
        try:
            published_after = bucket_start.strftime("%Y-%m-%dT00:00:00Z")
            published_before = bucket_end.strftime("%Y-%m-%dT00:00:00Z")
            headlines = freenews_client.fetch_geopolitical_headlines_range(published_after, published_before)
'''
GH_GEO_CACHE_NEW = '''        if bucket_start in self._geo_cache:
            return self._geo_cache[bucket_start]
        cached = db.get_backtest_cache(f"geo:v2:{bucket_start}")
        if cached is not None:
            self._geo_cache[bucket_start] = cached["value"]
            return cached["value"]
        try:
            # PRIOR week's headlines only: using this week's own headlines for
            # every day in it would let Monday "know" Friday's news.
            published_after = (bucket_start - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z")
            published_before = bucket_start.strftime("%Y-%m-%dT00:00:00Z")
            headlines = freenews_client.fetch_geopolitical_headlines_range(published_after, published_before)
'''
GH_GEO_END_OLD = '''            score = None  # a fetch/parse failure shouldn't crash the whole backtest
        self._geo_cache[bucket_start] = score
        return score
'''
GH_GEO_END_NEW = '''            score = None  # a fetch/parse failure shouldn't crash the whole backtest
        self._geo_cache[bucket_start] = score
        if score is not None:
            db.set_backtest_cache(f"geo:v2:{bucket_start}", score)
        return score
'''

GH_NEWS_CACHE_OLD = '''        if bucket_start in self._news_cache:
            return self._news_cache[bucket_start]
        try:
            published_after = bucket_start.strftime("%Y-%m-%dT00:00:00Z")
            published_before = bucket_end.strftime("%Y-%m-%dT00:00:00Z")
            headlines = freenews_client.fetch_gbp_usd_headlines_range(published_after, published_before)
'''
GH_NEWS_CACHE_NEW = '''        if bucket_start in self._news_cache:
            return self._news_cache[bucket_start]
        cached = db.get_backtest_cache(f"news:v2:{bucket_start}")
        if cached is not None:
            self._news_cache[bucket_start] = cached["value"]
            return cached["value"]
        try:
            # PRIOR week's headlines only (see geopolitical_score).
            published_after = (bucket_start - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00Z")
            published_before = bucket_start.strftime("%Y-%m-%dT00:00:00Z")
            headlines = freenews_client.fetch_gbp_usd_headlines_range(published_after, published_before)
'''
GH_NEWS_END_OLD = '''        except Exception:
            score = None
        self._news_cache[bucket_start] = score
        return score
'''
GH_NEWS_END_NEW = '''        except Exception:
            score = None
        self._news_cache[bucket_start] = score
        if score is not None:
            db.set_backtest_cache(f"news:v2:{bucket_start}", score)
        return score
'''

GH_COVERAGE = '''    def gauge_coverage(self, start: date, end: date) -> dict:
        """Per gauge, over the window: how many days had data, how many
        DISTINCT values it took, its range, and % of days bullish/bearish at
        the badge threshold. A gauge stuck on one value, or missing for part
        of the window, is flagged -- so a result can't silently rest on it."""
        thresholds = {"yield": 0.1, "cot": 0.1, "news": 0.15, "momentum": 0.15, "geo": 0.15, "rate_tone": 0.15}
        raw = {name: [] for name in thresholds}
        total_days = 0
        d = start
        while d <= end:
            iso = d.isoformat()
            total_days += 1
            y = self.yield_score(iso)
            day = {
                "yield": None if y is None else y / 1.5,
                "news": self.news_score(iso), "cot": self.cot_score(iso),
                "momentum": self.momentum_score(iso), "geo": self.geopolitical_score(iso),
                "rate_tone": self.rate_tone_blended_score(iso),
            }
            for name, v in day.items():
                raw[name].append(v)
            d += timedelta(days=1)
        report = {}
        for name, vals in raw.items():
            have = [v for v in vals if v is not None]
            th = thresholds[name]
            distinct = len({round(v, 4) for v in have})
            if not have:
                warning = "NO DATA"
            elif distinct == 1 and len(have) > 7:
                warning = "FLAT: one single value for the whole window"
            elif len(have) < total_days:
                warning = f"missing on {total_days - len(have)} days"
            else:
                warning = None
            report[name] = {
                "days_with_data": f"{len(have)}/{total_days}",
                "distinct_values": distinct,
                "min": round(min(have), 3) if have else None,
                "max": round(max(have), 3) if have else None,
                "pct_days_bullish": round(100 * sum(1 for v in have if v > th) / len(have)) if have else None,
                "pct_days_bearish": round(100 * sum(1 for v in have if v < -th) / len(have)) if have else None,
                "warning": warning,
            }
        return report

    def _week_bucket(self, target_date: str) -> tuple[date, date]:
'''

# ------------------------------------------------------------------ backtest.py
BT_IMPORT_OLD = "from datetime import datetime, timedelta, timezone\nfrom . import oanda_client\n"
BT_IMPORT_NEW = "import os\nfrom datetime import datetime, timedelta, timezone\nfrom . import oanda_client\n"

BT_RESULT_OLD = '''    result["macro_exit"] = macro_exit
'''
BT_RESULT_NEW = '''    result["macro_exit"] = macro_exit

    # ---- provenance: what exactly was tested, on which code ----
    if gauge_set == "all_gauges_badge":
        result["mode"] = result["mode"].replace(f"gate_all_entries_{gate_threshold}of3", "gate_all_entries_macro_majority")
    result["mode"] = f'{result["mode"]}__gauges_{gauge_set}'
    result["commit"] = os.environ.get("RENDER_GIT_COMMIT", "unknown")[:7]
    if gauge_set == "all_gauges_badge":
        result["definition"] = (
            "Majority vote over 6 slots, tie = no entry. yield (spread/1.5) and cot: +-0.1; "
            "news, momentum, geo, rate_tone: +-0.15. momentum is the STANDALONE US-data score; "
            "rate_tone is one blended reading = mean(latest Fed, latest BoE). "
            "news/geo come from the PRIOR calendar week's headlines (Monday-aligned) and are cached."
        )
        if gauge_hist is not None:
            result["gauge_coverage"] = gauge_hist.gauge_coverage(start.date(), now.date())
    result["known_limitations"] = [
        "Backtest momentum is a formula on CPI/NFP LEVELS; the live momentum gauge is an LLM reading of the same data. They can disagree.",
        "UNVERIFIED: CPI / NFP / UK-yield / COT series may be dated by observation period rather than release date, so the backtest may see them days to weeks early (lookahead). Not yet corrected.",
        "Gauges are sampled per day; intraday changes (e.g. a CPI print at 13:30) are not reflected until the next day's value.",
        "Small samples: a handful of trades says little. Compare windows, not single numbers.",
    ]
'''

edits = {
    "app/db.py": [(DB_TABLE_OLD, DB_TABLE_NEW)],
    "app/gauge_history.py": [
        (GH_IMPORT_OLD, GH_IMPORT_NEW),
        (GH_DECISION_OLD, GH_DECISION_NEW),
        ("    def _week_bucket(self, target_date: str) -> tuple[date, date]:\n" + GH_BUCKET_OLD,
         "    def _week_bucket(self, target_date: str) -> tuple[date, date]:\n" + GH_BUCKET_NEW),
        (GH_GEO_CACHE_OLD, GH_GEO_CACHE_NEW),
        (GH_GEO_END_OLD, GH_GEO_END_NEW),
        (GH_NEWS_CACHE_OLD, GH_NEWS_CACHE_NEW),
        (GH_NEWS_END_OLD, GH_NEWS_END_NEW),
    ],
    "app/backtest.py": [(BT_IMPORT_OLD, BT_IMPORT_NEW), (BT_RESULT_OLD, BT_RESULT_NEW)],
}

new_texts = {}
for path, reps in edits.items():
    text = pathlib.Path(path).read_text()
    for old, new in reps:
        n = text.count(old)
        if n != 1:
            print(f"FAILED on {path}: expected 1 match, found {n}, for:\n{old[:200]}...")
            sys.exit(1)
        text = text.replace(old, new)
    new_texts[path] = text

# coverage method goes in right before _week_bucket (which now has its docstring first)
gh = new_texts["app/gauge_history.py"]
anchor = "    def _week_bucket(self, target_date: str) -> tuple[date, date]:\n"
if gh.count(anchor) != 1:
    print("FAILED: _week_bucket anchor not found exactly once")
    sys.exit(1)
new_texts["app/gauge_history.py"] = gh.replace(anchor, GH_COVERAGE)
new_texts["app/db.py"] = new_texts["app/db.py"].rstrip("\n") + "\n" + DB_FUNCS

for path, text in new_texts.items():
    pathlib.Path(path).write_text(text)
    print("patched", path)
print("\nPatch applied cleanly.")
