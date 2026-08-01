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
from . import fred_client, cot_client, rate_tone_client, freenews_client, llm_client
from .calendar_schedule import FOMC_DATES_2026, BOE_MPC_DATES_2026


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

    def __init__(self, start_date: date, end_date: date):
        # Generous buffers so even the earliest backtest date has enough
        # lookback for YoY (needs ~13mo of CPI) and UK's monthly yield series.
        yield_start = (start_date - timedelta(days=400)).isoformat()
        momentum_start = (start_date - timedelta(days=400)).isoformat()
        cot_start = (start_date - timedelta(days=30)).isoformat()
        end = end_date.isoformat()

        yields = fred_client.fetch_yield_history(yield_start, end)
        self.us_yield_series = yields["us"]
        self.uk_yield_series = yields["uk"]

        momentum = fred_client.fetch_momentum_history(momentum_start, end)
        self.cpi_series = momentum["cpi"]
        self.nfp_series = momentum["nfp"]

        self.cot_series = cot_client.fetch_cot_history(cot_start, end)

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

    def _week_bucket(self, target_date: str) -> tuple[date, date]:
        target = datetime.strptime(target_date[:10], "%Y-%m-%d").date()
        days_since_start = max(0, (target - self._history_start).days)
        bucket_index = days_since_start // 7
        bucket_start = self._history_start + timedelta(days=bucket_index * 7)
        bucket_end = bucket_start + timedelta(days=7)
        return bucket_start, bucket_end

    def geopolitical_score(self, target_date: str) -> float | None:
        """Weekly-bucketed real historical reconstruction -- fetches real
        archived headlines for that week (only once, cached), runs them
        through the exact same LLM severity-judgment used live."""
        bucket_start, bucket_end = self._week_bucket(target_date)
        if bucket_start in self._geo_cache:
            return self._geo_cache[bucket_start]
        try:
            published_after = bucket_start.strftime("%Y-%m-%dT00:00:00Z")
            published_before = bucket_end.strftime("%Y-%m-%dT00:00:00Z")
            headlines = freenews_client.fetch_geopolitical_headlines_range(published_after, published_before)
            result = llm_client.interpret_geopolitical_headlines(headlines)
            score = result["score"]
        except Exception:
            score = None  # a fetch/parse failure shouldn't crash the whole backtest
        self._geo_cache[bucket_start] = score
        return score

    def news_score(self, target_date: str) -> float | None:
        """Weekly-bucketed real historical reconstruction of the GBP/USD
        news sentiment gauge -- same (GBP score - USD score)/2 combination
        as the live gauge, just built from real archived headlines for
        that week instead of today's."""
        bucket_start, bucket_end = self._week_bucket(target_date)
        if bucket_start in self._news_cache:
            return self._news_cache[bucket_start]
        try:
            published_after = bucket_start.strftime("%Y-%m-%dT00:00:00Z")
            published_before = bucket_end.strftime("%Y-%m-%dT00:00:00Z")
            headlines = freenews_client.fetch_gbp_usd_headlines_range(published_after, published_before)
            gbp_result = llm_client.interpret_currency_headlines(headlines["gbp"], "GBP")
            usd_result = llm_client.interpret_currency_headlines(headlines["usd"], "USD")
            score = round((gbp_result["score"] - usd_result["score"]) / 2, 4)
        except Exception:
            score = None
        self._news_cache[bucket_start] = score
        return score

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
