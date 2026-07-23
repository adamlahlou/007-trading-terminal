"""
Reconstructs what the yield, COT, and momentum gauges actually said at any
point in a historical window -- for backtesting the "allow continuation
re-entry if gauges agree" rule. Deliberately only uses the 3 gauges with
genuine historical time series (FRED, CFTC) -- news/geo/rate-tone would need
re-running LLM interpretation against archived data, a bigger separate task.

Critical correctness rule throughout: every lookup only considers data
dated ON OR BEFORE the target date. Using a later observation would be
lookahead bias -- silently "cheating" by letting the backtest know things
it couldn't have known yet, which would make the results meaningless.
"""
from __future__ import annotations
from datetime import date, timedelta
from . import fred_client, cot_client


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

    def votes_as_of(self, target_date: str, threshold: float = 0.1) -> dict:
        """Returns {gauge_name: -1/0/1} for whichever of the 3 gauges have
        data available yet at this point in the window."""
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
        return votes
