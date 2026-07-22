from __future__ import annotations
import logging
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from contextlib import asynccontextmanager

from . import db, oanda_client
from .scanner import run_scan, run_calendar_refresh, run_yield_refresh, run_news_refresh, run_cot_refresh, run_momentum_refresh, run_geo_refresh, BOX_SIZE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("007-terminal")

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # Every 30 minutes, on the hour and half-hour
    scheduler.add_job(lambda: asyncio.to_thread(run_scan), "cron", minute="0,30", id="thirty_min_scan")
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
    scheduler.add_job(lambda: asyncio.to_thread(run_geo_refresh), "cron", hour="*/2", id="geo_refresh")
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

    asyncio.create_task(_startup_scan())
    asyncio.create_task(_startup_calendar())
    asyncio.create_task(_startup_yields())
    asyncio.create_task(_startup_news())
    asyncio.create_task(_startup_cot())
    asyncio.create_task(_startup_momentum())
    asyncio.create_task(_startup_geo())
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
