"""Verification pipeline: quality gate -> duplicate check -> VLM rubric -> verdict."""

import time

from . import dedupe, quality, vlm
from ..config import DUPE_DISTANCE, get_settings
from .verdict import compute_verdict


def _ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


async def verify_photo(image_bytes: bytes, criteria: list, task_id: str, known_hashes: list) -> dict:
    """Run the full V1 verification pipeline on one photo.

    known_hashes: running list of (submission_id, task_id, hash_hex) so
    duplicates across tasks are detectable.
    """
    settings = get_settings()
    t_total = time.perf_counter()

    t0 = time.perf_counter()
    quality_result = quality.check_quality(image_bytes)
    t_quality = _ms(t0)

    phash = None
    try:
        phash = dedupe.compute_phash(image_bytes)
    except Exception:
        phash = None  # unreadable image; quality gate will catch it

    dupe = None
    rubric = []
    blind = None
    t_dupe = t_vlm = t_blind = 0.0

    if quality_result["ok"]:
        t0 = time.perf_counter()
        dupe = dedupe.find_closest_hash(phash, known_hashes, task_id) if phash else None
        t_dupe = _ms(t0)

        # A cross-task duplicate is rejected without waking the GPU.
        if dupe is None or int(dupe.get("distance", 999)) > DUPE_DISTANCE:
            t0 = time.perf_counter()
            rubric = await vlm.answer_rubric(image_bytes, criteria)
            t_vlm = _ms(t0)

            if settings.BLIND_PASS:
                t0 = time.perf_counter()
                blind = await vlm.blind_describe(image_bytes)
                t_blind = _ms(t0)
        else:
            rubric = vlm_stub_answers(criteria)

    result = compute_verdict(quality_result, dupe, rubric, criteria)

    details = {"blind_pass": settings.BLIND_PASS}
    if blind is not None:
        details["blind"] = blind  # logged only, never used to reject (V1)

    return {
        "task_id": task_id,
        "verdict": result["verdict"],
        "reasons": result["reasons"],
        "hint": result["hint"],
        "phash": phash,
        "quality": quality_result,
        "dupe": dupe,
        "rubric": rubric,
        "blind": blind,
        "details": details,
        "timings": {
            "quality_ms": t_quality,
            "dupe_ms": t_dupe,
            "vlm_ms": t_vlm,
            "blind_ms": t_blind,
            "total_ms": _ms(t_total),
        },
    }


def vlm_stub_answers(criteria: list) -> list:
    """Placeholder answers when the VLM is skipped (dupe already rejected)."""
    return [
        {"id": c["id"], "answer": "unclear", "evidence": "not evaluated, duplicate rejected"}
        for c in criteria
    ]
