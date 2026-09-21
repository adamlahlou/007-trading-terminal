from __future__ import annotations
import logging
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from contextlib import asynccontextmanager

from datetime import datetime, timedelta, timezone, date
from . import db, oanda_client, calendar_schedule, backtest, oanda_execution, scheduler_registry
from .scanner import run_scan, run_calendar_refresh, run_yield_refresh, run_news_refresh, run_cot_refresh, run_momentum_refresh, run_geo_refresh, run_fed_rate_tone_refresh, run_boe_rate_tone_refresh, run_fomc_minutes_refresh, BOX_SIZE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("007-terminal")

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    scheduler_registry.set_scheduler(scheduler)
    logger.info(f"Live execution enabled: {oanda_execution.LIVE_EXECUTION_ENABLED} (risk {oanda_execution.RISK_PCT_PER_TRADE}% per trade)")

    scheduler.add_job(lambda: asyncio.to_thread(run_scan), "cron", minute="0,15,30,45", id="fifteen_min_scan")
    scheduler.add_job(lambda: asyncio.to_thread(run_calendar_refresh), "cron", hour="*/6", id="calendar_refresh")
    scheduler.add_job(lambda: asyncio.to_thread(run_yield_refresh), "cron", hour="6", id="yield_refresh")
    scheduler.add_job(lambda: asyncio.to_thread(run_news_refresh), "cron", hour="*", id="news_refresh")
    scheduler.add_job(lambda: asyncio.to_thread(run_cot_refresh), "cron", hour="7", id="cot_refresh")
    scheduler.add_job(lambda: asyncio.to_thread(run_cot_refresh), "cron", day_of_week="fri", hour="19", minute="45", id="cot_refresh_friday")
    scheduler.add_job(lambda: asyncio.to_thread(run_momentum_refresh), "cron", hour="8", id="momentum_refresh")
    scheduler.add_job(lambda: asyncio.to_thread(run_geo_refresh), "cron", hour="*", id="geo_refresh")
    # Fed and BoE checked independently -- they used to share one "most
    # recent of either bank" lookup, but BoE meets just 1 day after Fed in
    # 5 of 8 months this year, so BoE always won that comparison and Fed's
    # decision was never actually processed. Now both run on their own.
    scheduler.add_job(lambda: asyncio.to_thread(run_fed_rate_tone_refresh), "cron", hour="*/4", id="fed_rate_tone_refresh")
    scheduler.add_job(lambda: asyncio.to_thread(run_boe_rate_tone_refresh), "cron", hour="*/4", id="boe_rate_tone_refresh")
    # FOMC Minutes: rare (8x/year), so a daily safety-net check plus precise
    # scheduling below (same pattern as everything else) is more than enough.
    scheduler.add_job(lambda: asyncio.to_thread(run_fomc_minutes_refresh), "cron", hour="15", id="fomc_minutes_refresh")

    now_utc = datetime.now(timezone.utc)
    for bank, decision_dt in calendar_schedule.get_rate_decision_datetimes():
        check_dt = decision_dt + timedelta(minutes=20)
        if check_dt > now_utc:
            refresh_fn = run_fed_rate_tone_refresh if bank == "Fed" else run_boe_rate_tone_refresh
            scheduler.add_job(
                lambda fn=refresh_fn: asyncio.to_thread(fn),
                "date",
                run_date=check_dt,
                id=f"rate_tone_precise_{bank}_{decision_dt.date()}",
            )

    nfp_start = now_utc.date()
    nfp_end = (now_utc + timedelta(days=365)).date()
    for nfp_dt in calendar_schedule.get_nfp_datetimes(nfp_start, nfp_end):
        check_dt = nfp_dt + timedelta(minutes=20)
        if check_dt > now_utc:
            scheduler.add_job(
                lambda: asyncio.to_thread(run_momentum_refresh),
                "date",
                run_date=check_dt,
                id=f"nfp_precise_{nfp_dt.date()}",
            )

    # FOMC Minutes precise scheduling -- known release time (meeting + 3
    # weeks, 2pm ET), so schedule an exact check ~20 min after each one for
    # every meeting on the books, rather than relying only on the daily poll.
    for meeting_date, release_dt in calendar_schedule.get_fomc_minutes_release_datetimes():
        check_dt = release_dt + timedelta(minutes=20)
        if check_dt > now_utc:
            scheduler.add_job(
                lambda: asyncio.to_thread(run_fomc_minutes_refresh),
                "date",
                run_date=check_dt,
                id=f"fomc_minutes_precise_{meeting_date}",
            )

    scheduler.start()

    async def _startup_scan():
        try:
            await asyncio.to_thread(run_scan)
        except Exception as e:
            logger.error(f"Startup scan failed: {e}")

    async def _startup_calendar():
        try:
            await asyncio.to_thread(run_calendar_refresh)
        except Exception as e:
            logger.error(f"Startup calendar refresh failed: {e}")

    async def _startup_yields():
        try:
            await asyncio.to_thread(run_yield_refresh)
        except Exception as e:
            logger.error(f"Startup yield refresh failed: {e}")

    async def _startup_news():
        try:
            await asyncio.to_thread(run_news_refresh)
        except Exception as e:
            logger.error(f"Startup news refresh failed: {e}")

    async def _startup_cot():
        try:
            await asyncio.to_thread(run_cot_refresh)
        except Exception as e:
            logger.error(f"Startup COT refresh failed: {e}")

    async def _startup_momentum():
        try:
            await asyncio.to_thread(run_momentum_refresh)
        except Exception as e:
            logger.error(f"Startup momentum refresh failed: {e}")

    async def _startup_geo():
        try:
            await asyncio.to_thread(run_geo_refresh)
        except Exception as e:
            logger.error(f"Startup geopolitical refresh failed: {e}")

    async def _startup_fed_rate_tone():
        try:
            await asyncio.to_thread(run_fed_rate_tone_refresh)
        except Exception as e:
            logger.error(f"Startup Fed rate tone refresh failed: {e}")

    async def _startup_boe_rate_tone():
        try:
            await asyncio.to_thread(run_boe_rate_tone_refresh)
        except Exception as e:
            logger.error(f"Startup BoE rate tone refresh failed: {e}")

    async def _startup_fomc_minutes():
        try:
            await asyncio.to_thread(run_fomc_minutes_refresh)
        except Exception as e:
            logger.error(f"Startup FOMC minutes refresh failed: {e}")

    asyncio.create_task(_startup_scan())
    asyncio.create_task(_startup_calendar())
    asyncio.create_task(_startup_yields())
    asyncio.create_task(_startup_news())
    asyncio.create_task(_startup_cot())
    asyncio.create_task(_startup_momentum())
    asyncio.create_task(_startup_geo())
    asyncio.create_task(_startup_fed_rate_tone())
    asyncio.create_task(_startup_boe_rate_tone())
    asyncio.create_task(_startup_fomc_minutes())
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/api/bricks")
async def api_bricks():
    return JSONResponse({"box_size": BOX_SIZE, "bricks": db.get_recent_bricks(limit=200)})


@app.get("/api/price")
async def api_price():
    try:
        price = await asyncio.to_thread(oanda_client.fetch_current_price)
        return JSONResponse(price)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/scan-now")
async def scan_now():
    try:
        result = await asyncio.to_thread(run_scan)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Scan failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/cron/scan")
async def cron_scan():
    try:
        result = await asyncio.to_thread(run_scan)
        return JSONResponse({"ok": True, **result})
    except Exception as e:
        logger.error(f"Scan failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)


@app.get("/api/calendar")
async def api_calendar():
    return JSONResponse({"events": db.get_calendar_events()})


@app.post("/api/calendar-refresh-now")
async def calendar_refresh_now():
    try:
        result = await asyncio.to_thread(run_calendar_refresh)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Calendar refresh failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/yields")
async def api_yields():
    state = db.get_yield_state()
    return JSONResponse(state or {})


@app.post("/api/yields-refresh-now")
async def yields_refresh_now():
    try:
        result = await asyncio.to_thread(run_yield_refresh)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Yield refresh failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/news")
async def api_news():
    state = db.get_news_state()
    return JSONResponse(state or {})


@app.post("/api/news-refresh-now")
async def news_refresh_now():
    try:
        result = await asyncio.to_thread(run_news_refresh)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"News refresh failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/cot")
async def api_cot():
    state = db.get_cot_state()
    return JSONResponse(state or {})


@app.post("/api/cot-refresh-now")
async def cot_refresh_now():
    try:
        result = await asyncio.to_thread(run_cot_refresh)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"COT refresh failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/momentum")
async def api_momentum():
    state = db.get_momentum_state()
    return JSONResponse(state or {})


@app.post("/api/momentum-refresh-now")
async def momentum_refresh_now():
    try:
        result = await asyncio.to_thread(run_momentum_refresh)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Momentum refresh failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/geo")
async def api_geo():
    state = db.get_geo_state()
    return JSONResponse(state or {})


@app.post("/api/geo-refresh-now")
async def geo_refresh_now():
    try:
        result = await asyncio.to_thread(run_geo_refresh)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Geopolitical refresh failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/rate-tone")
async def api_rate_tone():
    """Fed rate tone. See /api/boe-rate-tone for BoE's own, genuinely
    independent gauge -- these used to share one "most recent of either
    bank" lookup that always favored BoE (it meets 1 day after Fed in 5 of
    8 months this year), so Fed's own decision was never actually processed."""
    state = db.get_rate_tone_state()
    return JSONResponse(state or {})


@app.post("/api/rate-tone-refresh-now")
async def rate_tone_refresh_now(meeting_date: str = None):
    """meeting_date=YYYY-MM-DD (optional): manually backfill a specific
    past Fed meeting the routine checks missed (bypasses the normal 3-day
    lookback entirely), e.g. ?meeting_date=2026-09-16"""
    try:
        override = date.fromisoformat(meeting_date) if meeting_date else None
        result = await asyncio.to_thread(run_fed_rate_tone_refresh, True, override)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Fed rate tone refresh failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/boe-rate-tone")
async def api_boe_rate_tone():
    state = db.get_boe_rate_tone_state()
    return JSONResponse(state or {})


@app.post("/api/boe-rate-tone-refresh-now")
async def boe_rate_tone_refresh_now(meeting_date: str = None):
    """Same meeting_date override as /api/rate-tone-refresh-now, for BoE."""
    try:
        override = date.fromisoformat(meeting_date) if meeting_date else None
        result = await asyncio.to_thread(run_boe_rate_tone_refresh, True, override)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"BoE rate tone refresh failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/fomc-minutes")
async def api_fomc_minutes():
    """FOMC Minutes -- a separate, later-released document from the brief
    statement, tracked independently (not merged into /api/rate-tone)."""
    state = db.get_fomc_minutes_state()
    return JSONResponse(state or {})


@app.post("/api/fomc-minutes-refresh-now")
async def fomc_minutes_refresh_now():
    try:
        result = await asyncio.to_thread(run_fomc_minutes_refresh, True)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"FOMC minutes refresh failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/refresh-all")
async def refresh_all():
    jobs = {
        "scan": run_scan,
        "calendar": run_calendar_refresh,
        "yields": run_yield_refresh,
        "news": run_news_refresh,
        "cot": run_cot_refresh,
        "momentum": run_momentum_refresh,
        "geo": run_geo_refresh,
        "fed_rate_tone": lambda: run_fed_rate_tone_refresh(force=True),
        "boe_rate_tone": lambda: run_boe_rate_tone_refresh(force=True),
        "fomc_minutes": lambda: run_fomc_minutes_refresh(force=True),
    }

    async def _run(name, fn):
        try:
            await asyncio.to_thread(fn)
            return name, True, None
        except Exception as e:
            logger.error(f"Refresh-all: {name} failed: {e}")
            return name, False, str(e)

    results = await asyncio.gather(*(_run(name, fn) for name, fn in jobs.items()))
    return JSONResponse({
        "results": {name: {"ok": ok, "error": err} for name, ok, err in results}
    })


@app.get("/api/backtest")
async def api_backtest(days: int = 45, reversal_only: bool = False, continuation_override: str = None, gate_all_entries: bool = False, trailing_mode: str = "tight", gate_threshold: int = 2, start_date: str = None, end_date: str = None, debug_gauges: bool = False, gauge_set: str = "yield_cot_momentum"):
    try:
        result = await asyncio.to_thread(backtest.run_backtest, days, 0.0022, reversal_only, continuation_override, gate_all_entries, trailing_mode, gate_threshold, start_date, end_date, debug_gauges, gauge_set)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Backtest failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/api/live-trades")
async def api_live_trades():
    state = db.get_live_trade_state()
    events = db.get_live_trade_events(limit=200)
    return JSONResponse({"state": state, "events": events})


@app.get("/api/live-execution")
async def api_live_execution():
    try:
        real_position = await asyncio.to_thread(oanda_execution.get_open_position)
    except Exception as e:
        real_position = {"error": str(e)}
    events = db.get_live_execution_events(limit=100)
    return JSONResponse({
        "live_execution_enabled": oanda_execution.LIVE_EXECUTION_ENABLED,
        "risk_pct_per_trade": oanda_execution.RISK_PCT_PER_TRADE,
        "real_open_position": real_position,
        "events": events,
    })


@app.post("/api/emergency-flatten")
async def emergency_flatten():
    try:
        result = await asyncio.to_thread(oanda_execution.close_position)
        logger.warning(f"EMERGENCY FLATTEN triggered manually: {result}")
        return JSONResponse({"ok": True, "result": result})
    except Exception as e:
        logger.error(f"Emergency flatten failed: {e}")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=502)


@app.get("/api/gauge-history")
async def api_gauge_history(gauge: str = None, limit: int = 1000):
    """Real history of gauge readings over time -- foundation for finding
    genuine correlations between what the gauges said and what price/
    trades actually did afterward. gauge=geo (etc.) filters to one gauge;
    omit for every gauge's history together."""
    return JSONResponse({"history": db.get_gauge_history(gauge_name=gauge, limit=limit)})
