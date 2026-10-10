"""Throwaway spike: ask a local Ollama VLM the questions in spike/cases.json.

Usage (from repo root):
    uv run python spike/spike.py [--model MODEL] [--url URL] [--cases PATH] [--timeout SECS]

Uses only httpx + Pillow (plus the stdlib). See spike/README.md.
"""

import argparse
import base64
import io
import json
import sys
import time
from pathlib import Path

import httpx
from PIL import Image, ImageOps

SPIKE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SPIKE_DIR.parent
DEFAULT_MODEL = "qwen2.5vl:3b"
DEFAULT_URL = "http://localhost:11434"
MAX_LONG_SIDE = 768

SYSTEM_PROMPT = (
    "You are a strict photo checker. You answer questions about ONE photo. "
    "Rules: Answer \"no\" or \"unclear\" unless the thing is clearly visible in the photo. "
    "Never guess. Never assume something is present just because it is likely. "
    "Use only what you can actually see in the image. "
    "Give a one-line evidence string quoting what you can see (or that you cannot see it). "
    "Valid answers are exactly: yes, no, unclear."
)


def load_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env reader: KEY=VALUE lines, '#' comments, no interpolation."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def prepare_image(path: Path) -> str:
    """Load a photo, downscale to max 768px on the long side, return base64 JPEG."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGB")
        im.thumbnail((MAX_LONG_SIDE, MAX_LONG_SIDE))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def build_schema(n: int) -> dict:
    """JSON schema: one property q1..qN, each {answer: yes|no|unclear, evidence}."""
    item = {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "enum": ["yes", "no", "unclear"]},
            "evidence": {"type": "string"},
        },
        "required": ["answer", "evidence"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {f"q{i}": item for i in range(1, n + 1)},
        "required": [f"q{i}" for i in range(1, n + 1)],
        "additionalProperties": False,
    }


def ask_ollama(
    client: httpx.Client,
    url: str,
    model: str,
    image_b64: str,
    questions: list[str],
    timeout: float,
) -> dict[str, dict]:
    """Ask all questions in one chat call. Returns {qN: {answer, evidence}}.

    Retries once if the response is not valid JSON matching the schema
    (AGENTS.md rule 3); on second failure every answer is "unclear".
    """
    schema = build_schema(len(questions))
    user_lines = [f"q{i}: {q}" for i, q in enumerate(questions, start=1)]
    payload = {
        "model": model,
        "stream": False,
        "keep_alive": "10m",
        "format": schema,
        "options": {"temperature": 0, "num_ctx": 4096},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Photo questions:\n" + "\n".join(user_lines),
                "images": [image_b64],
            },
        ],
    }

    last_err = "no attempt"
    for attempt in (1, 2):
        try:
            resp = client.post(f"{url}/api/chat", json=payload, timeout=timeout)
            resp.raise_for_status()
            content = resp.json()["message"]["content"]
            parsed = validate(json.loads(content), len(questions))
            return parsed
        except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            last_err = f"{type(e).__name__}: {e}"
            if attempt == 1:
                print(f"    retry once (bad response: {last_err})", file=sys.stderr)

    print(f"    giving up, returning unclear: {last_err}", file=sys.stderr)
    return {
        f"q{i}": {"answer": "unclear", "evidence": "invalid JSON from model"}
        for i in range(1, len(questions) + 1)
    }


def validate(data: dict, n: int) -> dict[str, dict]:
    """Ensure every q1..qN exists with a valid answer and a string evidence."""
    if not isinstance(data, dict):
        raise ValueError(f"not an object: {data!r}")
    out: dict[str, dict] = {}
    for i in range(1, n + 1):
        item = data[f"q{i}"]
        if item["answer"] not in ("yes", "no", "unclear"):
            raise ValueError(f"bad answer for q{i}: {item['answer']!r}")
        out[f"q{i}"] = {"answer": item["answer"], "evidence": str(item["evidence"])}
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    dotenv = load_dotenv(REPO_ROOT / ".env")
    parser.add_argument("--model", default=dotenv.get("VLM_MODEL", DEFAULT_MODEL))
    parser.add_argument("--url", default=dotenv.get("OLLAMA_URL", DEFAULT_URL))
    parser.add_argument("--cases", type=Path, default=SPIKE_DIR / "cases.json")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    print(f"model={args.model} url={args.url} cases={len(cases)}")

    total = correct = answered = unclear = false_yes = skipped = 0
    started_all = time.perf_counter()

    with httpx.Client() as client:
        for case in cases:
            photo = SPIKE_DIR / "photos" / case["file"]
            questions = case["questions"]
            if not photo.is_file():
                skipped += 1
                print(f"\n{case['file']}: SKIPPED (not found at {photo})")
                continue

            t0 = time.perf_counter()
            image_b64 = prepare_image(photo)
            answers = ask_ollama(
                client, args.url, args.model, image_b64,
                [q["q"] for q in questions], args.timeout,
            )
            secs = time.perf_counter() - t0

            print(f"\n{case['file']}  ({secs:.1f}s)")
            for i, q in enumerate(questions, start=1):
                got = answers[f"q{i}"]["answer"]
                ev = answers[f"q{i}"]["evidence"]
                total += 1
                if got == "unclear":
                    unclear += 1
                else:
                    answered += 1
                    if got == q["expected"]:
                        correct += 1
                    if q["expected"] == "no" and got == "yes":
                        false_yes += 1
                mark = "ok  " if got == q["expected"] else "MISS"
                print(f"  [{mark}] expected={q['expected']:<7} got={got:<7} | {q['q']}")
                print(f"         evidence: {ev}")
            print(f"  time: {secs:.1f}s for this photo")

    elapsed = time.perf_counter() - started_all
    denom = answered if answered else 1
    acc = 100.0 * correct / denom if answered else 0.0
    print("\n" + "=" * 60)
    print(f"questions total : {total}   (skipped photos: {skipped})")
    print(f"accuracy        : {correct}/{answered} = {acc:.1f}%  (unclear excluded)")
    print(f"unclear answers : {unclear}")
    print(f"false-yes       : {false_yes}  (expected no, got yes)")
    print(f"wall time       : {elapsed:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
