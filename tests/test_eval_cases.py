"""Tests for eval case handling: inline rubrics vs library templates."""

import pytest

from eval.run_eval import case_criteria, case_task_id
from server.tasks.library import get_library, rubric_for

LIB = get_library()


def test_inline_rubric():
    criteria = [{"id": "c1", "q": "Is a cat visible?", "required": True}]
    case = {"file": "a.jpg", "rubric": {"criteria": criteria}, "expected": "pass"}
    assert case_criteria(case, LIB) == criteria


def test_template_id_uses_library_rubric():
    case = {"file": "a.jpg", "template_id": "nat_leaf_hunt", "expected": "pass"}
    assert case_criteria(case, LIB) == rubric_for(LIB["nat_leaf_hunt"])


def test_task_id_defaults_to_task_number():
    case = {"file": "a.jpg", "rubric": {"criteria": []}}
    assert case_task_id(case, 3) == "task-3"


def test_task_id_defaults_to_template_id():
    case = {"file": "a.jpg", "template_id": "mov_stairs"}
    assert case_task_id(case, 3) == "mov_stairs"


def test_explicit_task_id_wins():
    case = {"file": "a.jpg", "template_id": "mov_stairs", "task_id": "task-duplicate"}
    assert case_task_id(case, 1) == "task-duplicate"


def test_case_without_rubric_or_template_raises():
    with pytest.raises(ValueError, match="rubric or template_id"):
        case_criteria({"file": "a.jpg"}, LIB)


def test_unknown_template_raises():
    with pytest.raises(ValueError, match="unknown template_id"):
        case_criteria({"file": "a.jpg", "template_id": "nope_not_real"}, LIB)
