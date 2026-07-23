from __future__ import annotations
import logging
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from contextlib import asynccontextmanager

from datetime import datetime, timedelta, timezone
from . import db, oanda_client, calendar_schedule, backtest
from .scanner import run_scan, run_calendar_refresh, run_yield_refresh, run_news_refresh, run_cot_refresh, run_momentum_refresh, run_geo_refresh, run_rate_tone_refresh, BOX_SIZE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("007-terminal")

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # Every 15 minutes
    scheduler.add_job(lambda: asyncio.to_thread(run_scan), "cron", minute="0,15,30,45", id="fifteen_min_scan")
    # Calendar doesn't change minute to minute -- refresh every 6 hours
    scheduler.add_job(lambda: asyncio.to_thread(run_calendar_refresh), "cron", hour="*/6", id="calendar_refresh")
    # Yields move slowly (UK series is monthly) -- once a day is plenty
    scheduler.add_job(lambda: asyncio.to_thread(run_yield_refresh), "cron", hour="6", id="yield_refresh")
    # News moves faster -- every 3 hours, well within Marketaux's free 100/day
    scheduler.add_job(lambda: asyncio.to_thread(run_news_refresh), "cron", hour="*/3", id="news_refresh")
    # COT only updates weekly (Fridays) -- once a day easily catches it
    scheduler.add_job(lambda: asyncio.to_thread(run_cot_refresh), "cron", hour="7", id="cot_refresh")
    # NFP/CPI only update monthly -- once a day easily catches it
    scheduler.add_job(lambda: asyncio.to_thread(run_momentum_refresh), "cron", hour="8", id="momentum_refresh")
    # Geopolitical risk can move fast -- check more often than the GBP/USD news gauge
    scheduler.add_job(lambda: asyncio.to_thread(run_geo_refresh), "cron", hour="*", id="geo_refresh")
    # Rate decisions are rare -- checking every 4h easily catches one within a day of it happening
    scheduler.add_job(lambda: asyncio.to_thread(run_rate_tone_refresh), "cron", hour="*/4", id="rate_tone_refresh")

    # Precise scheduling: these release times are publicly known in advance,
    # so schedule an exact check ~20 min after each one instead of relying
    # only on the 4h poll above (which is kept as a safety net in case a
    # precise job doesn't fire for some reason, e.g. a redeploy at the wrong moment).
    now_utc = datetime.now(timezone.utc)
    for bank, decision_dt in calendar_schedule.get_rate_decision_datetimes():
        check_dt = decision_dt + timedelta(minutes=20)
        if check_dt > now_utc:
            scheduler.add_job(
                lambda: asyncio.to_thread(run_rate_tone_refresh),
                "date",
                run_date=check_dt,
                id=f"rate_tone_precise_{bank}_{decision_dt.date()}",
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

    async def _startup_rate_tone():
        try:
            await asyncio.to_thread(run_rate_tone_refresh)
        except Exception as e:
            logger.error(f"Startup rate tone refresh failed: {e}")

    asyncio.create_task(_startup_scan())
    asyncio.create_task(_startup_calendar())
    asyncio.create_task(_startup_yields())
    asyncio.create_task(_startup_news())
    asyncio.create_task(_startup_cot())
    asyncio.create_task(_startup_momentum())
    asyncio.create_task(_startup_geo())
    asyncio.create_task(_startup_rate_tone())
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
    """GET endpoint for an external scheduler (e.g. cron-job.org) to hit every 30 min."""
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
    state = db.get_rate_tone_state()
    return JSONResponse(state or {})


@app.post("/api/rate-tone-refresh-now")
async def rate_tone_refresh_now():
    try:
        result = await asyncio.to_thread(run_rate_tone_refresh)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Rate tone refresh failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/api/refresh-all")
async def refresh_all():
    """One button to refresh everything -- runs every gauge/data refresh
    concurrently and reports which ones succeeded or failed, rather than
    needing eight separate buttons scattered across the page."""
    jobs = {
        "scan": run_scan,
        "calendar": run_calendar_refresh,
        "yields": run_yield_refresh,
        "news": run_news_refresh,
        "cot": run_cot_refresh,
        "momentum": run_momentum_refresh,
        "geo": run_geo_refresh,
        "rate_tone": run_rate_tone_refresh,
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
async def api_backtest(days: int = 45, reversal_only: bool = False):
    """On-demand only -- not scheduled. Fetches historical OANDA data and
    simulates the Renko trade rules against it. Runs in a thread since it
    does real (slow-ish) API calls and computation.
    reversal_only=true tests the variant that only re-enters on a genuine
    reversal brick, rather than any same-direction continuation brick."""
    try:
        result = await asyncio.to_thread(backtest.run_backtest, days, 0.0022, reversal_only)
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Backtest failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=502)
