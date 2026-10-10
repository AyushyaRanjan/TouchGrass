"""DB-backed game service: reads/writes SQLite and applies rules.py.

Every function takes an open connection (`server.db.init_db()`) as its
first argument - no globals, so tests just pass a tmp-path connection.
AGENTS.md rule 8: XP/streak/level are never stored as counters; they are
derived from the submissions table on every read.
"""

import json
import sqlite3
from datetime import date, datetime, timezone

from ..config import get_settings
from ..tasks.library import get_library
from ..tasks.picker import pick_reroll, pick_task
from ..tasks.preferences import get_preferences
from . import rules

# Seed for the deterministic daily pick (picker only needs reproducibility).
RNG_SEED = "touchgrass"

VERDICTS = ("pass", "fail", "unsure", "retake", "rejected")


def _tz(tz: str | None) -> str:
    return tz or get_settings().TIMEZONE


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _difficulty(template_id: str) -> int:
    library = get_library()
    if template_id not in library:
        raise ValueError(f"unknown template_id in tasks table: {template_id!r}")
    return library[template_id].difficulty


def _task(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown task id {task_id}")
    return row


def _history(conn: sqlite3.Connection) -> list[tuple[date, str, str]]:
    """Past tasks as the picker wants them: (date, template_id, category)."""
    library = get_library()
    rows = conn.execute("SELECT date, template_id FROM tasks ORDER BY date").fetchall()
    history = []
    for row in rows:
        if row["template_id"] not in library:
            raise ValueError(f"unknown template_id in tasks table: {row['template_id']!r}")
        history.append(
            (date.fromisoformat(row["date"]), row["template_id"], library[row["template_id"]].category)
        )
    return history


# --- daily task -----------------------------------------------------------

def get_or_create_task(conn: sqlite3.Connection, day: date | str) -> sqlite3.Row:
    """The one task for a local date, picked deterministically on first use."""
    day = rules.as_date(day)
    row = conn.execute("SELECT * FROM tasks WHERE date = ?", (day.isoformat(),)).fetchone()
    if row is not None:
        return row

    template_id = pick_task(day, _history(conn), RNG_SEED, prefs=get_preferences(conn))
    try:
        conn.execute(
            "INSERT INTO tasks (date, template_id, rerolled, created_at) VALUES (?, ?, 0, ?)",
            (day.isoformat(), template_id, _now().isoformat()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()  # lost a race on the UNIQUE(date) - use the winner's row
        row = conn.execute("SELECT * FROM tasks WHERE date = ?", (day.isoformat(),)).fetchone()
        if row is None:
            raise
        return row
    return conn.execute("SELECT * FROM tasks WHERE date = ?", (day.isoformat(),)).fetchone()


def reroll_task(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row:
    """One free reroll per task per day (AGENTS.md game rules)."""
    task = _task(conn, task_id)
    if task["rerolled"]:
        raise ValueError("task already rerolled; one free reroll per day")

    day = date.fromisoformat(task["date"])
    template_id = pick_reroll(
        day, _history(conn), RNG_SEED, task["template_id"], prefs=get_preferences(conn)
    )
    if template_id is None:
        raise ValueError("no other template available to reroll to")

    conn.execute(
        "UPDATE tasks SET template_id = ?, rerolled = 1 WHERE id = ?",
        (template_id, task_id),
    )
    conn.commit()
    return _task(conn, task_id)


# --- submissions ----------------------------------------------------------

def record_submission(
    conn: sqlite3.Connection,
    *,
    client_submission_id: str,
    task_id: int,
    captured_at,
    received_at=None,
    image_path: str | None = None,
    thumb_path: str | None = None,
    phash: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    note: str | None = None,
    tz: str | None = None,
) -> sqlite3.Row:
    """Insert a pending submission after validating the capture rules.

    Computes local_date and attempt_number here; the verdict and XP come
    later via finalize_submission(). Raises ValueError with a human
    reason when the capture is invalid or the client id is a duplicate.
    """
    tz = _tz(tz)
    task = _task(conn, task_id)

    captured = rules.as_utc(captured_at)
    received = rules.as_utc(received_at) if received_at else _now()
    ok, reason = rules.validate_capture(captured, received, task["date"], tz)
    if not ok:
        raise ValueError(reason)

    # Attempt counting: retakes do not count as attempts (AGENTS.md), and
    # not-yet-finalized rows still count so a slow verification can't
    # grant a free first-attempt bonus.
    prior = conn.execute(
        "SELECT COUNT(*) FROM submissions "
        "WHERE task_id = ? AND (verdict IS NULL OR verdict != 'retake')",
        (task_id,),
    ).fetchone()[0]

    try:
        cursor = conn.execute(
            "INSERT INTO submissions (client_submission_id, task_id, image_path, "
            "thumb_path, phash, captured_at, local_date, lat, lon, note, received_at, "
            "status, verdict, hint, details_json, attempt_number, honor, xp_awarded, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, NULL, ?, 0, 0, NULL)",
            (
                client_submission_id, task_id, image_path, thumb_path, phash,
                captured.isoformat(),
                rules.local_date(captured, tz).isoformat(),
                lat, lon, note, received.isoformat(), prior + 1,
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ValueError(f"duplicate client_submission_id: {client_submission_id!r}") from exc

    return conn.execute("SELECT * FROM submissions WHERE id = ?", (cursor.lastrowid,)).fetchone()


def finalize_submission(
    conn: sqlite3.Connection,
    submission_id: int,
    verdict: str,
    hint: str | None = None,
    details: dict | None = None,
    phash: str | None = None,
) -> sqlite3.Row:
    """Store the pipeline verdict, flip status to done, award XP if pass.

    Also finalizes attempt_number = earlier non-retake submissions + 1
    (the record-time value is only an estimate made before earlier rows
    had their verdicts), matching AGENTS.md "retake is not an attempt".
    """
    if verdict not in VERDICTS:
        raise ValueError(f"unknown verdict {verdict!r}")
    row = conn.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown submission id {submission_id}")

    # Earlier submissions only (id < mine); a NULL verdict still counts so
    # an unfinished row can never grant a free first-attempt bonus.
    attempt = conn.execute(
        "SELECT COUNT(*) FROM submissions "
        "WHERE task_id = ? AND id < ? AND (verdict IS NULL OR verdict != 'retake')",
        (row["task_id"], submission_id),
    ).fetchone()[0] + 1

    # XP only for a pass, on the finalized attempt number.
    xp = 0
    if verdict == "pass":
        task = _task(conn, row["task_id"])
        xp = rules.xp_for_pass(_difficulty(task["template_id"]), attempt)

    conn.execute(
        "UPDATE submissions SET verdict = ?, hint = ?, details_json = ?, "
        "status = 'done', xp_awarded = ?, attempt_number = ?, "
        "phash = COALESCE(?, phash) WHERE id = ?",
        (
            verdict, hint,
            json.dumps(details) if details is not None else None,
            xp, attempt, phash, submission_id,
        ),
    )
    conn.commit()
    return conn.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()


def _honor_blocker(conn: sqlite3.Connection, submission_id: int) -> str | None:
    """Why this submission cannot be honored; None when it can be."""
    row = conn.execute(
        "SELECT s.*, t.template_id FROM submissions s "
        "JOIN tasks t ON t.id = s.task_id WHERE s.id = ?",
        (submission_id,),
    ).fetchone()
    if row is None:
        return f"unknown submission id {submission_id}"
    if row["status"] != "done":
        return "only a finished submission can be honored"
    if row["verdict"] == "pass":
        return "submission already passed; honor is for non-pass verdicts"
    if row["honor"]:
        return "submission already honored"

    # The task must not already be completed by any other submission.
    other = conn.execute(
        "SELECT COUNT(*) FROM submissions WHERE task_id = ? AND id != ? "
        "AND (verdict = 'pass' OR honor = 1)",
        (row["task_id"], submission_id),
    ).fetchone()[0]
    if other:
        return "task already completed"
    return None


def honor_available(conn: sqlite3.Connection, submission_id: int) -> bool:
    """Non-raising form of the complete_with_honor guards."""
    return _honor_blocker(conn, submission_id) is None


def complete_with_honor(conn: sqlite3.Connection, submission_id: int) -> sqlite3.Row:
    """"Mark done anyway": allowed once per task, for non-pass verdicts only."""
    blocker = _honor_blocker(conn, submission_id)
    if blocker:
        raise ValueError(blocker)

    row = conn.execute(
        "SELECT s.*, t.template_id FROM submissions s "
        "JOIN tasks t ON t.id = s.task_id WHERE s.id = ?",
        (submission_id,),
    ).fetchone()
    xp = rules.xp_for_honor(_difficulty(row["template_id"]))
    conn.execute(
        "UPDATE submissions SET honor = 1, xp_awarded = ? WHERE id = ?",
        (xp, submission_id),
    )
    conn.commit()
    return conn.execute("SELECT * FROM submissions WHERE id = ?", (submission_id,)).fetchone()


# --- reads ----------------------------------------------------------------

def completed_dates(conn: sqlite3.Connection) -> set[date]:
    """Local days with at least one completed task (pass OR honor)."""
    rows = conn.execute(
        "SELECT DISTINCT local_date FROM submissions "
        "WHERE (verdict = 'pass' OR honor = 1) AND local_date IS NOT NULL"
    ).fetchall()
    return {date.fromisoformat(row["local_date"]) for row in rows}


def get_stats(conn: sqlite3.Connection, today_local: date, days: int = 30) -> dict:
    """Everything the app shows, derived from the submissions table."""
    xp = conn.execute("SELECT COALESCE(SUM(xp_awarded), 0) FROM submissions").fetchone()[0]

    rows = conn.execute(
        "SELECT s.local_date, t.template_id FROM submissions s "
        "JOIN tasks t ON t.id = s.task_id "
        "WHERE (s.verdict = 'pass' OR s.honor = 1) AND s.local_date IS NOT NULL"
    ).fetchall()

    library = get_library()
    completions = [
        (date.fromisoformat(row["local_date"]), library[row["template_id"]].category)
        for row in rows
    ]
    completed_days = [day for day, _ in completions]

    return {
        "xp": xp,
        "level": rules.level(xp),
        "streak": rules.compute_streak(completed_days, today_local),
        "tasks_this_month": rules.tasks_this_month(completed_days, today_local),
        "category_counts": rules.category_counts(completions, days, today_local),
    }


def get_history(conn: sqlite3.Connection, limit: int = 20, offset: int = 0) -> list[dict]:
    """Most recent submissions first, joined with their task."""
    rows = conn.execute(
        "SELECT s.id, s.client_submission_id, s.task_id, t.date AS task_date, "
        "t.template_id, s.verdict, s.honor, s.xp_awarded, s.status, "
        "s.captured_at, s.local_date, s.received_at, s.note "
        "FROM submissions s LEFT JOIN tasks t ON t.id = s.task_id "
        "ORDER BY s.id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]
