"""Preview what the picker would choose for the next N days.

    uv run python -m server.tasks.preview --days 14

Replays the picker from an empty history, so day 2+ show the real
cooldown/category rules kicking in. Purely read-only: no server, no DB.
"""

import argparse
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .library import get_library
from .picker import pick_task
from ..config import get_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview daily task picks.")
    parser.add_argument("--days", type=int, default=14, help="how many days to show")
    parser.add_argument("--seed", default="preview", help="rng seed (changes the picks)")
    parser.add_argument(
        "--start",
        default=None,
        help="start date YYYY-MM-DD (default: today in the configured timezone)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.start:
        start = date.fromisoformat(args.start)
    else:
        start = datetime.now(ZoneInfo(get_settings().TIMEZONE)).date()

    library = get_library()
    history: list[tuple[date, str, str]] = []
    for i in range(args.days):
        day = start + timedelta(days=i)
        template_id = pick_task(day, history, args.seed, library)
        task = library[template_id]
        history.append((day, template_id, task.category))
        print(
            f"{day.isoformat()} ({day.strftime('%a')})  "
            f"{task.category:<11}  {task.title}  ({task.minutes} min)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
