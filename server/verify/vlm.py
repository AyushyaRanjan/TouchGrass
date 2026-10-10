"""Async Ollama client (AGENTS.md hard rules 2 and 3).

Rule 2: one asyncio.Lock around EVERY Ollama call, held across retries, so
        two requests are never in flight at once (4GB GPU, one model).
Rule 3: always request JSON via the `format` schema, validate it, retry once
        on invalid JSON, and on second failure fall back to "unclear".
"""

import asyncio
import base64
import io
import json
import re
import sys

import httpx
from PIL import Image, ImageOps

from ..config import (
    EVIDENCE_MIN_CHARS,
    EVIDENCE_MIN_WORDS,
    VLM_LONG_SIDE,
    Settings,
    get_settings,
)

_LOCK = asyncio.Lock()
TIMEOUT = 120.0
_ANSWERS = ("yes", "no", "unclear")

RUBRIC_SYSTEM_PROMPT = (
    "You verify one photo against a rubric. "
    "For every question, write the evidence field FIRST, then the answer. "
    f"Evidence must be a short phrase of at least {EVIDENCE_MIN_WORDS} words "
    "describing what you actually see in the photo, "
    'for example "yellow leaves on a tree branch" or "open sky over grass". '
    f'Too-short evidence such as "sun" or "green grass" (under {EVIDENCE_MIN_WORDS} words) '
    "is invalid and will be rejected. "
    "Do not repeat or paraphrase the question, and never put yes or no in the evidence. "
    'Answer "no" or "unclear" unless the thing is clearly visible in the photo. '
    "Never guess. Quote what you see. Valid answers: yes, no, unclear."
)

BLIND_SYSTEM_PROMPT = (
    "You are shown one photo. In 'description', write one sentence describing "
    "what you see. Then report whether the image appears to be a photo of a "
    "screen, monitor or printed material (is_screen_or_print: true or false) "
    "and whether the scene is outdoors (is_outdoors: true or false). "
    "Do not assume; if you are unsure, describe exactly what you can see."
)

BLIND_SCHEMA = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "is_screen_or_print": {"type": "boolean"},
        "is_outdoors": {"type": "boolean"},
    },
    "required": ["description", "is_screen_or_print", "is_outdoors"],
    "additionalProperties": False,
}


def encode_image(image_bytes: bytes) -> str:
    """Downscale to VLM_LONG_SIDE on the long side and return base64 JPEG."""
    with Image.open(io.BytesIO(image_bytes)) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
        im.thumbnail((VLM_LONG_SIDE, VLM_LONG_SIDE))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _normalize(text) -> str:
    """Lowercase and strip punctuation so echoes of the question are caught."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text).lower()).split())


def valid_evidence(evidence, question: str = "") -> bool:
    """Evidence must be >= 4 words and must not parrot the question.

    Parroting = at least 80% of the evidence words appear in the question
    (keyword overlap is fine, wholesale repetition is not).
    """
    ev_words = _normalize(evidence).split()
    if len(ev_words) < EVIDENCE_MIN_WORDS:
        return False
    q_words = set(_normalize(question).split())
    if q_words:
        overlap = sum(1 for w in ev_words if w in q_words) / len(ev_words)
        if overlap >= 0.8:
            return False
    return True


def build_rubric_schema(criteria: list) -> dict:
    """One property per criterion id. 'evidence' comes before 'answer'
    everywhere, so the model writes what it sees before committing."""
    item = {
        "type": "object",
        "properties": {
            "evidence": {
                "type": "string",
                "minLength": EVIDENCE_MIN_CHARS,
                "description": (
                    f"what you actually see, at least {EVIDENCE_MIN_WORDS} words, "
                    "written before the answer"
                ),
            },
            "answer": {"type": "string", "enum": list(_ANSWERS)},
        },
        "required": ["evidence", "answer"],
        "additionalProperties": False,
    }
    ids = [c["id"] for c in criteria]
    return {
        "type": "object",
        "properties": {cid: item for cid in ids},
        "required": ids,
        "additionalProperties": False,
    }


def _payload(settings: Settings, messages: list, schema: dict) -> dict:
    return {
        "model": settings.VLM_MODEL,
        "stream": False,
        "keep_alive": "10m",
        "format": schema,
        "options": {"temperature": 0, "num_ctx": 4096},
        "messages": messages,
    }


async def _post_chat(settings: Settings, messages: list, schema: dict) -> dict:
    """One HTTP call to /api/chat; returns the parsed JSON object."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{settings.OLLAMA_URL}/api/chat",
            json=_payload(settings, messages, schema),
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError(f"model returned a non-object: {type(data).__name__}")
    return data


def _rubric_item_ok(item, question: str) -> bool:
    return (
        isinstance(item, dict)
        and item.get("answer") in _ANSWERS
        and valid_evidence(item.get("evidence", ""), question)
    )


def _map_rubric(data, criteria: list) -> list:
    """Map schema output to [{id, answer, evidence}] in criteria order.
    Anything invalid becomes 'unclear' (rule 3 fallback)."""
    out = []
    for c in criteria:
        item = data.get(c["id"]) if isinstance(data, dict) else None
        if _rubric_item_ok(item, c["question"]):
            out.append({
                "id": c["id"],
                "answer": item["answer"],
                "evidence": " ".join(str(item["evidence"]).split()),
            })
        else:
            out.append({"id": c["id"], "answer": "unclear", "evidence": "invalid response from model"})
    return out


async def answer_rubric(image_bytes: bytes, criteria: list) -> list:
    """Answer all criteria in one call. Returns [{id, answer, evidence}]."""
    if not criteria:
        return []
    settings = get_settings()
    b64 = encode_image(image_bytes)
    schema = build_rubric_schema(criteria)
    system = {"role": "system", "content": RUBRIC_SYSTEM_PROMPT}
    user_text = "Photo questions (write at least 4 words of evidence for each, then answer):\n" + "\n".join(
        f"{c['id']}: {c['question']}" for c in criteria
    )
    # With temperature 0 an identical retry repeats the same mistake,
    # so the second attempt gets told exactly what was wrong.
    retry_text = (
        "\nYour previous evidence was invalid: it was too short or it repeated "
        "the question. In 'evidence' write a phrase of at least 4 words "
        "describing what you actually see in the photo, then the answer."
    )

    data = None
    error = "no attempt"
    async with _LOCK:
        for attempt in (1, 2):
            text = user_text if attempt == 1 else user_text + retry_text
            messages = [system, {"role": "user", "content": text, "images": [b64]}]
            try:
                data = await _post_chat(settings, messages, schema)
                if all(
                    _rubric_item_ok(data.get(c["id"]), c["question"]) for c in criteria
                ):
                    return _map_rubric(data, criteria)
                error = "invalid payload (evidence rules or missing answers)"
            except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
                error = f"{type(e).__name__}: {e}"
            if attempt == 1:
                print(f"vlm: retrying once ({error})", file=sys.stderr)
    print(f"vlm: giving up, falling back to unclear ({error})", file=sys.stderr)
    return _map_rubric(data, criteria)


def _blind_ok(data) -> bool:
    return (
        isinstance(data, dict)
        and isinstance(data.get("description"), str)
        and data["description"].strip() != ""
        and isinstance(data.get("is_screen_or_print"), bool)
        and isinstance(data.get("is_outdoors"), bool)
    )


async def blind_describe(image_bytes: bytes) -> dict:
    """Blind description, never mentions the task (AGENTS.md rule 5).
    Logged only; never used to reject in V1."""
    settings = get_settings()
    b64 = encode_image(image_bytes)
    messages = [
        {"role": "system", "content": BLIND_SYSTEM_PROMPT},
        {"role": "user", "content": "Describe this photo.", "images": [b64]},
    ]

    error = "no attempt"
    async with _LOCK:
        for attempt in (1, 2):
            try:
                data = await _post_chat(settings, messages, BLIND_SCHEMA)
                if _blind_ok(data):
                    return {
                        "description": " ".join(data["description"].split()),
                        "is_screen_or_print": data["is_screen_or_print"],
                        "is_outdoors": data["is_outdoors"],
                    }
                error = "invalid blind payload"
            except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
                error = f"{type(e).__name__}: {e}"
            if attempt == 1:
                print(f"vlm: retrying once ({error})", file=sys.stderr)
    print(f"vlm: giving up on blind description ({error})", file=sys.stderr)
    return {"description": "invalid response from model", "is_screen_or_print": None, "is_outdoors": None}
