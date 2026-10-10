"""Task preferences: merge/defaults, picker filters, fallback order, DB upgrade."""

from collections import Counter
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from server import db
from server.tasks.library import Task, load_library
from server.tasks.picker import pick_reroll, pick_task
from server.tasks.preferences import (
    DEFAULT_PREFERENCES,
    merge,
    get_preferences,
    is_default,
    put_preferences,
    reset_preferences,
)
from tests.test_library import good_task

LIB = load_library()


def prefs(**overrides):
    base = {
        "category_weights": dict(DEFAULT_PREFERENCES["category_weights"]),
        "social_comfort_max": DEFAULT_PREFERENCES["social_comfort_max"],
        "max_minutes": DEFAULT_PREFERENCES["max_minutes"],
    }
    if "category_weights" in overrides:
        base["category_weights"].update(overrides.pop("category_weights"))
    base.update(overrides)
    return merge(base)


def task(task_id: str, **kwargs) -> Task:
    return Task.model_validate(good_task(task_id, **kwargs))


def social(task_id: str, comfort: int, minutes: int = 15) -> Task:
    return task(
        task_id,
        category="social",
        comfort=comfort,
        minutes=minutes,
        note_prompt="How did it feel?",
    )


def test_defaults_when_no_row_and_no_conn():
    p = get_preferences(None)
    assert p.category_weights == DEFAULT_PREFERENCES["category_weights"]
    assert p.social_comfort_max == 2
    assert p.max_minutes is None


def test_no_row_picker_matches_default_prefs():
    day = date(2026, 6, 1)
    a = pick_task(day, [], "seed", LIB)
    b = pick_task(day, [], "seed", LIB, prefs=get_preferences(None))
    assert a == b


def test_merge_partial_keeps_other_defaults():
    merged = merge(None, {"category_weights": {"nature": "often"}})
    assert merged.category_weights["nature"] == "often"
    assert merged.category_weights["movement"] == "normal"
    assert merged.social_comfort_max == 2
    assert merged.max_minutes is None


def test_two_active_categories_checked_on_merged_result():
    stored = {
        "category_weights": {
            "nature": "normal",
            "movement": "normal",
            "exploration": "off",
            "social": "off",
            "creative": "off",
            "community": "off",
        }
    }
    with pytest.raises(ValidationError, match="at least two"):
        merge(stored, {"category_weights": {"movement": "off"}})


def test_off_never_picked():
    p = prefs(category_weights={"social": "off"})
    start = date(2026, 1, 1)
    history = []
    for i in range(80):
        day = start + timedelta(days=i)
        tid = pick_task(day, history, "off-sim", LIB, prefs=p)
        assert LIB[tid].category != "social"
        history.append((day, tid, LIB[tid].category))


def test_often_appears_more_than_rarely_over_200_days():
    p = prefs(category_weights={"nature": "often", "community": "rarely"})
    start = date(2026, 1, 1)
    history = []
    counts: Counter[str] = Counter()
    for i in range(200):
        day = start + timedelta(days=i)
        tid = pick_task(day, history, "weight-sim", LIB, prefs=p)
        counts[LIB[tid].category] += 1
        history.append((day, tid, LIB[tid].category))
    assert counts["nature"] > counts["community"] * 1.4
    assert counts["social"] > 0  # still on (default normal)


def test_comfort_filter():
    library = {
        "soc_gentle": social("soc_gentle", 1),
        "soc_bold": social("soc_bold", 3),
        "nat_a": task("nat_a"),
        "nat_b": task("nat_b", minutes=12),
    }
    p = prefs(social_comfort_max=1, category_weights={"movement": "off", "exploration": "off", "creative": "off", "community": "off"})
    history = []
    start = date(2026, 3, 1)
    for i in range(20):
        day = start + timedelta(days=i)
        tid = pick_task(day, history, "comfort", library, prefs=p)
        assert tid != "soc_bold"
        history.append((day, tid, library[tid].category))


def test_minutes_filter_and_fallback_drops_it():
    library = {
        "nat_short": task("nat_short", minutes=10),
        "nat_long": task("nat_long", minutes=60),
        "mov_short": task("mov_short", category="movement", minutes=10),
        "mov_long": task("mov_long", category="movement", minutes=60),
    }
    p = prefs(max_minutes=15, category_weights={
        "exploration": "off", "social": "off", "creative": "off", "community": "off",
    })
    day = date(2026, 4, 1)
    # Short tasks used inside the 7-day window, so they are blocked until minutes drop.
    history = [
        (day - timedelta(days=1), "nat_short", "nature"),
        (day - timedelta(days=2), "mov_short", "movement"),
    ]
    picked = pick_task(day, history, "minutes", library, prefs=p)
    assert library[picked].minutes > 15


def test_fallback_never_picks_off_or_over_comfort():
    library = {
        "nat_a": task("nat_a", minutes=10),
        "soc_bold": social("soc_bold", 3, minutes=10),
        "soc_offcat": social("soc_offcat", 1, minutes=10),
    }
    # Only nature is on; social is off. Tiny set: still must pick nature, not social.
    p = prefs(
        social_comfort_max=1,
        category_weights={"social": "off", "movement": "normal", "exploration": "off", "creative": "off", "community": "off"},
    )
    # movement has no templates in this library, so eligible is only nat_a.
    library["mov_a"] = task("mov_a", category="movement", minutes=10)
    day = date(2026, 5, 1)
    history = [(day - timedelta(days=i), "nat_a" if i % 2 else "mov_a", "nature" if i % 2 else "movement") for i in range(1, 10)]
    picked = pick_task(day, history, "hard", library, prefs=p)
    assert library[picked].category != "social"


def test_get_put_reset_roundtrip(tmp_path):
    conn = db.init_db(tmp_path / "p.db")
    today = date(2026, 6, 1)
    assert is_default(conn)
    put_preferences(conn, {"category_weights": {"nature": "often"}, "max_minutes": 30}, today)
    assert not is_default(conn)
    loaded = get_preferences(conn)
    assert loaded.category_weights["nature"] == "often"
    assert loaded.category_weights["movement"] == "normal"
    assert loaded.max_minutes == 30
    reset_preferences(conn, today)
    assert is_default(conn)
    assert get_preferences(conn).category_weights["nature"] == "normal"


def test_put_deletes_future_unused_tasks_not_today(tmp_path):
    conn = db.init_db(tmp_path / "p.db")
    today = date(2026, 6, 10)
    conn.execute(
        "INSERT INTO tasks (date, template_id, rerolled, created_at) VALUES (?, ?, 0, ?)",
        (today.isoformat(), "nat_leaf_hunt", "t"),
    )
    conn.execute(
        "INSERT INTO tasks (date, template_id, rerolled, created_at) VALUES (?, ?, 0, ?)",
        ((today + timedelta(days=1)).isoformat(), "mov_stairs", "t"),
    )
    conn.commit()
    put_preferences(conn, {"category_weights": {"nature": "often"}}, today)
    dates = {row["date"] for row in conn.execute("SELECT date FROM tasks").fetchall()}
    assert today.isoformat() in dates
    assert (today + timedelta(days=1)).isoformat() not in dates


def test_init_db_adds_preferences_to_old_database(tmp_path):
    path = tmp_path / "old.db"
    conn = db.connect(path)
    conn.executescript(
        """
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY,
            date TEXT UNIQUE NOT NULL,
            template_id TEXT NOT NULL,
            rerolled INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );
        CREATE TABLE submissions (
            id INTEGER PRIMARY KEY,
            client_submission_id TEXT UNIQUE NOT NULL,
            task_id INTEGER NOT NULL,
            captured_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            status TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()
    conn = db.init_db(path)
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "preferences" in names
    assert is_default(conn)
    assert get_preferences(conn).social_comfort_max == 2


def test_reroll_uses_same_preferences():
    p = prefs(category_weights={"social": "off"})
    day = date(2026, 3, 1)
    first = pick_task(day, [], "s", LIB, prefs=p)
    second = pick_reroll(day, [], "s", first, LIB, prefs=p)
    assert second is not None
    assert LIB[second].category != "social"
    assert second != first
