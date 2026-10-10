"""Player task preferences: category weights, social comfort, longest task.

Defaults live in DEFAULT_PREFERENCES and are used everywhere a field is
missing. A row is never created unless the player PUTs. get_preferences()
with no row (or no connection) returns the defaults.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .library import CATEGORIES

CategoryWeight = Literal["often", "normal", "rarely", "off"]

# Picker sampling weights. "off" is a hard filter, never sampled.
WEIGHTS: dict[str, float] = {"often": 3.0, "normal": 1.0, "rarely": 0.3, "off": 0.0}

DEFAULT_PREFERENCES: dict = {
    "category_weights": {category: "normal" for category in CATEGORIES},
    "social_comfort_max": 2,
    "max_minutes": None,
}


class Preferences(BaseModel):
    """Effective preferences after merging stored values onto defaults."""

    category_weights: dict[str, CategoryWeight]
    social_comfort_max: int = Field(ge=1, le=3)
    max_minutes: Literal[15, 30, 60] | None = None

    @model_validator(mode="after")
    def _two_active_and_known(self) -> "Preferences":
        extra = set(self.category_weights) - set(CATEGORIES)
        if extra:
            raise ValueError(f"unknown categories: {sorted(extra)}")
        missing = [c for c in CATEGORIES if c not in self.category_weights]
        if missing:
            raise ValueError(f"missing categories: {missing}")
        active = sum(1 for w in self.category_weights.values() if w != "off")
        if active < 2:
            raise ValueError("at least two categories must not be off")
        return self


class PreferencesPatch(BaseModel):
    """Partial update. Omitted fields keep their stored (or default) values."""

    category_weights: dict[str, CategoryWeight] | None = None
    social_comfort_max: int | None = Field(default=None, ge=1, le=3)
    max_minutes: Literal[15, 30, 60] | None = None


def merge(stored: dict | None, patch: dict | None = None) -> Preferences:
    """Fill gaps from DEFAULT_PREFERENCES, then apply a partial patch.

    Missing or null category entries fall back to the default for that
    category. max_minutes may be explicitly null ("any").
    """
    weights = dict(DEFAULT_PREFERENCES["category_weights"])
    comfort = DEFAULT_PREFERENCES["social_comfort_max"]
    minutes = DEFAULT_PREFERENCES["max_minutes"]

    def apply(src: dict | None) -> None:
        nonlocal weights, comfort, minutes
        if not src:
            return
        raw_weights = src.get("category_weights") or {}
        for category, value in raw_weights.items():
            if value is not None:
                weights[category] = value
        if src.get("social_comfort_max") is not None:
            comfort = src["social_comfort_max"]
        if "max_minutes" in src:
            minutes = src["max_minutes"]

    apply(stored)
    apply(patch)
    return Preferences(
        category_weights=weights,
        social_comfort_max=comfort,
        max_minutes=minutes,
    )


def _row_to_stored(row: sqlite3.Row) -> dict:
    return {
        "category_weights": json.loads(row["category_weights"]),
        "social_comfort_max": row["social_comfort_max"],
        "max_minutes": row["max_minutes"],
    }


def _load_stored(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM preferences WHERE id = 1").fetchone()
    return _row_to_stored(row) if row else None


def get_preferences(conn: sqlite3.Connection | None = None) -> Preferences:
    """Effective preferences. Never inserts a row."""
    if conn is None:
        return merge(None)
    return merge(_load_stored(conn))


def is_default(conn: sqlite3.Connection | None) -> bool:
    """True iff nothing has been customised (no row)."""
    if conn is None:
        return True
    row = conn.execute("SELECT 1 FROM preferences WHERE id = 1").fetchone()
    return row is None


def put_preferences(conn: sqlite3.Connection, patch: dict, today: date) -> Preferences:
    """Merge patch onto stored-or-defaults, upsert the single row, wipe future unused tasks."""
    merged = merge(_load_stored(conn), patch)
    payload = (
        json.dumps(merged.category_weights),
        merged.social_comfort_max,
        merged.max_minutes,
    )
    conn.execute(
        "INSERT INTO preferences (id, category_weights, social_comfort_max, max_minutes) "
        "VALUES (1, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "category_weights = excluded.category_weights, "
        "social_comfort_max = excluded.social_comfort_max, "
        "max_minutes = excluded.max_minutes",
        payload,
    )
    delete_future_unused_tasks(conn, today)
    conn.commit()
    return merged


def reset_preferences(conn: sqlite3.Connection, today: date) -> Preferences:
    """Delete the row so defaults apply. Future unused tasks are regenerated."""
    conn.execute("DELETE FROM preferences WHERE id = 1")
    delete_future_unused_tasks(conn, today)
    conn.commit()
    return merge(None)


def delete_future_unused_tasks(conn: sqlite3.Connection, today: date) -> None:
    """Drop tasks after today that have no submissions so they can be re-picked."""
    conn.execute(
        "DELETE FROM tasks WHERE date > ? AND id NOT IN (SELECT DISTINCT task_id FROM submissions)",
        (today.isoformat(),),
    )


def as_response(conn: sqlite3.Connection) -> dict:
    prefs = get_preferences(conn)
    data = prefs.model_dump()
    data["is_default"] = is_default(conn)
    return data
