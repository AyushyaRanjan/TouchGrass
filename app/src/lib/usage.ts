/**
 * Foreground screen-time per local calendar day. Honesty metric: the
 * phone should be the shortest part of touching grass.
 */

import { AppState, type AppStateStatus } from "react-native";

import { addUsageSeconds, getUsage, getUsageRange } from "@/db/local";

const FLUSH_MS = 15_000;

/** Phone-local YYYY-MM-DD. */
export function localDateKey(at: number = Date.now()): string {
  const d = new Date(at);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function startOfNextLocalDay(ymd: string): number {
  const [y, m, d] = ymd.split("-").map(Number);
  return new Date(y, m - 1, d + 1).getTime();
}

function shiftLocalDate(ymd: string, days: number): string {
  const [y, m, d] = ymd.split("-").map(Number);
  return localDateKey(new Date(y, m - 1, d + days).getTime());
}

/** "1m 40s", "2h 3m 5s", or "12s". Omits a leading 0h. */
export function formatDuration(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

let startedAt: number | null = null;
let startedDay: string | null = null;
let flushChain: Promise<void> = Promise.resolve();

async function flushOnce(): Promise<void> {
  if (startedAt === null || startedDay === null) return;
  const now = Date.now();
  let cursor = startedAt;
  let day = startedDay;
  while (cursor < now) {
    const rollover = startOfNextLocalDay(day);
    const end = Math.min(now, rollover);
    const delta = Math.floor((end - cursor) / 1000);
    if (delta > 0) await addUsageSeconds(day, delta);
    cursor = end;
    if (end >= rollover) day = localDateKey(end);
  }
  startedAt = now;
  startedDay = localDateKey(now);
}

function flush(): Promise<void> {
  flushChain = flushChain.then(flushOnce, flushOnce);
  return flushChain;
}

function onState(state: AppStateStatus): void {
  if (state === "active") {
    flushChain = flushChain.then(() => {
      startedAt = Date.now();
      startedDay = localDateKey();
    });
  } else {
    void flush().then(() => {
      if (AppState.currentState !== "active") {
        startedAt = null;
        startedDay = null;
      }
    });
  }
}

/** Call once from the root layout. Cleans up on unmount. */
export function startUsageTracker(): () => void {
  onState(AppState.currentState);
  const sub = AppState.addEventListener("change", onState);
  const timer = setInterval(() => {
    if (AppState.currentState === "active") void flush();
  }, FLUSH_MS);
  return () => {
    sub.remove();
    clearInterval(timer);
    void flush();
  };
}

export async function usageToday(): Promise<number> {
  await flush();
  return getUsage(localDateKey());
}

/** Mean over the last 7 local calendar days; days with no row count as 0. */
export async function usageSevenDayAverage(): Promise<number> {
  await flush();
  const today = localDateKey();
  const start = shiftLocalDate(today, -6);
  const rows = await getUsageRange(start, today);
  const byDate = new Map(rows.map((r) => [r.date, r.seconds]));
  let total = 0;
  for (let i = 6; i >= 0; i--) total += byDate.get(shiftLocalDate(today, -i)) ?? 0;
  return total / 7;
}
