"""FastAPI app: the HTTP layer only (AGENTS.md rules 4 and 5).

- Bearer token on everything except GET /health (secrets.compare_digest).
- No CORS, nothing extra mounted: single user, single machine.
- All game/verification logic stays in server/game/ and server/verify/;
  this file only validates input, calls services, and shapes responses.
- The background worker (server/worker.py) starts from the lifespan.
"""

import asyncio
import io
import json
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, ImageOps
from pydantic import ValidationError

from . import db, worker
from .config import get_settings
from .game import rules, service
from .tasks.library import get_library
from .tasks.preferences import PreferencesPatch, as_response, put_preferences, reset_preferences

# Repo root: data/images/ lives here (gitignored, rule 5: nowhere else).
ROOT = Path(__file__).resolve().parents[1]
IMAGES_DIR = ROOT / "data" / "images"

MAX_TASK_DAYS = 31
MAX_HISTORY_LIMIT = 200


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()          # tables must exist before the first request
    worker.reset_stuck()  # restart survival: processing -> pending
    loop_task = asyncio.create_task(worker.worker_loop())
    yield
    loop_task.cancel()
    with suppress(asyncio.CancelledError):
        await loop_task


app = FastAPI(title="Touch Grass", lifespan=lifespan)


# --- dependencies ----------------------------------------------------------

def require_token(authorization: str | None = Header(default=None)) -> None:
    """Everything except /health needs Authorization: Bearer <API_TOKEN>."""
    if not authorization:
        raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, get_settings().API_TOKEN):
        raise HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"})


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


Auth = Depends(require_token)
Conn = Depends(get_conn)


def _today() -> date:
    return rules.today_local(get_settings().TIMEZONE)


# --- helpers ---------------------------------------------------------------

def _task_row(conn, task_id: int):
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise HTTPException(404, f"unknown task id {task_id}")
    return row


def _task_completed(conn, task_id: int) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) FROM submissions "
        "WHERE task_id = ? AND (verdict = 'pass' OR honor = 1)",
        (task_id,),
    ).fetchone()
    return row[0] > 0


def _display_task(conn, task) -> dict:
    """Public task fields. The rubric never leaves the server (rule 1)."""
    entry = get_library()[task["template_id"]]
    submissions = conn.execute(
        "SELECT verdict, honor FROM submissions WHERE task_id = ? ORDER BY id",
        (task["id"],),
    ).fetchall()
    tdate = date.fromisoformat(task["date"])
    return {
        "id": task["id"],
        "date": task["date"],
        "template_id": task["template_id"],
        "category": entry.category,
        "title": entry.title,
        "why": entry.why,
        "minutes": entry.minutes,
        "difficulty": entry.difficulty,
        "proof_hint": entry.proof_hint,
        "note_prompt": entry.note_prompt,
        "completed": any(s["verdict"] == "pass" or s["honor"] for s in submissions),
        "rerolled": bool(task["rerolled"]),
        "latest_verdict": submissions[-1]["verdict"] if submissions else None,
        "day_ends_at": rules.next_midnight_utc(
            get_settings().TIMEZONE, for_date=tdate
        ).isoformat(),
    }


def _store_image(submission_id: int, raw: bytes) -> tuple[str, str | None]:
    """Save data/images/{id}.jpg + a 320px thumbnail; return stored paths.

    If the bytes are not decodable by Pillow we keep them as-is and skip
    the thumbnail; the pipeline's quality gate will verdict them 'retake'.
    """
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    image_path = IMAGES_DIR / f"{submission_id}.jpg"
    thumb_file = IMAGES_DIR / f"{submission_id}_thumb.jpg"
    thumb_path: str | None = None
    try:
        with Image.open(io.BytesIO(raw)) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.save(image_path, format="JPEG", quality=92)
            im.thumbnail((320, 320))
            im.save(thumb_file, format="JPEG", quality=85)
            thumb_path = str(thumb_file)
    except Exception:  # noqa: BLE001 - unreadable input is the gate's problem
        image_path.write_bytes(raw)
    return str(image_path), thumb_path


def _sniff_media(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


# --- endpoints -------------------------------------------------------------

@app.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "model": get_settings().VLM_MODEL,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/tasks")
def list_tasks(
    from_: date | None = Query(default=None, alias="from"),
    days: int = Query(default=1, ge=1),
    conn=Conn,
    _auth=Auth,
) -> dict:
    if days > MAX_TASK_DAYS:
        raise HTTPException(400, f"days must be <= {MAX_TASK_DAYS}")
    start = from_ or _today()
    tasks = [
        _display_task(conn, service.get_or_create_task(conn, start + timedelta(days=i)))
        for i in range(days)
    ]
    return {"tasks": tasks}


@app.get("/preferences")
def get_prefs(conn=Conn, _auth=Auth) -> dict:
    return as_response(conn)


@app.put("/preferences")
def update_prefs(body: PreferencesPatch, conn=Conn, _auth=Auth) -> dict:
    patch = body.model_dump(exclude_unset=True)
    try:
        put_preferences(conn, patch, _today())
    except ValidationError as exc:
        raise HTTPException(422, exc.errors()[0].get("msg", "invalid preferences")) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return as_response(conn)


@app.post("/preferences/reset")
def reset_prefs(conn=Conn, _auth=Auth) -> dict:
    reset_preferences(conn, _today())
    return as_response(conn)


@app.post("/tasks/{task_id}/reroll")
def reroll(task_id: int, conn=Conn, _auth=Auth) -> dict:
    task = _task_row(conn, task_id)
    if task["date"] != _today().isoformat():
        raise HTTPException(409, "reroll is only allowed for today's task")
    if _task_completed(conn, task_id):
        raise HTTPException(409, "task already completed")
    try:
        updated = service.reroll_task(conn, task_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"task": _display_task(conn, updated)}


@app.post("/submissions", status_code=201)
async def create_submission(
    task_id: int = Form(...),
    client_submission_id: str = Form(...),
    captured_at: str = Form(...),
    lat: float | None = Form(default=None),
    lon: float | None = Form(default=None),
    note: str | None = Form(default=None),
    image: UploadFile = File(...),
    conn=Conn,
    _auth=Auth,
):
    # Idempotent: the same client id returns the existing submission (200).
    existing = conn.execute(
        "SELECT id, status FROM submissions WHERE client_submission_id = ?",
        (client_submission_id,),
    ).fetchone()
    if existing:
        return JSONResponse(
            status_code=200,
            content={"submission_id": existing["id"], "status": existing["status"]},
        )

    if conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is None:
        raise HTTPException(404, f"unknown task id {task_id}")

    raw = await image.read()
    try:
        row = service.record_submission(
            conn,
            client_submission_id=client_submission_id,
            task_id=task_id,
            captured_at=captured_at,
            lat=lat,
            lon=lon,
            note=note,
        )
    except ValueError as exc:
        if str(exc).startswith("duplicate client_submission_id"):
            existing = conn.execute(
                "SELECT id, status FROM submissions WHERE client_submission_id = ?",
                (client_submission_id,),
            ).fetchone()
            return JSONResponse(
                status_code=200,
                content={"submission_id": existing["id"], "status": existing["status"]},
            )
        raise HTTPException(400, str(exc))  # capture rules / bad input

    try:
        image_path, thumb_path = _store_image(row["id"], raw)
    except OSError as exc:
        # Do not leave a pending row the worker cannot read.
        conn.execute(
            "UPDATE submissions SET status = 'error', error = ? WHERE id = ?",
            (f"failed to store image: {exc}", row["id"]),
        )
        conn.commit()
        raise HTTPException(500, "failed to store image")

    conn.execute(
        "UPDATE submissions SET image_path = ?, thumb_path = ? WHERE id = ?",
        (image_path, thumb_path, row["id"]),
    )
    conn.commit()
    return {"submission_id": row["id"], "status": "pending"}


@app.get("/submissions/{submission_id}")
def get_submission(submission_id: int, conn=Conn, _auth=Auth) -> dict:
    row = conn.execute(
        "SELECT * FROM submissions WHERE id = ?", (submission_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"unknown submission id {submission_id}")

    evidence = []
    if row["details_json"]:
        try:
            details = json.loads(row["details_json"])
            evidence = details.get("rubric") or []
        except json.JSONDecodeError:
            evidence = []

    return {
        "status": row["status"],
        "verdict": row["verdict"],
        "hint": row["hint"],
        "evidence": evidence,
        "xp_awarded": row["xp_awarded"],
        "honor_available": service.honor_available(conn, submission_id),
        "error": row["error"],
    }


@app.post("/submissions/{submission_id}/honor")
def honor_submission(submission_id: int, conn=Conn, _auth=Auth) -> dict:
    if conn.execute("SELECT 1 FROM submissions WHERE id = ?", (submission_id,)).fetchone() is None:
        raise HTTPException(404, f"unknown submission id {submission_id}")
    try:
        row = service.complete_with_honor(conn, submission_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {
        "submission_id": row["id"],
        "status": row["status"],
        "verdict": row["verdict"],
        "honor": row["honor"],
        "xp_awarded": row["xp_awarded"],
    }


@app.get("/stats")
def stats(conn=Conn, _auth=Auth) -> dict:
    today = _today()
    data = service.get_stats(conn, today, days=30)
    completed = service.completed_dates(conn)
    return {
        "xp": data["xp"],
        "level": data["level"],
        "streak_current": data["streak"]["current"],
        "streak_longest": data["streak"]["longest"],
        "tasks_this_month": data["tasks_this_month"],
        "completed_today": today in completed,
        "category_counts_30d": data["category_counts"],
        "last_30_days": rules.daily_completion_flags(completed, today, 30),
    }


@app.get("/history")
def history(
    limit: int = Query(default=50, ge=1),
    offset: int = Query(default=0, ge=0),
    conn=Conn,
    _auth=Auth,
) -> dict:
    rows = service.get_history(conn, limit=min(limit, MAX_HISTORY_LIMIT), offset=offset)
    library = get_library()
    items = []
    for row in rows:
        item = dict(row)
        item["title"] = library[row["template_id"]].title if row["template_id"] in library else None
        item["thumbnail_url"] = f"/images/{row['id']}?thumb=1"
        items.append(item)
    return {"submissions": items, "limit": min(limit, MAX_HISTORY_LIMIT), "offset": offset}


@app.get("/images/{submission_id}")
def get_image(submission_id: int, thumb: int = 0, conn=Conn, _auth=Auth) -> FileResponse:
    row = conn.execute(
        "SELECT image_path, thumb_path FROM submissions WHERE id = ?",
        (submission_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, f"unknown submission id {submission_id}")

    # thumb=1 falls back to the full image when no thumbnail was stored.
    rel = row["thumb_path"] if (thumb and row["thumb_path"]) else row["image_path"]
    if not rel:
        raise HTTPException(404, "no image stored for this submission")

    path = (ROOT / rel).resolve()
    if not path.is_relative_to((IMAGES_DIR).resolve()) or not path.is_file():
        raise HTTPException(404, "image file not found")

    with open(path, "rb") as fh:
        media_type = _sniff_media(fh.read(16))
    return FileResponse(path, media_type=media_type)
