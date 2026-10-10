/**
 * Offline-first sync engine. One function, sync(), guarded by a mutex.
 *
 * When the server is reachable it: caches tasks (today + 3 days), uploads
 * queued photos oldest first (XHR submitPhoto, idempotent via
 * client_submission_id), polls uploaded ones until they have a verdict,
 * sends queued "mark done anyway" requests, and refreshes stats.
 *
 * Error rules:
 *  - network / timeout / 5xx: keep queued, retry with exponential backoff (max 5 min)
 *  - HTTP 401: stop syncing and show "Token mismatch, check Settings" (no retry loop)
 *  - other 4xx (e.g. wrong-day capture): mark failed with the server's message, never retry
 */

import * as FileSystem from "expo-file-system/legacy";

import { ApiError, UploadError, type TouchGrassClient } from "@/api/client";
import type { Stats } from "@/api/types";
import {
  clearBackoff,
  listPending,
  notify,
  updatePending,
  upsertTasks,
  type PendingSubmission,
} from "@/db/local";
import { readCache, writeCache, STATS_CACHE_KEY } from "@/lib/cache";
import { backoffDelayMs, classifyFailure, isDue } from "@/sync/rules";

export const STATS_KEY = STATS_CACHE_KEY;
export const AUTH_MESSAGE = "Token mismatch, check Settings";
const POLL_AGAIN_MS = 3000;

export type SyncState = {
  online: boolean;
  syncing: boolean;
  authBlocked: boolean;
  authMessage: string | null;
  lastSyncAt: string | null;
};

let state: SyncState = { online: true, syncing: false, authBlocked: false, authMessage: null, lastSyncAt: null };
const stateListeners = new Set<() => void>();

export function getSyncState(): SyncState {
  return state;
}

export function subscribeSyncState(listener: () => void): () => void {
  stateListeners.add(listener);
  return () => {
    stateListeners.delete(listener);
  };
}

function setState(patch: Partial<SyncState>): void {
  state = { ...state, ...patch };
  stateListeners.forEach((l) => l());
}

/** Called when Settings are saved: a new token deserves a fresh try. */
export function clearAuthBlock(): void {
  setState({ authBlocked: false, authMessage: null });
}

export async function loadCachedStats(): Promise<Stats | null> {
  const cached = await readCache<Stats>(STATS_KEY);
  return cached ? cached.data : null;
}

class AuthBlocked extends Error {}

/** Turn any failure into a decision and apply it to the item (401 throws AuthBlocked to stop the pass). */
async function handleFailure(item: PendingSubmission, err: unknown): Promise<void> {
  const upload = err instanceof UploadError ? err : null;
  const api = err instanceof ApiError ? err : null;
  const kind = upload?.kind ?? (api && !api.isNetworkError ? "http" : "network");
  const status = upload?.status ?? api?.status;
  const message = upload?.message ?? (err instanceof Error ? err.message : String(err));
  const action = classifyFailure(kind, status);

  if (action === "block") {
    setState({ authBlocked: true, authMessage: AUTH_MESSAGE });
    // Keep the item queued; it is sent after the token is fixed.
    await updatePending(item.client_submission_id, { status: "queued", last_error: AUTH_MESSAGE });
    throw new AuthBlocked();
  }
  if (action === "fail") {
    await updatePending(item.client_submission_id, { status: "failed", last_error: message });
    return;
  }
  // A lost connection ends this pass; the next sync probes again.
  if (kind === "network" || kind === "timeout") setState({ online: false });
  const attempts = item.attempts + 1;
  await updatePending(item.client_submission_id, {
    status: "queued",
    attempts,
    last_error: message,
    next_attempt_at: new Date(Date.now() + backoffDelayMs(attempts)).toISOString(),
  });
}

/** Delete the local photo copy once the server has a final verdict AND a thumbnail. */
async function cleanupLocalPhoto(api: TouchGrassClient, item: PendingSubmission, token: string): Promise<void> {
  if (!item.image_uri || item.server_submission_id === null || !FileSystem.cacheDirectory) return;
  const dir = `${FileSystem.cacheDirectory}thumbs/`;
  const path = `${dir}${item.server_submission_id}_1.jpg`;
  try {
    await FileSystem.makeDirectoryAsync(dir, { intermediates: true }).catch(() => undefined);
    const info = await FileSystem.getInfoAsync(path);
    if (!info.exists) {
      const result = await FileSystem.downloadAsync(api.imageUrl(item.server_submission_id, true), path, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (result.status !== 200) {
        await FileSystem.deleteAsync(path, { idempotent: true });
        return; // thumbnail not on the server yet: keep the local photo
      }
    }
    await FileSystem.deleteAsync(item.image_uri, { idempotent: true });
    await updatePending(item.client_submission_id, { image_uri: null });
  } catch {
    /* try again on the next sync */
  }
}

async function runSync(api: TouchGrassClient, token: string, resetBackoff: boolean): Promise<boolean> {
  if (state.authBlocked) return false;

  // (a) reachability through the client's URL list
  const reachable = await api.probe();
  setState({ online: reachable });
  if (!reachable) return false;

  try {
    if (resetBackoff) await clearBackoff();

    // (b1) tasks: today + next 3 days
    try {
      const tasks = await api.tasks(undefined, 4);
      await upsertTasks(tasks.tasks);
    } catch (err) {
      await handleTopLevel(err);
    }

    // (b2) uploads, oldest first
    for (const item of (await listPending()).filter((p) => p.status === "queued" && p.image_uri)) {
      if (!isDue(item.next_attempt_at, Date.now())) continue;
      await updatePending(item.client_submission_id, { status: "uploading" });
      try {
        const created = await api.submitPhoto({
          taskId: item.task_id,
          clientSubmissionId: item.client_submission_id,
          capturedAt: item.captured_at,
          note: item.note ?? undefined,
          lat: item.lat ?? undefined,
          lon: item.lon ?? undefined,
          image: { uri: item.image_uri as string, name: "photo.jpg", type: "image/jpeg" },
        });
        await updatePending(item.client_submission_id, {
          status: "uploaded",
          server_submission_id: created.submission_id,
          last_error: null,
          next_attempt_at: null,
        });
      } catch (err) {
        await handleFailure(item, err);
        if (!state.online) break;
      }
    }

    // (b3) poll uploaded items
    for (const item of (await listPending()).filter((p) => p.status === "uploaded")) {
      if (item.server_submission_id === null) continue;
      try {
        const result = await api.getSubmission(item.server_submission_id);
        if (result.status === "done") {
          await updatePending(item.client_submission_id, {
            status: "done",
            verdict: result.verdict,
            hint: result.hint,
            evidence: result.evidence,
            honor_available: result.honor_available,
            last_error: null,
          });
        } else if (result.status === "error") {
          await updatePending(item.client_submission_id, {
            status: "failed",
            last_error: result.error ?? "The server could not check the photo.",
          });
        }
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          // The server forgot it (e.g. database reset): send it again.
          await updatePending(item.client_submission_id, { status: "queued", server_submission_id: null });
        } else {
          await handleTopLevel(err);
        }
      }
    }

    // (b4) queued "Mark done anyway"
    for (const item of (await listPending()).filter((p) => p.honor_queued && p.server_submission_id !== null)) {
      try {
        await api.honor(item.server_submission_id as number);
        await updatePending(item.client_submission_id, { honor_queued: false, honor_available: false });
      } catch (err) {
        if (err instanceof ApiError && err.status !== undefined && err.status !== 401 && err.status < 500) {
          // 409 etc.: it cannot be honored (any more); drop the request.
          await updatePending(item.client_submission_id, { honor_queued: false, last_error: err.message });
        } else {
          await handleTopLevel(err);
        }
      }
    }

    // (b5) stats, cached so the streak shows offline
    try {
      const stats = await api.stats();
      await writeCache(STATS_KEY, stats);
    } catch (err) {
      await handleTopLevel(err);
    }

    // Free local photos whose verdict is final and thumbnail exists on the server.
    for (const item of (await listPending()).filter((p) => p.status === "done" && p.image_uri)) {
      await cleanupLocalPhoto(api, item, token);
    }
  } catch (err) {
    if (!(err instanceof AuthBlocked)) throw err;
  }

  setState({ lastSyncAt: new Date().toISOString() });
  notify();
  return (await listPending()).some((p) => p.status === "uploaded");
}

/** A failure outside a single item: 401 blocks syncing, a lost connection ends this pass. */
async function handleTopLevel(err: unknown): Promise<void> {
  if (err instanceof ApiError) {
    if (err.status === 401) {
      setState({ authBlocked: true, authMessage: AUTH_MESSAGE });
      throw new AuthBlocked();
    }
    if (err.isNetworkError) setState({ online: false });
  }
}

let running: Promise<void> | null = null;
let pollTimer: ReturnType<typeof setTimeout> | null = null;

/** Mutex-guarded: a second call while a sync runs returns the running one. */
export function sync(
  api: TouchGrassClient,
  token: string,
  options: { resetBackoff?: boolean } = {},
): Promise<void> {
  if (running) return running;
  setState({ syncing: true });
  running = (async () => {
    try {
      const needsPoll = await runSync(api, token, options.resetBackoff ?? false);
      if (pollTimer) clearTimeout(pollTimer);
      // Items still being checked: look again in 3 s (AGENTS.md polling rule).
      if (needsPoll) pollTimer = setTimeout(() => void sync(api, token), POLL_AGAIN_MS);
    } catch (err) {
      console.warn("sync failed", err instanceof Error ? `${err.name}: ${err.message}` : String(err));
    } finally {
      running = null;
      setState({ syncing: false });
    }
  })();
  return running;
}
