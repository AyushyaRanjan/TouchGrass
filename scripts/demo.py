"""End-to-end demo client: talks to a running server over HTTP (httpx).

    uv run python scripts/demo.py path/to/photo.jpg
    uv run python scripts/demo.py photo.jpg --base http://127.0.0.1:8000

Flow: health -> today's task -> submit the photo -> poll until the
worker finishes -> print the verdict -> print stats. Exit code 0 only
when the submission ends with status "done".
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]


def read_env(key: str, default: str | None = None) -> str | None:
    """--flag > environment > repo .env > default."""
    if key in os.environ:
        return os.environ[key]
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return default


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the full Touch Grass flow.")
    parser.add_argument("photo", help="path to the photo to submit")
    parser.add_argument("--base", default="http://127.0.0.1:8000", help="server base URL")
    parser.add_argument("--token", default=None, help="API token (default: env/.env API_TOKEN)")
    parser.add_argument("--tz", default=None, help="IANA timezone (default: env/.env TIMEZONE)")
    parser.add_argument("--timeout", type=int, default=60, help="seconds to wait for the verdict")
    return parser


def check(response: httpx.Response) -> dict:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = exc.response.json().get("detail", exc.response.text) if exc.response.headers.get("content-type", "").startswith("application/json") else exc.response.text
        print(f"HTTP {exc.response.status_code} from {exc.request.url}: {detail}")
        sys.exit(1)
    return response.json()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    photo = Path(args.photo)
    if not photo.is_file():
        print(f"photo not found: {photo}")
        return 1

    token = args.token or read_env("API_TOKEN", "change-me")
    tz = args.tz or read_env("TIMEZONE", "Europe/London")
    headers = {"Authorization": f"Bearer {token}"}

    with httpx.Client(base_url=args.base, headers=headers, timeout=30.0) as client:
        # 1. Health (no auth needed, but proves the server is up).
        health = check(client.get("/health"))
        print(f"server ok, model={health['model']}, server_time={health['server_time']}")

        # 2. Today's task (local date in the server's timezone).
        today = datetime.now(timezone.utc).astimezone(ZoneInfo(tz)).date()
        start = today - timedelta(days=1)
        tasks = check(client.get("/tasks", params={"from": start.isoformat(), "days": 3}))["tasks"]
        task = next((t for t in tasks if t["date"] == today.isoformat()), tasks[0])
        print(f"\ntask for {task['date']} [{task['category']}, difficulty {task['difficulty']}]")
        print(f"  {task['title']}")
        print(f"  why: {task['why']}")
        print(f"  proof: {task['proof_hint']}")

        # 3. Submit the photo.
        captured_at = datetime.now(timezone.utc).isoformat()
        result = check(
            client.post(
                "/submissions",
                data={
                    "task_id": str(task["id"]),
                    "client_submission_id": f"demo-{int(time.time())}",
                    "captured_at": captured_at,
                },
                files={"image": (photo.name, photo.read_bytes(), "image/jpeg")},
            )
        )
        submission_id = result["submission_id"]
        print(f"\nsubmitted submission_id={submission_id} status={result['status']}")

        # 4. Poll until the worker finishes.
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            state = check(client.get(f"/submissions/{submission_id}"))
            if state["status"] in ("done", "error"):
                break
            print(".", end="", flush=True)
            time.sleep(2)
        else:
            print(f"\ntimed out after {args.timeout}s waiting for the verdict")
            return 1

        # 5. Print the verdict.
        print()
        if state["status"] == "error":
            print(f"verification errored: {state['error']}")
            return 1
        print(f"verdict: {state['verdict']}   xp: {state['xp_awarded']}")
        if state["hint"]:
            print(f"hint:    {state['hint']}")
        for item in state["evidence"]:
            print(f"  {item['id']} = {item['answer']}  ({item['evidence']})")
        if state["honor_available"]:
            print("honor available: POST /submissions/{}/honor".format(submission_id))

        # 6. Stats.
        stats = check(client.get("/stats"))
        print(
            f"\nstats: xp={stats['xp']} level={stats['level']} "
            f"streak={stats['streak_current']}/{stats['streak_longest']} "
            f"this_month={stats['tasks_this_month']} "
            f"completed_today={stats['completed_today']}"
        )
    return 0 if state["verdict"] else 1


if __name__ == "__main__":
    sys.exit(main())
