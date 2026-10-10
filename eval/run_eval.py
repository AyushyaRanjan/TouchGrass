"""Offline evaluation harness.

Reads eval/cases.json (list of cases, see below) with photos in
eval/photos/, runs every case through the full verification pipeline in
order, and keeps a running hash list so duplicates across tasks are
detectable.

Each case needs either:
  - "rubric": {criteria: [...]}   an inline rubric (any task id), or
  - "template_id": "nat_leaf_hunt" the rubric is taken from the task library.
task_id defaults to template_id, else "task-N" (explicit "task_id" wins).

Run from repo root:  uv run python -m eval.run_eval
"""

import asyncio
import json
import sys
import time
from pathlib import Path

from server.tasks.library import get_library, rubric_for
from server.verify.pipeline import verify_photo

EVAL_DIR = Path(__file__).resolve().parent
VERDICTS = ("pass", "fail", "unsure", "retake", "rejected")


def load_cases() -> list:
    path = EVAL_DIR / "cases.json"
    return json.loads(path.read_text(encoding="utf-8"))


def case_task_id(case: dict, index: int) -> str:
    if "task_id" in case:
        return case["task_id"]
    if "template_id" in case:
        return case["template_id"]
    return f"task-{index}"


def case_criteria(case: dict, library: dict) -> list:
    """The rubric for a case: inline, or converted from a library template."""
    if "template_id" in case:
        template_id = case["template_id"]
        if template_id not in library:
            raise ValueError(f"case {case.get('file', '?')}: unknown template_id {template_id!r}")
        return rubric_for(library[template_id])
    if "rubric" in case:
        return case["rubric"]["criteria"]
    raise ValueError(f"case {case.get('file', '?')}: needs rubric or template_id")


def evidence_line(result: dict) -> str:
    if not result.get("rubric"):
        return ""
    return " | ".join(
        f'{a["id"]}={a["answer"]} ("{a["evidence"]}")' for a in result["rubric"]
    )


async def run() -> int:
    cases = load_cases()
    library = get_library()
    known_hashes: list = []
    rows: list = []

    for i, case in enumerate(cases, start=1):
        task_id = case_task_id(case, i)
        photo = EVAL_DIR / "photos" / case["file"]
        expected = case["expected"]

        if not photo.is_file():
            rows.append({
                "file": case["file"], "task_id": task_id, "expected": expected,
                "got": "skipped", "seconds": 0.0, "evidence": "", "hint": "",
                "reasons": f"photo not found: {photo.name}",
            })
            print(f"[{i}/{len(cases)}] {case['file']}: SKIPPED (photo not found)")
            continue

        print(f"[{i}/{len(cases)}] {case['file']} (expected {expected}) ...", flush=True)
        t0 = time.perf_counter()
        result = await verify_photo(
            photo.read_bytes(), case_criteria(case, library), task_id, known_hashes
        )
        seconds = time.perf_counter() - t0

        if result["phash"]:
            # running hash list: later cases can only duplicate EARLIER photos
            known_hashes.append((case["file"], task_id, result["phash"]))

        rows.append({
            "file": case["file"], "task_id": task_id, "expected": expected,
            "got": result["verdict"], "seconds": seconds,
            "evidence": evidence_line(result),
            "hint": result["hint"], "reasons": "; ".join(result["reasons"]),
        })
        print(f"    -> {result['verdict']} in {seconds:.1f}s")

    return report(rows)


def report(rows: list) -> int:
    processed = [r for r in rows if r["got"] != "skipped"]
    skipped = len(rows) - len(processed)
    n = len(processed)

    expected_pass = [r for r in processed if r["expected"] == "pass"]
    false_rejects = [r for r in expected_pass if r["got"] != "pass"]
    fr_split = {
        v: sum(1 for r in false_rejects if r["got"] == v)
        for v in ("fail", "unsure", "retake", "rejected")
    }

    expected_other = [r for r in processed if r["expected"] != "pass"]
    false_accepts = [r for r in expected_other if r["got"] == "pass"]

    unsure = [r for r in processed if r["got"] == "unsure"]
    mismatches = [r for r in processed if r["got"] != r["expected"]]

    def pct(part, whole):
        return f"{100.0 * len(part) / len(whole):.1f}%" if whole else "n/a"

    print()
    print("=" * 72)
    print(f"cases                : {len(rows)}  (processed {n}, skipped {skipped})")
    print(f"false-reject rate    : {pct(false_rejects, expected_pass)}"
          f"  ({len(false_rejects)}/{len(expected_pass)} expected pass, got something else)")
    print(f"    split            : fail={fr_split['fail']} unsure={fr_split['unsure']}"
          f" retake={fr_split['retake']} rejected={fr_split['rejected']}")
    print(f"false-accept rate    : {pct(false_accepts, expected_other)}"
          f"  ({len(false_accepts)}/{len(expected_other)} expected not-pass, got pass)")
    print(f"unsure rate          : {pct(unsure, processed)}  ({len(unsure)}/{n})")
    if processed:
        avg = sum(r["seconds"] for r in processed) / len(processed)
        print(f"avg seconds per image: {avg:.1f}s")
    print(f"mismatches           : {len(mismatches)}")

    if mismatches:
        print()
        print("-" * 72)
        print(f"{'file':<24} {'expected':<9} {'got':<9} {'s':>6}  evidence")
        print("-" * 72)
        for r in mismatches:
            print(f"{r['file']:<24} {r['expected']:<9} {r['got']:<9} {r['seconds']:>6.1f}")
            if r["reasons"]:
                print(f"    reasons : {r['reasons']}")
            if r["hint"]:
                print(f"    hint    : {r['hint']}")
            if r["evidence"]:
                print(f"    evidence: {r['evidence']}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
