"""Game rule + service tests (AGENTS.md rules 8/9, "Game rules (V1)").

Covers: timezone/midnight behaviour, capture validation, XP/level,
streaks (gaps, alive, longest, month rollover), honor rules, attempt
counting, the DB service, and scripts/fake_days.py.
"""

import importlib.util
import itertools
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from server import db
from server.game import rules, service
from server.game.service import RNG_SEED
from server.tasks.library import CATEGORIES, get_library
from server.tasks.picker import pick_task

TZ = "Europe/London"
KOL = "Asia/Kolkata"
SUB_IDS = itertools.count(1)


@pytest.fixture
def conn(tmp_path):
    c = db.init_db(tmp_path / "test.db")
    yield c
    c.close()


def submit(conn, day, *, verdict="pass", task_id=None, tz=TZ,
           captured=None, received=None, finalize=True, client_id=None):
    """Record (+ finalize) one submission; defaults to local noon of `day`."""
    if task_id is None:
        task_id = service.get_or_create_task(conn, day)["id"]
    if captured is None:
        captured = datetime.combine(day, time(12, 0), tzinfo=ZoneInfo(tz))
    if received is None:
        received = captured + timedelta(minutes=1)
    row = service.record_submission(
        conn,
        client_submission_id=client_id or f"sub-{next(SUB_IDS)}",
        task_id=task_id,
        captured_at=captured,
        received_at=received,
        tz=tz,
    )
    if finalize:
        row = service.finalize_submission(conn, row["id"], verdict)
    return row


def difficulty_of(task_row) -> int:
    return get_library()[task_row["template_id"]].difficulty


# --- rules: timezones and capture validation ------------------------------

def test_local_date_splits_midnight_between_two_timezones():
    # 20:00 UTC in June: London (BST) is still Jun 5, Kolkata is already Jun 6.
    instant = "2026-06-05T20:00:00Z"
    assert rules.local_date(instant, "Europe/London") == date(2026, 6, 5)
    assert rules.local_date(instant, "Asia/Kolkata") == date(2026, 6, 6)


def test_validate_capture_timezone_decides_the_day():
    instant = "2026-06-05T20:00:00Z"
    received = "2026-06-05T20:01:00Z"
    ok_kol, _ = rules.validate_capture(instant, received, "2026-06-06", KOL)
    ok_london, reason = rules.validate_capture(instant, received, "2026-06-06", TZ)
    assert ok_kol is True
    assert ok_london is False
    assert "2026-06-05" in reason and "2026-06-06" in reason


def test_capture_tuesday_verified_thursday_still_valid():
    # Tuesday Oct 6 captured, Thursday Oct 8 verified - within the 7-day window.
    ok, reason = rules.validate_capture(
        "2026-10-06T20:00:00Z", "2026-10-08T20:00:00Z", "2026-10-06", TZ
    )
    assert ok is True and reason == ""


def test_validate_capture_wrong_day_rejected():
    ok, reason = rules.validate_capture(
        "2026-10-06T10:00:00Z", "2026-10-06T10:01:00Z", "2026-10-07", TZ
    )
    assert ok is False
    assert "2026-10-06" in reason and "2026-10-07" in reason


def test_validate_capture_future_rejected():
    # Captured 10 minutes after the server received it.
    ok, reason = rules.validate_capture(
        "2026-10-06T12:00:00Z", "2026-10-06T11:50:00Z", "2026-10-06", TZ
    )
    assert ok is False and "future" in reason


def test_validate_capture_future_boundary_five_minutes_ok():
    ok, _ = rules.validate_capture(
        "2026-10-06T12:00:00Z", "2026-10-06T11:55:00Z", "2026-10-06", TZ
    )
    assert ok is True


def test_validate_capture_seven_days_old_rejected():
    ok, reason = rules.validate_capture(
        "2026-10-06T12:00:00Z", "2026-10-14T12:00:00Z", "2026-10-06", TZ
    )
    assert ok is False and "older than 7 days" in reason


def test_validate_capture_seven_days_boundary_ok():
    ok, _ = rules.validate_capture(
        "2026-10-06T12:00:00Z", "2026-10-13T12:00:00Z", "2026-10-06", TZ
    )
    assert ok is True


# --- rules: XP and level ---------------------------------------------------

def test_first_attempt_bonus():
    assert rules.xp_for_pass(2, 1) == 25   # 10*2 + 5
    assert rules.xp_for_pass(2, 2) == 20   # no bonus
    assert rules.xp_for_pass(3, 1) == 35
    assert rules.xp_for_pass(1, None) == 10


def test_honor_xp_is_half_floor():
    assert rules.xp_for_honor(1) == 5
    assert rules.xp_for_honor(2) == 10
    assert rules.xp_for_honor(3) == 15


def test_level_boundaries():
    assert rules.level(0) == 1
    assert rules.level(99) == 1
    assert rules.level(100) == 2
    assert rules.level(295) == 3


# --- rules: streaks --------------------------------------------------------

def test_gap_day_breaks_streak():
    days = [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 4)]  # Oct 3 missing
    assert rules.compute_streak(days, date(2026, 10, 4)) == {"current": 1, "longest": 2}


def test_yesterday_done_keeps_streak_alive_when_today_missing():
    days = [date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7)]
    assert rules.compute_streak(days, date(2026, 10, 8)) == {"current": 3, "longest": 3}


def test_neither_today_nor_yesterday_means_zero_current():
    days = [date(2026, 10, 5)]
    assert rules.compute_streak(days, date(2026, 10, 7))["current"] == 0


def test_longest_streak_spans_runs():
    days = [date(2026, 9, d) for d in range(1, 6)] + [date(2026, 10, d) for d in (5, 6, 7)]
    result = rules.compute_streak(days, date(2026, 10, 8))
    assert result == {"current": 3, "longest": 5}


def test_streak_across_month_boundary():
    days = [date(2025, 12, 30), date(2025, 12, 31), date(2026, 1, 1), date(2026, 1, 2)]
    assert rules.compute_streak(days, date(2026, 1, 2)) == {"current": 4, "longest": 4}


def test_tasks_this_month_rollover():
    days = [date(2025, 12, 31), date(2026, 1, 1), date(2026, 1, 2)]
    assert rules.tasks_this_month(days, date(2026, 1, 2)) == 2
    assert rules.tasks_this_month([date(2025, 12, 31)], date(2026, 1, 1)) == 0


def test_category_counts_window_and_zero_fill():
    completions = [
        (date(2026, 10, 6), "nature"),
        (date(2026, 10, 1), "nature"),
        (date(2026, 9, 1), "movement"),  # outside the 30-day window
    ]
    counts = rules.category_counts(completions, 30, date(2026, 10, 6))
    assert set(counts) == set(CATEGORIES)
    assert counts["nature"] == 2
    assert counts["movement"] == 0
    assert rules.category_counts(completions, 1, date(2026, 10, 6))["nature"] == 1


# --- service: daily task ---------------------------------------------------

def test_get_or_create_task_idempotent_and_deterministic(conn):
    day = date(2026, 10, 6)
    first = service.get_or_create_task(conn, day)
    second = service.get_or_create_task(conn, day)
    assert first["id"] == second["id"]
    assert first["template_id"] == pick_task(day, [], RNG_SEED)
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1


def test_consecutive_days_never_repeat_category(conn):
    days = [date(2026, 10, 1) + timedelta(days=i) for i in range(7)]
    categories = [
        get_library()[service.get_or_create_task(conn, day)["template_id"]].category
        for day in days
    ]
    assert all(a != b for a, b in zip(categories, categories[1:]))


def test_reroll_changes_template_exactly_once(conn):
    task = service.get_or_create_task(conn, date(2026, 10, 6))
    rerolled = service.reroll_task(conn, task["id"])
    assert rerolled["template_id"] != task["template_id"]
    assert rerolled["rerolled"] == 1
    with pytest.raises(ValueError, match="already rerolled"):
        service.reroll_task(conn, task["id"])


# --- service: submissions --------------------------------------------------

def test_capture_tuesday_verified_thursday_counts_for_tuesday(conn):
    tuesday = date(2026, 10, 6)  # Tuesday; verified Thursday Oct 8
    task = service.get_or_create_task(conn, tuesday)
    row = submit(
        conn, tuesday, task_id=task["id"],
        captured=datetime(2026, 10, 6, 20, 0, tzinfo=ZoneInfo(TZ)),
        received=datetime(2026, 10, 8, 20, 0, tzinfo=ZoneInfo(TZ)),
    )
    assert row["local_date"] == "2026-10-06"
    stats = service.get_stats(conn, date(2026, 10, 8))
    assert stats["streak"]["longest"] == 1  # only Tuesday counts


def test_record_rejects_wrong_day(conn):
    task = service.get_or_create_task(conn, date(2026, 10, 6))
    with pytest.raises(ValueError, match="was taken on"):
        submit(
            conn, date(2026, 10, 6), task_id=task["id"],
            captured=datetime(2026, 10, 5, 12, 0, tzinfo=ZoneInfo(TZ)),
            received=datetime(2026, 10, 5, 12, 1, tzinfo=ZoneInfo(TZ)),
        )


def test_record_rejects_future_capture(conn):
    task = service.get_or_create_task(conn, date(2026, 10, 6))
    with pytest.raises(ValueError, match="future"):
        submit(
            conn, date(2026, 10, 6), task_id=task["id"],
            captured=datetime(2026, 10, 6, 12, 0, tzinfo=ZoneInfo(TZ)),
            received=datetime(2026, 10, 6, 11, 50, tzinfo=ZoneInfo(TZ)),
        )


def test_record_rejects_seven_day_old_capture(conn):
    task = service.get_or_create_task(conn, date(2026, 10, 6))
    with pytest.raises(ValueError, match="older than 7 days"):
        submit(
            conn, date(2026, 10, 6), task_id=task["id"],
            captured=datetime(2026, 10, 6, 12, 0, tzinfo=ZoneInfo(TZ)),
            received=datetime(2026, 10, 14, 12, 0, tzinfo=ZoneInfo(TZ)),
        )


def test_record_rejects_duplicate_client_id(conn):
    day = date(2026, 10, 6)
    submit(conn, day, client_id="app-1")
    with pytest.raises(ValueError, match="duplicate client_submission_id"):
        submit(conn, day, client_id="app-1")


def test_attempt_numbers_ignore_retakes(conn):
    task = service.get_or_create_task(conn, date(2026, 10, 6))
    diff = difficulty_of(task)
    retake = submit(conn, date(2026, 10, 6), task_id=task["id"], verdict="retake")
    assert retake["attempt_number"] == 1
    assert retake["xp_awarded"] == 0

    # The retake above must not burn the first-attempt bonus.
    passed = submit(conn, date(2026, 10, 6), task_id=task["id"], verdict="pass")
    assert passed["attempt_number"] == 1
    assert passed["xp_awarded"] == 10 * diff + 5


def test_pass_xp_first_versus_second_attempt(conn):
    task = service.get_or_create_task(conn, date(2026, 10, 6))
    diff = difficulty_of(task)
    first = submit(conn, date(2026, 10, 6), task_id=task["id"], verdict="pass")
    second = submit(conn, date(2026, 10, 6), task_id=task["id"], verdict="pass")
    assert first["xp_awarded"] == 10 * diff + 5
    assert second["attempt_number"] == 2
    assert second["xp_awarded"] == 10 * diff


# --- service: honor --------------------------------------------------------

def test_honor_happy_path_awards_half_xp(conn):
    day = date(2026, 10, 7)
    task = service.get_or_create_task(conn, day)
    failed = submit(conn, day, task_id=task["id"], verdict="fail")
    assert failed["xp_awarded"] == 0
    honored = service.complete_with_honor(conn, failed["id"])
    assert honored["honor"] == 1
    assert honored["xp_awarded"] == rules.xp_for_honor(difficulty_of(task))


def test_honor_only_once_per_task(conn):
    day = date(2026, 10, 7)
    task = service.get_or_create_task(conn, day)
    first = submit(conn, day, task_id=task["id"], verdict="unsure")
    service.complete_with_honor(conn, first["id"])

    # Honoring the same submission again fails.
    with pytest.raises(ValueError, match="already"):
        service.complete_with_honor(conn, first["id"])

    # A second non-pass submission cannot honor the already-completed task.
    second = submit(conn, day, task_id=task["id"], verdict="fail")
    with pytest.raises(ValueError, match="already completed"):
        service.complete_with_honor(conn, second["id"])


def test_honor_rejected_when_verdict_is_pass(conn):
    day = date(2026, 10, 7)
    task = service.get_or_create_task(conn, day)
    passed = submit(conn, day, task_id=task["id"], verdict="pass")
    with pytest.raises(ValueError, match="non-pass"):
        service.complete_with_honor(conn, passed["id"])


def test_honor_rejected_when_not_done(conn):
    day = date(2026, 10, 7)
    task = service.get_or_create_task(conn, day)
    pending = submit(conn, day, task_id=task["id"], finalize=False)
    assert pending["status"] == "pending"
    with pytest.raises(ValueError, match="finished"):
        service.complete_with_honor(conn, pending["id"])


def test_honor_counts_for_streak(conn):
    day = date(2026, 10, 7)
    task = service.get_or_create_task(conn, day)
    failed = submit(conn, day, task_id=task["id"], verdict="fail")
    service.complete_with_honor(conn, failed["id"])
    stats = service.get_stats(conn, date(2026, 10, 8))
    assert stats["streak"]["current"] == 1


# --- service: reads --------------------------------------------------------

def test_get_stats_derives_everything(conn):
    d1, d2 = date(2026, 10, 5), date(2026, 10, 6)
    passed = submit(conn, d1, verdict="pass")
    task2 = service.get_or_create_task(conn, d2)
    failed = submit(conn, d2, task_id=task2["id"], verdict="fail")
    honored = service.complete_with_honor(conn, failed["id"])

    stats = service.get_stats(conn, date(2026, 10, 6))
    expected_xp = passed["xp_awarded"] + honored["xp_awarded"]
    assert stats["xp"] == expected_xp            # derived, never a stored counter
    assert stats["level"] == rules.level(expected_xp)
    assert stats["streak"] == {"current": 2, "longest": 2}
    assert stats["tasks_this_month"] == 2
    assert set(stats["category_counts"]) == set(CATEGORIES)
    assert sum(stats["category_counts"].values()) == 2


def test_get_history_newest_first_with_limit_and_offset(conn):
    days = [date(2026, 10, 5), date(2026, 10, 6), date(2026, 10, 7)]
    for day in days:
        submit(conn, day)

    history = service.get_history(conn, limit=2)
    assert [h["task_date"] for h in history] == ["2026-10-07", "2026-10-06"]
    assert history[0]["verdict"] == "pass"
    assert service.get_history(conn, limit=10, offset=1)[0]["task_date"] == "2026-10-06"
    assert len(service.get_history(conn, limit=10, offset=2)) == 1


# --- scripts/fake_days.py --------------------------------------------------

def load_fake_days():
    path = Path(__file__).resolve().parents[1] / "scripts" / "fake_days.py"
    spec = importlib.util.spec_from_file_location("fake_days", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fake_days_runs_once_then_requires_force(tmp_path):
    script = load_fake_days()
    db_path = tmp_path / "fake.db"

    # 10 requested days, gap at offset 5 -> 9 rows.
    assert script.main(["--db", str(db_path), "--days", "10"]) == 0
    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 9
    assert conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0] == 9
    conn.close()

    # Not empty -> refused without --force.
    assert script.main(["--db", str(db_path), "--days", "10"]) == 1

    # --force wipes and refills.
    assert script.main(["--db", str(db_path), "--days", "10", "--force"]) == 0
    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 9
    assert conn.execute("SELECT COUNT(*) FROM submissions WHERE verdict = 'pass'").fetchone()[0] == 9
    conn.close()


# --- rules + service: stats helpers ---------------------------------------

def test_today_local_applies_timezone_offsets():
    # Kiritimati (UTC+14) is always exactly 1-2 calendar days ahead of UTC-12,
    # proving the configured offset is applied (tzdata works on Windows too).
    west = rules.today_local("Etc/GMT+12")
    east = rules.today_local("Pacific/Kiritimati")
    assert 1 <= (east - west).days <= 2


def test_daily_completion_flags_fill_window_oldest_first():
    today = date(2026, 10, 8)
    flags = rules.daily_completion_flags({date(2026, 10, 6), date(2026, 10, 8)}, today, days=3)
    assert flags == [
        {"date": "2026-10-06", "completed": True},
        {"date": "2026-10-07", "completed": False},
        {"date": "2026-10-08", "completed": True},
    ]
    assert len(rules.daily_completion_flags(set(), today)) == 30


def test_completed_dates_counts_pass_or_honor(conn):
    d1, d2 = date(2026, 10, 5), date(2026, 10, 6)
    submit(conn, d1, verdict="pass")
    task2 = service.get_or_create_task(conn, d2)
    failed = submit(conn, d2, task_id=task2["id"], verdict="fail")
    assert service.completed_dates(conn) == {d1}

    service.complete_with_honor(conn, failed["id"])
    assert service.completed_dates(conn) == {d1, d2}


def test_honor_available_tracks_blockers(conn):
    day = date(2026, 10, 7)
    task = service.get_or_create_task(conn, day)
    pending = submit(conn, day, task_id=task["id"], finalize=False)
    assert service.honor_available(conn, pending["id"]) is False  # not finished

    failed = service.finalize_submission(conn, pending["id"], "fail")
    assert service.honor_available(conn, failed["id"]) is True

    service.complete_with_honor(conn, failed["id"])
    assert service.honor_available(conn, failed["id"]) is False  # already honored
    assert service.honor_available(conn, 999999) is False  # unknown id


# --- rules: the task window is the whole local day ---------------------------

def _local(day: date, hh: int, mm: int, tz: str) -> datetime:
    """A local wall-clock time in `tz`, converted to UTC."""
    return datetime.combine(day, time(hh, mm), tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)


# A plain day, plus both kinds of DST change on the task date itself:
# London falls back on 2026-10-25 (25h day), New York springs forward
# on 2026-03-08 (23h day).
WINDOW_CASES = [
    ("Europe/London", date(2026, 10, 6)),
    ("Europe/London", date(2026, 10, 25)),
    ("America/New_York", date(2026, 3, 8)),
]


@pytest.mark.parametrize("tz,day", WINDOW_CASES)
def test_capture_window_first_and_last_minute_accepted(tz, day):
    for hh, mm in [(0, 1), (23, 59)]:
        captured = _local(day, hh, mm, tz)
        ok, reason = rules.validate_capture(
            captured, captured + timedelta(minutes=1), day.isoformat(), tz
        )
        assert ok is True, f"{hh:02d}:{mm:02d} {tz}: {reason}"


@pytest.mark.parametrize("tz,day", WINDOW_CASES)
def test_capture_window_next_midnight_rejected(tz, day):
    next_day = day + timedelta(days=1)
    captured = _local(next_day, 0, 0, tz)
    ok, reason = rules.validate_capture(
        captured, captured + timedelta(minutes=1), day.isoformat(), tz
    )
    assert ok is False
    assert next_day.isoformat() in reason and day.isoformat() in reason


@pytest.mark.parametrize("tz,day", WINDOW_CASES)
def test_capture_window_late_photo_uploaded_next_morning_accepted(tz, day):
    # Taken at 23:55, only reaches the server at 08:00 the next day.
    captured = _local(day, 23, 55, tz)
    received = _local(day + timedelta(days=1), 8, 0, tz)
    ok, reason = rules.validate_capture(captured, received, day.isoformat(), tz)
    assert ok is True, reason


@pytest.mark.parametrize(
    "tz,day,expected",
    [
        ("Europe/London", date(2026, 10, 6), "2026-10-06T23:00:00+00:00"),  # BST
        ("Europe/London", date(2026, 10, 25), "2026-10-26T00:00:00+00:00"),  # back to GMT
        ("America/New_York", date(2026, 3, 8), "2026-03-09T04:00:00+00:00"),  # now EDT
        ("Asia/Kolkata", date(2026, 10, 10), "2026-10-10T18:30:00+00:00"),
    ],
)
def test_next_midnight_utc(tz, day, expected):
    assert rules.next_midnight_utc(tz, for_date=day).isoformat() == expected
