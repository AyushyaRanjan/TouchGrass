"""HTTP API tests (AGENTS.md rules 4 and 9): auth, endpoints, worker flow.

The VLM pipeline is replaced by an async fake; everything else (DB,
capture rules, picker, worker loop, image storage, honor/stats) runs for
real against a tmp-path database. No CORS, no external calls.
"""

import io
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import server.main as main_module
from server import db, worker
from server.config import Settings
from server.tasks.library import CATEGORIES

TEST_TOKEN = "test-token"
TZ = "Europe/London"
AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}

# What the mocked pipeline returns; tests mutate it via the `state` fixture.
FAKE_RESULT = {
    "verdict": "pass",
    "reasons": [],
    "hint": None,
    "phash": "abcd1234abcd1234",
    "quality": {"ok": True},
    "dupe": {"duplicate": False},
    "rubric": [{"id": "criterion_one", "answer": "yes", "evidence": "the task is clearly visible"}],
    "blind": {"screen": False},
    "details": {},
    "timings": {},
}


def today_local() -> date:
    return datetime.now(timezone.utc).astimezone(ZoneInfo(TZ)).date()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def jpeg_bytes() -> bytes:
    """A minimal valid JPEG so the real Pillow storage path is exercised."""
    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), (120, 180, 90)).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture
def state():
    return {"calls": [], "raise": None, "result": dict(FAKE_RESULT)}


@pytest.fixture
def client(tmp_path, monkeypatch, state):
    settings = Settings(API_TOKEN=TEST_TOKEN, TIMEZONE=TZ)
    monkeypatch.setattr(main_module, "get_settings", lambda: settings)
    monkeypatch.setattr("server.game.service.get_settings", lambda: settings)
    monkeypatch.setattr(worker, "INTERVAL", 0.05)
    monkeypatch.setattr(main_module, "IMAGES_DIR", tmp_path / "images")
    monkeypatch.setattr(db, "DEFAULT_DB_PATH", str(tmp_path / "api.db"))

    async def fake_verify(image_bytes, criteria, task_id, known_hashes):
        state["calls"].append({"task_id": task_id, "criteria": criteria})
        if state["raise"]:
            raise RuntimeError(state["raise"])
        return dict(state["result"])

    monkeypatch.setattr(worker, "verify_photo", fake_verify)

    with TestClient(main_module.app) as test_client:
        test_client.headers.update(AUTH)
        yield test_client


def create_task(client) -> dict:
    return client.get("/tasks").json()["tasks"][0]


def submit_photo(client, task_id, *, captured_at=None, client_id=None, image=None) -> dict:
    response = client.post(
        "/submissions",
        data={
            "task_id": str(task_id),
            "client_submission_id": client_id or f"c-{uuid.uuid4()}",
            "captured_at": captured_at or now_iso(),
        },
        files={"image": ("proof.jpg", image or jpeg_bytes(), "image/jpeg")},
    )
    return response


def wait_finished(client, submission_id: int, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/submissions/{submission_id}").json()
        if data["status"] in ("done", "error"):
            return data
        time.sleep(0.05)
    raise AssertionError("verification did not finish in time")


# --- auth -------------------------------------------------------------------

def test_health_needs_no_token(client):
    response = client.get("/health", headers={"Authorization": ""})
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["model"]  # model name surfaced for the app


def test_endpoints_require_bearer_token(client):
    no_auth = {"Authorization": ""}
    for method, url in [
        ("get", "/tasks"),
        ("get", "/stats"),
        ("get", "/history"),
        ("get", "/submissions/1"),
        ("get", "/images/1"),
        ("post", "/tasks/1/reroll"),
        ("post", "/submissions/1/honor"),
        ("get", "/preferences"),
        ("put", "/preferences"),
        ("post", "/preferences/reset"),
    ]:
        response = getattr(client, method)(url, headers=no_auth)
        assert response.status_code == 401, f"{method.upper()} {url}"


def test_wrong_token_rejected(client):
    response = client.get("/tasks", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


# --- tasks ------------------------------------------------------------------

def test_task_response_never_leaks_the_rubric(client):
    task = create_task(client)
    assert task["date"] == today_local().isoformat()
    assert task["category"] in CATEGORIES
    assert task["title"] and task["why"] and task["proof_hint"]
    assert task["completed"] is False
    assert task["rerolled"] is False
    assert task["latest_verdict"] is None
    # The rubric/questions must never appear anywhere in the payload:
    # no rubric-ish keys, and nothing nested that could hide one.
    assert not ({"criteria", "rubric", "questions", "answers", "evidence"} & set(task))
    assert all(not isinstance(value, (list, dict)) for value in task.values())


def test_tasks_days_bounds(client):
    assert client.get("/tasks", params={"days": 0}).status_code == 422
    assert client.get("/tasks", params={"days": 32}).status_code == 400
    ok = client.get("/tasks", params={"days": 3})
    assert ok.status_code == 200
    assert len(ok.json()["tasks"]) == 3


def test_tasks_include_day_ends_at_next_local_midnight(client):
    tasks = client.get("/tasks", params={"days": 3}).json()["tasks"]
    for task in tasks:
        ends = datetime.fromisoformat(task["day_ends_at"])
        assert ends.utcoffset() == timedelta(0)  # UTC
        next_day = date.fromisoformat(task["date"]) + timedelta(days=1)
        expected = datetime.combine(next_day, datetime.min.time(), tzinfo=ZoneInfo(TZ))
        assert ends == expected


def test_reroll_once_and_only_for_today(client):
    task = create_task(client)

    # A task from yesterday cannot be rerolled.
    yesterday = client.get(
        "/tasks", params={"from": (today_local() - timedelta(days=1)).isoformat()}
    ).json()["tasks"][0]
    old = client.post(f"/tasks/{yesterday['id']}/reroll")
    assert old.status_code == 409 and "today" in old.json()["detail"]

    first = client.post(f"/tasks/{task['id']}/reroll")
    assert first.status_code == 200
    rerolled = first.json()["task"]
    assert rerolled["template_id"] != task["template_id"]
    assert rerolled["rerolled"] is True

    second = client.post(f"/tasks/{task['id']}/reroll")
    assert second.status_code == 409 and "rerolled" in second.json()["detail"]


# --- submissions ------------------------------------------------------------

def test_submit_reaches_done_and_awards_first_attempt_xp(client, state):
    task = create_task(client)
    response = submit_photo(client, task["id"])
    assert response.status_code == 201
    submission_id = response.json()["submission_id"]
    assert response.json()["status"] == "pending"

    data = wait_finished(client, submission_id)
    assert data["verdict"] == "pass"
    assert data["xp_awarded"] == 10 * task["difficulty"] + 5  # first attempt
    assert data["evidence"] == state["result"]["rubric"]
    assert data["honor_available"] is False  # a pass cannot be honored
    assert data["hint"] is None

    # The worker called the pipeline once with this task's rubric.
    assert len(state["calls"]) == 1
    assert state["calls"][0]["task_id"] == task["id"]
    assert state["calls"][0]["criteria"]  # rubric passed to the VLM layer

    # Photos are served back with a sniffed image content type.
    full = client.get(f"/images/{submission_id}")
    assert full.status_code == 200
    assert full.headers["content-type"].startswith("image/")
    thumb = client.get(f"/images/{submission_id}", params={"thumb": 1})
    assert thumb.status_code == 200

    # Stats reflect the completion.
    stats = client.get("/stats").json()
    assert stats["completed_today"] is True
    assert stats["xp"] == data["xp_awarded"]


def test_submit_is_idempotent_per_client_id(client, state):
    task = create_task(client)
    client_id = "app-fixed-id-1"
    first = submit_photo(client, task["id"], client_id=client_id)
    assert first.status_code == 201
    wait_finished(client, first.json()["submission_id"])

    second = submit_photo(client, task["id"], client_id=client_id)
    assert second.status_code == 200  # idempotent hit, not a new row
    assert second.json()["submission_id"] == first.json()["submission_id"]
    assert len(state["calls"]) == 1  # not verified twice


def test_submit_rejects_wrong_local_day(client):
    task = create_task(client)
    stale = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    response = submit_photo(client, task["id"], captured_at=stale)
    assert response.status_code == 400
    assert "task is for" in response.json()["detail"]


def test_submit_rejects_future_capture(client):
    task = create_task(client)
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    response = submit_photo(client, task["id"], captured_at=future)
    assert response.status_code == 400
    assert "future" in response.json()["detail"]


def test_unknown_ids_return_404(client):
    assert client.post(
        "/submissions",
        data={"task_id": "999999", "client_submission_id": "x", "captured_at": now_iso()},
        files={"image": ("p.jpg", jpeg_bytes(), "image/jpeg")},
    ).status_code == 404
    assert client.get("/submissions/999999").status_code == 404
    assert client.get("/images/999999").status_code == 404
    assert client.post("/submissions/999999/honor").status_code == 404


def test_worker_error_marks_submission_and_blocks_honor(client, state):
    task = create_task(client)
    state["raise"] = "ollama exploded"  # armed before the job can start
    submission_id = submit_photo(client, task["id"]).json()["submission_id"]

    data = wait_finished(client, submission_id)
    assert data["status"] == "error"
    assert "ollama exploded" in data["error"]
    assert data["verdict"] is None
    assert data["honor_available"] is False
    assert client.post(f"/submissions/{submission_id}/honor").status_code == 409


def test_honor_flow_awards_half_xp_and_updates_history_and_stats(client, state):
    task = create_task(client)
    state["result"].update(verdict="fail", hint="No task was visible in the photo.")
    submission_id = submit_photo(client, task["id"]).json()["submission_id"]

    data = wait_finished(client, submission_id)
    assert data["verdict"] == "fail"
    assert "No task was visible" in data["hint"]
    assert data["xp_awarded"] == 0
    assert data["honor_available"] is True

    honored = client.post(f"/submissions/{submission_id}/honor")
    assert honored.status_code == 200
    assert honored.json()["honor"] == 1
    expected_xp = (10 * task["difficulty"]) // 2
    assert honored.json()["xp_awarded"] == expected_xp

    # Honoring twice is a conflict.
    assert client.post(f"/submissions/{submission_id}/honor").status_code == 409

    stats = client.get("/stats").json()
    assert stats["xp"] == expected_xp
    assert stats["completed_today"] is True
    assert stats["last_30_days"][-1]["date"] == today_local().isoformat()

    history = client.get("/history", params={"limit": 1}).json()
    assert history["submissions"][0]["verdict"] == "fail"
    assert history["submissions"][0]["honor"] == 1
    assert history["submissions"][0]["title"]
    assert history["submissions"][0]["thumbnail_url"].endswith("?thumb=1")
    assert client.get("/history", params={"limit": 1, "offset": 1}).json()["submissions"] == []


def test_stats_shape(client):
    stats = client.get("/stats").json()
    assert set(stats) == {
        "xp", "level", "streak_current", "streak_longest", "tasks_this_month",
        "completed_today", "category_counts_30d", "last_30_days",
    }
    assert stats["xp"] == 0 and stats["level"] == 1
    assert stats["completed_today"] is False
    assert set(stats["category_counts_30d"]) == set(CATEGORIES)
    assert len(stats["last_30_days"]) == 30
    assert stats["last_30_days"][-1] == {"date": today_local().isoformat(), "completed": False}


def test_preferences_default_then_partial_put_and_reset(client):
    data = client.get("/preferences").json()
    assert data["is_default"] is True
    assert data["social_comfort_max"] == 2
    assert data["max_minutes"] is None
    assert data["category_weights"]["nature"] == "normal"

    updated = client.put("/preferences", json={"category_weights": {"nature": "often"}}).json()
    assert updated["is_default"] is False
    assert updated["category_weights"]["nature"] == "often"
    assert updated["category_weights"]["movement"] == "normal"
    assert updated["social_comfort_max"] == 2

    client.put("/preferences", json={"max_minutes": 30, "social_comfort_max": 1})
    again = client.get("/preferences").json()
    assert again["category_weights"]["nature"] == "often"
    assert again["max_minutes"] == 30
    assert again["social_comfort_max"] == 1

    reset = client.post("/preferences/reset").json()
    assert reset["is_default"] is True
    assert reset["category_weights"]["nature"] == "normal"
    assert reset["max_minutes"] is None


def test_preferences_two_active_rule_on_merged_result(client):
    payload = {
        "category_weights": {
            "nature": "normal",
            "movement": "normal",
            "exploration": "off",
            "social": "off",
            "creative": "off",
            "community": "off",
        }
    }
    assert client.put("/preferences", json=payload).status_code == 200
    response = client.put("/preferences", json={"category_weights": {"movement": "off"}})
    assert response.status_code == 422


def test_preferences_put_regenerates_future_tasks_not_today(client):
    before = client.get("/tasks", params={"days": 4}).json()["tasks"]
    today_id = before[0]["id"]
    today_template = before[0]["template_id"]
    client.put("/preferences", json={"category_weights": {"nature": "often"}})
    # Future rows are gone until the next GET recreates them.
    conn = db.connect()
    remaining = [row["date"] for row in conn.execute("SELECT date FROM tasks ORDER BY date").fetchall()]
    conn.close()
    assert remaining == [before[0]["date"]]
    after = client.get("/tasks", params={"days": 4}).json()["tasks"]
    assert after[0]["id"] == today_id
    assert after[0]["template_id"] == today_template
    assert len(after) == 4
