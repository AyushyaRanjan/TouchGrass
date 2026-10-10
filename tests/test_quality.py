"""Quality gate tests on synthetic images."""

import io

import numpy as np
from PIL import Image, ImageFilter

from server.verify.quality import check_quality


def make_jpeg(width=1280, height=960, seed=0, amplitude=255, offset=0, blur=0) -> bytes:
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 255, (height, width, 3)).astype(np.float32)
    arr = arr * (amplitude / 255.0) + offset
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    if blur:
        im = im.filter(ImageFilter.GaussianBlur(blur))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def test_good_photo_passes():
    result = check_quality(make_jpeg())
    assert result == {"ok": True, "reasons": []}


def test_unreadable_bytes_fail():
    result = check_quality(b"definitely not an image")
    assert result["ok"] is False
    assert result["reasons"] == ["Not a readable image"]


def test_too_small_fails():
    result = check_quality(make_jpeg(width=320, height=240))
    assert result["ok"] is False
    assert any("too small" in r for r in result["reasons"])


def test_too_dark_fails():
    result = check_quality(make_jpeg(amplitude=40, offset=5))
    assert result["ok"] is False
    assert any("dark" in r for r in result["reasons"])


def test_too_blurry_fails():
    result = check_quality(make_jpeg(blur=12))
    assert result["ok"] is False
    assert any("blurry" in r for r in result["reasons"])


def test_multiple_reasons_collected():
    result = check_quality(make_jpeg(width=320, height=240, amplitude=40, offset=5))
    assert result["ok"] is False
    assert len(result["reasons"]) >= 2
