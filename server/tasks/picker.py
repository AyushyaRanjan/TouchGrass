"""Deterministic daily task picker.

Rules (AGENTS.md):
- Deterministic: same (day, history, seed, prefs) always gives the same task.
- Category weights (often=3, normal=1, rarely=0.3); "off" is never picked.
- Social tasks with comfort > social_comfort_max are never picked.
- Tasks longer than max_minutes are skipped until that filter is relaxed.
- Avoid any template used in the last 14 days.
- Avoid a category used the day before.
- If nothing remains, relax only: cooldown 14 -> 7 days, then drop
  max_minutes, then allow yesterday's category, then ignore cooldown.
  Never relax "off" or social comfort.

History entries are `(date, template_id, category)` tuples.
"""

import random
from datetime import date, timedelta

from .library import Task, get_library
from .preferences import WEIGHTS, Preferences, get_preferences

# How many previous days a template stays off-limits (before any fallback).
COOLDOWN_DAYS = 14
SHORT_COOLDOWN_DAYS = 7


def _rng(rng_seed: str, day: date, salt: str) -> random.Random:
    # Salt separates pick from reroll so a reroll never collides by design.
    return random.Random(f"{rng_seed}|{day.isoformat()}|{salt}")


def _used_recent(history: list[tuple[date, str, str]], day: date, days: int) -> set[str]:
    """Template ids used in [day-days, day] (includes today if present)."""
    if days <= 0:
        return set()
    low = day - timedelta(days=days)
    return {template_id for d, template_id, _ in history if low <= d <= day}


def _yesterday_category(history: list[tuple[date, str, str]], day: date) -> str | None:
    """Category of the most recent history entry strictly before `day`."""
    prior = [entry for entry in history if entry[0] < day]
    if not prior:
        return None
    return max(prior, key=lambda entry: entry[0])[2]


def _eligible(
    library: dict[str, Task],
    prefs: Preferences,
    enforce_minutes: bool,
) -> set[str]:
    """Hard filters: never off, never over social comfort; minutes optional."""
    chosen: set[str] = set()
    for tid, task in library.items():
        weight = prefs.category_weights.get(task.category, "normal")
        if weight == "off":
            continue
        if task.category == "social" and (task.comfort or 0) > prefs.social_comfort_max:
            continue
        if enforce_minutes and prefs.max_minutes is not None and task.minutes > prefs.max_minutes:
            continue
        chosen.add(tid)
    return chosen


def _candidates(
    library: dict[str, Task],
    history: list[tuple[date, str, str]],
    day: date,
    prefs: Preferences,
    *,
    cooldown_days: int,
    enforce_minutes: bool,
    avoid_yesterday: bool,
    exclude_id: str | None,
) -> list[str]:
    eligible = _eligible(library, prefs, enforce_minutes)
    if exclude_id:
        eligible.discard(exclude_id)
    used = _used_recent(history, day, cooldown_days)
    options = eligible - used
    if avoid_yesterday:
        yesterday = _yesterday_category(history, day)
        if yesterday:
            options = {tid for tid in options if library[tid].category != yesterday}
    return sorted(options)


def _weighted_choice(
    rng: random.Random,
    options: list[str],
    library: dict[str, Task],
    prefs: Preferences,
) -> str:
    weights = [WEIGHTS[prefs.category_weights[library[tid].category]] for tid in options]
    return rng.choices(options, weights=weights, k=1)[0]


def _pick(
    day: date,
    history: list[tuple[date, str, str]],
    rng_seed: str,
    salt: str,
    library: dict[str, Task],
    prefs: Preferences,
    exclude_id: str | None,
) -> str | None:
    rng = _rng(rng_seed, day, salt)
    # Relax only in this order. Off and social comfort stay hard.
    stages = [
        (COOLDOWN_DAYS, True, True),
        (SHORT_COOLDOWN_DAYS, True, True),
        (SHORT_COOLDOWN_DAYS, False, True),  # drop max_minutes
        (SHORT_COOLDOWN_DAYS, False, False),  # allow yesterday's category
        (0, False, False),  # ignore cooldown so a tiny library still picks
    ]
    for cooldown_days, enforce_minutes, avoid_yesterday in stages:
        options = _candidates(
            library,
            history,
            day,
            prefs,
            cooldown_days=cooldown_days,
            enforce_minutes=enforce_minutes,
            avoid_yesterday=avoid_yesterday,
            exclude_id=exclude_id,
        )
        if options:
            return _weighted_choice(rng, options, library, prefs)
    return None


def pick_task(
    day: date,
    history: list[tuple[date, str, str]],
    rng_seed: str,
    library: dict[str, Task] | None = None,
    prefs: Preferences | None = None,
) -> str:
    """Return the template id for `day`. Deterministic for identical inputs."""
    library = library if library is not None else get_library()
    prefs = prefs if prefs is not None else get_preferences(None)
    picked = _pick(day, history, rng_seed, "pick", library, prefs, exclude_id=None)
    if not picked:
        raise ValueError(f"library has no tasks to pick from ({day})")
    return picked


def pick_reroll(
    day: date,
    history: list[tuple[date, str, str]],
    rng_seed: str,
    current_id: str,
    library: dict[str, Task] | None = None,
    prefs: Preferences | None = None,
) -> str | None:
    """One free reroll: a different valid template for the same day.

    Same rules as pick_task but `current_id` is always excluded.
    Returns None only if the library is too small to offer anything else.
    """
    library = library if library is not None else get_library()
    prefs = prefs if prefs is not None else get_preferences(None)
    return _pick(day, history, rng_seed, "reroll", library, prefs, exclude_id=current_id)
