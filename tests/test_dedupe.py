"""Perceptual-hash duplicate detection tests."""

import io

import numpy as np
from PIL import Image

from server.verify.dedupe import compute_phash, find_closest_hash


def make_png(seed) -> bytes:
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 255, (480, 640, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def test_phash_is_deterministic():
    assert compute_phash(make_png(1)) == compute_phash(make_png(1))


def test_phash_differs_for_different_images():
    assert compute_phash(make_png(1)) != compute_phash(make_png(2))


def test_no_candidates_returns_none():
    h = compute_phash(make_png(1))
    assert find_closest_hash(h, [], "task-a") is None


def test_same_task_entries_are_excluded():
    h = compute_phash(make_png(1))
    entries = [("s1", "task-a", h)]
    assert find_closest_hash(h, entries, "task-a") is None


def test_finds_cross_task_match_at_distance_zero():
    h = compute_phash(make_png(1))
    entries = [("s1", "task-a", h), ("s2", "task-b", h)]
    result = find_closest_hash(h, entries, "task-a")
    assert result["distance"] == 0
    assert result["match"]["submission_id"] == "s2"
    assert result["match"]["task_id"] == "task-b"


def test_returns_nearest_of_several():
    h = compute_phash(make_png(1))
    far = compute_phash(make_png(2))
    entries = [("far", "task-b", far), ("near", "task-c", h)]
    result = find_closest_hash(h, entries, "task-a")
    assert result["match"]["submission_id"] == "near"


def test_malformed_hash_is_skipped():
    h = compute_phash(make_png(1))
    entries = [("bad", "task-b", "not-a-hash"), ("good", "task-c", h)]
    result = find_closest_hash(h, entries, "task-a")
    assert result["match"]["submission_id"] == "good"
