# Spike: VLM question answering

Throwaway script that sends photos to a local Ollama VLM and checks its
answers against expected yes/no values. Uses only httpx + Pillow.

## Add your photos

1. Put your photos in `spike/photos/` (the folder is gitignored, so your
   photos never end up in git). Example: `spike/photos/garden.jpg`.
2. Edit `spike/cases.json` — one entry per photo:

   ```json
   {
     "file": "garden.jpg",
     "questions": [
       { "q": "Is there green grass visible in the photo?", "expected": "yes" },
       { "q": "Is a laptop screen visible in the photo?", "expected": "no" }
     ]
   }
   ```

   - `file` is the filename inside `spike/photos/`.
   - `q` is the question text (yes/no answerable).
   - `expected` is what YOU think the correct answer is: `"yes"` or `"no"`.
   - Add as many questions per photo as you like.

## Run

Ollama must be running and the model pulled first:

```bash
ollama pull qwen2.5vl:3b
```

From the repo root:

```bash
uv run python spike/spike.py
```

Options:

- `--model MODEL` — override the model (default: `VLM_MODEL` from `.env`, else `qwen2.5vl:3b`)
- `--url URL` — override the Ollama URL (default: `OLLAMA_URL` from `.env`, else `http://localhost:11434`)
- `--cases PATH` — use a different cases file
- `--timeout SECS` — per-request timeout (default 120)

## Output

Per photo: expected vs got + model evidence, and seconds per photo.
At the end: overall accuracy (unclear answers excluded from the
denominator), count of unclear answers, and the count of **false-yes**
answers (expected no, got yes) — that last number is the one that
matters for cheating resistance.
