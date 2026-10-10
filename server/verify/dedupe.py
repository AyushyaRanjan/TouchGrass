"""Perceptual hashing for duplicate detection across tasks."""

import io

import imagehash
from PIL import Image, ImageOps


def compute_phash(image_bytes: bytes) -> str:
    """Return the perceptual hash of an image as a hex string."""
    with Image.open(io.BytesIO(image_bytes)) as im:
        im = ImageOps.exif_transpose(im)
        return str(imagehash.phash(im.convert("L")))


def find_closest_hash(phash: str, entries, exclude_task_id: str) -> dict | None:
    """Find the closest known hash from a DIFFERENT task.

    entries: list of (submission_id, task_id, hash_hex).
    Returns {"distance": int, "match": {submission_id, task_id, hash}} or
    None when there is no candidate (all entries belong to exclude_task_id).
    """
    query = imagehash.hex_to_hash(phash)
    best_dist = None
    best_entry = None
    for submission_id, task_id, other in entries:
        if task_id == exclude_task_id:
            continue
        try:
            dist = int(query - imagehash.hex_to_hash(other))
        except Exception:
            continue  # skip malformed hashes
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_entry = (submission_id, task_id, other)

    if best_entry is None:
        return None
    return {
        "distance": best_dist,
        "match": {
            "submission_id": best_entry[0],
            "task_id": best_entry[1],
            "hash": best_entry[2],
        },
    }
