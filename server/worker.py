"""Background verification worker (AGENTS.md rule 2: exactly one worker).

Jobs live in the submissions table, not in memory, so a crash or restart
mid-job is safe: on startup anything stuck in "processing" goes back to
"pending" and the loop picks it up again.

Everything runs in one event loop (the server's), which keeps the
module-level asyncio.Lock in verify/vlm.py bound to a single loop.

Started from the FastAPI lifespan in server/main.py.
"""

import asyncio
import sys
from pathlib import Path

from . import db
from .tasks.library import get_library, rubric_for
from .verify.pipeline import verify_photo

# How often the loop looks for work (tests lower this).
INTERVAL = 2.0

# Repo root, to resolve image_path values stored relative to it.
ROOT = Path(__file__).resolve().parents[1]


def reset_stuck() -> None:
    """Back to pending after a restart killed us mid-job."""
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE submissions SET status = 'pending' WHERE status = 'processing'"
        )
        conn.commit()
    finally:
        conn.close()


def claim_next(conn) -> int | None:
    """Atomically claim the oldest pending submission; None when idle."""
    row = conn.execute(
        "SELECT id FROM submissions WHERE status = 'pending' ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    cursor = conn.execute(
        "UPDATE submissions SET status = 'processing', error = NULL "
        "WHERE id = ? AND status = 'pending'",
        (row["id"],),
    )
    conn.commit()
    return row["id"] if cursor.rowcount == 1 else None


async def process_one() -> bool:
    """Verify one pending submission end to end. True if work was done."""
    from .game import service  # deferred: avoids an import cycle at module load

    conn = db.connect()
    try:
        submission_id = claim_next(conn)
        if submission_id is None:
            return False

        try:
            row = conn.execute(
                "SELECT s.task_id, s.image_path, t.template_id "
                "FROM submissions s JOIN tasks t ON t.id = s.task_id "
                "WHERE s.id = ?",
                (submission_id,),
            ).fetchone()

            image_file = Path(row["image_path"])
            if not image_file.is_absolute():
                image_file = ROOT / image_file  # legacy relative rows
            image_bytes = image_file.read_bytes()

            # Hashes of earlier submissions so cross-task duplicates work.
            known_hashes = [
                (r["id"], r["task_id"], r["phash"])
                for r in conn.execute(
                    "SELECT id, task_id, phash FROM submissions WHERE phash IS NOT NULL"
                )
            ]

            library = get_library()
            criteria = rubric_for(library[row["template_id"]])
            # task_id must match the type stored in known_hashes (int).
            result = await verify_photo(image_bytes, criteria, row["task_id"], known_hashes)

            service.finalize_submission(
                conn,
                submission_id,
                result["verdict"],
                hint=result["hint"],
                details=result,
                phash=result.get("phash"),
            )
        except Exception as exc:  # noqa: BLE001 - one bad job must not kill the loop
            conn.execute(
                "UPDATE submissions SET status = 'error', error = ? WHERE id = ?",
                (f"{type(exc).__name__}: {exc}", submission_id),
            )
            conn.commit()
            print(f"worker: submission {submission_id} failed: {exc}", file=sys.stderr)
        return True
    finally:
        conn.close()


async def worker_loop() -> None:
    """Forever: take the oldest pending job, every INTERVAL seconds."""
    while True:
        try:
            await process_one()
        except Exception as exc:  # noqa: BLE001 - never let the loop die
            print(f"worker: loop error: {exc}", file=sys.stderr)
        await asyncio.sleep(INTERVAL)
