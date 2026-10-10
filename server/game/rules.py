"""Pure game rules (AGENTS.md "Game rules (V1)").

No DB access, no I/O, no settings - just functions on plain values, so
every rule is unit-testable in isolation (AGENTS.md rule 9). The DB
service layer (service.py) is the only caller in production.

All timestamps are UTC ISO strings or naive/aware datetimes; naive is
treated as UTC. `tz` is an IANA name from the TIMEZONE setting.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..tasks.library import CATEGORIES

# A capture may be at most this far ahead of server time (5 minutes).
MAX_FUTURE_SKEW = timedelta(minutes=5)
# A capture must not be older than this when it reaches the server.
MAX_CAPTURE_AGE = timedelta(days=7)


def as_utc(value) -> datetime:
    """Normalize a string or datetime to an aware UTC datetime."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)  # accepts 'Z' on Python 3.11+
    if isinstance(value, date) and not isinstance(value, datetime):
        raise ValueError(f"expected a datetime, got a date: {value!r}")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)  # naive = UTC
    return value.astimezone(timezone.utc)


def as_date(value) -> date:
    """Accept a date or a YYYY-MM-DD string."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(value)


def local_date(captured_at_utc, tz: str) -> date:
    """The calendar day of a capture in the player's timezone."""
    return as_utc(captured_at_utc).astimezone(ZoneInfo(tz)).date()


def validate_capture(captured_at, received_at, task_date, tz: str) -> tuple[bool, str]:
    """Check the capture rules; returns (ok, reason) with reason '' when ok.

    Checked in order - future, too old, wrong day - so each failure gets
    its own message:
      1. captured_at not more than 5 min in the future of server time
      2. captured_at not older than 7 days at receipt
      3. local date of captured_at == the task's local date
    """
    captured = as_utc(captured_at)
    received = as_utc(received_at)
    task_day = as_date(task_date)

    if captured - received > MAX_FUTURE_SKEW:
        return False, "Photo is from the future (more than 5 minutes ahead of server time)."
    if received - captured > MAX_CAPTURE_AGE:
        return False, "Photo is older than 7 days."
    day = captured.astimezone(ZoneInfo(tz)).date()
    if day != task_day:
        return False, f"Photo was taken on {day.isoformat()}, but the task is for {task_day.isoformat()}."
    return True, ""


def xp_for_pass(difficulty: int, attempt_number: int | None) -> int:
    """10 * difficulty, +5 if it was the first counted attempt."""
    xp = 10 * difficulty
    if attempt_number == 1:
        xp += 5
    return xp


def xp_for_honor(difficulty: int) -> int:
    """Honor completion = floor(10 * difficulty / 2)."""
    return (10 * difficulty) // 2


def level(xp: int) -> int:
    """Level = xp // 100 + 1."""
    return xp // 100 + 1


def compute_streak(completed_local_dates, today_local: date) -> dict:
    """{current, longest} from the local days that have a completion.

    The current streak stays alive if today is not done yet but
    yesterday was (AGENTS.md game rules).
    """
    done = set(completed_local_dates)

    # Current: walk back from today, or from yesterday if today is blank.
    current = 0
    if today_local in done:
        anchor = today_local
    elif today_local - timedelta(days=1) in done:
        anchor = today_local - timedelta(days=1)
    else:
        anchor = None
    while anchor is not None and anchor in done:
        current += 1
        anchor -= timedelta(days=1)

    # Longest: longest consecutive run over all completed days.
    longest = run = 0
    previous = None
    for day in sorted(done):
        if previous is not None and day == previous + timedelta(days=1):
            run += 1
        else:
            run = 1
        longest = max(longest, run)
        previous = day

    return {"current": current, "longest": longest}


def tasks_this_month(completions, today_local: date) -> int:
    """How many completed tasks fall in today_local's calendar month."""
    return sum(
        1
        for day in completions
        if day.year == today_local.year and day.month == today_local.month
    )


def category_counts(completions, days: int, today_local: date) -> dict:
    """Completed tasks per category over the last `days` local days.

    completions: iterable of (local_date, category). Returns every
    category (zero-filled) so callers can render a stable table.
    """
    start = today_local - timedelta(days=days - 1)
    counts = {category: 0 for category in CATEGORIES}
    for day, category in completions:
        if start <= day <= today_local and category in counts:
            counts[category] += 1
    return counts


def daily_completion_flags(completed_local_dates, today_local: date, days: int = 30) -> list[dict]:
    """One {date, completed} entry per local day, oldest first, ending today."""
    done = set(completed_local_dates)
    start = today_local - timedelta(days=days - 1)
    return [
        {"date": (start + timedelta(days=i)).isoformat(), "completed": (start + timedelta(days=i)) in done}
        for i in range(days)
    ]


def today_local(tz: str) -> date:
    """Today's date in the player's timezone."""
    return datetime.now(timezone.utc).astimezone(ZoneInfo(tz)).date()


def next_midnight_utc(tz: str, for_date: date | None = None) -> datetime:
    """UTC timestamp of the next local midnight (start of next day) in `tz`."""
    if for_date is None:
        for_date = today_local(tz)
    local_midnight_next = datetime(
        for_date.year, for_date.month, for_date.day, tzinfo=ZoneInfo(tz)
    ) + timedelta(days=1)
    return local_midnight_next.astimezone(timezone.utc)

