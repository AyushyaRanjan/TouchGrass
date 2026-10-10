"""DEV ONLY: delete all of today's submissions so you can re-test today's task.

    uv run python scripts/reset_today.py
    uv run python scripts/reset_today.py --yes     # skip the y/N question

Removes the database rows and the image files (photo + thumbnail) of every
submission that belongs to today's task (local date in TIMEZONE). Today's
task itself, its reroll state and all other days are left alone.

Image files are only deleted when they sit under data/images/ (AGENTS.md
rule 5: photos live nowhere else).
"""

import argparse
import sys
from pathlib import Path

# Run as a script from repo root: make `server` importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import db  # noqa: E402
from server.config import get_settings  # noqa: E402
from server.game import rules  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
IMAGES_DIR = ROOT / "data" / "images"


def todays_submissions(conn, today) -> list:
    """Submission rows belonging to the task dated `today` (local date)."""
    return conn.execute(
        "SELECT s.id, s.image_path, s.thumb_path FROM submissions s "
        "JOIN tasks t ON t.id = s.task_id WHERE t.date = ? ORDER BY s.id",
        (today.isoformat(),),
    ).fetchall()


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def delete_submissions(conn, rows, images_dir: Path = IMAGES_DIR) -> tuple[int, int]:
    """Delete the rows and their image files; returns (rows, files) deleted."""
    files = 0
    for row in rows:
        for stored in (row["image_path"], row["thumb_path"]):
            if not stored:
                continue
            path = Path(stored)
            if not path.is_absolute():
                path = ROOT / path  # legacy relative rows
            if _inside(path, images_dir) and path.is_file():
                path.unlink()
                files += 1
    conn.executemany("DELETE FROM submissions WHERE id = ?", [(row["id"],) for row in rows])
    conn.commit()
    return len(rows), files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DEV ONLY: delete today's submissions.")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--db", default=None, help="database path (default data/touchgrass.db)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    conn = db.init_db(args.db)
    today = rules.today_local(get_settings().TIMEZONE)
    rows = todays_submissions(conn, today)

    print("WARNING: dev only. This permanently deletes today's submissions")
    print(f"(database rows and image files) for {today.isoformat()}.")
    if not rows:
        print("Nothing to delete: no submissions for today.")
        return 0
    print(f"Found {len(rows)} submission(s): ids {[row['id'] for row in rows]}.")

    if not args.yes and input("Delete them? [y/N] ").strip().lower() != "y":
        print("Cancelled. Nothing was deleted.")
        return 1

    deleted, files = delete_submissions(conn, rows)
    print(f"Deleted {deleted} submission(s) and {files} image file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
