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
from .scanner import run_scan, BOX_SIZE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("007-terminal")

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # Every 30 minutes, on the hour and half-hour
    scheduler.add_job(lambda: asyncio.to_thread(run_scan), "cron", minute="0,30", id="thirty_min_scan")
    scheduler.start()

    async def _startup_scan():
        try:
            await asyncio.to_thread(run_scan)
        except Exception as e:
            logger.error(f"Startup scan failed: {e}")

    asyncio.create_task(_startup_scan())
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
