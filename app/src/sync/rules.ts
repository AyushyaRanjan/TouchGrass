/** Pure helpers for the sync engine (no I/O), so they are easy to unit-test later. */

import type { Task } from "@/api/types";

export const BACKOFF_BASE_MS = 5000;
export const BACKOFF_MAX_MS = 5 * 60 * 1000;

/** Exponential backoff: 5 s * 2^attempts, capped at 5 minutes. */
export function backoffDelayMs(attempts: number): number {
  return Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** Math.max(0, attempts));
}

export type FailureAction = "retry" | "block" | "fail";

/**
 * What to do with an item after an upload/API failure.
 * network / timeout / 5xx -> retry later; 401 -> stop syncing; other 4xx -> failed for good.
 */
export function classifyFailure(kind: "network" | "timeout" | "http" | "client", status?: number): FailureAction {
  if (kind === "network" || kind === "timeout") return "retry";
  if (kind === "http") {
    if (status === 401) return "block";
    if (status !== undefined && status >= 500) return "retry";
    return "fail";
  }
  return "fail";
}

/** True when the item may be tried now. */
export function isDue(nextAttemptAt: string | null, now: number): boolean {
  return !nextAttemptAt || Date.parse(nextAttemptAt) <= now;
}

/** Today = the cached task whose day has not ended yet and ends soonest. */
export function pickToday(tasks: Task[], now: number): Task | null {
  const open = tasks.filter((t) => Date.parse(t.day_ends_at) > now);
  open.sort((a, b) => Date.parse(a.day_ends_at) - Date.parse(b.day_ends_at));
  return open[0] ?? null;
}
