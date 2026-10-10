"""Insert fake completed days so stats/history look alive in the app.

    uv run python scripts/fake_days.py             # only on an empty database
    uv run python scripts/fake_days.py --force     # wipe tasks + submissions, refill
    uv run python scripts/fake_days.py --days 30

Ends yesterday and skips a couple of deterministic gaps (offsets 5 and
13) so the streak is not just N days. Uses the real task picker, the
real capture validation and the real XP rules - only the photo is skipped.
"""

import argparse
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Run as a script from repo root: make `server` importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server import db  # noqa: E402
from server.config import get_settings  # noqa: E402
from server.game import service  # noqa: E402

# Deterministic skipped days (offset from yesterday) = the "couple of gaps".
GAPS = {5, 13}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seed fake completed days.")
    parser.add_argument("--days", type=int, default=20, help="how many days back (default 20)")
    parser.add_argument("--force", action="store_true", help="wipe tasks + submissions first")
    parser.add_argument("--db", default=None, help="database path (default data/touchgrass.db)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    conn = db.init_db(args.db)

    existing = conn.execute(
        "SELECT (SELECT COUNT(*) FROM tasks) + (SELECT COUNT(*) FROM submissions)"
    ).fetchone()[0]
    if existing and not args.force:
        print("Database is not empty; rerun with --force to wipe tasks and submissions.")
        return 1
    if args.force:
        conn.execute("DELETE FROM submissions")
        conn.execute("DELETE FROM tasks")
        conn.commit()

    tz = get_settings().TIMEZONE
    yesterday = datetime.now(ZoneInfo(tz)).date() - timedelta(days=1)
    inserted = 0
    for offset in range(args.days):
        if offset in GAPS:
            continue
        day = yesterday - timedelta(days=offset)
        task = service.get_or_create_task(conn, day)

        # A photo taken at local noon, received a minute later: passes all checks.
        captured = datetime.combine(day, time(12, 0), tzinfo=ZoneInfo(tz))
        received = captured + timedelta(minutes=1)
        row = service.record_submission(
            conn,
            client_submission_id=f"fake-{day.isoformat()}",
            task_id=task["id"],
            captured_at=captured,
            received_at=received,
            tz=tz,
        )
        service.finalize_submission(conn, row["id"], "pass")
        inserted += 1

    stats = service.get_stats(conn, yesterday + timedelta(days=1))
    print(f"Inserted {inserted} fake days ({args.days} requested, gaps at offsets {sorted(GAPS)}).")
    print(
        f"xp={stats['xp']} level={stats['level']} "
        f"streak={stats['streak']} this_month={stats['tasks_this_month']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
