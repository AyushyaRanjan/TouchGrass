"""Photo quality gate: blurry / dark / low-res photos are sent back for a retake."""

import io

import cv2
import numpy as np
from PIL import Image, ImageOps

from ..config import BLUR_THRESHOLD, BRIGHTNESS_THRESHOLD, MIN_SHORT_SIDE, QUALITY_LONG_SIDE


def check_quality(image_bytes: bytes) -> dict:
    """Return {"ok": bool, "reasons": [human-friendly strings]}.

    Blur and brightness are measured on a copy downscaled to
    QUALITY_LONG_SIDE on the long side; the resolution check uses the
    original dimensions.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as im:
            im = ImageOps.exif_transpose(im)
            width, height = im.size
            gray = im.convert("L")
    except Exception:
        return {"ok": False, "reasons": ["Not a readable image"]}

    reasons = []

    if min(width, height) < MIN_SHORT_SIDE:
        reasons.append("Image too small, move a little closer")

    gray.thumbnail((QUALITY_LONG_SIDE, QUALITY_LONG_SIDE))
    arr = np.asarray(gray)
    if float(arr.mean()) < BRIGHTNESS_THRESHOLD:
        reasons.append("Too dark, find some light")
    if float(cv2.Laplacian(arr, cv2.CV_64F).var()) < BLUR_THRESHOLD:
        reasons.append("Too blurry, hold still")

    return {"ok": not reasons, "reasons": reasons}
