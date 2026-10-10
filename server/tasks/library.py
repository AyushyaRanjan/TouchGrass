"""Loads and validates server/tasks/library.yaml.

The library is the single source of truth for tasks (AGENTS.md rule 6).
Anything wrong with it fails loudly at load time: duplicate ids, bad
categories, criteria that are not answerable from one photo, safety
problems, or a category dropping below its minimum count.

`rubric_for(task)` converts a task's criteria into the exact shape
server.verify.pipeline expects: id / question / required.
"""

import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

LIBRARY_PATH = Path(__file__).resolve().parent / "library.yaml"

# Minimum tasks per category in the shipped library (validated at load).
DISTRIBUTION_MINIMUMS = {
    "nature": 10,
    "movement": 7,
    "exploration": 6,
    "social": 8,
    "creative": 5,
    "community": 4,
}
CATEGORIES = tuple(DISTRIBUTION_MINIMUMS)

# Criteria must be yes/no questions a VLM can answer from one photo.
_YES_NO = re.compile(r"^(Is|Are|Does|Do|Did|Has|Have|Can|Could|Was|Were)\b")
_ID_SLUG = re.compile(r"^[a-z][a-z0-9_]*$")
_CRIT_ID = re.compile(r"^c\d+$")

# Hard safety rules: never in title, why, proof_hint or a criterion.
_SAFETY_BANNED = re.compile(
    r"\b(alcohol|beer|wine|liquor|drunk|pub|night|dark|alley|trespass|"
    r"trespassing|child|children|kid|kids|minor|minors)\b",
    re.IGNORECASE,
)

# People-words are banned inside social criteria: those must be
# place/setting-based, never about strangers or faces (spec: social
# criteria describe places only, so no criterion can be about a person).
_PEOPLE_WORDS = re.compile(
    r"\b(face|faces|person|people|men|man|women|woman|stranger|strangers|"
    r"crowd|customer|customers|somebody|someone|anyone|nobody)\b",
    re.IGNORECASE,
)


class LibraryError(Exception):
    """The task library is invalid. Message says exactly what is wrong."""


class Criterion(BaseModel):
    """One rubric line: a single yes/no question about one visual thing."""

    id: str
    q: str
    required: bool = True

    @field_validator("id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        if not _CRIT_ID.match(v):
            raise ValueError(f"criterion id must look like 'c1', got {v!r}")
        return v

    @field_validator("q")
    @classmethod
    def _question_format(cls, v: str) -> str:
        if not _YES_NO.match(v):
            raise ValueError(f"criterion must start with a yes/no verb (Is/Are/Does...): {v!r}")
        if len(v) < 10:
            raise ValueError(f"criterion too short to be answerable: {v!r}")
        return v


class Task(BaseModel):
    """One entry from library.yaml."""

    id: str
    category: str
    title: str
    why: str
    minutes: int = Field(gt=0)
    difficulty: int = Field(ge=1, le=3)
    proof_hint: str
    needs_dry: bool = False
    comfort: int | None = None
    note_prompt: str | None = None
    criteria: list[Criterion]

    @field_validator("id")
    @classmethod
    def _id_slug(cls, v: str) -> str:
        if not _ID_SLUG.match(v):
            raise ValueError(f"id must be a lowercase slug (nat_leaf_hunt): {v!r}")
        return v

    @field_validator("category")
    @classmethod
    def _known_category(cls, v: str) -> str:
        if v not in CATEGORIES:
            raise ValueError(f"category must be one of {CATEGORIES}, got {v!r}")
        return v

    @model_validator(mode="after")
    def _check(self) -> "Task":
        # Safety scan across every user-facing string.
        for field in ("title", "why", "proof_hint"):
            text = getattr(self, field)
            hit = _SAFETY_BANNED.search(text)
            if hit:
                raise ValueError(f"{field} contains banned safety word {hit.group(0)!r}: {text!r}")

        # 2-4 criteria, at least 2 required, unique ids.
        if not (2 <= len(self.criteria) <= 4):
            raise ValueError(f"{self.id}: needs 2-4 criteria, got {len(self.criteria)}")
        if sum(c.required for c in self.criteria) < 2:
            raise ValueError(f"{self.id}: needs at least 2 required criteria")
        ids = [c.id for c in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{self.id}: duplicate criterion ids {ids}")

        # Social tasks: comfort range, a note prompt, and place-only criteria.
        if self.category == "social":
            if self.comfort is None or not (1 <= self.comfort <= 3):
                raise ValueError(f"{self.id}: social tasks need comfort 1-3")
            if not self.note_prompt:
                raise ValueError(f"{self.id}: social tasks need a note_prompt")
            for c in self.criteria:
                hit = _PEOPLE_WORDS.search(c.q)
                if hit:
                    raise ValueError(
                        f"{self.id}: social criteria must describe places, not people "
                        f"(found {hit.group(0)!r}): {c.q!r}"
                    )
        else:
            if self.comfort is not None:
                raise ValueError(f"{self.id}: only social tasks have comfort")
            for c in self.criteria:
                hit = _PEOPLE_WORDS.search(c.q)
                if hit:
                    raise ValueError(
                        f"{self.id}: criteria must never reference people "
                        f"(found {hit.group(0)!r}): {c.q!r}"
                    )
        return self


def load_library(path: Path = LIBRARY_PATH) -> dict[str, Task]:
    """Read and validate the YAML. Raises LibraryError on any problem."""
    if not path.exists():
        raise LibraryError(f"task library not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise LibraryError(f"{path} must be a YAML list of tasks")

    tasks: dict[str, Task] = {}
    for i, raw in enumerate(data):
        if not isinstance(raw, dict):
            raise LibraryError(f"{path}: entry {i} is not a mapping")
        entry_id = raw.get("id", f"#{i}")
        try:
            task = Task.model_validate(raw)
        except ValidationError as e:
            raise LibraryError(f"{path}: bad entry {entry_id}: {e}") from e
        if task.id in tasks:
            raise LibraryError(f"{path}: duplicate task id {task.id!r}")
        tasks[task.id] = task

    counts = Counter(t.category for t in tasks.values())
    for category, minimum in DISTRIBUTION_MINIMUMS.items():
        if counts[category] < minimum:
            raise LibraryError(
                f"{path}: {category} has {counts[category]} tasks, need at least {minimum}"
            )
    return tasks


@lru_cache
def get_library() -> dict[str, Task]:
    """The validated library, loaded once per process."""
    return load_library()


def rubric_for(task: Task) -> list[dict]:
    """Criteria in the exact shape server.verify.pipeline expects."""
    return [
        {"id": c.id, "question": c.q, "required": c.required}
        for c in task.criteria
    ]
