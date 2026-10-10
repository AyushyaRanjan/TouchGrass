"""Verify one real photo against one task library template.

Runs the actual production pipeline (quality gate -> duplicate check ->
VLM rubric -> verdict), the same code path the server uses.

    uv run python -m eval.verify_photo --template nat_leaf_hunt --photo spike/photos/outdoor.jpeg
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

from server.tasks.library import get_library, rubric_for
from server.verify.pipeline import verify_photo


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the verification pipeline on one photo.")
    parser.add_argument("--template", required=True, help="task id from server/tasks/library.yaml")
    parser.add_argument("--photo", required=True, help="path to the photo file")
    return parser


async def run(args: argparse.Namespace) -> int:
    library = get_library()
    if args.template not in library:
        print(f"unknown template: {args.template!r} (see server/tasks/library.yaml)")
        return 1

    photo = Path(args.photo)
    if not photo.is_file():
        print(f"photo not found: {photo}")
        return 1

    task = library[args.template]
    print(f"template : {task.id} ({task.category})")
    print(f"title    : {task.title}")
    print(f"proof    : {task.proof_hint}")
    print(f"criteria : {len(task.criteria)}"
          f" ({sum(c.required for c in task.criteria)} required)")
    print(f"photo    : {photo} ({photo.stat().st_size} bytes)")
    print("running pipeline ...", flush=True)

    t0 = time.perf_counter()
    result = await verify_photo(photo.read_bytes(), rubric_for(task), args.template, [])
    seconds = time.perf_counter() - t0

    print()
    print(f"verdict  : {result['verdict']}  ({seconds:.1f}s)")
    if result["hint"]:
        print(f"hint     : {result['hint']}")
    if result["reasons"]:
        print(f"reasons  : {'; '.join(result['reasons'])}")
    quality = result.get("quality") or {}
    print(f"quality  : ok={quality.get('ok')}  {'; '.join(quality.get('reasons', []))}")
    if result.get("rubric"):
        print("rubric   :")
        for answer in result["rubric"]:
            print(f"  {answer['id']} = {answer['answer']}  ({answer['evidence']})")
    else:
        print("rubric   : (not evaluated)")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(build_parser().parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
