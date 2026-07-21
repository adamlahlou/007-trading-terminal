from __future__ import annotations
import sqlite3
import os
import json

DB_PATH = os.environ.get("SCANNER_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "terminal.db"))


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bricks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seq INTEGER NOT NULL,
            direction INTEGER NOT NULL,
            open_price REAL NOT NULL,
            close_price REAL NOT NULL,
            formed_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS engine_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            box_size REAL NOT NULL,
            anchor REAL,
            last_close REAL,
            direction INTEGER NOT NULL DEFAULT 0,
            last_candle_time TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS calendar_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_time TEXT NOT NULL,
            country TEXT NOT NULL,
            event TEXT NOT NULL,
            impact TEXT NOT NULL,
            actual TEXT,
            estimate TEXT,
            prev TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS yield_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            us_yield REAL NOT NULL,
            us_date TEXT NOT NULL,
            uk_yield REAL NOT NULL,
            uk_date TEXT NOT NULL,
            spread REAL NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS news_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            score REAL NOT NULL,
            article_count INTEGER NOT NULL,
            headlines_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cot_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            report_date TEXT NOT NULL,
            lev_long REAL NOT NULL,
            lev_short REAL NOT NULL,
            lev_net REAL NOT NULL,
            prior_net REAL,
            gauge_score REAL NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def load_state(box_size: float) -> dict:
    conn = get_conn()
    row = conn.execute("SELECT * FROM engine_state WHERE id = 1").fetchone()
    conn.close()
    if row is None:
        return {"box_size": box_size, "anchor": None, "last_close": None, "direction": 0, "last_candle_time": None}
    return dict(row)


def save_state(box_size: float, anchor, last_close, direction: int, last_candle_time: str | None):
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO engine_state (id, box_size, anchor, last_close, direction, last_candle_time)
        VALUES (1, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            box_size=excluded.box_size,
            anchor=excluded.anchor,
            last_close=excluded.last_close,
            direction=excluded.direction,
            last_candle_time=excluded.last_candle_time
        """,
        (box_size, anchor, last_close, direction, last_candle_time),
    )
    conn.commit()
    conn.close()


def append_bricks(bricks: list[dict]):
    if not bricks:
        return
    conn = get_conn()
    last_seq_row = conn.execute("SELECT MAX(seq) AS m FROM bricks").fetchone()
    next_seq = (last_seq_row["m"] or 0) + 1
    conn.executemany(
        "INSERT INTO bricks (seq, direction, open_price, close_price, formed_at) VALUES (?, ?, ?, ?, ?)",
        [
            (next_seq + i, b["direction"], b["open"], b["close"], b["formed_at"])
            for i, b in enumerate(bricks)
        ],
    )
    conn.commit()
    conn.close()


def get_recent_bricks(limit: int = 200) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM bricks ORDER BY seq DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    rows = list(reversed(rows))
    return [
        {
            "seq": r["seq"],
            "direction": r["direction"],
            "open": r["open_price"],
            "close": r["close_price"],
            "formed_at": r["formed_at"],
        }
        for r in rows
    ]


def get_brick_count() -> int:
    conn = get_conn()
    row = conn.execute("SELECT COUNT(*) AS c FROM bricks").fetchone()
    conn.close()
    return row["c"]


def replace_calendar_events(events: list[dict]):
    """Wipes and replaces the calendar cache with a fresh fetch (it's a
    rolling window of upcoming events, not a historical log)."""
    conn = get_conn()
    conn.execute("DELETE FROM calendar_events")
    conn.executemany(
        """
        INSERT INTO calendar_events (event_time, country, event, impact, actual, estimate, prev)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (e["time"], e["country"], e["event"], e["impact"],
             str(e["actual"]) if e["actual"] is not None else None,
             str(e["estimate"]) if e["estimate"] is not None else None,
             str(e["prev"]) if e["prev"] is not None else None)
            for e in events
        ],
    )
    conn.commit()
    conn.close()


def get_calendar_events() -> list[dict]:
    conn = get_conn()
    rows = conn.execute("SELECT * FROM calendar_events ORDER BY event_time ASC").fetchall()
    conn.close()
    return [
        {
            "time": r["event_time"],
            "country": r["country"],
            "event": r["event"],
            "impact": r["impact"],
            "actual": r["actual"],
            "estimate": r["estimate"],
            "prev": r["prev"],
        }
        for r in rows
    ]


def save_yield_state(us_yield, us_date, uk_yield, uk_date, spread, updated_at):
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO yield_state (id, us_yield, us_date, uk_yield, uk_date, spread, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            us_yield=excluded.us_yield, us_date=excluded.us_date,
            uk_yield=excluded.uk_yield, uk_date=excluded.uk_date,
            spread=excluded.spread, updated_at=excluded.updated_at
        """,
        (us_yield, us_date, uk_yield, uk_date, spread, updated_at),
    )
    conn.commit()
    conn.close()


def get_yield_state() -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM yield_state WHERE id = 1").fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)


def save_news_state(score, article_count, headlines, updated_at):
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO news_state (id, score, article_count, headlines_json, updated_at)
        VALUES (1, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            score=excluded.score, article_count=excluded.article_count,
            headlines_json=excluded.headlines_json, updated_at=excluded.updated_at
        """,
        (score, article_count, json.dumps(headlines), updated_at),
    )
    conn.commit()
    conn.close()


def get_news_state() -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM news_state WHERE id = 1").fetchone()
    conn.close()
    if row is None:
        return None
    return {
        "score": row["score"],
        "article_count": row["article_count"],
        "headlines": json.loads(row["headlines_json"]),
        "updated_at": row["updated_at"],
    }


def save_cot_state(report_date, lev_long, lev_short, lev_net, prior_net, gauge_score, updated_at):
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO cot_state (id, report_date, lev_long, lev_short, lev_net, prior_net, gauge_score, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            report_date=excluded.report_date, lev_long=excluded.lev_long,
            lev_short=excluded.lev_short, lev_net=excluded.lev_net,
            prior_net=excluded.prior_net, gauge_score=excluded.gauge_score,
            updated_at=excluded.updated_at
        """,
        (report_date, lev_long, lev_short, lev_net, prior_net, gauge_score, updated_at),
    )
    conn.commit()
    conn.close()


def get_cot_state() -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT * FROM cot_state WHERE id = 1").fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)
