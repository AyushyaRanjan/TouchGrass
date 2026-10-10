# Touch Grass: agent instructions

## What this is
Single-user app that gives one daily outdoor task (categories: nature, movement, exploration, social, creative, community). The user submits a live photo as proof from a mobile app. A laptop server verifies it with a local VLM served by Ollama.

## Stack
- Server: Python 3.11, FastAPI, SQLite (stdlib sqlite3, no ORM), httpx, Pillow, imagehash, OpenCV (headless), PyYAML, tzdata (IANA timezones on Windows), managed with uv. Run from repo root: `uv run uvicorn server.main:app --host 0.0.0.0 --port 8000`
- VLM: Ollama at OLLAMA_URL, model from VLM_MODEL setting (default qwen2.5vl:3b). Use /api/chat with `images` (base64), `format` (JSON schema), `stream: false`, `keep_alive: "10m"`, options {temperature: 0, num_ctx: 4096}.
- Mobile: Expo SDK 57 (React Native, TypeScript, expo-router) in /app, using expo-camera, expo-image-manipulator, expo-location, expo-sqlite, expo-network, expo-build-properties and AsyncStorage. The app is offline-first (see the Offline-first rules below). The server API is unchanged by this. Package/bundle id is `com.dagger.touchgrass`. Android `usesCleartextTraffic` and iOS ATS `NSAllowsArbitraryLoads` are on so the phone can talk HTTP to the laptop over Tailscale (already encrypted), not the public internet. Standalone APK: `cd app && eas build --profile preview --platform android`.
- Server images (thumbnails, proof photos) are downloaded through the `useAuthedImage` hook (expo-file-system/legacy `downloadAsync` with an Authorization header, cached under cacheDirectory/thumbs, only after the token has loaded) and shown from the local file. Never use `<Image source={{ headers }}>`.
- Upload flow: capture with CameraView (`facing` back|front, default back; last choice in AsyncStorage `tg:session:cameraFacing` for this JS session only, reset to back on the next launch; `mirror={false}` so a front-camera proof is not flipped). Resize to max 1280px wide, JPEG quality 0.8, send with a UUID `client_submission_id`, then poll GET /submissions/{id} every 3 seconds until done. Location is optional and never blocks capture. The flip and shutter buttons are disabled while a photo is being taken. The 5 s timeout applies only to /health probing and server-URL selection; the photo upload (POST /submissions) uses XMLHttpRequest, because Expo's fetch rejects React Native `{ uri, name, type }` FormData parts. It goes to the last working base with a 90 s timeout and never sets Content-Type manually (React Native adds the multipart boundary). Upload errors are reported honestly: network ("Couldn't reach your server"), timeout, 401 ("Token mismatch, check Settings"), other HTTP errors (the server's detail text) and client-side failures ("The app couldn't prepare the photo"), with a technical detail line under the message.
- Offline-first rules (app):
  - Local store (`app/src/db/local.ts`, expo-sqlite): `cached_tasks(date PK, json, fetched_at)` and `pending_submissions(client_submission_id PK, task_id, image_uri, captured_at, lat, lon, note, status [queued|uploading|uploaded|done|failed], server_submission_id, verdict, hint, evidence, honor_available, attempts, last_error, created_at)` plus the bookkeeping columns `next_attempt_at` (backoff) and `honor_queued` (a "Mark done anyway" waiting to be sent). `app_usage(date PK, seconds)` stores foreground time per phone-local day (honesty metric on Stats).
  - Every captured photo is copied to `FileSystem.documentDirectory/photos/` before it is queued. The copy is deleted only after a final verdict AND a thumbnail exists on the server.
  - Sync engine (`app/src/sync/engine.ts`): one `sync()` guarded by a mutex. Order: probe /health via the client's URL list; if reachable, cache tasks for today + 3 days, upload queued items oldest first, poll uploaded-but-not-done items, send queued honors, refresh stats. Triggers: app open, AppState foreground, expo-network change, every 60 s while open. Uploads are idempotent via `client_submission_id`.
  - Error rules: `network`/`timeout` keep the item queued with exponential backoff (max delay 5 min). HTTP 401 stops syncing and shows "Token mismatch, check Settings" (no retry loop; cleared when Settings are saved). Other HTTP 4xx (e.g. capture on the wrong day) mark the item failed with the server's message and are never retried. Uploads always use the XHR `submitPhoto()`; never fetch for multipart.
  - Today reads `cached_tasks` first and works fully offline; with no cache it says "Open the app once while connected to your server." An item that is queued/uploading/uploaded makes the task "provisionally done" (dotted flame, "+1 pending"). Verdicts arrive later: pass confirms; fail/unsure/retake show the hint with Retake and "Mark done anyway (half XP)" (online it calls the server, offline it is queued).
  - Queued items show "Counts for <capture date>" (local date of `captured_at`). Rerolling is online-only and disabled offline with an explanation. A "Waiting to sync (N)" pill shows on Today. History groups by local day (one collapsed row: outcome pass/honor/not done, title, best-attempt thumbnail; tap expands attempts, including queued status). All photo previews use `useAuthedImage` (or the local file for queued items). Settings is a tab only — no floating gear. Difficulty dots use accessibilityLabel "Difficulty N of 3".
  - Last `/stats` and `/history` responses are cached in AsyncStorage (`tg:cache:stats`, `tg:cache:history`). When the server is unreachable, Stats and History show the cached values with a "Last updated <time>, offline" label. Closed result cards are stored in `tg:dismissed` (in memory + AsyncStorage) so Close is instant and polling/sync never re-opens them. Stats also shows local screen time (today + 7-day average of the last 7 local days, missing days as 0) as an honesty metric — the screen should be the shortest part of the experience.
- Config via pydantic-settings reading .env: API_TOKEN, OLLAMA_URL, VLM_MODEL, TIMEZONE (IANA name, e.g. Europe/London, America/New_York, Asia/Kolkata), BLIND_PASS (bool, default false).

## Layout
server/ (main.py, config.py, db.py, worker.py, verify/, tasks/, game/), eval/ (cases.json, photos/ gitignored, run_eval.py), spike/, tests/, scripts/, data/ (gitignored: sqlite db + images), app/ (Expo: app.config.ts, eas.json, package com.dagger.touchgrass), docs/FIELD_TEST.md. Never commit `.env`, `data/*` (except `.gitkeep`), eval/spike photos, APKs, or signing credentials.

## Hard rules
1. The VLM NEVER decides pass/fail. It only answers rubric questions (yes/no/unclear + short evidence). Verdict logic is pure, unit-tested Python in server/verify/verdict.py.
2. One model on a 4GB GPU. Never run two Ollama requests at once: a single worker, plus one asyncio.Lock around every Ollama call.
3. Always request JSON via Ollama's `format` schema; validate with pydantic; retry once on invalid JSON; on second failure return answer "unclear".
4. Single user. No accounts. All endpoints except GET /health require `Authorization: Bearer <API_TOKEN>` (use secrets.compare_digest).
5. Photos are stored only under data/images/. Nothing is sent to any external service. (Open-Meteo weather is allowed only in V1.5.)
6. Tasks come from a hand-written YAML library (server/tasks/library.yaml). The LLM does not generate tasks or rubrics in V1.
7. No friends, leaderboards, notifications or social features in V1.
8. Derive state from data. Do NOT store counters for xp or streak. Compute from the submissions table.
9. Keep code simple and commented. Any non-HTTP logic gets unit tests.
10. Do not add libraries that are not listed here without asking.
11. Proof photos are LIVE CAPTURE ONLY (expo-camera CameraView). No image picker and no gallery/media-library access anywhere in the codebase.

## Verdict rules (V1)
Pipeline per submission: quality gate -> duplicate check -> VLM rubric -> verdict.
- retake: blur/dark/low-res. VLM is not called. Does not count as an attempt for bonuses.
- rejected: perceptual-hash distance <= 6 to a photo from a DIFFERENT task.
- pass: every `required` criterion answered yes.
- fail: any required criterion answered no. Hint = what was missing, plus the model's evidence.
- unsure: no required "no", but at least one required "unclear".
- Screen-or-print detection (blind pass) is LOGGED ONLY, never used to reject, in V1.
- Optional (non-required) criteria are logged only.
- "Mark done anyway": allowed for any non-pass verdict, once per task, awards half XP (floor).

## Game rules (V1)
- XP: pass = 10 * difficulty, +5 if it was the first attempt on that task. Honor completion = floor(10 * difficulty / 2). Level = xp // 100 + 1.
- A task is "completed" if it has a submission with verdict pass OR honor=1.
- A task's window is the whole local day.
- App display: Today says "Anytime today" with no countdown. After 6 pm local time, if the task is not done, show a gentle "Ends tonight at midnight" (from the server's `day_ends_at`), never red or urgent. The app refreshes Today when the day rolls over (checked on foreground and every minute). Once the task is completed, Today shows exactly one completion card (soft green, check icon, dimmed title): pass says "Done for today. Go outside."; honor says "Marked done (half XP)" in a muted tone; both include "+N XP · N day streak". No header "Done" pill and no "Last attempt" chip. The camera button and reroll stay hidden.
- Streak = consecutive local days (in TIMEZONE) that have at least one completed task, using the photo's CAPTURE time (not verification time). The current streak stays alive if today is not done yet but yesterday was.
- Submission must satisfy: local date of captured_at == task date; captured_at not more than 5 min in the future of server time; not older than 7 days.
- Tasks per day: deterministic pick from the library, using player preferences (see below), avoiding the last 14 days' templates and avoiding the same category two days running. One free reroll per day (same preferences).

## Task preferences
Defaults live in `DEFAULT_PREFERENCES` in `server/tasks/preferences.py` (every category `normal`, `social_comfort_max=2`, `max_minutes=null`). Never insert a preferences row unless the player PUTs. `get_preferences()` returns defaults merged with whatever is stored; missing or null fields (including individual categories) fall back to the default. `is_default` is true iff no row exists.
- Weights: often=3, normal=1, rarely=0.3, off=0. At least two categories must not be `off` (checked on the merged result).
- Hard filters (never relaxed): `off` categories, social tasks with `comfort > social_comfort_max`. Soft: skip tasks with `minutes > max_minutes`.
- If no valid task remains, relax only: 14-day cooldown down to 7, then drop `max_minutes`. Yesterday's category and cooldown may then be relaxed so pick cannot fail; never relax `off` or social comfort.
- `GET /preferences` returns effective values plus `is_default`. `PUT /preferences` accepts a partial body, merges, then deletes future tasks (`date > today`) that have no submissions so they regenerate. Today's task is left alone until a reroll. `POST /preferences/reset` deletes the row.
- App Settings: "Your tasks" at the top (Often / Normal / Rarely / Never per category, social comfort Gentle / Medium / Bold, longest task 15 / 30 / 60 / Any) with "Changes apply from tomorrow. A reroll uses them right away." Connection (URLs, token, test) is a collapsible section below. Offline: show cached effective values read-only (`tg:cache:preferences`) and "Connect to your server to change these." Reset to defaults calls `POST /preferences/reset`.
