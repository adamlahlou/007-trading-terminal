"""
Reconstructs what the yield, COT, momentum, and (optionally) rate-tone
gauges actually said at any point in a historical window -- for
backtesting entry rules that gate on real historical fundamentals rather
than today's live snapshot. Geopolitical/news are deliberately excluded --
those need re-running LLM interpretation against many archived headlines
per day, a much bigger separate task than rate-tone (which only needs one
real fetch+interpretation per actual meeting, cached).

Critical correctness rule throughout: every lookup only considers data
dated ON OR BEFORE the target date. Using a later observation would be
lookahead bias -- silently "cheating" by letting the backtest know things
it couldn't have known yet, which would make the results meaningless.
"""
from __future__ import annotations
from datetime import date, timedelta, datetime
from . import db, fred_client, cot_client, rate_tone_client, freenews_client, llm_client
from .calendar_schedule import FOMC_DATES_2026, BOE_MPC_DATES_2026


# Approximate PUBLICATION lags (days from the period a number covers to when it
# was actually public). Deliberately on the conservative (late) side. Without
# these, the backtest "knew" e.g. September CPI on 1 September.
US_YIELD_LAG_DAYS = 1
UK_YIELD_LAG_DAYS = 60   # monthly OECD series on FRED, published with a long delay
CPI_LAG_DAYS = 45        # month-start date; released ~mid next month
NFP_LAG_DAYS = 38        # month-start date; released first Friday of next month
COT_LAG_DAYS = 4         # as-of Tuesday; public Friday afternoon


def _shift_series(series: list[tuple[float, str]], days: int) -> list[tuple[float, str]]:
    out = []
    for value, d in series:
        shifted = (datetime.strptime(d[:10], "%Y-%m-%d").date() + timedelta(days=days)).isoformat()
        out.append((value, shifted))
    return out


def _shift_cot(rows: list[dict], days: int) -> list[dict]:
    out = []
    for r in rows:
        r2 = dict(r)
        r2["report_date"] = (datetime.strptime(r["report_date"][:10], "%Y-%m-%d").date() + timedelta(days=days)).isoformat()
        out.append(r2)
    return out


def _value_as_of(series: list[tuple[float, str]], target_date: str) -> float | None:
    """series: [(value, date_str)] ascending. Returns the value from the
    most recent entry with date <= target_date, or None if none exists yet."""
    best = None
    for value, d in series:
        if d[:10] <= target_date[:10]:
            best = value
        else:
            break
    return best


class GaugeHistory:
    """Fetches once, then answers point-in-time lookups cheaply for every
    candle in the backtest without re-fetching."""

    def __init__(self, start_date: date, end_date: date, light: bool = False):
        # Generous buffers so even the earliest backtest date has enough
        # lookback for YoY (needs ~13mo of CPI) and UK's monthly yield series.
        yield_start = (start_date - timedelta(days=400)).isoformat()
        momentum_start = (start_date - timedelta(days=400)).isoformat()
        cot_start = (start_date - timedelta(days=30)).isoformat()
        end = end_date.isoformat()

        # light=True (live change-gauges): skip the yield/momentum history, which they don't use.
        yields = {"us": [], "uk": [], "uk_source": "boe"} if light else fred_client.fetch_yield_history(yield_start, end)
        self.us_yield_series = _shift_series(yields["us"], US_YIELD_LAG_DAYS)
        # BoE daily data is public next working day; the FRED/OECD monthly fallback lags ~60 days.
        uk_lag = UK_YIELD_LAG_DAYS if yields.get("uk_source", "fred") == "fred" else US_YIELD_LAG_DAYS
        self.uk_yield_series = _shift_series(yields["uk"], uk_lag)

        momentum = {"cpi": [], "nfp": []} if light else fred_client.fetch_momentum_history(momentum_start, end)
        self.cpi_series = _shift_series(momentum["cpi"], CPI_LAG_DAYS)
        self.nfp_series = _shift_series(momentum["nfp"], NFP_LAG_DAYS)

        self.cot_series = _shift_cot(cot_client.fetch_cot_history(cot_start, end), COT_LAG_DAYS)

        # Rate tone: NOT eagerly fetched (unlike the others) -- each real
        # meeting needs a slow real fetch + LLM call, so we only do that
        # once per actual meeting, lazily, cached by (bank, date).
        self._all_decisions = sorted(
            [("Fed", d) for d, _ in FOMC_DATES_2026] + [("BoE", d) for d, _ in BOE_MPC_DATES_2026],
            key=lambda x: x[1],
        )
        self._rate_tone_cache: dict[tuple[str, date], float | None] = {}

        # News/geopolitical: weekly buckets (anchored to start_date), each
        # fetched+interpreted once and cached -- keeps real API/LLM calls
        # to ~4-5 per gauge per month rather than one per query date.
        self._history_start = start_date
        self._geo_cache: dict[date, float | None] = {}
        self._news_cache: dict[date, float | None] = {}
        self._geo_article_counts: dict[date, int] = {}
        self._news_article_counts: dict[date, dict] = {}

    def rate_tone_score(self, target_date: str) -> float | None:
        """Finds the most recent FOMC/BoE meeting on or before target_date,
        fetches the REAL historical statement for it (only once -- cached
        after that), and returns its GBPUSD-directional score. Returns
        None if no meeting has happened yet at this point in the window."""
        target = datetime.strptime(target_date[:10], "%Y-%m-%d").date()
        most_recent = None
        for bank, meeting_date in self._all_decisions:
            if meeting_date <= target:
                most_recent = (bank, meeting_date)
            else:
                break
        if most_recent is None:
            return None

        if most_recent in self._rate_tone_cache:
            return self._rate_tone_cache[most_recent]

        bank, meeting_date = most_recent
        try:
            statement_text = rate_tone_client.fetch_statement_text(bank, meeting_date)
            result = rate_tone_client.interpret_rate_statement(bank, statement_text)
            # Fed hawkish -> USD strength -> GBPUSD bearish (inverted).
            # BoE hawkish -> GBP strength -> GBPUSD bullish (direct).
            score = -result["score"] if bank == "Fed" else result["score"]
        except Exception:
            score = None  # a fetch/parse failure shouldn't crash the whole backtest
        self._rate_tone_cache[most_recent] = score
        return score

    def _decision_score(self, bank: str, meeting_date) -> float | None:
        key = (bank, meeting_date)
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

    def gauge_coverage(self, start: date, end: date) -> dict:
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
        """Calendar weeks (Monday-aligned), NOT anchored to the backtest's
        start date -- so a given date always lands in the same bucket (and
        the same cached score) whatever window is being tested."""
        target = datetime.strptime(target_date[:10], "%Y-%m-%d").date()
        bucket_start = target - timedelta(days=target.weekday())
        return bucket_start, bucket_start + timedelta(days=7)

    def geopolitical_score(self, target_date: str) -> float | None:
        """Weekly-bucketed real historical reconstruction -- fetches real
        archived headlines for that week (only once, cached), runs them
        through the exact same LLM severity-judgment used live."""
        bucket_start, bucket_end = self._week_bucket(target_date)
        if bucket_start in self._geo_cache:
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
            self._geo_article_counts[bucket_start] = len(headlines)
            result = llm_client.interpret_geopolitical_headlines(headlines)
            score = result["score"]
        except Exception:
            score = None  # a fetch/parse failure shouldn't crash the whole backtest
        self._geo_cache[bucket_start] = score
        if score is not None:
            db.set_backtest_cache(f"geo:v2:{bucket_start}", score)
        return score

    def geo_article_count(self, target_date: str) -> int | None:
        """Diagnostic: how many raw articles were actually fetched for this
        week (before any GBP/USD classification) -- distinguishes 'no
        articles found at all' from 'articles found but none relevant'."""
        bucket_start, _ = self._week_bucket(target_date)
        return self._geo_article_counts.get(bucket_start)

    def news_score(self, target_date: str) -> float | None:
        """Weekly-bucketed real historical reconstruction of the GBP/USD
        news sentiment gauge -- same (GBP score - USD score)/2 combination
        as the live gauge, just built from real archived headlines for
        that week instead of today's."""
        bucket_start, bucket_end = self._week_bucket(target_date)
        if bucket_start in self._news_cache:
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
            self._news_article_counts[bucket_start] = {
                "gbp": len(headlines["gbp"]), "usd": len(headlines["usd"]),
                "total_fetched": headlines.get("total_fetched"),
            }
            gbp_result = llm_client.interpret_currency_headlines(headlines["gbp"], "GBP")
            usd_result = llm_client.interpret_currency_headlines(headlines["usd"], "USD")
            score = round((gbp_result["score"] - usd_result["score"]) / 2, 4)
        except Exception:
            score = None
        self._news_cache[bucket_start] = score
        if score is not None:
            db.set_backtest_cache(f"news:v2:{bucket_start}", score)
        return score

    def news_article_counts(self, target_date: str) -> dict | None:
        """Diagnostic: how many GBP/USD-classified articles were found for
        this week, so a 0.0 score can be told apart as 'genuinely no
        matching headlines' vs some other issue."""
        bucket_start, _ = self._week_bucket(target_date)
        return self._news_article_counts.get(bucket_start)

    def yield_score(self, target_date: str) -> float | None:
        us = _value_as_of(self.us_yield_series, target_date)
        uk = _value_as_of(self.uk_yield_series, target_date)
        if us is None or uk is None:
            return None
        return uk - us

    def cot_score(self, target_date: str) -> float | None:
        best = None
        for row in self.cot_series:
            if row["report_date"] <= target_date[:10]:
                best = row["gauge_score"]
            else:
                break
        return best

    def momentum_score(self, target_date: str) -> float | None:
        # Find the index of the latest CPI/NFP observation on or before
        # target_date, then look back within the SAME list for YoY/MoM --
        # mirrors exactly how the live gauge computes it, just at a
        # historical point instead of "now".
        cpi_idx = None
        for i, (_, d) in enumerate(self.cpi_series):
            if d[:10] <= target_date[:10]:
                cpi_idx = i
            else:
                break
        nfp_idx = None
        for i, (_, d) in enumerate(self.nfp_series):
            if d[:10] <= target_date[:10]:
                nfp_idx = i
            else:
                break

        if cpi_idx is None or cpi_idx < 12 or nfp_idx is None or nfp_idx < 1:
            return None  # not enough history yet at this point in the window

        latest_cpi = self.cpi_series[cpi_idx][0]
        year_ago_cpi = self.cpi_series[cpi_idx - 12][0]
        cpi_yoy = (latest_cpi - year_ago_cpi) / year_ago_cpi * 100

        latest_nfp = self.nfp_series[nfp_idx][0]
        prev_nfp = self.nfp_series[nfp_idx - 1][0]
        nfp_change = latest_nfp - prev_nfp

        norm_nfp = max(-1.0, min(1.0, nfp_change / 300.0))
        norm_cpi = max(-1.0, min(1.0, (cpi_yoy - 2.0) / 3.0))
        hot_data_score = (norm_nfp + norm_cpi) / 2
        return -hot_data_score  # same inversion as the live gauge

    def votes_as_of(self, target_date: str, threshold: float = 0.1, include_rate_tone: bool = False, include_news_geo: bool = False) -> dict:
        """Returns {gauge_name: -1/0/1} for whichever gauges have data
        available yet at this point in the window. include_rate_tone=True
        adds rate_tone; include_news_geo=True adds news and geo (both
        weekly-bucketed real reconstructions). Default False on both
        preserves the original yield/COT/momentum-only gate exactly as validated."""
        votes = {}
        y, c, m = self.yield_score(target_date), self.cot_score(target_date), self.momentum_score(target_date)
        for name, score in (("yield", y), ("cot", c), ("momentum", m)):
            if score is None:
                continue
            if score > threshold:
                votes[name] = 1
            elif score < -threshold:
                votes[name] = -1
            else:
                votes[name] = 0
        if include_rate_tone:
            rt = self.rate_tone_score(target_date)
            if rt is not None:
                votes["rate_tone"] = 1 if rt > threshold else (-1 if rt < -threshold else 0)
        if include_news_geo:
            geo = self.geopolitical_score(target_date)
            if geo is not None:
                votes["geo"] = 1 if geo > threshold else (-1 if geo < -threshold else 0)
            news = self.news_score(target_date)
            if news is not None:
                votes["news"] = 1 if news > threshold else (-1 if news < -threshold else 0)
        return votes

    def badge_votes_as_of(self, target_date: str) -> dict:
        """Mirrors the frontend macro badge EXACTLY -- the same 6 slots
        (yield, news, cot, momentum blended with rate tone, geo, rate_tone
        standalone) with each gauge's own real display threshold, not one
        uniform number. Built so the live/backtest entry gate can never
        show a different verdict than what the badge visibly displays."""
        votes = {}

        y = self.yield_score(target_date)
        if y is not None:
            norm = y / 1.5  # GAUGE_CLAMP, same normalization the yield panel uses
            votes["yield"] = 1 if norm > 0.1 else (-1 if norm < -0.1 else 0)

        news = self.news_score(target_date)
        if news is not None:
            votes["news"] = 1 if news > 0.15 else (-1 if news < -0.15 else 0)

        c = self.cot_score(target_date)
        if c is not None:
            votes["cot"] = 1 if c > 0.1 else (-1 if c < -0.1 else 0)

        m = self.momentum_score(target_date)
        rt = self.rate_tone_blended_score(target_date)  # one blended Fed+BoE reading, like the panel
        if m is not None:
            votes["momentum"] = 1 if m > 0.15 else (-1 if m < -0.15 else 0)  # standalone, like the panel

        geo = self.geopolitical_score(target_date)
        if geo is not None:
            votes["geo"] = 1 if geo > 0.15 else (-1 if geo < -0.15 else 0)

        if rt is not None:
            votes["rate_tone"] = 1 if rt > 0.15 else (-1 if rt < -0.15 else 0)

        return votes

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


def macro_verdict(votes: dict) -> int:
    """Same rule as the frontend's updateMacroBadge(): more bullish votes
    than bearish -> 1 (BULLISH MACRO), more bearish -> -1 (BEARISH MACRO),
    a tie (including all-neutral) -> 0 (MIXED MACRO, no entry). Shared by
    live_trader.py and backtest.py so they can never diverge from each
    other or from the badge."""
    bullish = sum(1 for v in votes.values() if v > 0)
    bearish = sum(1 for v in votes.values() if v < 0)
    if bullish > bearish:
        return 1
    if bearish > bullish:
        return -1
    return 0
