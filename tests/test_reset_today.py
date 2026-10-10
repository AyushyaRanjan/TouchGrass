"""scripts/reset_today.py: only today's submissions and their image files go."""

import importlib.util
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from server import db
from server.game import service

TZ = "Europe/London"


def load_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "reset_today.py"
    spec = importlib.util.spec_from_file_location("reset_today", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def add_submission(conn, day, client_id, image=None, thumb=None):
    task = service.get_or_create_task(conn, day)
    captured = datetime.combine(day, time(12, 0), tzinfo=ZoneInfo(TZ))
    row = service.record_submission(
        conn,
        client_submission_id=client_id,
        task_id=task["id"],
        captured_at=captured,
        received_at=captured + timedelta(minutes=1),
        tz=TZ,
    )
    conn.execute(
        "UPDATE submissions SET image_path = ?, thumb_path = ? WHERE id = ?",
        (str(image) if image else None, str(thumb) if thumb else None, row["id"]),
    )
    conn.commit()
    return row["id"]


def test_deletes_only_todays_rows_and_images_inside_images_dir(tmp_path):
    script = load_script()
    conn = db.init_db(tmp_path / "t.db")
    images = tmp_path / "images"
    images.mkdir()
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"x")

    today = date(2026, 10, 10)
    mine, mine_thumb = images / "1.jpg", images / "1_thumb.jpg"
    other = images / "2.jpg"
    for f in (mine, mine_thumb, other):
        f.write_bytes(b"x")

    add_submission(conn, today, "a", mine, mine_thumb)
    add_submission(conn, today, "b", outside)  # outside images dir: row goes, file stays
    other_id = add_submission(conn, today - timedelta(days=1), "c", other)

    rows = script.todays_submissions(conn, today)
    assert len(rows) == 2

    deleted, files = script.delete_submissions(conn, rows, images_dir=images)
    assert (deleted, files) == (2, 2)
    assert not mine.exists() and not mine_thumb.exists()
    assert outside.exists()  # never touches files outside data/images/
    assert other.exists()  # yesterday's photo untouched

    left = [r["id"] for r in conn.execute("SELECT id FROM submissions")]
    assert left == [other_id]
    # Today's task itself is kept.
    assert conn.execute("SELECT COUNT(*) FROM tasks WHERE date = ?", (today.isoformat(),)).fetchone()[0] == 1


def test_nothing_to_delete_is_a_noop(tmp_path):
    script = load_script()
    conn = db.init_db(tmp_path / "t.db")
    assert script.todays_submissions(conn, date(2026, 10, 10)) == []
    assert script.delete_submissions(conn, [], images_dir=tmp_path) == (0, 0)
