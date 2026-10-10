/**
 * Group history (server rows + local-only queue items) by the capture's
 * local calendar day, pick a day's outcome, and choose the best thumbnail.
 */

import type { HistoryRow, Task } from "@/api/types";
import type { PendingSubmission } from "@/db/local";

export type HistoryAttempt =
  | { kind: "local"; key: string; item: PendingSubmission }
  | { kind: "server"; key: string; row: HistoryRow };

export type DayOutcome = "pass" | "honor" | "not_done" | "waiting";

export type DayGroup = {
  date: string; // YYYY-MM-DD
  title: string;
  outcome: DayOutcome;
  best: HistoryAttempt;
  attempts: HistoryAttempt[];
};

/** Display a YYYY-MM-DD as "Sat 10 Oct". Noon avoids UTC-midnight shifting the day. */
export function formatDayLabel(ymd: string): string {
  const d = new Date(`${ymd}T12:00:00`);
  if (Number.isNaN(d.getTime())) return ymd;
  const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${WEEKDAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}`;
}

/** Phone-local YYYY-MM-DD of a UTC ISO capture time. */
export function localDateKey(iso: string): string {
  const d = new Date(iso);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function dateOf(attempt: HistoryAttempt): string {
  if (attempt.kind === "server") {
    return attempt.row.local_date ?? (attempt.row.captured_at ? localDateKey(attempt.row.captured_at) : "unknown");
  }
  return localDateKey(attempt.item.captured_at);
}

function titleOf(attempt: HistoryAttempt, tasks: Task[]): string {
  if (attempt.kind === "server") {
    return attempt.row.title ?? `Task #${attempt.row.task_id}`;
  }
  return tasks.find((t) => t.id === attempt.item.task_id)?.title ?? `Task #${attempt.item.task_id}`;
}

function hasThumb(attempt: HistoryAttempt): boolean {
  if (attempt.kind === "server") return true;
  return attempt.item.server_submission_id !== null || !!attempt.item.image_uri;
}

function isPass(attempt: HistoryAttempt): boolean {
  return attempt.kind === "server" ? attempt.row.verdict === "pass" : attempt.item.verdict === "pass";
}

function isHonor(attempt: HistoryAttempt): boolean {
  if (attempt.kind === "server") return attempt.row.honor > 0;
  return attempt.item.honor_queued;
}

function isInFlight(attempt: HistoryAttempt): boolean {
  if (attempt.kind !== "local") return false;
  const s = attempt.item.status;
  return s === "queued" || s === "uploading" || s === "uploaded";
}

export function dayOutcome(attempts: HistoryAttempt[]): DayOutcome {
  if (attempts.some(isPass)) return "pass";
  if (attempts.some(isHonor)) return "honor";
  if (attempts.length > 0 && attempts.every(isInFlight)) return "waiting";
  return "not_done";
}

/** Prefer a pass, then an honor, then anything with a photo. */
export function bestAttempt(attempts: HistoryAttempt[]): HistoryAttempt {
  return attempts.find(isPass) ?? attempts.find(isHonor) ?? attempts.find(hasThumb) ?? attempts[0];
}

/**
 * Local items not yet on the server, plus server rows, grouped by local day
 * (newest day first). Attempts inside a day keep newest-first order.
 */
export function groupHistoryDays(
  serverRows: HistoryRow[],
  pending: PendingSubmission[],
  tasks: Task[],
): DayGroup[] {
  const onServer = new Set(serverRows.map((row) => row.client_submission_id));
  const localOnly = pending.filter((p) => !onServer.has(p.client_submission_id));

  const attempts: HistoryAttempt[] = [
    ...localOnly.map((item): HistoryAttempt => ({ kind: "local", key: `l-${item.client_submission_id}`, item })),
    ...serverRows.map((row): HistoryAttempt => ({ kind: "server", key: `s-${row.id}`, row })),
  ];

  const byDate = new Map<string, HistoryAttempt[]>();
  for (const attempt of attempts) {
    const date = dateOf(attempt);
    const list = byDate.get(date) ?? [];
    list.push(attempt);
    byDate.set(date, list);
  }

  return [...byDate.entries()]
    .sort(([a], [b]) => (a < b ? 1 : a > b ? -1 : 0))
    .map(([date, dayAttempts]) => {
      const best = bestAttempt(dayAttempts);
      return {
        date,
        title: titleOf(best, tasks),
        outcome: dayOutcome(dayAttempts),
        best,
        attempts: dayAttempts,
      };
    });
}
