# 007 Trading Terminal (v1)

GBP/USD Renko brick engine — 30min candles from OANDA, traditional Renko
reversal rules, Bloomberg-terminal-styled dashboard. No indicators or macro
layer yet (that's v2) -- this is just a faithful, gap-free brick builder.

## Environment variables required

- `OANDA_API_TOKEN` -- your OANDA practice account's Personal Access Token
- `OANDA_ACCOUNT_ID` -- your practice account ID (looks like `101-004-XXXXXXX-001`)
- `OANDA_ENV` -- `practice` (default) or `live`
- `BOX_SIZE` -- Renko box size, defaults to `0.0022` to match your TradingView chart

## Run locally

```bash
pip install -r requirements.txt
export OANDA_API_TOKEN=your_token_here
export OANDA_ACCOUNT_ID=your_account_id_here
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000

## Deploy (Render, free tier)

1. Push this folder to a GitHub repo.
2. On Render: New -> Blueprint -> point at the repo (reads `render.yaml`).
3. Once created, go to the service's **Environment** tab and add:
   - `OANDA_API_TOKEN`
   - `OANDA_ACCOUNT_ID`
4. Deploy.

### Keep it scanning every 30 minutes (free tier)

Same pattern as the crypto scanner -- free Render instances sleep when idle,
so an external ping wakes them up on schedule:

1. https://cron-job.org (free) -> create an account
2. New cron job:
   - URL: `https://<your-app>.onrender.com/api/cron/scan`
   - Schedule: every 30 minutes
3. Save.

## How the brick engine works

- Fetches new 30min GBP/USD candles from OANDA since the last processed
  candle (no gaps, no re-fetching everything each time)
- Approximates each candle's intra-bar path (open -> low -> high -> close,
  or open -> high -> low -> close depending on direction) and feeds each
  point through the brick state machine
- A brick continues in the current direction once price moves a full box
  size past the last brick's close; a reversal requires a full 2x box move
  (the "traditional" Renko rule, same as your TradingView chart)
- Engine state (last close, direction, last candle processed) persists in
  SQLite between scans, so bricks are continuous across restarts

## Project structure

- `app/oanda_client.py` -- OANDA v20 API candle fetching
- `app/renko.py` -- the brick construction algorithm itself
- `app/scanner.py` -- orchestrates fetch -> build bricks -> save
- `app/db.py` -- SQLite storage (bricks + persisted engine state)
- `app/main.py` -- FastAPI app, routes, scheduler
- `templates/`, `static/` -- the dashboard
