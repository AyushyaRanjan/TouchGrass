/**
 * Local (on-phone) SQLite store for offline-first behaviour (expo-sqlite).
 *
 * - cached_tasks: the server's daily tasks, so Today works offline.
 * - pending_submissions: the upload queue. Photos are copied to
 *   documentDirectory/photos so they survive cache clearing.
 *
 * Extra columns beyond the base spec: next_attempt_at (retry backoff) and
 * honor_queued ("Mark done anyway" tapped while offline).
 * app_usage(date, seconds) is foreground time per local day (honesty metric).
 * Queue writes call notify() so screens re-read; usage writes do not.
 */

import * as SQLite from "expo-sqlite";

import type { Answer, EvidenceItem, Task, Verdict } from "@/api/types";

export type PendingStatus = "queued" | "uploading" | "uploaded" | "done" | "failed";

export type PendingSubmission = {
  client_submission_id: string;
  task_id: number;
  image_uri: string | null;
  captured_at: string;
  lat: number | null;
  lon: number | null;
  note: string | null;
  status: PendingStatus;
  server_submission_id: number | null;
  verdict: Verdict | null;
  hint: string | null;
  evidence: EvidenceItem[];
  honor_available: boolean;
  attempts: number;
  last_error: string | null;
  created_at: string;
  next_attempt_at: string | null;
  honor_queued: boolean;
};

type Row = Omit<PendingSubmission, "evidence" | "honor_available" | "honor_queued"> & {
  evidence: string | null;
  honor_available: number | null;
  honor_queued: number | null;
};

const SCHEMA = `
CREATE TABLE IF NOT EXISTS cached_tasks (
  date TEXT PRIMARY KEY,
  json TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_submissions (
  client_submission_id TEXT PRIMARY KEY,
  task_id INTEGER NOT NULL,
  image_uri TEXT,
  captured_at TEXT NOT NULL,
  lat REAL,
  lon REAL,
  note TEXT,
  status TEXT NOT NULL CHECK (status IN ('queued','uploading','uploaded','done','failed')),
  server_submission_id INTEGER,
  verdict TEXT,
  hint TEXT,
  evidence TEXT,
  honor_available INTEGER,
  attempts INTEGER DEFAULT 0,
  last_error TEXT,
  created_at TEXT NOT NULL,
  next_attempt_at TEXT,
  honor_queued INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS app_usage (
  date TEXT PRIMARY KEY,
  seconds INTEGER NOT NULL
);
`;

let dbPromise: Promise<SQLite.SQLiteDatabase> | null = null;

function getDb(): Promise<SQLite.SQLiteDatabase> {
  if (!dbPromise) {
    dbPromise = (async () => {
      const db = await SQLite.openDatabaseAsync("touchgrass.db");
      await db.execAsync(SCHEMA);
      return db;
    })();
  }
  return dbPromise;
}

// --- change notifications ------------------------------------------------------

const listeners = new Set<() => void>();

/** Subscribe to local-store changes; returns an unsubscribe function. */
export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function notify(): void {
  listeners.forEach((listener) => listener());
}

// --- cached tasks ----------------------------------------------------------------

export async function upsertTasks(tasks: Task[]): Promise<void> {
  const db = await getDb();
  const fetchedAt = new Date().toISOString();
  for (const task of tasks) {
    await db.runAsync(
      "INSERT OR REPLACE INTO cached_tasks (date, json, fetched_at) VALUES (?, ?, ?)",
      task.date,
      JSON.stringify(task),
      fetchedAt,
    );
  }
  notify();
}

/** All cached tasks, oldest date first. */
export async function getCachedTasks(): Promise<Task[]> {
  const db = await getDb();
  const rows = await db.getAllAsync<{ json: string }>("SELECT json FROM cached_tasks ORDER BY date");
  return rows.map((row) => JSON.parse(row.json) as Task);
}

// --- pending submissions ---------------------------------------------------------

function fromRow(row: Row): PendingSubmission {
  let evidence: EvidenceItem[] = [];
  try {
    evidence = row.evidence ? (JSON.parse(row.evidence) as EvidenceItem[]) : [];
  } catch {
    evidence = [];
  }
  return {
    ...row,
    evidence: evidence.map((e) => ({ ...e, answer: e.answer as Answer })),
    honor_available: !!row.honor_available,
    honor_queued: !!row.honor_queued,
  };
}

export type NewPending = {
  client_submission_id: string;
  task_id: number;
  image_uri: string;
  captured_at: string;
  lat: number | null;
  lon: number | null;
  note: string | null;
};

export async function insertPending(item: NewPending): Promise<void> {
  const db = await getDb();
  await db.runAsync(
    `INSERT INTO pending_submissions
      (client_submission_id, task_id, image_uri, captured_at, lat, lon, note, status, attempts, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?)`,
    item.client_submission_id,
    item.task_id,
    item.image_uri,
    item.captured_at,
    item.lat,
    item.lon,
    item.note,
    new Date().toISOString(),
  );
  notify();
}

/** All items, oldest first. */
export async function listPending(): Promise<PendingSubmission[]> {
  const db = await getDb();
  const rows = await db.getAllAsync<Row>("SELECT * FROM pending_submissions ORDER BY created_at, rowid");
  return rows.map(fromRow);
}

export async function getPending(id: string): Promise<PendingSubmission | null> {
  const db = await getDb();
  const row = await db.getFirstAsync<Row>("SELECT * FROM pending_submissions WHERE client_submission_id = ?", id);
  return row ? fromRow(row) : null;
}

export type PendingPatch = Partial<
  Omit<PendingSubmission, "client_submission_id" | "evidence" | "honor_available" | "honor_queued">
> & {
  evidence?: EvidenceItem[];
  honor_available?: boolean;
  honor_queued?: boolean;
};

export async function updatePending(id: string, patch: PendingPatch): Promise<void> {
  const entries = Object.entries(patch).filter(([, value]) => value !== undefined);
  if (entries.length === 0) return;
  const columns = entries.map(([key]) => `${key} = ?`).join(", ");
  const values = entries.map(([key, value]) => {
    if (key === "evidence") return JSON.stringify(value);
    if (typeof value === "boolean") return value ? 1 : 0;
    return value as string | number | null;
  });
  const db = await getDb();
  await db.runAsync(`UPDATE pending_submissions SET ${columns} WHERE client_submission_id = ?`, ...values, id);
  notify();
}

/** After a restart, 'uploading' items go back to 'queued' (uploads are idempotent). */
export async function resetStuckUploading(): Promise<void> {
  const db = await getDb();
  await db.runAsync("UPDATE pending_submissions SET status = 'queued' WHERE status = 'uploading'");
  notify();
}

/** Make every queued item due now (used on foreground / network change). */
export async function clearBackoff(): Promise<void> {
  const db = await getDb();
  await db.runAsync("UPDATE pending_submissions SET next_attempt_at = NULL WHERE status = 'queued'");
}

/** Items still waiting to reach the server (queued or uploading). */
export async function queuedCount(): Promise<number> {
  const db = await getDb();
  const row = await db.getFirstAsync<{ n: number }>(
    "SELECT COUNT(*) AS n FROM pending_submissions WHERE status IN ('queued','uploading')",
  );
  return row?.n ?? 0;
}

// --- app usage (foreground seconds per local day) ----------------------------

/** Add `delta` seconds to the given local YYYY-MM-DD. Does not notify screens. */
export async function addUsageSeconds(date: string, delta: number): Promise<void> {
  if (delta <= 0) return;
  const db = await getDb();
  await db.runAsync(
    "INSERT INTO app_usage (date, seconds) VALUES (?, ?) ON CONFLICT(date) DO UPDATE SET seconds = seconds + excluded.seconds",
    date,
    delta,
  );
}

export async function getUsage(date: string): Promise<number> {
  const db = await getDb();
  const row = await db.getFirstAsync<{ seconds: number }>("SELECT seconds FROM app_usage WHERE date = ?", date);
  return row?.seconds ?? 0;
}

/** Inclusive YYYY-MM-DD range, oldest first. Days with no row are omitted. */
export async function getUsageRange(from: string, to: string): Promise<{ date: string; seconds: number }[]> {
  const db = await getDb();
  return db.getAllAsync<{ date: string; seconds: number }>(
    "SELECT date, seconds FROM app_usage WHERE date >= ? AND date <= ? ORDER BY date",
    from,
    to,
  );
}
