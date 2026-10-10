"""SQLite access layer (stdlib sqlite3, no ORM).

One small helper (`connect`) + one init function (`init_db`). WAL mode
so the future worker can write while the HTTP layer reads; foreign keys
on so tasks.submissions stays consistent.

AGENTS.md rule 9: this module only runs SQL. Game logic lives in
server/game/, photo verification in server/verify/.
"""

import sqlite3
from pathlib import Path

# Repo-root/data/ is gitignored (db + uploaded images live here).
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "touchgrass.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY,
    date        TEXT UNIQUE NOT NULL,   -- local date, YYYY-MM-DD
    template_id TEXT NOT NULL,          -- id from server/tasks/library.yaml
    rerolled    INTEGER DEFAULT 0,      -- 0/1; one free reroll per day
    created_at  TEXT NOT NULL           -- UTC ISO
);

CREATE TABLE IF NOT EXISTS submissions (
    id                  INTEGER PRIMARY KEY,
    client_submission_id TEXT UNIQUE NOT NULL,  -- id from the mobile app
    task_id             INTEGER NOT NULL REFERENCES tasks(id),
    image_path          TEXT,           -- under data/images/ only (rule 5)
    thumb_path          TEXT,
    phash               TEXT,           -- perceptual hash hex
    captured_at         TEXT NOT NULL,  -- UTC ISO, from the photo
    local_date          TEXT,           -- local_date(captured_at, TIMEZONE)
    lat                 REAL,
    lon                 REAL,
    note                TEXT,
    received_at         TEXT NOT NULL,  -- UTC ISO, server receipt
    status              TEXT NOT NULL CHECK (status IN ('pending','processing','done','error')),
    verdict             TEXT CHECK (verdict IN ('pass','fail','unsure','retake','rejected')),
    hint                TEXT,
    details_json        TEXT,           -- pipeline details as JSON
    attempt_number      INTEGER,        -- retakes do not count as attempts
    honor               INTEGER DEFAULT 0,  -- 0/1 "mark done anyway"
    xp_awarded          INTEGER DEFAULT 0,  -- per-row XP; totals are SUMmed (rule 8)
    error               TEXT
);

CREATE INDEX IF NOT EXISTS idx_submissions_task ON submissions(task_id);

CREATE TABLE IF NOT EXISTS preferences (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    category_weights    TEXT NOT NULL,
    social_comfort_max  INTEGER NOT NULL,
    max_minutes         INTEGER
);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Open a connection with Row access and sane pragmas.

    check_same_thread=False: FastAPI runs sync dependencies in a threadpool
    and async endpoints on the event loop, so one connection can legitimately
    be touched from two threads across a request boundary. Each request and
    worker job opens its own connection, and WAL serializes writes.
    """
    conn = sqlite3.connect(str(path or DEFAULT_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path: Path | str | None = None) -> sqlite3.Connection:
    """Create tables if missing and return an open connection."""
    conn = connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
