"""Pipeline integration tests with the VLM stubbed out (no network)."""

from types import SimpleNamespace

import pytest

from server.verify import pipeline, vlm
from tests.test_quality import make_jpeg

CRITERIA = [{"id": "outdoors", "question": "Was this photo taken outdoors?", "required": True}]


@pytest.fixture
def vlm_calls(monkeypatch):
    calls = {"rubric": 0, "blind": 0}

    async def fake_rubric(image_bytes, criteria):
        calls["rubric"] += 1
        return [{"id": c["id"], "answer": "yes", "evidence": "sky and grass are visible"} for c in criteria]

    async def fake_blind(image_bytes):
        calls["blind"] += 1
        return {"description": "green field", "is_screen_or_print": False, "is_outdoors": True}

    monkeypatch.setattr(vlm, "answer_rubric", fake_rubric)
    monkeypatch.setattr(vlm, "blind_describe", fake_blind)
    return calls


@pytest.mark.asyncio
async def test_pass_flow_returns_full_result(vlm_calls):
    result = await pipeline.verify_photo(make_jpeg(), CRITERIA, "task-1", [])
    assert result["verdict"] == "pass"
    assert result["task_id"] == "task-1"
    assert result["phash"]
    assert result["quality"]["ok"] is True
    assert result["dupe"] is None
    assert result["rubric"][0]["answer"] == "yes"
    assert set(result["timings"]) == {"quality_ms", "dupe_ms", "vlm_ms", "blind_ms", "total_ms"}
    assert vlm_calls["rubric"] == 1
    assert vlm_calls["blind"] == 0  # BLIND_PASS defaults to false


@pytest.mark.asyncio
async def test_retake_never_calls_the_vlm(vlm_calls):
    dark = make_jpeg(amplitude=40, offset=5)
    result = await pipeline.verify_photo(dark, CRITERIA, "task-1", [])
    assert result["verdict"] == "retake"
    assert vlm_calls["rubric"] == 0
    assert result["rubric"] == []


@pytest.mark.asyncio
async def test_cross_task_duplicate_skips_the_vlm(vlm_calls):
    photo = make_jpeg()
    first = await pipeline.verify_photo(photo, CRITERIA, "task-1", [])
    known = [(first["task_id"], first["task_id"], first["phash"])]
    second = await pipeline.verify_photo(photo, CRITERIA, "task-2", known)
    assert second["verdict"] == "rejected"
    assert second["dupe"]["distance"] == 0
    # only the FIRST call woke the GPU; the duplicate was skipped
    assert vlm_calls["rubric"] == 1


@pytest.mark.asyncio
async def test_same_task_hash_does_not_reject(vlm_calls):
    photo = make_jpeg()
    first = await pipeline.verify_photo(photo, CRITERIA, "task-1", [])
    known = [("s1", "task-1", first["phash"])]
    second = await pipeline.verify_photo(photo, CRITERIA, "task-1", known)
    assert second["verdict"] == "pass"
    assert vlm_calls["rubric"] == 2  # same-task hash must not reject or skip


@pytest.mark.asyncio
async def test_blind_pass_called_when_enabled(monkeypatch, vlm_calls):
    fake = SimpleNamespace(BLIND_PASS=True)
    monkeypatch.setattr(pipeline, "get_settings", lambda: fake)
    result = await pipeline.verify_photo(make_jpeg(), CRITERIA, "task-1", [])
    assert vlm_calls["blind"] == 1
    assert result["details"]["blind"]["is_outdoors"] is True
    assert result["verdict"] == "pass"  # blind result never affects the verdict
