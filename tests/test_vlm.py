"""VLM client tests: schema shape, evidence validation, retry and fallback logic.

No network: _post_chat is monkeypatched.
"""

import json

import pytest

from server.verify import vlm
from server.verify.vlm import answer_rubric, build_rubric_schema, valid_evidence

CRITERIA = [
    {"id": "leaves", "question": "Are leaves yellow, orange or red?", "required": True},
    {"id": "outdoors", "question": "Was this photo taken outdoors?", "required": True},
]


@pytest.fixture(autouse=True)
def fake_encoding(monkeypatch):
    """The tests are about JSON/evidence logic, not image encoding."""
    monkeypatch.setattr(vlm, "encode_image", lambda b: "ZmFrZQ==")


def good_data():
    return {
        "leaves": {"evidence": "leaves are all green", "answer": "no"},
        "outdoors": {"evidence": "grass and open sky visible", "answer": "yes"},
    }


# --- evidence rules -------------------------------------------------------

def test_evidence_needs_four_words():
    assert not valid_evidence("green grass")
    assert valid_evidence("lots of green grass here")


def test_evidence_rejects_empty_and_yes_no():
    assert not valid_evidence("")
    assert not valid_evidence(None)
    assert not valid_evidence("yes it is")


def test_evidence_must_not_repeat_question():
    q = "Are leaves yellow, orange or red?"
    assert not valid_evidence("are leaves yellow orange or red", q)


def test_evidence_must_not_be_substring_of_question():
    q = "Is the photo taken indoors in a room?"
    assert not valid_evidence("photo taken indoors in a room", q)


def test_evidence_may_contain_keywords_from_question():
    q = "Are leaves yellow, orange or red?"
    assert valid_evidence("all leaves look green to me", q)


# --- schema ---------------------------------------------------------------

def test_schema_has_evidence_before_answer():
    schema = build_rubric_schema(CRITERIA)
    item = schema["properties"]["leaves"]
    assert list(item["properties"].keys()) == ["evidence", "answer"]
    assert item["required"] == ["evidence", "answer"]


def test_schema_evidence_has_grammar_enforced_min_length():
    schema = build_rubric_schema(CRITERIA)
    assert schema["properties"]["leaves"]["properties"]["evidence"]["minLength"] >= 16


def test_schema_covers_all_criteria_ids():
    schema = build_rubric_schema(CRITERIA)
    assert schema["required"] == ["leaves", "outdoors"]


# --- answer_rubric --------------------------------------------------------

@pytest.mark.asyncio
async def test_happy_path(monkeypatch):
    calls = []

    async def fake_post(settings, messages, schema):
        calls.append(1)
        return good_data()

    monkeypatch.setattr(vlm, "_post_chat", fake_post)
    result = await answer_rubric(b"unused", CRITERIA)
    assert len(calls) == 1
    assert result == [
        {"id": "leaves", "answer": "no", "evidence": "leaves are all green"},
        {"id": "outdoors", "answer": "yes", "evidence": "grass and open sky visible"},
    ]


@pytest.mark.asyncio
async def test_invalid_evidence_retries_once_then_succeeds(monkeypatch):
    calls = []
    bad = {
        "leaves": {"evidence": "no", "answer": "no"},
        "outdoors": {"evidence": "grass and open sky visible", "answer": "yes"},
    }

    async def fake_post(settings, messages, schema):
        calls.append(1)
        return bad if len(calls) == 1 else good_data()

    monkeypatch.setattr(vlm, "_post_chat", fake_post)
    result = await answer_rubric(b"unused", CRITERIA)
    assert len(calls) == 2
    assert result[0]["answer"] == "no"


@pytest.mark.asyncio
async def test_second_invalid_payload_falls_back_to_unclear(monkeypatch):
    calls = []
    bad = {
        "leaves": {"evidence": "no", "answer": "no"},  # < 4 words
        "outdoors": {"evidence": "was this photo taken outdoors", "answer": "yes"},
    }

    async def fake_post(settings, messages, schema):
        calls.append(1)
        return bad

    monkeypatch.setattr(vlm, "_post_chat", fake_post)
    result = await answer_rubric(b"unused", CRITERIA)
    assert len(calls) == 2
    assert all(a["answer"] == "unclear" for a in result)
    assert all(a["evidence"] == "invalid response from model" for a in result)


@pytest.mark.asyncio
async def test_invalid_json_retries_then_falls_back(monkeypatch):
    calls = []

    async def fake_post(settings, messages, schema):
        calls.append(1)
        raise json.JSONDecodeError("boom", "doc", 0)

    monkeypatch.setattr(vlm, "_post_chat", fake_post)
    result = await answer_rubric(b"unused", CRITERIA)
    assert len(calls) == 2
    assert all(a["answer"] == "unclear" for a in result)


@pytest.mark.asyncio
async def test_empty_criteria_skips_the_model(monkeypatch):
    async def fake_post(settings, messages, schema):
        raise AssertionError("model must not be called")

    monkeypatch.setattr(vlm, "_post_chat", fake_post)
    assert await answer_rubric(b"unused", []) == []


@pytest.mark.asyncio
async def test_blind_fallback_on_bad_payload(monkeypatch):
    calls = []

    async def fake_post(settings, messages, schema):
        calls.append(1)
        return {"description": "a tree"}

    monkeypatch.setattr(vlm, "_post_chat", fake_post)
    result = await vlm.blind_describe(b"unused")
    assert len(calls) == 2
    assert result == {
        "description": "invalid response from model",
        "is_screen_or_print": None,
        "is_outdoors": None,
    }


def test_blind_prompt_never_mentions_task():
    assert "task" not in vlm.BLIND_SYSTEM_PROMPT.lower()
    assert "rubric" not in vlm.BLIND_SYSTEM_PROMPT.lower()
