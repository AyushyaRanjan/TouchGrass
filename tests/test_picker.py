"""Unit tests for the deterministic daily task picker."""

from datetime import date, timedelta

from server.tasks.library import load_library
from server.tasks.picker import COOLDOWN_DAYS, pick_reroll, pick_task

LIB = load_library()

# Task builders for tiny-fixture tests (bypasses YAML, uses real model rules).
from tests.test_library import good_task  # noqa: E402
from server.tasks.library import Task  # noqa: E402


def tiny_library() -> dict:
    return {
        "nat_a": Task.model_validate(good_task("nat_a")),
        "nat_b": Task.model_validate(good_task("nat_b")),
    }


def test_pick_is_deterministic():
    day = date(2026, 6, 1)
    first = pick_task(day, [], "seed", LIB)
    second = pick_task(day, [], "seed", LIB)
    assert first == second


def test_pick_ignores_history_order():
    day = date(2026, 6, 10)
    history = [
        (date(2026, 6, 1), "nat_leaf_hunt", "nature"),
        (date(2026, 6, 5), "mov_stairs", "movement"),
    ]
    assert pick_task(day, history, "s", LIB) == pick_task(day, list(reversed(history)), "s", LIB)


def test_empty_history_works():
    assert pick_task(date(2026, 1, 1), [], "x", LIB) in LIB


def test_120_day_run_respects_rules():
    start = date(2026, 1, 1)
    history = []
    picks = []
    for i in range(120):
        day = start + timedelta(days=i)
        template_id = pick_task(day, history, "sim", LIB)
        task = LIB[template_id]
        picks.append((day, template_id, task.category))
        history.append((day, template_id, task.category))

    # All picks are real templates.
    assert all(t in LIB for _, t, _ in picks)

    # Never the same category two days running.
    for yesterday, today in zip(picks, picks[1:]):
        assert today[2] != yesterday[2], f"{today[0]}: {today[2]} after {yesterday[2]}"

    # No template reused within the cooldown window.
    for i, (day, template_id, _) in enumerate(picks):
        for prev_day, prev_id, _ in picks[max(0, i - COOLDOWN_DAYS):i]:
            assert template_id != prev_id, f"{template_id} reused on {day} (prev {prev_day})"

    # Decent variety over four months.
    assert len({t for _, t, _ in picks}) >= 25


def test_recent_template_avoided_next_day():
    day = date(2026, 3, 1)
    first = pick_task(day, [], "s", LIB)
    history = [(day, first, LIB[first].category)]
    second = pick_task(day + timedelta(days=1), history, "s", LIB)
    assert second != first


def test_reroll_returns_a_different_valid_template():
    day = date(2026, 3, 1)
    first = pick_task(day, [], "s", LIB)
    second = pick_reroll(day, [], "s", first, LIB)
    assert second is not None
    assert second != first
    assert second in LIB


def test_reroll_is_deterministic():
    day = date(2026, 3, 1)
    first = pick_task(day, [], "s", LIB)
    assert pick_reroll(day, [], "s", first, LIB) == pick_reroll(day, [], "s", first, LIB)


def test_fallback_on_tiny_library():
    tiny = tiny_library()
    day = date(2026, 3, 1)
    # Both used recently, both nature: every rule is exhausted.
    history = [
        (day - timedelta(days=1), "nat_a", "nature"),
        (day - timedelta(days=2), "nat_b", "nature"),
    ]
    picked = pick_task(day, history, "s", tiny)
    assert picked in tiny

    rerolled = pick_reroll(day, history, "s", picked, tiny)
    # With only two tasks and both blocked, a different one must still come back.
    assert rerolled is not None
    assert rerolled != picked


def test_reroll_with_single_task_library_returns_none():
    single = {"nat_a": Task.model_validate(good_task("nat_a"))}
    day = date(2026, 3, 1)
    assert pick_reroll(day, [], "s", "nat_a", single) is None
