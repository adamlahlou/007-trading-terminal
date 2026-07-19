from __future__ import annotations
import sqlite3
import os

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
